"""Destinos citados no pedido. O texto do assunto permanece intacto."""

import re
import unicodedata
from dataclasses import dataclass

from jarvis.modules.assistant.intent import fold, tidy

PROVIDERS = {"google": "Google", "icloud": "iCloud"}
DOTS = {"google": "🟢", "icloud": "🔵"}
LEGEND = "🟢 - Google\n🔵 - iCloud"
APPLE = r"(?:i[ -]?cloud|apple(?: calendar)?|i[ -]?phone)"
GOOGLE = r"(?:google(?: (?:calendar|agenda))?)(?! (?:meet|drive|maps|ads|workspace|docs|sheets|fotos|io|i/o)\b)"
NAME = rf"(?:{GOOGLE}|{APPLE})"
PREP = r"(?:n[oa]s?|d[oa]s?|em|pelo|pela|para (?:o|a)|pr[oa]|via)"
OWN = r"(?:(?:minh[ao]s?|meus?|sua|seu) )?"
BOOK = r"(?:agendas?|calendarios?)"
BOOK_PREFIX = rf"{OWN}{BOOK}(?: pessoal)?(?: (?:do|da|de))? "
ALTERNATIVE = re.compile(rf"\b{NAME}\s+ou\s+(?:(?:{PREP}) )?(?:{BOOK_PREFIX})?{NAME}\b")
PAIR = r"(?:nos dois calendarios|nas duas agendas|em (?:ambos os calendarios|ambas as agendas))"
TARGET = re.compile(
    rf"\b(?P<pair>{PAIR})(?!\w)|"
    rf"\b(?:(?P<modifier>somente|so|apenas|exclusivamente) )?"
    rf"(?:(?:{PREP} )?{BOOK_PREFIX}|{PREP} {OWN}|(?<= e )|(?<= ou )|(?<= exceto )|(?<= menos )|(?<= sem )|(?<= nao ))"
    rf"(?P<name>{NAME})(?!\w)"
)
QUESTION_NAME = re.compile(rf"\b(?P<name>{NAME})\b")
NEGATION = re.compile(r"(?:\be )?\b(?:mas nao|nao|exceto|menos|sem)\s*$")


@dataclass(frozen=True)
class CalendarScope:
    text: str
    providers: frozenset[str] = frozenset()
    problem: str | None = None


def request_scope(raw: str, *, query: bool = False) -> CalendarScope:
    """Retira qualificadores de provedor e distingue inclusão, exclusão e alternativa."""
    raw = unicodedata.normalize("NFC", raw)
    normal = fold(raw)
    if ALTERNATIVE.search(normal):
        return CalendarScope(
            raw,
            problem="Você quer o Google, o iCloud ou os dois? Repita o pedido com o destino escolhido.",
        )
    if re.search(
        rf"\b(?:{PREP} (?:{BOOK_PREFIX})?|{BOOK_PREFIX})(?:outlook|exchange|yahoo|microsoft)(?!\w)",
        normal,
    ):
        return CalendarScope(
            raw,
            problem="As agendas disponíveis são Google e iCloud. Em qual delas você quer fazer esse pedido?",
        )
    matches = list(TARGET.finditer(normal))
    if query and not matches:
        matches = list(QUESTION_NAME.finditer(normal))
    included: set[str] = set()
    excluded: set[str] = set()
    pieces: list[str] = []
    previous = 0
    for marker in matches:
        prefix = normal[previous : marker.start()]
        negation = NEGATION.search(prefix)
        # “reunião da Apple” e “aula do Google” são assuntos. Em uma gravação
        # um genitivo isolado só identifica destino depois de outro provedor.
        fragment = marker[0]
        connector = bool(previous and re.fullmatch(r"\s*(?:e|ou|,)\s*", prefix))
        if not query and not connector and not negation:
            if re.fullmatch(rf"d[oa] {NAME}|{NAME}", fragment):
                continue
        if re.fullmatch(r"\s*ou\s*", prefix) and previous:
            return CalendarScope(
                raw,
                problem="Você quer o Google, o iCloud ou os dois? Repita o pedido com o destino escolhido.",
            )
        if previous and re.fullmatch(r"\s*(?:e|,)\s*", prefix):
            prefix = ""
        content_end = marker.start()
        if negation:
            content_end = previous + negation.start()
        if prefix:
            pieces.append(raw[previous:content_end])
        if marker.groupdict().get("pair"):
            names = set(PROVIDERS)
        else:
            names = {"google" if marker["name"].startswith("google") else "icloud"}
        (excluded if negation else included).update(names)
        previous = marker.end()
    if not included and not excluded:
        return CalendarScope(raw)
    pieces.append(raw[previous:])
    requested = (included or set(PROVIDERS)) - excluded
    if not requested or included & excluded:
        return CalendarScope(
            raw,
            problem="Quais agendas devo usar? Diga Google, iCloud ou os dois ao repetir o pedido.",
        )
    text = tidy(" ".join(" ".join(pieces).split()))
    return CalendarScope(text, frozenset(requested))
