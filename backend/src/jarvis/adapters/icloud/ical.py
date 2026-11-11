"""Projeção de VEVENT e edição pontual do recurso inteiro, inclusive exceções e VALARM."""

import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from jarvis.modules.calendar.service import ZONE

PROPERTY = re.compile(r'^((?:[^":]|"[^"]*")*):(.*)$')
RULES = {"RRULE", "RDATE", "EXDATE", "EXRULE"}


def name(line: str) -> str:
    return re.split(r"[;:]", line, maxsplit=1)[0].upper()


@dataclass
class Component:
    kind: str
    lines: list[str] = field(default_factory=list)
    children: list["Component"] = field(default_factory=list)

    def get(self, key: str, default=""):
        prop = next((line for line in self.lines if name(line) == key), "")
        return parts(prop)[1] if prop else default

    def set(self, key, value):
        self.lines = [line for line in self.lines if name(line) != key]
        if value is not None:
            self.lines.append(value)

    def content(self):
        yield "BEGIN:" + self.kind
        yield from self.lines
        for child in self.children:
            yield from child.content()
        yield "END:" + self.kind


def parts(line):
    match = PROPERTY.fullmatch(line)
    if not match:
        raise ValueError("Propriedade iCalendar inválida.")
    return match[1], match[2]


def parse(raw: str) -> Component:
    stack: list[Component] = []
    root = None
    unfolded = re.sub(r"\r?\n[ \t]", "", raw.replace("\r\n", "\n"))
    for line in unfolded.split("\n"):
        if not line:
            continue
        header, value = parts(line)
        if header.upper() == "BEGIN":
            if len(stack) > 30 or (not stack and root):
                raise ValueError("Componente inesperado.")
            component = Component(value.upper())
            if stack:
                stack[-1].children.append(component)
            else:
                root = component
            stack.append(component)
        elif header.upper() == "END":
            if not stack or stack.pop().kind != value.upper():
                raise ValueError("Componente incompleto.")
        elif stack:
            stack[-1].lines.append(line)
        else:
            raise ValueError("Conteúdo fora do calendário.")
    if stack or not root or root.kind != "VCALENDAR":
        raise ValueError("Calendário incompleto.")
    return root


def serialize(calendar: Component) -> bytes:
    lines = []
    for line in calendar.content():
        raw = line.encode()
        while len(raw) > 75:
            end = 75
            while raw[end] & 0xC0 == 0x80:
                end -= 1
            lines.append(raw[:end])
            raw = b" " + raw[end:]
        lines.append(raw)
    return b"\r\n".join(lines) + b"\r\n"


def point(line, fallback):
    header, value = parts(line)
    if re.fullmatch(r"\d{8}", value):
        return {"date": datetime.strptime(value, "%Y%m%d").date().isoformat()}
    tz = re.search(r'(?:^|;)TZID=("[^"]+"|[^;]+)', header, re.I)
    zone = ZoneInfo(tz[1].strip('"')) if tz else ZoneInfo(fallback)
    dt = datetime.fromisoformat(value)
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=zone)
    return {
        "dateTime": dt.isoformat(),
        "timeZone": zone.key if tz or not value.endswith("Z") else "UTC",
    }


def instant(p):
    if "date" in p:
        return datetime.combine(datetime.fromisoformat(p["date"]).date(), time.min, ZONE)
    return datetime.fromisoformat(p["dateTime"])


def component_key(event, fallback):
    rid = next((v for v in event.lines if name(v) == "RECURRENCE-ID"), None)
    return str(int(instant(point(rid, fallback)).timestamp())) if rid else ""


def events(calendar, resource, etag, fallback):
    projected = []
    for event in calendar.children:
        if event.kind != "VEVENT":
            continue
        fields = {name(v): v for v in event.lines}
        exc = fields.get("RECURRENCE-ID")
        first = fields.get("DTSTART") or exc
        if not first:
            raise ValueError("Evento sem início.")
        start = point(first, fallback)
        if "DTEND" in fields:
            end = point(fields["DTEND"], fallback)
        else:
            duration = timedelta(days=1) if "date" in start else timedelta(seconds=1)
            if raw := event.get("DURATION"):
                match = re.fullmatch(
                    r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", raw
                )
                if not match:
                    raise ValueError("Duração inválida.")
                weeks, days, hours, minutes, seconds = [int(v or 0) for v in match.groups()]
                duration = timedelta(
                    weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds
                )
            finish = instant(start) + duration
            end = (
                {"date": finish.date().isoformat()}
                if "date" in start
                else {"dateTime": finish.isoformat()}
            )
        rec = component_key(event, fallback)
        data = dict(
            id=resource + ("#" + rec if rec else ""),
            etag=etag,
            summary=re.sub(
                r"\\([nN,;\\])",
                lambda m: "\n" if m[1].lower() == "n" else m[1],
                event.get("SUMMARY"),
            ),
            start=start,
            end=end,
            recurrence=[v for v in event.lines if name(v) in RULES],
        )
        if exc:
            data.update(recurringEventId=resource, originalStartTime=point(exc, fallback))
        if event.get("STATUS").upper() == "CANCELLED":
            data["status"] = "cancelled"
        projected.append(data)
    return projected


def date_line(key, dt, all_day, zone):
    dt = dt.astimezone(zone)
    if all_day:
        return f"{key};VALUE=DATE:{dt:%Y%m%d}"
    if zone.key == "UTC":
        return f"{key}:{dt:%Y%m%dT%H%M%SZ}"
    return f"{key};TZID={zone.key}:{dt:%Y%m%dT%H%M%S}"


def updated(calendar, row, uid, now):
    zone = ZoneInfo(row.timezone)
    recurrence = str(int(row.original_at.timestamp())) if row.original_at else ""
    event = next(
        (
            e
            for e in calendar.children
            if e.kind == "VEVENT" and component_key(e, zone.key) == recurrence
        ),
        None,
    )
    if event is None:
        master = next(
            (e for e in calendar.children if e.kind == "VEVENT" and not component_key(e, zone.key)),
            None,
        )
        event = deepcopy(master) if master and row.original_at else Component("VEVENT")
        if not master and not row.all_day:
            # Para criações do Jarvis, instantes UTC dispensam um VTIMEZONE externo.
            zone = ZoneInfo("UTC")
        calendar.children.append(event)
        event.set("UID", "UID:" + (master.get("UID") if master else uid))
        if row.original_at and master:
            original = point(next(v for v in master.lines if name(v) == "DTSTART"), row.timezone)
            original_zone = ZoneInfo(original.get("timeZone", ZONE.key))
            event.set(
                "RECURRENCE-ID",
                date_line("RECURRENCE-ID", row.original_at, "date" in original, original_zone),
            )
    escaped = (
        row.title.replace("\\", "\\\\")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )
    event.set("SUMMARY", "SUMMARY:" + escaped)
    event.set("DTSTART", date_line("DTSTART", row.starts_at, row.all_day, zone))
    event.set("DURATION", None)
    event.set("DTEND", date_line("DTEND", row.ends_at, row.all_day, zone))
    event.set("DTSTAMP", now.astimezone(UTC).strftime("DTSTAMP:%Y%m%dT%H%M%SZ"))
    event.set("SEQUENCE", "SEQUENCE:" + str(int(event.get("SEQUENCE", "0")) + 1))
    event.set("X-JARVIS-MUTATION", f"X-JARVIS-MUTATION:{row.id}:{row.version}")
    if row.deleted:
        event.set("STATUS", "STATUS:CANCELLED")
    event.lines = [v for v in event.lines if name(v) not in RULES] + row.rules
    return calendar
