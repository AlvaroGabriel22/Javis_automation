"""Interpreta o horário do comando usando a data de recebimento no fuso da agenda."""

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from jarvis.modules.assistant.dates import read_day
from jarvis.modules.assistant.intent import fold
from jarvis.modules.assistant.numbers_pt import NUMBER, read_number
from jarvis.modules.assistant.numbers_pt import TENS as TENS
from jarvis.modules.assistant.numbers_pt import UNITS as UNITS

MANAUS = ZoneInfo("America/Manaus")
DURATION = re.compile(
    rf"(?:daqui(?: a| ha)?|em) (?P<count>{NUMBER}|meia) (?P<unit>segundos?|minutos?|horas?|dias?)\b"
)
CLOCK = re.compile(
    rf"(?:(?:para as|pras|as|a|ao) )?(?:(?P<noon>meio[- ]dia|meia[- ]noite)|"
    rf"(?P<hour>{NUMBER})(?: *h(?: *(?P<hminute>[0-9]{{1,2}})(?: *min(?:utos?)?)?)?|:(?P<minute>[0-9]{{2}})|"
    rf"(?: horas?)?(?: e (?P<spoken>{NUMBER}|meia)(?: minutos?)?)?)"
    rf"(?: (?:da|de) (?P<period>manha|tarde|noite|madrugada))?)(?![\w:/])"
)


@dataclass(frozen=True)
class DeliveryTime:
    instant: datetime
    label: str
    relative: bool = False


def _clock_time(clock: re.Match[str]) -> time:
    if clock["noon"]:
        return time(12 if clock["noon"].startswith("meio") else 0)
    hour = read_number(clock["hour"])
    spoken = clock["spoken"]
    minute = (
        30
        if spoken == "meia"
        else read_number(spoken or clock["minute"] or clock["hminute"] or "0")
    )
    if period := clock["period"]:
        if not 1 <= hour <= 12:
            raise ValueError
        if hour == 12:
            hour = 0 if period in {"noite", "madrugada"} else 12
        elif period in {"tarde", "noite"}:
            hour += 12
    return time(hour, minute)


def time_prefix(phrase: str, received: datetime) -> tuple[DeliveryTime, int]:
    """Lê o horário inicial e devolve a posição em que começa o restante da frase."""
    local = received.astimezone(MANAUS)
    normal = fold(phrase)
    relative = DURATION.match(normal)
    if relative:
        amount, unit = relative["count"], relative["unit"]
        if amount == "meia" and unit == "hora":
            count, unit = 30, "minuto"
        else:
            count = read_number(amount)
        if count < 1:
            raise ValueError
        unit = unit.rstrip("s")
        seconds = {"segundo": 1, "minuto": 60, "hora": 3600, "dia": 86400}[unit]
        instant = local + timedelta(seconds=count * seconds)
        quantity = ("uma" if unit == "hora" else "um") if count == 1 else str(count)
        label = f"Daqui a {quantity} {unit}{'s' if count != 1 else ''}"
        return DeliveryTime(instant, label, relative=True), relative.end()

    day, day_end = read_day(normal, local.date())
    clock_start = len(normal) - len(normal[day_end:].lstrip(" ,"))
    clock = CLOCK.match(normal, clock_start)
    if not clock:
        raise ValueError
    end = clock.end()
    explicit_day = bool(day_end)
    if gap := re.match(r"[, ]+(?:(?:de|do) )?", normal[end:]):
        after = end + gap.end()
        other_day, length = read_day(normal[after:], local.date())
        if length:
            if day_end:
                raise ValueError
            day, explicit_day = other_day, True
            end = after + length
    instant = datetime.combine(day, _clock_time(clock), MANAUS)
    if not explicit_day and instant <= local:
        instant += timedelta(days=1)
    return DeliveryTime(instant, f"Em {instant:%d/%m/%Y às %H:%M} (Manaus)"), end


def delivery_time(phrase: str, received: datetime) -> DeliveryTime:
    normal = " ".join(fold(phrase).split()).strip(" ,.!?")
    due, end = time_prefix(normal, received)
    if end != len(normal):
        raise ValueError
    return due
