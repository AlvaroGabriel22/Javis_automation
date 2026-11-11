"""Painel financeiro do proprietário do vínculo ativo."""

from datetime import date
from hashlib import sha256
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from jarvis.http.database import Db
from jarvis.modules.calendar.service import ZONE, bounds
from jarvis.modules.errors import Problem
from jarvis.modules.personal.finance import ledger_rows, monthly_budget
from jarvis.modules.personal.models import LedgerEntry
from jarvis.modules.spreadsheets import exports, service
from jarvis.modules.spreadsheets.models import SheetNote

Cents = Annotated[int, Field(strict=True, ge=0, le=99_999_999_999)]
NoteKey = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$"),
]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Correction(Input):
    amount_cents: Annotated[int, Field(strict=True, ge=1, le=99_999_999_999)]
    expected_version: Annotated[int, Field(strict=True, ge=1)]


class Reserve(Input):
    month: str
    amount_cents: Cents


class Note(Input):
    day: date
    description: str = Field(min_length=1, max_length=240)
    quantity: str = Field(default="", max_length=80)


class GoogleTarget(Input):
    enabled: Annotated[bool, Field(strict=True)]


def month_start(value: str) -> date:
    try:
        result = date.fromisoformat(value + "-01")
        if result.strftime("%Y-%m") == value and 1 < result.year < 9999:
            return result
    except ValueError:
        pass
    raise Problem(422, "finance_month", "Informe o mês no formato AAAA-MM.")


def binding_token(route: str) -> str:
    return sha256(route.encode()).hexdigest()


SeenBinding = Annotated[str | None, Header(alias="X-Jarvis-Binding")]


def bound_route(expected: str | None) -> str:
    route = exports.active_route()
    if not route:
        raise Problem(
            409,
            "finance_unbound",
            "Conecte o WhatsApp e vincule seu número para acessar os dados financeiros.",
        )
    if expected != binding_token(route):
        raise Problem(
            409,
            "finance_binding_changed",
            "O vínculo mudou. Reabra as finanças e confira os dados antes de continuar.",
        )
    return route


def routes(auth, protected):
    router = APIRouter(prefix="/api/v1/finance", tags=["Finanças e planilhas"])
    read = [Depends(auth)]
    write = [Depends(protected)]

    @router.get("", dependencies=read)
    def overview(db: Db, request: Request, month: str | None = None):
        first = (
            month_start(month)
            if month is not None
            else request.app.state.clock().astimezone(ZONE).date().replace(day=1)
        )
        end = bounds(first, "month")[1].date()
        route = exports.active_route()
        entries, notes = [], []
        income = expense = reserve = 0
        budget = None
        if route:
            limit = monthly_budget(db, route, first)
            budget = limit.amount if limit else None
            reserve = service.reserve_cents(db, route, first)
            rows = db.scalars(
                select(LedgerEntry)
                .where(*ledger_rows(route, first, end), LedgerEntry.kind.in_(("expense", "income")))
                .order_by(LedgerEntry.day.desc(), LedgerEntry.inbound_id.desc())
            )
            for row in rows:
                if row.kind == "expense":
                    expense += row.amount
                else:
                    income += row.amount
                entries.append(
                    {
                        "id": f"F{row.inbound_id}",
                        "day": row.day,
                        "kind": row.kind,
                        "description": row.description,
                        "amount_cents": row.amount,
                        "version": row.version,
                    }
                )
            notes = [
                {
                    "id": f"P{n.id}",
                    "day": n.day,
                    "description": n.description,
                    "quantity": n.quantity,
                }
                for n in db.scalars(
                    select(SheetNote)
                    .where(
                        SheetNote.owner_id == 1,
                        SheetNote.route == route,
                        SheetNote.deleted.is_(False),
                        SheetNote.day >= first,
                        SheetNote.day < end,
                    )
                    .order_by(SheetNote.day.desc(), SheetNote.id.desc())
                )
            ]
        return {
            "month": first.strftime("%Y-%m"),
            "bound": bool(route),
            "binding_token": binding_token(route) if route else None,
            "entries": entries,
            "notes": notes,
            "totals": {
                "expense_cents": expense,
                "income_cents": income,
                "budget_cents": budget,
                "reserve_cents": reserve,
                "available_cents": budget - expense - reserve if budget is not None else None,
            },
            "sheets": exports.statuses(db, route),
        }

    @router.patch("/entries/F{entry_id}", dependencies=write, status_code=204)
    def correct(entry_id: int, data: Correction, db: Db, expected: SeenBinding = None):
        route = bound_route(expected)
        service.correct_amount(
            db, route, entry_id, data.amount_cents, expected_version=data.expected_version
        )
        db.commit()
        return Response(status_code=204)

    @router.put("/reserve", dependencies=write, status_code=204)
    def reserve(data: Reserve, db: Db, expected: SeenBinding = None):
        route = bound_route(expected)
        service.set_reserve(db, route, month_start(data.month), data.amount_cents)
        db.commit()
        return Response(status_code=204)

    @router.post("/notes", dependencies=write, status_code=201)
    def add_note(data: Note, key: NoteKey, db: Db, expected: SeenBinding = None):
        route = bound_route(expected)
        note = service.create_note(
            db,
            route,
            data.day,
            data.description,
            data.quantity,
            request_key="panel:" + sha256(key.encode()).hexdigest(),
        )
        db.commit()
        return {"id": f"P{note.id}"}

    @router.put("/exports/google", dependencies=write, status_code=204)
    def configure_google(data: GoogleTarget, db: Db, expected: SeenBinding = None):
        route = bound_route(expected)
        exports.set_google_enabled(db, route, data.enabled)
        db.commit()
        return Response(status_code=204)

    @router.post("/exports/{target}", dependencies=write, status_code=204)
    def request_export(target: Literal["excel", "google"], db: Db, expected: SeenBinding = None):
        route = bound_route(expected)
        exports.request_sync(db, route, target)
        db.commit()
        return Response(status_code=204)

    @router.get("/exports/excel/file", dependencies=read)
    def download(db: Db, expected: SeenBinding = None):
        route = bound_route(expected)
        file = exports.download_path(db, route)
        return FileResponse(
            file,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="Jarvis.xlsx",
        )

    return router
