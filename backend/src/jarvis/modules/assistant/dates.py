"""Datas usadas na identificação e na interpretação dos comandos em português."""

import re
from collections.abc import Sequence
from datetime import date, timedelta

from jarvis.modules.assistant.numbers_pt import SPOKEN_NUMBER, read_number

MONTHS: Sequence[str] = (
    "janeiro fevereiro marco abril maio junho julho agosto setembro outubro novembro dezembro".split()
)
WEEKDAYS: Sequence[str] = "segunda terca quarta quinta sexta sabado domingo".split()
RELATIVE = {"hoje": 0, "amanha": 1, "depois de amanha": 2}
MONTH_NAME = "|".join(MONTHS)
DAY_NUMBER = rf"(?:\d{{1,2}}|primeiro|{SPOKEN_NUMBER})"
YEAR = rf"(?:\d{{4}}|dois mil(?: e (?:{SPOKEN_NUMBER}))?)"
CALENDAR_DATE = (
    rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}(?:/\d{{4}})?|"
    rf"{DAY_NUMBER} de (?:{MONTH_NAME})(?: de {YEAR})?)"
)
DAY = re.compile(
    rf"(?:(?:na |no |nesta |neste )?(?:proxim[ao] )?(?P<weekday>{'|'.join(WEEKDAYS)})(?:[- ]feira)?"
    rf"|(?P<relative>{'|'.join(RELATIVE)})"
    rf"|(?:no dia |dia )(?P<numbered>{CALENDAR_DATE}|{DAY_NUMBER})|(?P<concrete>{CALENDAR_DATE}|\d{{1,2}}(?= as\b)))\b"
)
# “hoje, 08/11/2026” identifica um único dia. As duas indicações precisam concordar.
ANNOTATION = re.compile(rf"[, ]+(?:(?:no )?dia )?(?P<concrete>{CALENDAR_DATE})\b")


class ConflictingDates(ValueError):
    pass


def day_number(value: str) -> int:
    return 1 if value == "primeiro" else read_number(value)


def year_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if value == "dois mil":
        return 2000
    return 2000 + read_number(value.removeprefix("dois mil e "))


def _concrete(value: str, anchor: date) -> date:
    if "-" in value:
        return date.fromisoformat(value)
    if " de " in value:
        day, month, *year = value.split(" de ")
        return date(
            year_number(year[0]) if year else anchor.year, MONTHS.index(month) + 1, day_number(day)
        )
    if "/" not in value:
        return anchor.replace(day=day_number(value))
    day, *parts = map(int, value.split("/"))
    return date(
        parts[1] if len(parts) == 2 else anchor.year, parts[0] if parts else anchor.month, day
    )


def read_day(text: str, anchor: date) -> tuple[date, int]:
    match = DAY.match(text)
    if not match:
        return anchor, 0
    if concrete := match["concrete"] or match["numbered"]:
        return _concrete(concrete, anchor), match.end()
    if match["relative"]:
        day = anchor + timedelta(days=RELATIVE[match["relative"]])
    else:
        delta = WEEKDAYS.index(match["weekday"]) - anchor.weekday()
        suffix = re.match(
            r" (?:da (?:semana (que vem|passada|anterior)|(?:proxima|ultima) semana)|(passad[ao]|que vem))\b",
            text[match.end() :],
        )
        if suffix:
            previous = any(word in suffix[0] for word in ("passad", "anterior", "ultima"))
            offset = delta - 7 if previous else delta + 7
            if suffix[2]:
                offset = (delta % 7 or 7) if not previous else -((-delta) % 7 or 7)
            return anchor + timedelta(days=offset), match.end() + suffix.end()
        distance = delta % 7
        day = anchor + timedelta(days=distance or (7 if "proxim" in match[0] else 0))
    annotation = ANNOTATION.match(text, match.end())
    if annotation:
        if day != _concrete(annotation["concrete"], day):
            raise ConflictingDates
        return day, annotation.end()
    return day, match.end()
