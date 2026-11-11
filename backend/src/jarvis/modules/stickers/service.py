"""Pedidos explícitos de figurinha na conversa privada do dono."""

import re

from sqlalchemy.orm import Session

from jarvis.modules.assistant.intent import fold
from jarvis.modules.calendar.periods import command_text
from jarvis.modules.messaging.models import StickerReply
from jarvis.modules.messaging.queue import Turn
from jarvis.modules.stickers.catalog import BY_NAME, CATALOG, sticker_bytes

LIST = re.compile(
    r"(?:(?:listar?|liste|mostr[ae]|ver) (?:o )?(?:catalogo de |as )?|"
    r"quais (?:as |sao as )?|catalogo de )"
    r"(?:figurinhas|stickers)(?: (?:do jarvis|disponiveis|voce tem|vc tem))?"
)
SEND = re.compile(
    r"(?:(?:me )?(?:mand[ae]r?|envi[ae]r?)|manda-me|envie-me)"
    r"(?: (?:pra mim|para mim|aqui))? (?:uma? |a |o )?(?:figurinha|sticker)"
    r"(?: (?:de |do |da |chamad[ao] )?(?P<name>.+))?"
)
CATALOG_TEXT = ", ".join(item.name for item in CATALOG)
HELP = f"Tenho estas figurinhas: {CATALOG_TEXT}. Peça, por exemplo, “mande a figurinha de bom dia”."


def handle_sticker(db: Session, turn: Turn) -> str | None:
    if turn.kind not in {"audio", "text"}:
        return None
    request = fold(command_text(turn.text))
    if LIST.fullmatch(request):
        return HELP
    match = SEND.fullmatch(request)
    if not match:
        return None
    name = match["name"] or "combinado"
    name = re.sub(r"(?:^| )(?:pra mim|para mim|aqui|agora)$", "", name).strip()
    chosen = BY_NAME.get(name or "combinado")
    if not chosen:
        if re.search(r"\b(?:pra|pro|para|ao|aos)\b", name):
            return "Posso mandar uma figurinha do catálogo aqui na nossa conversa. " + HELP
        return HELP
    try:
        sticker_bytes(chosen.key)
    except (OSError, ValueError):
        return "O arquivo dessa figurinha está indisponível no catálogo local."
    db.add(StickerReply(inbound_id=turn.id, sticker_key=chosen.key))
    return f"[Figurinha · {chosen.name}]"
