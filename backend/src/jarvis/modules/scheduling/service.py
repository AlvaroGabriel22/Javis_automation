from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.commands import execute_once, page
from jarvis.adapters.persistence.models import Schedule
from jarvis.modules.errors import Problem
from jarvis.modules.identity.contacts import require_contact

EDITABLE = ("scheduled", "needs_review", "failed")


def _future(value: datetime, now: datetime) -> datetime:
    if value.utcoffset() is None or value <= now:
        raise Problem(422, "schedule_in_past", "Escolha um horário futuro com fuso.")
    return value.astimezone(UTC)


def stage_schedule(
    db: Session,
    owner: int,
    contact_id: UUID,
    scheduled_at: datetime,
    text: str,
    now: datetime,
    route_key: str | None,
    recurrence: str = "none",
    *,
    sid: UUID | None = None,
    immediate: bool = False,
) -> Schedule:
    if (
        recurrence not in {"none", "daily", "weekly"}
        or not text.strip()
        or len(text) > 4000
        or "\0" in text
    ):
        raise Problem(422, "invalid_schedule", "Confira a mensagem e a repetição.")
    instant = now.astimezone(UTC) if immediate else _future(scheduled_at, now)
    require_contact(db, owner, contact_id)
    row = Schedule(
        owner_id=owner,
        contact_id=contact_id,
        scheduled_at=instant,
        text=text,
        created_at=now,
        status="scheduled",
        route_key=route_key,
        recurrence=recurrence,
    )
    if sid is not None:
        row.id = sid
    db.add(row)
    db.flush()
    return row


def create_schedule(
    db: Session,
    owner: int,
    key: str,
    contact_id: UUID,
    scheduled_at: datetime,
    text: str,
    now: datetime,
    route_key: str | None = None,
    recurrence: str = "none",
) -> tuple[Schedule, bool]:
    payload = {
        "contact_id": str(contact_id),
        "scheduled_at": scheduled_at.astimezone(UTC).isoformat(),
        "text": text,
    }
    # Mantém as impressões digitais das solicitações idempotentes anteriores à S02.
    if recurrence != "none":
        payload["recurrence"] = recurrence
    return execute_once(
        db,
        owner,
        key,
        Schedule,
        payload,
        lambda sid: stage_schedule(
            db, owner, contact_id, scheduled_at, text, now, route_key, recurrence, sid=sid
        ),
    )


def _edited(db: Session, owner: int, sid: UUID, stmt) -> Schedule:
    row = db.scalar(stmt.returning(Schedule), execution_options={"populate_existing": True})
    if row is not None:
        return row
    if not db.scalar(select(Schedule.id).where(Schedule.id == sid, Schedule.owner_id == owner)):
        raise Problem(404, "schedule_not_found", "Agendamento não encontrado.")
    raise Problem(
        409,
        "schedule_locked",
        "Esse envio já foi iniciado, pertence a outro vínculo ou não pode ser alterado.",
    )


def stage_reschedule(
    db: Session, owner: int, sid: UUID, scheduled_at: datetime, now: datetime, route_key: str | None
) -> Schedule:
    instant = _future(scheduled_at, now)
    stmt = update(Schedule).where(
        Schedule.id == sid, Schedule.owner_id == owner, Schedule.status.in_(EDITABLE)
    )
    if route_key:
        stmt = stmt.where(or_(Schedule.route_key.is_(None), Schedule.route_key == route_key))
    values = {
        "scheduled_at": instant,
        "status": "scheduled",
        "result_detail": None,
        "retry_at": None,
        "attempts": 0,
    }
    if route_key:
        values["route_key"] = route_key
    return _edited(db, owner, sid, stmt.values(**values))


def stage_cancel(db: Session, owner: int, sid: UUID, route_key: str | None = None) -> Schedule:
    stmt = update(Schedule).where(
        Schedule.id == sid,
        Schedule.owner_id == owner,
        Schedule.status.in_((*EDITABLE, "cancelled")),
    )
    if route_key:
        stmt = stmt.where(Schedule.route_key == route_key)
    return _edited(db, owner, sid, stmt.values(status="cancelled", retry_at=None))


def cancel_schedule(db: Session, owner: int, sid: UUID) -> Schedule:
    row = stage_cancel(db, owner, sid)
    db.commit()
    return row


def list_schedules(db: Session, owner: int, cursor: UUID | None, limit: int):
    return page(db, owner, Schedule, cursor, limit)
