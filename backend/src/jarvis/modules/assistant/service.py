"""Comandos locais executados dentro da transação que confirma o turno."""

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import Contact, MessageDraft, Owner, Schedule
from jarvis.modules.assistant.arithmetic import arithmetic
from jarvis.modules.assistant.calendar_followup import resume_event
from jarvis.modules.assistant.intent import (
    fold,
    mentions_message_request,
    parse_command,
    tidy,
    unclear_timed_audio,
)
from jarvis.modules.assistant.recipients import (
    Recipient,
    add_alias,
    candidates,
    materialize,
    phone_number,
    visible_name,
)
from jarvis.modules.assistant.relay import continue_request, draft_reply, start_request
from jarvis.modules.calendar.chat import personal
from jarvis.modules.errors import Problem
from jarvis.modules.identity.preferences import Preferences
from jarvis.modules.messaging.queue import Turn, valid_text
from jarvis.modules.personal.service import handle_personal
from jarvis.modules.receipts.service import reply_receipt
from jarvis.modules.scheduling.service import EDITABLE, stage_cancel
from jarvis.modules.stickers.preferences import handle_preference
from jarvis.modules.stickers.service import handle_sticker

STATUS = {
    "scheduled": "agendado",
    "cancelled": "cancelado",
    "sending": "em transmissão",
    "accepted": "aceito pelo WhatsApp",
    "needs_review": "aguarda revisão",
    "uncertain": "resultado incerto",
    "failed": "falhou",
}


def _card(db: Session, turn: Turn) -> str:
    try:
        card = json.loads(turn.text)
        if not isinstance(card, dict):
            raise ValueError
        name, phones = card["name"], card["phones"]
        if (
            not isinstance(name, str)
            or not valid_text(name, 120)
            or not isinstance(phones, list)
            or not 1 <= len(phones) <= 20
        ):
            raise ValueError
        numbers = []
        for value in phones:
            if not isinstance(value, str) or not (number := phone_number(value)):
                raise ValueError
            if number not in numbers:
                numbers.append(number)
    except (ValueError, KeyError, RecursionError):
        return (
            "Não consegui ler os números deste cartão. Envie um cartão com nome e telefone com DDI."
        )
    for number in numbers:
        target = Recipient(name.strip(), number)
        row = materialize(db, turn.chat_id, target)
        if row.name != target.name and row.source_route in (None, turn.chat_id):
            add_alias(db, turn.chat_id, target, target.name)
    return f"Recebi o cartão de {name.strip()}. " + " · ".join(numbers)


def _rows(db: Session, route: str):
    return (
        select(Schedule, Contact)
        .join(Contact, Schedule.contact_id == Contact.id)
        .where(Schedule.owner_id == 1, Schedule.route_key == route)
        .order_by(Schedule.scheduled_at, Schedule.id)
    )


def _describe(db: Session, route: str, row: Schedule, contact: Contact) -> str:
    at = row.scheduled_at.astimezone(ZoneInfo("America/Manaus"))
    part = row.text.replace("\n", " ")[:55]
    return (
        f"#{str(row.id)[:8]} · {at:%d/%m/%Y %H:%M} · {visible_name(db, contact, route)}"
        f" · {part} · {STATUS.get(row.status, row.status)}"
    )


def _cancel(db: Session, route: str, target: str) -> str:
    ref = re.fullmatch(r"#([\da-f-]{8,36})", target, re.I)
    stmt = _rows(db, route)
    if ref:
        # UUIDs são portáveis no banco; compare o prefixo de exibição em Python.
        matches = [r for r in db.execute(stmt) if str(r[0].id).startswith(ref[1].lower())]
    elif target:
        phones = [c.phone for c in candidates(db, route, target)]
        matches = db.execute(
            stmt.where(Schedule.status.in_(EDITABLE), Contact.phone.in_(phones))
        ).all()
    else:
        return "Qual agendamento deseja cancelar? Envie “listar agendamentos” e depois “cancele #código”."
    if not matches:
        return "Não encontrei esse agendamento neste vínculo. Envie “listar agendamentos” para conferir os códigos."
    if len(matches) > 1:
        lines = [_describe(db, route, row, contact) for row, contact in matches[:8]]
        return (
            "Encontrei mais de um. Responda “cancele #código” com um destes códigos:\n"
            + "\n".join(lines)
        )
    row, contact = matches[0]
    try:
        stage_cancel(db, 1, row.id, route_key=route)
    except Problem:
        db.refresh(row)
        return f"Este agendamento está com o estado «{STATUS.get(row.status, row.status)}» e não pode ser cancelado."
    return f"Cancelado o agendamento #{str(row.id)[:8]} para {visible_name(db, contact, route)}."


def handle(
    db: Session, turn: Turn, now: datetime, *, unavailable=(), google_linked=False
) -> str | None:
    command = parse_command(turn.text, audio=turn.kind == "audio") if turn.kind != "card" else None
    owner = db.get(Owner, 1)
    if owner is None:
        return (
            "Cadastre o dono pelo painel local antes de usar a agenda."
            if command or turn.kind == "card"
            else None
        )
    if turn.kind == "card":
        return _card(db, turn)
    preference = handle_preference(db, turn)
    if preference is not None:
        return preference
    profile = Preferences.for_owner(owner)
    normal = tidy(fold(turn.text)).removeprefix("jarvis, ").rstrip(" ?")
    if normal in {
        "o que voce pode fazer",
        "o que vc sabe fazer",
        "o que voce sabe fazer",
        "quais sao suas habilidades",
        "quais ferramentas voce tem",
    }:
        return (
            "Posso cuidar da sua agenda, te lembrar de compromissos e programar recados.\n\n"
            "Nas finanças, registro gastos, leio cupons e confiro quanto sobra no orçamento. "
            "Os lançamentos vão para a sua planilha.\n\n"
            "Também lembro do que você me conta, leio imagens e mando figurinhas. "
            "Com a busca conectada, posso pesquisar e te passar as fontes."
        )
    if normal in {"quem e voce", "quem e vc", "quem voce e"}:
        return "Sou o Jarvis, seu assistente pessoal."
    if normal in {"quem sou eu", "qual e meu nome", "qual o meu nome", "qual meu nome"}:
        return f"Você é {profile.preferred_name}."
    if normal == "como voce me chama":
        return f"Você prefere que eu te chame de {profile.vocative}."
    receipt = reply_receipt(db, turn, now)
    if receipt is not None:
        return receipt
    draft = db.get(MessageDraft, turn.chat_id)
    if draft and not (command and command.get("body")) and draft_reply(normal, now):
        return continue_request(db, turn, draft, profile, now)
    if not draft:
        local_result = (
            handle_sticker(db, turn) or handle_personal(db, turn, now) or arithmetic(turn.text)
        )
        if local_result is not None:
            return local_result
        continuation = resume_event(db, turn, now, google_linked)
        if continuation is not None:
            return continuation
    calendar_reply = personal(db, turn, now, unavailable=unavailable, google_linked=google_linked)
    if calendar_reply is not None:
        return calendar_reply
    if draft and command is None:
        return continue_request(db, turn, draft, profile, now)
    if not draft and normal in {
        "agora",
        "envie agora",
        "pode enviar agora",
        "pode mandar agora",
        "mais tarde",
    }:
        return "Não há um recado aguardando sua escolha. Informe o destinatário e a mensagem."
    if command is None:
        if turn.kind == "audio" and unclear_timed_audio(turn.text):
            return "Ouvi um possível pedido de envio, mas não identifiquei todos os dados. Nada foi agendado. Repita o destinatário e o recado."
        if mentions_message_request(turn.text):
            return "Nenhum envio foi registrado. Diga “envie para”, o nome do contato e o recado."
        return None
    match command["action"]:
        case "list":
            rows = db.execute(
                _rows(db, turn.chat_id)
                .where(
                    Schedule.status.in_(
                        ("scheduled", "sending", "needs_review", "uncertain", "failed")
                    )
                )
                .limit(11)
            ).all()
            if not rows:
                return "Não há agendamentos pendentes neste vínculo."
            trailer = "\nHá mais resultados no painel." if len(rows) > 10 else ""
            return (
                "Agenda deste vínculo, no horário de Manaus:\n"
                + "\n".join(_describe(db, turn.chat_id, row, contact) for row, contact in rows[:10])
                + trailer
            )
        case "cancel":
            return _cancel(db, turn.chat_id, command["recipient"])
        case "clarify_cancel":
            return "Para escolher o agendamento exato, envie “listar agendamentos” e depois “cancele #código”."
        case "alias":
            opts = candidates(db, turn.chat_id, command["recipient"])
            alias = command["alias"]
            if not valid_text(alias, 120):
                return "Escolha um apelido de até 120 caracteres."
            if len(opts) != 1:
                return "Informe o número completo para identificar o contato. Exemplo: “chame +5592999990000 de Rafa”."
            if not add_alias(db, turn.chat_id, opts[0], alias):
                return "Não consegui salvar esse apelido neste vínculo. Você pode usar o telefone completo."
            return f"Salvei o apelido {alias} para {opts[0].name}."
        case "original_audio":
            return "Posso transcrever seu áudio e responder em texto. O envio do arquivo de voz original ainda não está disponível."
        case "create":
            return start_request(db, turn, command, profile, now)
        case "move":
            return "Para remarcar, abra Agenda de envios no painel, escolha o agendamento e toque em Remarcar."
    return None
