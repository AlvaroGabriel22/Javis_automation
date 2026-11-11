"""O cursor e a projeção mudam na mesma transação, após a leitura de todas as páginas."""

from datetime import timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from jarvis.adapters.google.client import Google, GoogleError, path_for
from jarvis.adapters.persistence.db import utcnow
from jarvis.modules.calendar.models import CalendarImportIssue, CalendarSource, Entry
from jarvis.modules.calendar.projection import apply_remote, stamp
from jarvis.modules.calendar.service import ZONE, clear_notices


def discover(engine, google):
    entries, _ = google.pages("users/me/calendarList", {})
    with Session(engine) as db, db.begin():
        rows = {
            r.id: r
            for r in db.scalars(select(CalendarSource).where(CalendarSource.provider == "google"))
        }
        first = not rows
        default_available = not db.scalar(
            select(CalendarSource.id).where(CalendarSource.write_default)
        )
        for data in entries:
            key = data["id"]
            row = rows.pop(key, None)
            if not row:
                primary = bool(data.get("primary"))
                row = CalendarSource(
                    id=key,
                    selected=first and primary,
                    write_default=default_available
                    and first
                    and primary
                    and data.get("accessRole") in ("owner", "writer"),
                )
                db.add(row)
            row.name = data.get("summaryOverride") or data.get("summary") or key
            row.writable = data.get("accessRole") in ("owner", "writer")
            row.available = data.get("accessRole") in ("reader", "owner", "writer")
            row.timezone = data.get("timeZone") or ZONE.key
        for row in rows.values():
            row.available = False
            row.error = "Agenda sem acesso no Google."


def payload(row):
    tz = ZoneInfo(row.timezone)

    def point(dt):
        dt = dt.astimezone(tz)
        return (
            {"date": dt.date().isoformat()}
            if row.all_day
            else {"dateTime": dt.isoformat(), "timeZone": tz.key}
        )

    return {
        "summary": row.title,
        "start": point(row.starts_at),
        "end": point(row.ends_at),
        "recurrence": row.rules or [],
        "extendedProperties": {"private": {"jarvisMutation": f"{row.id}:{row.version}"}},
    }


def push(engine, google, eid: UUID):
    with Session(engine) as db:
        row = db.get_one(Entry, eid)
        source, rid = row.source_id, row.remote_id
        if not source:
            return
        state, version, etag = row.state, row.version, row.etag
        body = payload(row)
        parent, original = row.parent, row.original_at
        if parent:
            body.pop("recurrence", None)
    if not rid and parent and original:
        matches, _ = google.pages(
            path_for(source, parent) + "/instances",
            {"originalStart": original.isoformat(), "showDeleted": "true"},
        )
        match = next(
            (
                r
                for r in matches
                if r.get("originalStartTime")
                and stamp(r["originalStartTime"], ZoneInfo(row.timezone)) == original
            ),
            None,
        )
        if not match:
            raise GoogleError(409)
        rid, etag = match["id"], match["etag"]
        with Session(engine) as db, db.begin():
            pending_row = db.get_one(Entry, eid, with_for_update=True)
            pending_row.remote_id, pending_row.etag = rid, etag
    if not rid:
        raise GoogleError(409)
    path = path_for(source, rid)
    merged = None
    reconciled = False
    try:
        if state == "create":
            merged = google.request("POST", path_for(source), body=dict(body, id=rid))
        elif state == "update":
            if not etag:
                raise GoogleError(412)
            merged = google.request("PATCH", path, body=body, etag=etag)
        elif state == "delete":
            if not etag:
                try:
                    previous = google.request("GET", path)
                except GoogleError as err:
                    if err.status not in (404, 410):
                        raise
                else:
                    mark = (
                        previous.get("extendedProperties", {})
                        .get("private", {})
                        .get("jarvisMutation", "")
                    )
                    if not mark.startswith(str(eid) + ":"):
                        raise GoogleError(412)
                    google.request("DELETE", path, etag=previous["etag"])
            else:
                google.request("DELETE", path, etag=etag)
        else:
            return
    except GoogleError as err:
        if state == "delete" and err.status in (404, 410):
            pass
        elif err.status in (409, 412, 404, 410):
            try:
                current = google.request("GET", path)
            except GoogleError as lookup:
                if lookup.status not in (404, 410):
                    raise
                current = {"id": rid, "status": "cancelled"}
            if not (
                state != "delete"
                and current.get("extendedProperties", {}).get("private", {}).get("jarvisMutation")
                == f"{eid}:{version}"
            ):
                with Session(engine) as db, db.begin():
                    row = db.get_one(Entry, eid, with_for_update=True)
                    if row.version == version:
                        row.state = "clean"
                        row.etag = None
                        source_row = db.get_one(CalendarSource, source)
                        apply_remote(db, source_row, current, source_row.timezone)
                        row.detail = "Conflito de edição. O Google alterou este item. Confira antes de editar novamente."
                        row.state = "conflict"
                return
            merged = current
            reconciled = True
        else:
            raise
    with Session(engine) as db, db.begin():
        row = db.get_one(Entry, eid, with_for_update=True)
        if merged:
            row.etag = merged["etag"]
        if row.version == version:
            row.state, row.detail = "clean", None
            if reconciled and merged:
                # O evento pode ter sido editado no celular depois do aceite perdido.
                row.etag = None
                source_row = db.get_one(CalendarSource, source)
                apply_remote(db, source_row, merged, source_row.timezone)
        elif row.state == "create":
            row.state = "update"


def import_events(db, source, events, *, full: bool):
    """Isola um recurso ilegível, preservando sua última cópia e os demais eventos."""
    seen = set()
    unknown = 0
    issues = {
        row.remote_id: row
        for row in db.scalars(
            select(CalendarImportIssue).where(CalendarImportIssue.source_id == source.id)
        )
    }
    ordered = sorted(events, key=lambda e: isinstance(e, dict) and bool(e.get("recurringEventId")))
    for event in ordered:
        if (
            not isinstance(event, dict)
            or not isinstance(event.get("id"), str)
            or not 0 < len(event["id"]) <= 1024
            or not event["id"].isprintable()
        ):
            unknown += 1
            continue
        rid = event["id"]
        seen.add(rid)
        try:
            with db.begin_nested():
                if any(
                    event.get(k) is not None and not isinstance(event[k], str)
                    for k in ("etag", "summary", "status", "recurringEventId")
                ):
                    raise ValueError("Campo de evento inválido.")
                if any(
                    event.get(k) is not None and not isinstance(event[k], dict)
                    for k in ("start", "end", "originalStartTime")
                ):
                    raise ValueError("Data de evento inválida.")
                rules = event.get("recurrence", [])
                if not isinstance(rules, list) or not all(isinstance(rule, str) for rule in rules):
                    raise ValueError("Recorrência de evento inválida.")
                apply_remote(db, source, event, source.timezone)
        except (KeyError, TypeError, ValueError, OverflowError, DataError):
            issue = issues.get(rid)
            if issue is None:
                issue = CalendarImportIssue(source_id=source.id, remote_id=rid)
                issues[rid] = issue
                db.add(issue)
            etag = event.get("etag")
            issue.etag = etag[:256] if isinstance(etag, str) and etag.isprintable() else None
        else:
            if prior := issues.pop(rid, None):
                db.delete(prior)
    if full and not unknown:
        for rid in issues.keys() - seen:
            db.delete(issues.pop(rid))
    return seen if not unknown else None, len(issues) + unknown


def sync_source(engine, google, sid, clock=utcnow):
    with Session(engine) as db:
        source = db.get_one(CalendarSource, sid)
        cursor = (
            source.cursor
            if source.last_full and clock() - source.last_full < timedelta(days=7)
            else None
        )
        pending = list(
            db.scalars(
                select(Entry.id).where(
                    Entry.source_id == sid, Entry.state.in_(("create", "update", "delete"))
                )
            )
        )
    write_error = None
    for eid in pending:
        try:
            push(engine, google, eid)
        except GoogleError as exc:
            write_error = str(exc)
            with Session(engine) as db, db.begin():
                db.get_one(Entry, eid).detail = write_error
    path = path_for(sid)
    params = {"singleEvents": "false", "showDeleted": "true", "maxResults": 2500}
    try:
        events, next_cursor = google.pages(path, dict(params, syncToken=cursor))
    except GoogleError as err:
        if not cursor or err.status != 410:
            raise
        cursor = None
        events, next_cursor = google.pages(path, params)
    if not next_cursor:
        raise GoogleError(502)
    with Session(engine) as db, db.begin():
        # Inicia também no SQLite a transação de escrita antes dos savepoints por evento.
        db.execute(update(CalendarSource).where(CalendarSource.id == sid).values(last_sync=clock()))
        source = db.get_one(CalendarSource, sid, with_for_update=True)
        seen, invalid = import_events(db, source, events, full=not cursor)
        if not cursor and seen is not None:
            for row in db.scalars(
                select(Entry).where(
                    Entry.source_id == sid,
                    Entry.state.in_(("clean", "conflict")),
                    Entry.deleted.is_(False),
                )
            ):
                if row.remote_id not in seen:
                    clear_notices(db, row)
                    row.version += 1
                    row.deleted = True
        if seen is not None:
            # Cada evento ilegível com identidade está registrado. Um delta futuro
            # corrige/retira o item sem reler a agenda inteira a cada consulta.
            source.cursor = next_cursor
            if not cursor:
                source.last_full = clock()
        messages = []
        if invalid:
            messages.append(
                f"Leitura parcial no Google. Eventos incompatíveis: {invalid}. Os demais estão disponíveis."
            )
        if write_error:
            messages.append(write_error)
        source.error = " ".join(messages) or None


def sync(engine: Engine, google: Google):
    try:
        discover(engine, google)
    except (GoogleError, KeyError, ValueError, OSError) as exc:
        with Session(engine) as db, db.begin():
            for source in db.scalars(
                select(CalendarSource).where(CalendarSource.provider == "google")
            ):
                source.error = (
                    str(exc)
                    if isinstance(exc, GoogleError)
                    else "Falha ao ler o calendário Google."
                )
        raise
    with Session(engine) as db:
        ids = list(
            db.scalars(
                select(CalendarSource.id).where(
                    CalendarSource.provider == "google",
                    CalendarSource.available,
                    CalendarSource.selected
                    | CalendarSource.write_default
                    | CalendarSource.id.in_(
                        select(Entry.source_id).where(
                            Entry.state.in_(("create", "update", "delete"))
                        )
                    ),
                )
            )
        )
    for sid in ids:
        try:
            sync_source(engine, google, sid)
        except (GoogleError, KeyError, ValueError, TypeError, OverflowError) as exc:
            with Session(engine) as db, db.begin():
                db.get_one(CalendarSource, sid).error = (
                    str(exc)
                    if isinstance(exc, GoogleError)
                    else "O Google retornou dados de calendário inválidos."
                )
