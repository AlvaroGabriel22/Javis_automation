"""Ajustes visuais só na prosa que o modelo redigiu para a conversa privada."""

import re

from jarvis.modules.assistant.intent import fold

VERBATIM = re.compile(
    r"\b(?:literalmente|exatamente|sem (?:mudar|alterar)|palavra por palavra|"
    r"transcrev[ae]|repita|repet[ei]|copi[ae]|codigo|json|markdown|regex|poema|poesia)\b"
)
SENTENCE_BREAK = re.compile(r"(?<=[.?!]) +(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ])")
ABBREVIATION = re.compile(r"(?:\b(?:sr[as]?|dr[as]?|prof[a]?|av|pág|ex|etc)|\b[A-Z])\.$", re.I)
HEADING = re.compile(r"^#{1,4} +([^#]+?)\s*$")
BOLD = re.compile(r"(?<![\w*\\])\*\*(?=[^\s*])([^*\n]+?)(?<!\s)\*\*(?!\*)")
LITERAL_MARKS = ('"', "'", "“", "”", "«", "»", "`", "://", "|")


def _paragraph(line: str) -> str:
    # Copiar instruções, endereços, valores e citações exige preservar sua distribuição.
    if (
        len(line) <= 400
        or line[:1].isspace()
        or re.match(r"(?:[-*•>]|\d+[.)] )", line)
        or any(mark in line for mark in LITERAL_MARKS)
    ):
        return line
    pieces = []
    start = 0
    for boundary in SENTENCE_BREAK.finditer(line):
        if boundary.start() - start < 230 or ABBREVIATION.search(line[: boundary.start()]):
            continue
        pieces.append(line[start : boundary.start()])
        start = boundary.end()
    return "\n\n".join([*pieces, line[start:]])


def requests_literal(text: str) -> bool:
    return bool(VERBATIM.search(fold(text)))


def render_chat(text: str, *, literal: bool = False) -> str:
    if literal or "```" in text:
        return text
    lines = []
    for line in text.split("\n"):
        if (
            not line[:1].isspace()
            and not line.startswith(">")
            and not any(mark in line for mark in LITERAL_MARKS)
        ):
            if heading := HEADING.fullmatch(line):
                line = "*" + BOLD.sub(r"\1", heading[1].strip()) + "*"
            else:
                line = BOLD.sub(r"*\1*", line)
        lines.append(_paragraph(line))
    # Aumentar o corpo com uma quebra tornaria uma resposta no limite impossível de entregar.
    formatted = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return formatted if len(formatted) <= 4000 else text
