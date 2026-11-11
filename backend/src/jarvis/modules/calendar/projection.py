"""Atualiza a agenda local a partir de eventos normalizados pelos provedores."""

from datetime import datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select

from jarvis.modules.calendar.models import Entry
from jarvis.modules.calendar.service import ZONE, clear_notices, recurrence_set


def stamp(value, zone):
    if "date" in value:
        return datetime.combine(datetime.fromisoformat(value["date"]).date(), time.min, zone)
    dt = datetime.fromisoformat(value["dateTime"])
    return dt if dt.tzinfo else dt.replace(tzinfo=zone)


def apply_remote(db, source, event, fallback_zone):
    rid = event["id"]
    row = db.scalar(
        select(Entry).where(Entry.source_id == source.id, Entry.remote_id == rid).with_for_update()
    )
    if row and (
        row.state not in ("clean", "conflict")
        or row.etag == event.get("etag")
        and row.deleted == (event.get("status") == "cancelled")
    ):
        return
    exc = event.get("originalStartTime")
    gone = event.get("status") == "cancelled"
    if gone and not row and not exc:
        return
    zone = ZoneInfo(
        event.get("start", {}).get("timeZone") or (exc or {}).get("timeZone") or fallback_zone
    )
    if row:
        clear_notices(db, row)
        row.version += 1
    else:
        row = Entry(id=uuid4(), kind="event", source_id=source.id, remote_id=rid, version=1)
        db.add(row)
    row.deleted, row.etag, row.state, row.detail = gone, event.get("etag"), "clean", None
    if gone and not exc and row.title is not None:
        if row.rules:
            for child in db.scalars(
                select(Entry).where(Entry.source_id == source.id, Entry.parent == rid)
            ):
                clear_notices(db, child)
                child.deleted = True
                child.state = "clean"
                child.version += 1
        return
    start = event.get("start") or exc
    if "date" in start:
        zone = ZONE
    row.starts_at = stamp(start, zone)
    row.ends_at = stamp(event["end"], zone) if event.get("end") else row.starts_at
    row.all_day = "date" in start
    row.title = event.get("summary") or "(Sem título)"
    row.timezone = zone.key
    row.parent = event.get("recurringEventId")
    row.original_at = stamp(exc, zone) if exc else None
    row.rules = event.get("recurrence") or []
    if row.rules:
        recurrence_set(row)
    # Aviso pessoal do Jarvis. O provedor mantém seus próprios alertas.
    if row.parent:
        master = db.scalar(
            select(Entry).where(Entry.source_id == source.id, Entry.remote_id == row.parent)
        )
        if master:
            clear_notices(db, master)
            row.alert_minutes = master.alert_minutes
