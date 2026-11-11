"""Agenda do proprietário. Um intervalo termina no início do próximo período."""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from dateutil.rrule import rrule, rruleset, rrulestr
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import String, cast, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from jarvis.modules.calendar.models import CalendarSource, Entry, Routine
from jarvis.modules.calendar.scope import PROVIDERS as PROVIDERS
from jarvis.modules.errors import Problem
from jarvis.modules.messaging.models import Inbound, Outbound

ZONE = ZoneInfo("America/Manaus")
Period = Literal["day", "week", "month"]
RULES = {
    "daily": "RRULE:FREQ=DAILY",
    "weekly": "RRULE:FREQ=WEEKLY",
    "monthly": "RRULE:FREQ=MONTHLY",
}


class EventInput(BaseModel):
    kind: Literal["event", "reminder"] = "event"
    title: str = Field(min_length=1, max_length=300)
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    all_day: bool = False
    recurrence: Literal["none", "daily", "weekly", "monthly", "custom"] = "none"
    alert_minutes: int | None = Field(default=None, ge=0, le=10080)
    source_id: str | None = Field(default=None, max_length=512)


class RoutineInput(BaseModel):
    morning: bool = True
    evening: bool = True
    birthday: str | None = Field(default=None, pattern=r"^\d\d-\d\d$")
    leap_day: Literal["02-28", "03-01"] = "02-28"


def routine(db: Session) -> Routine:
    row = db.get(Routine, 1)
    if row is None:
        row = Routine(owner_id=1, morning=True, evening=True, leap_day="02-28")
    return row


def set_routine(db: Session, data: RoutineInput):
    if data.birthday:
        try:
            date.fromisoformat("2000-" + data.birthday)
        except ValueError:
            raise Problem(422, "birthday", "Confira o dia e o mês do aniversário.") from None
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    db.execute(insert(Routine).values(owner_id=1).on_conflict_do_nothing())
    row = db.get_one(Routine, 1, with_for_update=True)
    for enabled, key in (
        (data.morning, "routine:7:"),
        (data.evening, "routine:21:"),
        (row.birthday == data.birthday and row.leap_day == data.leap_day, "birthday:"),
    ):
        if not enabled:
            ids = select(Inbound.id).where(Inbound.message_id.startswith(key))
            db.execute(
                update(Outbound)
                .where(Outbound.state == "ready", Outbound.id.in_(ids))
                .values(state="failed")
            )
    for name, value in data.model_dump().items():
        setattr(row, name, value)


def bounds(anchor: date, period: Period):
    start = anchor
    if period == "week":
        start -= timedelta(days=start.weekday())
    if period == "month":
        start = start.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
    else:
        end = start + timedelta(days=7 if period == "week" else 1)
    return datetime.combine(start, time.min, ZONE), datetime.combine(end, time.min, ZONE)


@dataclass
class Occurrence:
    entry: Entry
    start: datetime
    end: datetime
    copies: list[Entry] = field(default_factory=list)


def recurrence_set(row: Entry):
    zone = ZoneInfo(row.timezone)
    start = row.starts_at.astimezone(zone)
    if row.all_day:
        start = start.replace(tzinfo=None)
    result = rruleset()
    result.rdate(start)
    for line in row.rules:
        header, _, value = line.partition(":")
        kind, *parameters = header.split(";")
        if kind in ("RRULE", "EXRULE"):
            rule = rrulestr("RRULE:" + value, dtstart=start)
            if not isinstance(rule, rrule):
                raise ValueError("Esperada uma regra por linha do calendário.")
            (result.rrule if kind == "RRULE" else result.exrule)(rule)
        elif kind in ("RDATE", "EXDATE"):
            tzid = next((p[5:] for p in parameters if p.startswith("TZID=")), None)
            date_zone = ZoneInfo(tzid) if tzid else zone
            for raw in value.split(","):
                instant = datetime.fromisoformat(raw)
                if row.all_day:
                    instant = instant.replace(tzinfo=None)
                elif instant.tzinfo is None:
                    instant = instant.replace(tzinfo=date_zone)
                (result.rdate if kind == "RDATE" else result.exdate)(instant)
        else:
            raise ValueError("Regra de calendário desconhecida.")
    return result


def occurrences(
    db: Session,
    start: datetime,
    end: datetime,
    *,
    merge: bool = True,
    providers: frozenset[str] = frozenset(),
):
    visible = select(CalendarSource.id).where(CalendarSource.selected, CalendarSource.available)
    if providers:
        visible = visible.where(CalendarSource.provider.in_(providers))
        origins = Entry.source_id.in_(visible)
    else:
        origins = or_(Entry.source_id.is_(None), Entry.source_id.in_(visible))
    stmt = select(Entry).where(
        origins,
        Entry.deleted.is_(False),
        Entry.starts_at < end,
        or_(Entry.ends_at > start, cast(Entry.rules, String) != "[]"),
    )
    rows = db.scalars(stmt).all()
    result: list[Occurrence] = []
    for row in rows:
        if not row.rules:
            if row.ends_at > start:
                result.append(Occurrence(row, row.starts_at, row.ends_at))
            continue
        tz = ZoneInfo(row.timezone)
        dt = row.starts_at.astimezone(tz)
        duration = row.ends_at.astimezone(tz) - dt
        overrides = set(
            db.scalars(
                select(Entry.original_at).where(
                    Entry.source_id == row.source_id,
                    Entry.parent == (row.remote_id or str(row.id)),
                    Entry.original_at >= start - duration - timedelta(days=1),
                    Entry.original_at < end,
                )
            )
        )
        lower = start.astimezone(tz) - duration - timedelta(days=1)
        upper = end.astimezone(tz)
        if row.all_day:
            lower, upper = lower.replace(tzinfo=None), upper.replace(tzinfo=None)
        rule = recurrence_set(row)
        for instant in rule.between(lower, upper, inc=True):
            if row.all_day:
                instant = instant.replace(tzinfo=tz)
            until = instant + duration
            if instant < end and until > start and instant.astimezone(UTC) not in overrides:
                result.append(Occurrence(row, instant, until))
    ordered = sorted(
        result,
        key=lambda item: (
            item.start,
            item.entry.title,
            item.entry.id != item.entry.pair_id,
            str(item.entry.id),
        ),
    )
    if not merge:
        return ordered
    groups = {}
    output = []
    for item in ordered:
        row = item.entry
        if row.pair_id:
            key = (row.pair_id, row.title, item.start, item.end, row.all_day, tuple(row.rules))
            if key in groups:
                groups[key].copies.append(row)
                continue
            groups[key] = item
        item.copies.append(row)
        output.append(item)
    return output


def conflicts(db: Session, row: Entry):
    if row.kind != "event" or row.deleted:
        return []
    return [
        item.entry.title
        for item in occurrences(db, row.starts_at, row.ends_at)
        if item.entry.id != row.id
        and item.entry.kind == "event"
        and not (row.pair_id and row.pair_id == item.entry.pair_id)
    ]


def clear_notices(db: Session, row: Entry):
    ids = select(Inbound.id).where(Inbound.message_id.startswith(f"cal:{row.id}:"))
    db.execute(
        update(Outbound)
        .where(Outbound.id.in_(ids), Outbound.state == "ready")
        .values(state="failed")
    )


def editable(db: Session, row: Entry):
    if row.deleted:
        raise Problem(404, "event_absent", "Compromisso ou lembrete removido.")
    if row.source_id:
        source = db.get_one(CalendarSource, row.source_id)
        if not source.writable or not source.available:
            raise Problem(409, "read_only", "Esta agenda está indisponível para edição.")


def locked(db: Session, eid: UUID, version: int):
    row = db.get(Entry, eid, with_for_update=True)
    if row is None or row.deleted:
        raise Problem(404, "event_absent", "Compromisso ou lembrete não encontrado.")
    editable(db, row)
    # Compare-and-swap inclusive no SQLite, que não implementa FOR UPDATE.
    ok = db.scalar(
        update(Entry)
        .where(Entry.id == eid, Entry.version == version, Entry.deleted.is_(False))
        .values(version=Entry.version + 1)
        .returning(Entry.version)
    )
    if ok is None:
        raise Problem(
            409, "event_changed", "O item mudou. Atualize a agenda e confira a nova versão."
        )
    clear_notices(db, row)
    return row


def write(
    db: Session,
    data: EventInput,
    *,
    now: datetime,
    route: str | None = None,
    existing: Entry | None = None,
    eid: UUID | None = None,
):
    start = data.starts_at.astimezone(ZONE)
    end = data.ends_at or start + timedelta(hours=1)
    if data.all_day:
        start = datetime.combine(start.date(), time.min, ZONE)
        end = (
            datetime.combine(end.astimezone(ZONE).date(), time.min, ZONE)
            if data.ends_at
            else start + timedelta(days=1)
        )
    if data.kind == "reminder":
        if data.recurrence != "none" or data.all_day or data.alert_minutes is not None:
            raise Problem(
                422, "reminder_options", "O lembrete avulso precisa de um horário e ocorre uma vez."
            )
        if start <= now:
            raise Problem(422, "past_reminder", "Escolha um horário futuro para o lembrete.")
        end = start + timedelta(seconds=1)
    title = data.title.strip()
    if end <= start or not title:
        raise Problem(422, "event_input", "Confira o título e o intervalo do compromisso.")
    if existing and existing.kind != data.kind:
        raise Problem(422, "event_kind", "O tipo do item não pode ser alterado.")
    row = existing or Entry(
        id=eid or uuid4(),
        kind=data.kind,
        route_key=route,
        timezone=ZONE.key,
        rules=[],
        state="local",
        version=1,
        deleted=False,
    )
    if not existing and data.kind == "event":
        if "source_id" in data.model_fields_set:
            target = db.get(CalendarSource, data.source_id) if data.source_id else None
            if data.source_id and (not target or not target.selected):
                raise Problem(409, "write_calendar", "Escolha uma das agendas selecionadas.")
        else:
            target = db.scalar(select(CalendarSource).where(CalendarSource.write_default))
        if target:
            if not target.available or not target.writable:
                raise Problem(409, "write_calendar", "Selecione uma agenda de escrita disponível.")
            row.source_id = target.id
            row.remote_id = row.id.hex + (".ics" if target.provider == "icloud" else "")
            row.state = "create"
    if existing and row.source_id and row.state != "create":
        row.state = "update"
    row.title, row.starts_at, row.ends_at, row.all_day = title, start, end, data.all_day
    if data.recurrence == "custom":
        if not existing or not row.rules:
            raise Problem(422, "rule", "Regra externa indisponível.")
    else:
        row.rules = [RULES[data.recurrence]] if data.recurrence in RULES else []
    row.alert_minutes, row.notified_at, row.detail = data.alert_minutes, None, None
    db.add(row)
    db.flush()
    return row


def remove(db: Session, row: Entry):
    if row.rules:
        for child in db.scalars(
            select(Entry).where(
                Entry.source_id == row.source_id, Entry.parent == (row.remote_id or str(row.id))
            )
        ):
            clear_notices(db, child)
            child.deleted = True
            child.state = "clean" if row.source_id else "local"
    row.deleted = True
    if row.source_id:
        row.state = "delete"


def view(item: Occurrence):
    row = item.entry
    recurrence = next(
        (k for k, v in RULES.items() if [v] == row.rules), "custom" if row.rules else "none"
    )
    return dict(
        id=row.id,
        kind=row.kind,
        title=row.title,
        starts_at=item.start,
        ends_at=item.end,
        master_start=row.starts_at,
        master_end=row.ends_at,
        all_day=row.all_day,
        recurrence=recurrence,
        version=row.version,
        state=row.state,
        detail=row.detail,
        alert_minutes=row.alert_minutes,
        source_id=row.source_id,
        copies=[
            {"source_id": copy.source_id, "state": copy.state, "detail": copy.detail}
            for copy in item.copies
            if copy.source_id
        ],
    )


def change_targets(db: Session, eid: UUID, version: int, instant: datetime | None = None):
    primary = db.get(Entry, eid)
    if not primary or not primary.pair_id:
        return [change_target(db, eid, version, instant)]
    peers = db.scalars(
        select(Entry).where(Entry.pair_id == primary.pair_id).order_by(Entry.id).with_for_update()
    ).all()

    def shape(r: Entry):
        return (r.title, r.starts_at, r.ends_at, r.all_day, r.rules, r.deleted)

    if any(shape(peer) != shape(primary) for peer in peers):
        raise Problem(
            409,
            "copies_changed",
            "As cópias divergem. Confira e concilie o compromisso no Google e no iCloud antes de alterá-lo pelo Jarvis.",
        )
    changed = {
        peer.id: change_target(db, peer.id, version if peer.id == eid else peer.version, instant)
        for peer in peers
    }
    first = changed.pop(eid)
    result = [first, *changed.values()]
    if instant is not None:
        for row in result:
            row.pair_id = first.id
    return result


def change_target(db: Session, eid: UUID, version: int, instant: datetime | None):
    master = locked(db, eid, version)
    if instant is None:
        return master
    if (
        instant.tzinfo is None
        or not master.rules
        or not any(
            i.entry.id == eid and i.start == instant
            for i in occurrences(db, instant, instant + timedelta(seconds=1), merge=False)
        )
    ):
        raise Problem(409, "occurrence_changed", "Essa ocorrência mudou. Atualize a agenda.")
    if master.source_id and master.state not in ("clean", "conflict"):
        raise Problem(409, "series_pending", "Aguarde a sincronização da série.")
    instance = Entry(
        id=uuid4(),
        kind="event",
        title=master.title,
        starts_at=instant,
        ends_at=instant + (master.ends_at - master.starts_at),
        timezone=master.timezone,
        all_day=master.all_day,
        rules=[],
        route_key=master.route_key,
        parent=master.remote_id or str(master.id),
        original_at=instant,
        source_id=master.source_id,
        state="local",
        version=1,
        deleted=False,
        alert_minutes=master.alert_minutes,
    )
    if master.source_id and db.get_one(CalendarSource, master.source_id).provider == "icloud":
        instance.etag = master.etag
        instance.remote_id = f"{master.remote_id}#{int(instant.timestamp())}"
    db.add(instance)
    return instance
