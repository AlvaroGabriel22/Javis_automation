from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import select

from jarvis.adapters.google.client import root
from jarvis.adapters.google.panel import MAX_CLIENT_FILE, PanelLink
from jarvis.adapters.icloud import account as apple
from jarvis.adapters.persistence.commands import execute_once
from jarvis.http.database import Db
from jarvis.modules.calendar import service
from jarvis.modules.calendar.models import CalendarSource, Entry
from jarvis.modules.calendar.service import EventInput, Occurrence, Period, RoutineInput
from jarvis.modules.errors import Problem


class AppleCredentials(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    password: SecretStr = Field(min_length=1, max_length=256)


class Selection(BaseModel):
    read: list[str]
    write: str | None
    chat_targets: list[str] | None = Field(default=None, max_length=2)


def routes(auth, protected):
    router = APIRouter(prefix="/api/v1", tags=["Agenda pessoal"])
    read = [Depends(auth)]
    write = [Depends(protected)]
    link = PanelLink()

    @router.get("/calendar/google", dependencies=read)
    def google_status():
        return link.status()

    @router.put("/calendar/google/client", dependencies=write)
    async def google_client(request: Request):
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > MAX_CLIENT_FILE:
                raise Problem(413, "google_file_size", "Selecione um arquivo JSON de até 32 KiB.")
            data.extend(chunk)
        return link.import_client(bytes(data))

    @router.post("/calendar/google/connect", dependencies=write)
    def google_connect():
        return link.start()

    @router.put("/calendar/icloud", dependencies=write)
    def icloud_connect(data: AppleCredentials, db: Db):
        return apple.connect(
            db, data.email.strip().casefold(), data.password.get_secret_value().strip()
        )

    @router.delete("/calendar/icloud", dependencies=write)
    def icloud_disconnect(db: Db):
        return apple.disconnect(db)

    @router.get("/events", dependencies=read)
    def events(db: Db, anchor: date, period: Period = "week"):
        start, end = service.bounds(anchor, period)
        items = service.occurrences(db, start, end)
        return {"start": start, "end": end, "items": [service.view(item) for item in items]}

    @router.post("/events", dependencies=write)
    def create(
        data: EventInput,
        request: Request,
        db: Db,
        key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
    ):
        row, _ = execute_once(
            db,
            1,
            key,
            Entry,
            data.model_dump(
                mode="json",
                exclude=set() if "source_id" in data.model_fields_set else {"source_id"},
            ),
            lambda eid: service.write(db, data, now=request.app.state.clock(), eid=eid),
        )
        return {
            **service.view(Occurrence(row, row.starts_at, row.ends_at)),
            "conflicts": service.conflicts(db, row),
        }

    @router.patch("/events/{eid}", dependencies=write)
    def edit(
        eid: UUID,
        version: int,
        data: EventInput,
        request: Request,
        db: Db,
        occurrence: datetime | None = None,
    ):
        copies = service.change_targets(db, eid, version, occurrence)
        for copy in copies:
            service.write(db, data, now=request.app.state.clock(), existing=copy)
        row = copies[0]
        result = {
            **service.view(Occurrence(row, row.starts_at, row.ends_at)),
            "conflicts": service.conflicts(db, row),
        }
        db.commit()
        return result

    @router.delete("/events/{eid}", dependencies=write)
    def cancel(eid: UUID, version: int, db: Db, occurrence: datetime | None = None):
        copies = service.change_targets(db, eid, version, occurrence)
        for copy in copies:
            service.remove(db, copy)
        db.commit()
        return {"state": copies[0].state}

    @router.get("/calendar", dependencies=read)
    def settings(db: Db):
        rows = db.scalars(select(CalendarSource).order_by(CalendarSource.name))
        folder = root()
        error = folder / "error"
        try:
            sync_error = error.read_text()
        except FileNotFoundError:
            sync_error = None
        problems = list(db.scalars(select(Entry).where(Entry.state == "conflict")))
        return {
            "icloud": apple.status(db),
            "authorized": (folder / "token.json").is_file(),
            "error": sync_error,
            "sources": [
                dict(
                    id=r.id,
                    name=r.name,
                    provider=r.provider,
                    writable=r.writable,
                    selected=r.selected,
                    write_default=r.write_default,
                    chat_target=r.chat_target,
                    last_sync=r.last_sync,
                    error=r.error,
                    available=r.available,
                )
                for r in rows
            ],
            "conflicts": [{"title": r.title, "detail": r.detail} for r in problems],
            "routines": RoutineInput.model_validate(service.routine(db), from_attributes=True),
        }

    @router.put("/calendar/sources", dependencies=write)
    def sources(data: Selection, db: Db):
        rows = {r.id: r for r in db.scalars(select(CalendarSource).with_for_update())}
        keys = set(data.read)
        if data.write:
            keys.add(data.write)
        chat = data.chat_targets
        if chat is not None:
            if any(k not in rows or not rows[k].writable for k in chat) or len(
                {rows[k].provider for k in chat}
            ) != len(chat):
                raise Problem(422, "chat_targets", "Escolha um destino de escrita por provedor.")
            keys.update(chat)
        if any(k not in rows or not rows[k].available for k in keys) or (
            data.write and not rows[data.write].writable
        ):
            raise Problem(
                422, "source", "Confira as agendas selecionadas e a permissão de escrita."
            )
        for r in rows.values():
            r.selected, r.write_default = r.id in keys, r.id == data.write
            if chat is not None or not r.selected:
                r.chat_target = r.id in (chat or [])
        db.commit()
        return {"saved": True}

    @router.put("/calendar/routines", dependencies=write)
    def routines(data: RoutineInput, db: Db):
        service.set_routine(db, data)
        db.commit()
        return data

    return router
