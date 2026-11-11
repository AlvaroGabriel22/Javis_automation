from calendar import isleap
from datetime import date, datetime, timedelta

from sqlalchemy import Engine, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import Owner
from jarvis.modules.assistant.greetings import greeting_options
from jarvis.modules.calendar.models import CalendarSource, Entry
from jarvis.modules.calendar.scope import PROVIDERS
from jarvis.modules.calendar.service import ZONE, occurrences, routine
from jarvis.modules.identity.preferences import Preferences
from jarvis.modules.messaging.models import Chat, Inbound, Outbound
from jarvis.modules.messaging.reactions import CONVERSATION_BREAK
from jarvis.modules.stickers.policy import reaction_safe_reply


def stage(
    db: Session, route: str, key: str, text: str, now: datetime, expires: datetime | None = None
):
    insert = pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert
    db.execute(insert(Chat).values(chat_id=route).on_conflict_do_nothing())
    mid = db.scalar(
        insert(Inbound)
        .values(
            chat_id=route,
            message_id=key,
            kind="notice",
            text="[Aviso pessoal]",
            received_at=now,
            available_at=now,
            state="completed",
            attempts=0,
        )
        .on_conflict_do_nothing()
        .returning(Inbound.id)
    )
    if mid is not None:
        db.add(
            Outbound(
                id=mid, text=text, state="ready", available_at=now, attempts=0, expires_at=expires
            )
        )


def birthday_today(birthday: str | None, leap_day: str, day: date):
    when = leap_day if birthday == "02-29" and not isleap(day.year) else birthday
    return when == day.strftime("%m-%d")


def _routine_greeting(db: Session, route: str, due: datetime, label: str, profile: Preferences):
    last_words = db.scalar(
        select(Inbound.text)
        .where(
            Inbound.chat_id == route,
            Inbound.kind.in_(("text", "audio")),
            Inbound.received_at >= due - CONVERSATION_BREAK,
        )
        .order_by(Inbound.id.desc())
        .limit(1)
    )
    options = greeting_options(
        label, profile, social=not last_words or reaction_safe_reply(last_words)
    )
    offset = 6 if due.hour == 21 else 0
    return options[(due.date().toordinal() + offset) % len(options)]


def tick(engine: Engine, route: str, now: datetime):
    local = now.astimezone(ZONE)
    with Session(engine) as db, db.begin():
        owner = db.get(Owner, 1)
        if not owner:
            return
        settings = routine(db)
        profile = Preferences.for_owner(owner)
        for enabled, hour, label in (
            (settings.morning, 7, "Bom dia"),
            (settings.evening, 21, "Boa noite"),
        ):
            due = local.replace(hour=hour, minute=0, second=0, microsecond=0)
            if enabled and due <= local < due + timedelta(minutes=15):
                stage(
                    db,
                    route,
                    f"routine:{hour}:{local.date()}",
                    _routine_greeting(db, route, due, label, profile),
                    now,
                    due + timedelta(minutes=15),
                )
        if local.hour >= 7 and birthday_today(settings.birthday, settings.leap_day, local.date()):
            key = f"birthday:{local.year}"
            if not db.scalar(select(Inbound.id).where(Inbound.message_id == key).limit(1)):
                tomorrow = (local + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                stage(
                    db,
                    route,
                    key,
                    f"Feliz aniversário, {profile.preferred_name}! Um abraço do Jarvis.",
                    now,
                    tomorrow,
                )
        pending = db.scalars(
            select(Entry)
            .where(Entry.confirmation_pending, Entry.route_key == route)
            .with_for_update()
        ).all()
        for primary in pending:
            copies = (
                db.scalars(select(Entry).where(Entry.pair_id == primary.id)).all()
                if primary.pair_id
                else [primary]
            )
            if any(copy.deleted for copy in copies):
                primary.confirmation_pending = False
                continue
            if all(copy.state == "clean" for copy in copies):
                primary.confirmation_pending = False
                common = all(
                    (copy.title, copy.starts_at, copy.ends_at, copy.all_day)
                    == (primary.title, primary.starts_at, primary.ends_at, primary.all_day)
                    for copy in copies
                )
                sources = {db.get_one(CalendarSource, copy.source_id).provider for copy in copies}
                where = " e no ".join(name for key, name in PROVIDERS.items() if key in sources)
                when = primary.starts_at.astimezone(ZONE).strftime(
                    "%d/%m/%Y · dia inteiro" if primary.all_day else "%d/%m/%Y às %H:%M"
                )
                text = (
                    f"Confirmado no {where}: {primary.title} · {when} (Manaus)."
                    if common
                    else f"As cópias de “{primary.title}” têm dados diferentes no Google e no iCloud. Consulte sua agenda para conferir."
                )
                stage(db, route, f"calendar-pair:{primary.id}", text, now)
                continue
            for copy in copies:
                if copy.state == "clean":
                    continue
                source = db.get_one(CalendarSource, copy.source_id)
                error = copy.detail or source.error
                if error or not source.available:
                    name = PROVIDERS[source.provider]
                    text = (
                        f"Ainda não consegui confirmar “{copy.title}” no {name} ({source.name}). "
                        + (error[:240] if error else "A agenda está indisponível.")
                    )
                    stage(db, route, f"calendar-issue:{copy.id}", text, now)
        due_reminders = (
            select(Entry)
            .where(
                Entry.kind == "reminder",
                Entry.deleted.is_(False),
                Entry.notified_at.is_(None),
                Entry.starts_at <= now,
                or_(Entry.route_key.is_(None), Entry.route_key == route),
            )
            .order_by(Entry.starts_at)
            .limit(100)
        )
        for row in db.scalars(due_reminders):
            when = row.starts_at.astimezone(ZONE)
            stage(
                db,
                route,
                f"cal:{row.id}:{row.version}",
                f"Lembrete de {when:%d/%m às %H:%M}: {row.title}",
                now,
            )
            row.notified_at = now
        # Só expande séries se houver algum compromisso com aviso configurado.
        if not db.scalar(
            select(Entry.id)
            .where(Entry.alert_minutes.is_not(None), Entry.deleted.is_(False))
            .limit(1)
        ):
            return
        for item in occurrences(
            db, now - timedelta(minutes=15), now + timedelta(days=7, seconds=1)
        ):
            row = item.entry
            if (
                row.alert_minutes is None
                or row.state not in ("clean", "local")
                or row.route_key not in (None, route)
            ):
                continue
            if row.source_id:
                source = db.get_one(CalendarSource, row.source_id)
                if (
                    source.error
                    or not source.last_sync
                    or now - source.last_sync > timedelta(minutes=5)
                ):
                    continue
            due = item.start - timedelta(minutes=row.alert_minutes)
            expires = min(due + timedelta(minutes=15), item.end)
            if due <= now < expires:
                held = db.scalar(
                    update(Entry)
                    .where(
                        Entry.id == row.id, Entry.version == row.version, Entry.deleted.is_(False)
                    )
                    .values(version=Entry.version)
                    .returning(Entry.id)
                )
                if held is None:
                    continue
                when = item.start.astimezone(ZONE)
                stage(
                    db,
                    route,
                    f"cal:{row.id}:{int(item.start.timestamp())}:{row.version}",
                    f"Compromisso em {when:%d/%m às %H:%M}: {row.title}",
                    now,
                    expires,
                )
