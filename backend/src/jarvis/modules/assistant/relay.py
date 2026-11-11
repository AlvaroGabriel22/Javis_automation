"""Diálogo de envio. O rascunho, a agenda e a confirmação usam a transação do turno."""

import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import DeviceContact, MessageDraft
from jarvis.modules.assistant.intent import WHEN, fold, tidy
from jarvis.modules.assistant.recipients import Recipient, candidates, materialize, phone_number
from jarvis.modules.assistant.timing import MANAUS, delivery_time
from jarvis.modules.identity.preferences import Preferences
from jarvis.modules.messaging.queue import Turn, valid_text
from jarvis.modules.scheduling.service import stage_schedule

CANCEL_REPLY = {
    "cancelar",
    "cancela",
    "cancele",
    "nao",
    "nao envie",
    "desista",
    "esquece",
    "deixa pra la",
}
LATER_REPLY = {
    "agendar",
    "agende",
    "mais tarde",
    "depois",
    "agendar para mais tarde",
    "quero agendar",
    "para mais tarde",
}


def _time_words(words: str) -> str:
    normal = tidy(fold(words))
    normal = re.sub(r"[, ]+por favor$", "", normal)
    normal = re.sub(
        r"^(?:sim[, ]+)?(?:(?:eu )?quero |pode )?(?:enviar|envie|envia|mandar|mande|manda|agendar|agende|agenda)(?: (?:para|pra))?\s+",
        "",
        normal,
    )
    return normal


def draft_reply(words: str, at: datetime) -> bool:
    normal = tidy(fold(words))
    answer = _time_words(normal)
    if normal in CANCEL_REPLY or answer in LATER_REPLY or answer in {"agora", "agora mesmo", "ja"}:
        return True
    if not WHEN.match(answer):
        return False
    try:
        delivery_time(answer, at)
    except (OverflowError, ValueError):
        return False
    return True


def _preview(body: str) -> str:
    return body if len(body) <= 900 else body[:900] + "…"


def _timing_question(draft: MessageDraft, profile: Preferences) -> str:
    return (
        f"Recado para {draft.contact_name}:\n«{_preview(draft.body)}»\n\n"
        f"Deseja enviar a mensagem agora ou agendar para mais tarde, {profile.vocative}?"
    )


def _set_time(draft: MessageDraft, words: str, received: datetime, now: datetime) -> bool:
    normal = _time_words(words)
    if normal in {"agora", "agora mesmo", "ja"}:
        draft.immediate, draft.scheduled_at = True, None
        return True
    try:
        due = delivery_time(normal, received).instant
    except (ValueError, OverflowError):
        return False
    if due <= now:
        return False
    draft.immediate, draft.scheduled_at = False, due
    return True


def _choice_question(draft: MessageDraft) -> str:
    assert draft.choices
    lines = [
        f"{i}. {c['name']} · {c['phone']} ({c['source']})"
        for i, c in enumerate(draft.choices[:8], 1)
    ]
    return (
        f"Encontrei mais de um contato para «{draft.contact_name}». Qual deles?\n"
        + "\n".join(lines)
        + "\nResponda com a opção ou o telefone completo."
    )


def _pick(draft: MessageDraft, answer: str) -> bool:
    choices = draft.choices or []
    number = phone_number(answer)
    selected = [c for c in choices if c["phone"] == number] if number else []
    match = re.fullmatch(r"(?:a |o )?(?:opcao |numero )?(\d{1,2})", answer)
    ordinal = {
        "um": 1,
        "uma": 1,
        "dois": 2,
        "duas": 2,
        "primeiro": 1,
        "primeira": 1,
        "segundo": 2,
        "segunda": 2,
        "terceiro": 3,
        "terceira": 3,
    }
    pos = (
        int(match[1])
        if match
        else ordinal.get(re.sub(r"^(?:[ao] )?(?:opcao |numero )?", "", answer), 0)
    )
    if 1 <= pos <= min(8, len(choices)):
        selected = [choices[pos - 1]]
    if not selected:
        if answer in {
            "whatsapp",
            "o do whatsapp",
            "agenda do whatsapp",
            "o da agenda",
            "o da agenda do whatsapp",
        }:
            selected = [c for c in choices if c["source"] == "agenda do WhatsApp"]
        elif answer in {"painel", "o do painel", "cadastro do jarvis", "o do jarvis"}:
            selected = [c for c in choices if c["source"] == "painel"]
    if len(selected) != 1:
        return False
    draft.phone, draft.contact_name = selected[0]["phone"], selected[0]["name"]
    draft.choices = None
    return True


def _save_or_ask(db: Session, draft: MessageDraft, profile: Preferences, now: datetime) -> str:
    if draft.phone is None:
        return _choice_question(draft)
    if not draft.body:
        return f"Qual recado devo enviar para {draft.contact_name}, {profile.vocative}?"
    text = f"Jarvis, a pedido de {profile.preferred_name}:\n\n{draft.body}"
    if not valid_text(text, 4000):
        db.delete(draft)
        return "O recado ficou longo demais. Repita o pedido com uma mensagem mais curta."
    if not draft.immediate and draft.scheduled_at is None:
        return _timing_question(draft, profile)
    due = now if draft.immediate else draft.scheduled_at
    assert due is not None
    if not draft.immediate and due <= now:
        draft.scheduled_at = None
        return "O horário já passou. " + _timing_question(draft, profile)
    contact = materialize(db, draft.route_key, Recipient(draft.contact_name, draft.phone))
    row = stage_schedule(
        db, 1, contact.id, due, text, now, draft.route_key, immediate=draft.immediate
    )
    at = (
        "agora"
        if draft.immediate
        else due.astimezone(MANAUS).strftime("em %d/%m/%Y às %H:%M:%S (Manaus)")
    )
    intro = f"Registrei o envio #{str(row.id)[:8]} para {draft.contact_name}, {at}."
    preview = _preview(draft.body)
    db.delete(draft)
    return f"{intro}\n\n«{preview}»\n\nTe aviso por aqui quando o WhatsApp responder."


def start_request(
    db: Session, turn: Turn, command: dict, profile: Preferences, now: datetime
) -> str:
    previous = db.get(MessageDraft, turn.chat_id)
    if previous:
        db.delete(previous)
        db.flush()
    query = command["recipient"]
    if not query:
        return "Quem deve receber a mensagem? Repita o pedido com o destinatário."
    when = command["when"]
    draft = MessageDraft(route_key=turn.chat_id, body=command["body"].strip(), immediate=False)
    if when and not _set_time(draft, when, turn.received_at or now, now):
        return "Não entendi um horário futuro ou ele já passou. Repita o pedido com o horário desejado."
    # O gateway constrói a chave com conta, telefone verificado do dono e ID do vínculo.
    route = turn.chat_id.split("|")
    if fold(query) in {"mim", "eu", "mim mesmo", "mim mesma", "meu whatsapp", "meu numero"}:
        if len(route) < 2 or not (number := phone_number(route[1])):
            return "Não consegui identificar seu número neste vínculo."
        options = [Recipient(profile.preferred_name, number)]
    else:
        options = candidates(db, turn.chat_id, query)
        if not options and fold(query).startswith(("minha ", "meu ")):
            options = candidates(db, turn.chat_id, query.split(" ", 1)[1])
    if not options:
        return f"Não encontrei «{query[:120]}». Repita o pedido com o nome cadastrado ou o número com DDD."
    if len(options) > 1:
        directory = set(
            db.scalars(select(DeviceContact.phone).where(DeviceContact.route_key == turn.chat_id))
        )
        draft.contact_name = query[:120]
        draft.choices = [
            {
                "phone": c.phone,
                "name": c.name,
                "source": "agenda do WhatsApp" if c.phone in directory else "painel",
            }
            for c in options
        ]
    else:
        draft.contact_name, draft.phone = options[0].name, options[0].phone
    db.add(draft)
    db.flush()
    return _save_or_ask(db, draft, profile, now)


def continue_request(
    db: Session, turn: Turn, draft: MessageDraft, profile: Preferences, now: datetime
) -> str:
    words = turn.text.strip()
    normal = tidy(fold(words))
    if normal in CANCEL_REPLY:
        db.delete(draft)
        return "Descartei o pedido. Nenhuma mensagem foi agendada."
    if draft.choices:
        if not _pick(draft, normal):
            return _choice_question(draft)
    elif not draft.body:
        if not _set_time(draft, words, turn.received_at or now, now):
            draft.body = words
    elif normal in LATER_REPLY or _time_words(normal) in LATER_REPLY:
        return f"Para qual dia e horário, {profile.vocative}?"
    elif not _set_time(draft, words, turn.received_at or now, now):
        return f"O recado para {draft.contact_name} está aguardando. Responda “agora”, indique um horário futuro ou diga “cancelar”."
    return _save_or_ask(db, draft, profile, now)
