from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from jarvis.adapters.icloud import ical
from jarvis.adapters.icloud.client import AppleError, member_url
from jarvis.adapters.persistence.db import utcnow
from jarvis.modules.calendar.models import CalendarSource, Entry
from jarvis.modules.calendar.projection import apply_remote
from jarvis.modules.calendar.service import clear_notices

PENDING = ("create", "update", "delete")


def discover(db, account, calendars):
    sources = {
        s.id: s
        for s in db.scalars(select(CalendarSource).where(CalendarSource.provider == "icloud"))
    }
    first = not any(s.available for s in sources.values())
    default = db.scalar(select(CalendarSource.id).where(CalendarSource.write_default))
    # Quando já há Google configurado, preserve inclusive a preferência de gravar localmente.
    may_choose_default = first and not db.scalar(
        select(CalendarSource.id).where(CalendarSource.provider == "google").limit(1)
    )
    for cal in calendars:
        key = "icloud:" + sha256((account.casefold() + "\n" + cal.url).encode()).hexdigest()
        row = sources.pop(key, None)
        if row is None:
            row = CalendarSource(
                id=key,
                provider="icloud",
                selected=first,
                write_default=bool(may_choose_default and not default and cal.writable),
            )
            db.add(row)
            if row.write_default:
                default = key
        elif first and not row.available:
            row.selected = True
            row.write_default = bool(may_choose_default and not default and cal.writable)
            if row.write_default:
                default = key
        row.name, row.remote_url, row.writable, row.available = (
            cal.name[:256],
            cal.url,
            cal.writable,
            True,
        )
    for row in sources.values():
        row.available = row.write_default = False
        row.error = "Agenda sem acesso no iCloud."


def related(db, sid, resource):
    return db.scalars(
        select(Entry).where(
            Entry.source_id == sid, or_(Entry.remote_id == resource, Entry.parent == resource)
        )
    ).all()


def retired(db, row):
    if not row.deleted:
        clear_notices(db, row)
        row.deleted = True
        row.version += 1


def project(db, source, resource, tag, bundle):
    items = ical.events(bundle, resource, tag, source.timezone) if bundle else []
    present = {e["id"] for e in items}
    for row in related(db, source.id, resource):
        if row.state not in PENDING and row.remote_id not in present:
            retired(db, row)
            if bundle and row.parent:
                # A exceção desapareceu do recurso. A ocorrência volta a seguir a série.
                row.parent = None
                row.original_at = None
    for event in sorted(items, key=lambda e: bool(e.get("recurringEventId"))):
        apply_remote(db, source, event, source.timezone)


def confirms_creation(row, resource, tag, bundle):
    uid = resource.removesuffix(".ics") + "@jarvis.local"
    master = next(
        (c for c in bundle.children if c.kind == "VEVENT" and not c.get("RECURRENCE-ID")), None
    )
    if master is None:
        raise ValueError("O iCloud ainda não retornou o VEVENT criado.")
    if master.get("UID") != uid:
        return False
    # Alguns servidores normalizam o VEVENT e removem propriedades X-*. Confira o
    # UID e os campos do compromisso, incluindo as datas exclusivas de dia inteiro.
    item = next(e for e in ical.events(bundle, resource, tag, row.timezone) if e["id"] == resource)
    return (
        item.get("status") != "cancelled"
        and item["summary"] == row.title.replace("\r\n", "\n").replace("\r", "\n")
        and ("date" in item["start"]) == row.all_day
        and ical.instant(item["start"]) == row.starts_at.replace(microsecond=0)
        and ical.instant(item["end"]) == row.ends_at.replace(microsecond=0)
    )


def transmit(client, source, row):
    resource = row.parent or row.remote_id
    address = member_url(source.remote_url, resource)
    mark = f"{row.id}:{row.version}"
    try:
        tag, raw = client.get(address)
        bundle = ical.parse(raw)
    except AppleError as err:
        if err.status not in (404, 410):
            raise
        tag, bundle = None, None

    if row.state == "create" and bundle:
        return resource, tag, bundle, not confirms_creation(row, resource, tag, bundle)
    if bundle and any(c.get("X-JARVIS-MUTATION") == mark for c in bundle.children):
        return resource, tag, bundle, False
    if row.state == "delete" and not bundle:
        return resource, None, None, False
    if row.state != "create" and (bundle is None or row.etag != tag or tag is None):
        return resource, tag, bundle, True
    if row.state == "delete" and not row.parent:
        client.delete(address, tag)
        return resource, None, None, False
    if not bundle:
        bundle = ical.Component(
            "VCALENDAR", ["VERSION:2.0", "PRODID:-//Jarvis//Agenda//PT", "CALSCALE:GREGORIAN"]
        )
    ical.updated(bundle, row, resource.removesuffix(".ics") + "@jarvis.local", datetime.now(UTC))
    client.put(address, ical.serialize(bundle), tag)
    tag, raw = client.get(address)
    stored = ical.parse(raw)
    conflict = row.state == "create" and not confirms_creation(row, resource, tag, stored)
    return resource, tag, stored, conflict


def push(engine, client, sid, eid):
    with Session(engine) as db:
        source = db.get_one(CalendarSource, sid)
        row = db.get_one(Entry, eid)
        version = row.version
        if row.state not in PENDING:
            return
        resource, tag, bundle, conflict = transmit(client, source, row)
    with Session(engine) as db, db.begin():
        current = db.get_one(Entry, eid, with_for_update=True)
        if current.version != version:
            # Edição feita no painel enquanto a revisão anterior era transmitida.
            if not conflict:
                current.etag = tag
                if current.state == "create":
                    current.state = "update"
            return
        current.state = "clean"
        current.etag = None  # aplica inclusive a resposta canônica de uma criação local
        source = db.get_one(CalendarSource, sid)
        if conflict:
            current.deleted = False
        project(db, source, resource, tag, bundle)
        if not conflict:
            for sibling in related(db, sid, resource):
                if sibling.state in PENDING and sibling.etag == row.etag:
                    sibling.etag = tag
        else:
            current.state = "conflict"
            current.detail = (
                "O iCloud alterou este compromisso. Confira a versão da agenda antes de editar."
            )


def sync_source(engine, client, sid, clock=utcnow):
    with Session(engine) as db:
        source = db.get_one(CalendarSource, sid)
        url = source.remote_url
        pending = list(
            db.scalars(select(Entry.id).where(Entry.source_id == sid, Entry.state.in_(PENDING)))
        )
    error = None
    for eid in pending:
        try:
            push(engine, client, sid, eid)
        except (AppleError, KeyError, ValueError, OverflowError) as exc:
            error = (
                str(exc)
                if isinstance(exc, AppleError)
                else "Não foi possível transmitir um compromisso ao iCloud."
            )
            with Session(engine) as db, db.begin():
                db.get_one(Entry, eid).detail = error
            if isinstance(exc, AppleError) and exc.status in (0, 401):
                raise
    listing = client.members(url)
    with Session(engine) as db:
        existing = {
            r.remote_id: r.etag
            for r in db.scalars(
                select(Entry).where(
                    Entry.source_id == sid, Entry.parent.is_(None), Entry.deleted.is_(False)
                )
            )
        }
    for rid, tag in listing.items():
        if existing.get(rid) == tag:
            continue
        try:
            actual, raw = client.get(member_url(url, rid))
            with Session(engine) as db, db.begin():
                project(db, db.get_one(CalendarSource, sid), rid, actual, ical.parse(raw))
        except (KeyError, ValueError, OverflowError):
            error = "Um compromisso do iCloud tem formato incompatível. Sua última cópia foi preservada."
    with Session(engine) as db, db.begin():
        source = db.get_one(CalendarSource, sid, with_for_update=True)
        for rid in existing.keys() - listing.keys():
            project(db, source, rid, None, None)
        source.last_sync, source.error = clock(), error
