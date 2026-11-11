"""Reconhece consultas de agenda e resolve o período no fuso do proprietário."""

import re
import unicodedata
from datetime import date, timedelta

from jarvis.modules.assistant.dates import (
    DAY,
    DAY_NUMBER,
    MONTH_NAME,
    MONTHS,
    YEAR,
    read_day,
    year_number,
)
from jarvis.modules.assistant.intent import LEAD, WHEN, fold, tidy
from jarvis.modules.calendar.models import CalendarFocus
from jarvis.modules.calendar.scope import request_scope
from jarvis.modules.calendar.service import Period, bounds
from jarvis.modules.errors import Problem

NOUN = r"(?:agenda|calendario|compromissos?|eventos?)"
ASK = re.compile(
    r"^(?:qual|quais|como|quando|o que|que|tenho|eu tenho|tem|ha|existe|liste?|listar|mostr[ae]|consult[ae]|consultar|ver|veja|verifique|confira|me (?:fal[ae]|dig[ae]|mostr[ae])|fal[ae]|dig[ae]|resum[ae])\b"
)
TIME = re.compile(
    r"\b(?:hoje|ontem|amanha|dia|semana|mes|"
    + MONTH_NAME
    + r"|segunda|terca|quarta|quinta|sexta|sabado|domingo)\b|\b\d{1,2}/\d{1,2}\b"
)
EXPLICIT = re.compile(r"\b" + DAY.pattern)
MONTH = re.compile(r"\b(" + MONTH_NAME + rf")(?: (?:de |em )?({YEAR}))?\b")
DAILY_QUERY = re.compile(
    r"(?:o que (?:eu )?tenho|(?:eu )?tenho (?:alguma coisa|algo|(?:algum |alguns )?(?:compromissos?|eventos?)))"
    r"(?: (?:agendad[oa]s?|marcad[oa]s?|previst[oa]s?))?(?: n[ao] (?:minha |meu )?(?:agenda|calendario))?"
)
FINAL_ORDER = re.compile(
    r"(?P<body>.+?)[.!;,]\s*(?:marqu[ea]|agende|coloque|anote|insira|adicione)"
    r"(?: (?:isso|isto|esse compromisso))? (?P<target>(?:n[ao]|em) (?:minha |meu )?(?:agenda|calendario)\b.*)"
)


def command_text(raw: str):
    raw = unicodedata.normalize("NFC", " ".join(raw.split()))
    lead = LEAD.match(fold(raw))
    raw = tidy(raw[lead.end() :] if lead else raw)
    extra = re.match(
        r"^(?:(?:que )?(?:voce|vc|ce) )?(?:consegue|conseguiria|poderia|pode) ", fold(raw)
    )
    if extra:
        raw = raw[extra.end() :]
    raw = re.sub(r"[, ]+(?:por favor|por gentileza)$", "", raw, flags=re.I)
    return re.sub(r"^(?:de )?saber (?:sobre )?", "", raw, flags=re.I)


def postposed_event(normal: str) -> re.Match[str] | None:
    if not (
        WHEN.match(normal)
        or re.match(r"^(?:no dia|terei|(?:eu )?tenho|havera|vai (?:ter|haver))\b", normal)
    ):
        return None
    return FINAL_ORDER.fullmatch(normal)


def is_query(raw: str):
    text = fold(command_text(raw))
    if re.search(r"\b(?:mensage(?:m|ns)|envios?|recados?)\b", text) or postposed_event(text):
        return False
    if re.match(
        r"^(?:(?:toda )?a )?(?:minha agenda|meu calendario|meus (?:compromissos|eventos)|agenda pessoal|agenda (?:de|da|do)|agenda$)\b",
        text,
    ):
        return True
    if re.match(
        r"^(?:como (?:esta|ta|sera|vai ser)|(?:mostr[ae]|veja|ver|resuma)) (?:a |o )?(?:minha semana|meu (?:dia|mes))\b",
        text,
    ):
        return True
    if re.fullmatch(r"(?:e )?(?:(?:n|d)?aquel[ae]) (?:dia|semana|mes)", text):
        return True
    scope = request_scope(text, query=True)
    if day := DAY.match(scope.text):
        tail = scope.text[day.end() :].lstrip(" ,")
        if (raw.rstrip().endswith("?") or tail.startswith("o que ")) and DAILY_QUERY.fullmatch(
            tail
        ):
            return True
    if (
        scope.providers
        and re.match(r"^(?:e |somente |apenas |so )", text)
        and re.fullmatch(
            r"(?:e(?: |$))?(?:so |apenas |somente )?(?:os|as|o|a)?", scope.text.strip(" ?")
        )
    ):
        return True
    if scope.providers and (
        re.match(r"^(?:agenda|calendario)\b", text) or re.match(r"^e (?:n[ao]|d[oa]) ", text)
    ):
        return (
            not WHEN.search(scope.text)
            or re.fullmatch(r"(?:agenda|calendario)?\s*(?:de |para )?(?:hoje|amanha)", scope.text)
            is not None
        )
    if not ASK.match(text):
        return False
    if re.search(r"\b" + NOUN + r"\b", text):
        return True
    if scope.providers and re.fullmatch(
        r"(?:mostr[ae]|veja|consulte|liste|ver|confira)(?: os)?", scope.text
    ):
        return True
    return bool(
        re.search(r"\b(?:tenho|marcado|agendado|o que (?:tem|ha))\b", text) and TIME.search(text)
    )


def unclear():
    return Problem(
        422,
        "period",
        "Qual período quer consultar? Diga, por exemplo, «dia 15 de novembro», «semana de 15/11» ou «mês de dezembro».",
    )


def month_anchor(today: date, text: str):
    first = today.replace(day=1)
    if re.search(r"\b(proximo|seguinte|que vem)\b", text):
        return (first + timedelta(days=32)).replace(day=1)
    if re.search(r"\b(passado|anterior|ultimo)\b", text):
        return (first - timedelta(days=1)).replace(day=1)
    return first


def requested_period(
    text: str, today: date, focus: CalendarFocus | None = None
) -> tuple[date, Period]:
    scope = request_scope(text, query=True)
    if (
        focus
        and scope.providers
        and (text.startswith("e ") or re.search(r"\b(?:somente|apenas|so)\b", text))
        and not TIME.search(scope.text)
    ):
        if focus.period == "month":
            return focus.anchor, "month"
        if focus.period == "day":
            return focus.anchor, "day"
        return focus.anchor, "week"
    week = bool(re.search(r"\bsemana\b", text))
    month = bool(re.search(r"\bmes\b", text))
    if re.search(
        r"\b(entre|ultimos|proximos|fim de semana|final de semana|quinzena|bimestre|trimestre|ano inteiro)\b",
        text,
    ):
        raise unclear()
    if re.search(r"\b(?:(?:n|d)?aquel[ae])\b", text) and not (
        EXPLICIT.search(text) or MONTH.search(text)
    ):
        period: Period = "week" if week else "month" if month else "day"
        if (
            not focus
            or (period == "day" and focus.period != "day")
            or (period == "week" and focus.period == "month")
        ):
            raise unclear()
        if period == "month" and focus.period == "week":
            start, end = bounds(focus.anchor, "week")
            if start.month != (end - timedelta(days=1)).month:
                raise unclear()
        return focus.anchor, period
    found = EXPLICIT.search(text)
    if found:
        anchor = (
            month_anchor(today, text)
            if month and found["numbered"] and re.fullmatch(DAY_NUMBER, found["numbered"])
            else today
        )
        day, length = read_day(text[found.start() :], anchor)
        rest = text[found.start() + length :]
        if EXPLICIT.search(rest) or re.match(r"[/\d-]", rest):
            raise unclear()
        week_anchor = re.search(r"\bsemana\b", text[: found.start()])
        return day, "week" if week_anchor else "day"
    if match := MONTH.search(text):
        if week or MONTH.search(text, match.end()):
            raise unclear()
        return date(
            year_number(match[2]) if match[2] else today.year, MONTHS.index(match[1]) + 1, 1
        ), "month"
    if any(c.isdigit() for c in text) or re.search(r"\bdaqui\b", text) or (week and month):
        raise unclear()
    if month:
        return month_anchor(today, text), "month"
    if week:
        if re.search(r"\b(proxima|seguinte|que vem)\b", text):
            today += timedelta(days=7)
        elif re.search(r"\b(passada|anterior|ultima)\b", text):
            today -= timedelta(days=7)
        return today, "week"
    if "ontem" in text:
        return today - timedelta(days=1), "day"
    if re.search(r"\b(dia|diaria)\b", text):
        if re.search(r"\b(?:meu dia|do dia|neste dia|esse dia|este dia)\b", text):
            return today, "day"
        raise unclear()
    return today, "week"
