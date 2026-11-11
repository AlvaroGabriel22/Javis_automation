"""Reserva e conclusão de uma ocorrência. A chamada ao WhatsApp acontece fora da transação."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import Contact, Schedule, ScheduleAttempt
from jarvis.modules.messaging.models import Chat, Inbound, Outbound

GRACE = timedelta(minutes=5)
IN_FLIGHT = timedelta(minutes=2)
ZONE = ZoneInfo("America/Manaus")


@dataclass(frozen=True)
class Job:
    id: UUID
    token: UUID
    phone: str
    text: str
    due: datetime
    requested_at: datetime | None = None

    def text_at(self, at: datetime) -> str:
        if self.requested_at is None:
            return self.text
        minutes = int((at - self.requested_at).total_seconds() // 60)
        if minutes < 1:
            since = "agora há pouco"
        elif minutes < 60:
            since = "há um minuto" if minutes == 1 else f"há {minutes} minutos"
        else:
            since = self.requested_at.astimezone(ZONE).strftime("em %d/%m/%Y às %H:%M")
        return f"{self.text} Recebi o pedido {since}."


def _notify(db: Session, row: Schedule, at: datetime, message: str) -> None:
    if not row.route_key:
        return
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    db.execute(insert(Chat).values(chat_id=row.route_key).on_conflict_do_nothing())
    event = db.scalar(
        insert(Inbound)
        .values(
            chat_id=row.route_key,
            message_id=f"schedule:{row.id}:{row.status}:{row.scheduled_at.isoformat()}",
            kind="notice",
            text="[Resultado do agendamento]",
            received_at=at,
            available_at=at,
            state="completed",
            attempts=0,
        )
        .on_conflict_do_nothing()
        .returning(Inbound.id)
    )
    if event is not None:
        contact = db.get_one(Contact, row.contact_id)
        when = row.scheduled_at.astimezone(ZONE).strftime("%d/%m às %H:%M")
        db.add(
            Outbound(
                id=event,
                text=f"#{str(row.id)[:8]} · {contact.name} · {when} (Manaus). {message}",
                state="ready",
                available_at=at,
                attempts=0,
            )
        )


def claim(engine: Engine, route: str, now: datetime) -> Job | None:
    eligible = (
        Schedule.route_key == route,
        Schedule.status == "scheduled",
        Schedule.scheduled_at <= now,
        Schedule.scheduled_at >= now - GRACE,
        or_(Schedule.retry_at.is_(None), Schedule.retry_at <= now),
    )
    candidate = (
        select(Schedule.id)
        .where(*eligible)
        .order_by(Schedule.scheduled_at, Schedule.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    token = uuid4()
    with Session(engine) as db, db.begin():
        row = db.scalar(
            update(Schedule)
            .where(Schedule.id == candidate, *eligible)
            .values(status="sending", active_attempt=token, attempts=Schedule.attempts + 1)
            .returning(Schedule)
        )
        if row is None:
            return None
        db.add(ScheduleAttempt(schedule_id=row.id, token=token, started_at=now, outcome="sending"))
        return Job(
            row.id,
            token,
            db.get_one(Contact, row.contact_id).phone,
            row.text,
            row.scheduled_at,
            row.relay_requested_at,
        )


def _next(db: Session, row: Schedule, now: datetime) -> None:
    if row.recurrence not in ("daily", "weekly"):
        return
    step = timedelta(days=1 if row.recurrence == "daily" else 7)
    due = row.scheduled_at.astimezone(ZONE) + step
    while due <= now:
        due += step
    db.add(
        Schedule(
            owner_id=row.owner_id,
            contact_id=row.contact_id,
            text=row.text,
            route_key=row.route_key,
            recurrence=row.recurrence,
            scheduled_at=due,
            created_at=now,
            status="scheduled",
        )
    )


def finish(
    engine: Engine, job: Job, outcome: str, now: datetime, receipt: str | None = None
) -> None:
    with Session(engine) as db, db.begin():
        row = db.scalar(
            update(Schedule)
            .where(
                Schedule.id == job.id,
                Schedule.status == "sending",
                Schedule.active_attempt == job.token,
            )
            .values(active_attempt=None)
            .returning(Schedule)
        )
        if row is None:
            return
        attempt = db.get_one(ScheduleAttempt, (row.id, job.token))
        attempt.ended_at, attempt.outcome, attempt.external_id = now, outcome, receipt
        if (
            outcome in {"not_sent", "lookup_incomplete"}
            and now <= row.scheduled_at + GRACE
            and row.attempts < 4
        ):
            row.status = "scheduled"
            row.retry_at = now + timedelta(seconds=5 * 2 ** (row.attempts - 1))
            return
        status, detail = {
            "accepted": ("accepted", "O WhatsApp aceitou o envio."),
            "uncertain": (
                "uncertain",
                "O WhatsApp não confirmou o resultado. Confira a conversa antes de enviar novamente.",
            ),
            "invalid_number": (
                "failed",
                "O WhatsApp informou que o número consultado não está registrado. Confira o contato.",
            ),
            "lookup_incomplete": (
                "failed",
                "A consulta ao WhatsApp não identificou o destinatário. Confira o número na agenda do aparelho e remarque.",
            ),
            "late": ("needs_review", "O horário passou. Escolha outro horário no painel."),
            "not_sent": (
                "failed",
                "O envio não foi iniciado. Confira o canal e remarque no painel.",
            ),
        }[outcome]
        row.status, row.result_detail = status, detail
        if outcome == "accepted":
            _next(db, row, now)
        _notify(db, row, now, detail)


def recover(engine: Engine, route: str, now: datetime) -> None:
    with Session(engine) as db, db.begin():
        late = (
            Schedule.status == "scheduled",
            Schedule.route_key == route,
            Schedule.scheduled_at < now - GRACE,
        )
        selected = (
            select(Schedule.id)
            .where(*late)
            .order_by(Schedule.scheduled_at)
            .limit(32)
            .with_for_update(skip_locked=True)
        )
        for row in db.scalars(
            update(Schedule)
            .where(Schedule.id.in_(selected), *late)
            .values(status="needs_review", result_detail="Prazo de envio ultrapassado.")
            .returning(Schedule)
        ):
            _notify(db, row, now, "O prazo de envio passou. Escolha outro horário no painel.")
        abandoned = db.execute(
            select(Schedule.id, Schedule.active_attempt, Schedule.scheduled_at)
            .join(
                ScheduleAttempt,
                (Schedule.id == ScheduleAttempt.schedule_id)
                & (Schedule.active_attempt == ScheduleAttempt.token),
            )
            .where(
                Schedule.status == "sending",
                Schedule.route_key == route,
                ScheduleAttempt.started_at <= now - IN_FLIGHT,
            )
            .limit(32)
        ).all()
    for sid, token, due in abandoned:
        finish(engine, Job(sid, token, "", "", due), "uncertain", now)
