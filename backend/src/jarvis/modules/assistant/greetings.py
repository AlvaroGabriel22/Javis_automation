"""Saudações privadas, escolhidas pelo tratamento do dono e pelo contexto da conversa."""

import re
from typing import TYPE_CHECKING

from sqlalchemy import select

from jarvis.modules.assistant.intent import fold
from jarvis.modules.identity.preferences import CASUAL_VOCATIVES, Preferences
from jarvis.modules.messaging.models import Inbound, Outbound

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from jarvis.modules.messaging.queue import Turn


_STANDALONE = re.compile(
    r"(?:jarvis[\s,.!?]+)?(oi|ola|bom dia|boa noite)(?:[\s,.!?]+jarvis)?[.!?…]*"
)
_SALUTATIONS = {
    "Bom dia": (
        "Bom dia, {}!",
        "Bom dia, {}! Dormiu bem?",
        "Bom dia, {}! Que seu dia comece bem.",
    ),
    "Boa noite": (
        "Boa noite, {}!",
        "Boa noite, {}! Descansa bem.",
        "Boa noite, {}! Bons sonhos.",
    ),
}


def standalone_greeting(text: str) -> str | None:
    words = fold(" ".join(text.split()))
    match = _STANDALONE.fullmatch(words)
    if match is None:
        return None
    return {"oi": "Olá", "ola": "Olá", "bom dia": "Bom dia", "boa noite": "Boa noite"}[match[1]]


def greeting_options(label: str, profile: Preferences, *, social: bool = True) -> tuple[str, ...]:
    if not social or not profile.uses_masculine_endearments:
        return (f"{label}, {profile.vocative}.",)
    if label == "Olá":
        return ("Opa, meu rei!", "Opa, meu rei, o que o senhor manda?")
    phrases = _SALUTATIONS[label]
    return tuple(phrases[i % len(phrases)].format(name) for i, name in enumerate(CASUAL_VOCATIVES))


def next_greeting(
    db: "Session", turn: "Turn", label: str, profile: Preferences, *, social: bool
) -> str:
    options = greeting_options(label, profile, social=social)
    if len(options) == 1:
        return options[0]
    # O apelido do último bom-dia também conta na próxima boa-noite, inclusive
    # quando a saudação anterior saiu pela rotina automática.
    related_labels = tuple(_SALUTATIONS) if label in _SALUTATIONS else (label,)
    positions = {
        line: index
        for related in related_labels
        for index, line in enumerate(greeting_options(related, profile))
    }
    previous = db.scalar(
        select(Outbound.text)
        .join(Inbound, Inbound.id == Outbound.id)
        .where(
            Inbound.chat_id == turn.chat_id,
            Inbound.id < turn.id,
            Inbound.kind.in_(("text", "audio", "notice")),
            Outbound.text.in_(tuple(positions)),
        )
        .order_by(Inbound.id.desc())
        .limit(1)
    )
    return options[(positions[previous] + 1) % len(options)] if previous is not None else options[0]
