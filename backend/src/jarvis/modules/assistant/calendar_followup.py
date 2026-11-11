"""Retoma um pedido explícito de evento cuja data foi dada antes do título."""

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis.modules.assistant.dates import DAY, read_day
from jarvis.modules.assistant.intent import fold
from jarvis.modules.assistant.timing import CLOCK
from jarvis.modules.calendar.chat import QUALIFIED, REPORTED, personal
from jarvis.modules.calendar.models import CalendarChoice
from jarvis.modules.calendar.scope import PROVIDERS, request_scope
from jarvis.modules.calendar.service import ZONE
from jarvis.modules.messaging.models import Inbound, Outbound
from jarvis.modules.messaging.queue import Turn

WINDOW = timedelta(minutes=15)
ORDER = r"(?:marque|agende|registre|crie) (?:o |um )?(?:evento|compromisso)"
START = re.compile(rf"(?:{ORDER} (?:para )?(?P<after>.+)|(?P<before>.+?)[, ]+{ORDER})")
TITLE = re.compile(r"(?:(?:o )?(?:titulo|assunto)(?: (?:e|sera))?\s*[:,-]?\s+)")
CANCEL = {"cancelar", "cancela", "esquece", "esqueca", "deixa pra la", "deixe para la", "desisti"}
TIMED = re.compile(r"(?:as |ao meio[- ]dia|a meia[- ]noite|\d{1,2}(?::\d{2}|h\b|h\d{2}))")
UNSAFE = re.compile(
    r"[?;\n]|\b(?:nao|cancele|cancelar|esquece|esqueca|desisti|deixa pra la|"
    r"marque|agende|adicione|registre|apague|lembre|em vez|talvez|ou|"
    r"ignore|obrigad[oa]|valeu|qual|quais|quando|sera que)\b"
)


@dataclass(frozen=True)
class Opening:
    day: date
    providers: frozenset[str]

    def prompt(self) -> str:
        return (
            f"Qual é o título do compromisso de {self.day:%d/%m/%Y}? "
            "Pode responder “às 19h, aula de inglês” ou “título: aula de inglês” para o dia inteiro."
        )


def opening(raw: str, received: datetime) -> Opening | None:
    raw = unicodedata.normalize("NFC", " ".join(raw.split()))
    if "?" in raw or not re.search(ORDER, fold(raw)):
        return None
    scope = request_scope(raw)
    if scope.problem or not (match := START.fullmatch(fold(scope.text).rstrip(".!"))):
        return None
    fragment = (match["after"] or match["before"]).strip(" ,")
    try:
        day, end = read_day(fragment, received.astimezone(ZONE).date())
    except (OverflowError, ValueError):
        return None
    return Opening(day, scope.providers) if end and end == len(fragment) else None


def resume_event(db: Session, turn: Turn, now: datetime, google_linked: bool) -> str | None:
    if turn.kind not in {"audio", "text"}:
        return None
    raw = unicodedata.normalize("NFC", " ".join(turn.text.split()))
    normal = fold(raw)
    received = turn.received_at or now
    if (initial := opening(raw, received)) is not None:
        if initial.day < now.astimezone(ZONE).date():
            return "Essa data já passou. Qual é a nova data do compromisso?"
        if db.get(CalendarChoice, turn.chat_id) is None:
            return initial.prompt()
        return None

    # Uma conversa nova nunca é um título implícito. Só consultamos o histórico
    # para respostas que tragam uma data, horário ou um marcador explícito de título.
    cancelled = normal.rstrip(".!") in CANCEL
    if not (cancelled or DAY.match(normal) or TIMED.match(normal) or TITLE.match(normal)):
        return None
    if not cancelled and (
        UNSAFE.search(fold(turn.text)) or QUALIFIED.search(normal) or REPORTED.search(normal)
    ):
        return None
    previous = db.execute(
        select(
            Inbound.text,
            Inbound.kind,
            Inbound.state,
            Inbound.received_at,
            Outbound.text.label("reply"),
            Outbound.state.label("delivery"),
        )
        .outerjoin(Outbound, Outbound.id == Inbound.id)
        .where(Inbound.chat_id == turn.chat_id, Inbound.id < turn.id, Inbound.kind != "notice")
        .order_by(Inbound.id.desc())
        .limit(1)
    ).first()
    if previous is None:
        return None
    if (
        previous.kind not in {"text", "audio"}
        or previous.state != "completed"
        or previous.delivery not in {"ready", "transmitting", "accepted"}
        or not now - WINDOW <= previous.received_at <= received <= now
    ):
        return None
    initial = opening(previous.text, previous.received_at)
    if initial is None or previous.reply != initial.prompt():
        return None
    if cancelled:
        return "Tudo bem. Descartei o pedido de criar esse compromisso."
    scope = request_scope(raw)
    if scope.problem:
        return scope.problem
    if initial.providers and scope.providers and initial.providers != scope.providers:
        return "Para mudar o destino, repita o pedido com o título, a data e a agenda escolhida."
    tail = scope.text
    try:
        day, end = read_day(fold(tail), received.astimezone(ZONE).date())
    except (ValueError, OverflowError):
        return "Essa data não é válida. Repita o pedido com o título, a data e o horário."
    if not end:
        day = initial.day
    tail = tail[end:].lstrip(" ,–-")
    if title := TITLE.match(fold(tail)):
        tail = tail[title.end() :].strip()
    elif not TIMED.match(fold(tail)):
        return None
    if not tail or (clock := CLOCK.match(fold(tail))) and not tail[clock.end() :].strip(" ,.-"):
        return (
            "Ainda falta o título. Repita a data e o título em um pedido para marcar o compromisso."
        )
    targets = scope.providers or initial.providers
    target = ""
    if len(targets) == 2:
        target = " nos dois calendários"
    elif targets:
        target = " no " + PROVIDERS[next(iter(targets))]
    command = replace(turn, text=f"Marque {day:%Y-%m-%d} {tail}{target}")
    return personal(db, command, now, google_linked=google_linked)
