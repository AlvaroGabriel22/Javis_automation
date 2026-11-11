"""Coordena as ferramentas pessoais sem manter uma transação SQL aberta durante I/O."""

import json
from dataclasses import replace
from datetime import datetime
from typing import Protocol, cast

from sqlalchemy.orm import Session

from jarvis.adapters.ai.ollama import ModelUnavailable
from jarvis.adapters.ai.persona import ConversationalReply
from jarvis.adapters.calendar_refresh import google_connected, refresh_for_query
from jarvis.adapters.persistence.models import MessageDraft, Owner
from jarvis.adapters.web_search import is_private_lookup
from jarvis.modules.assistant.greetings import next_greeting, standalone_greeting
from jarvis.modules.assistant.personal_planner import personal_candidate, plan_personal
from jarvis.modules.assistant.planner import (
    CalendarJSON,
    calendar_candidate,
    plan_calendar,
    retryable_parse_reply,
)
from jarvis.modules.assistant.research import research_answer, research_query
from jarvis.modules.assistant.service import handle
from jarvis.modules.calendar.periods import is_query
from jarvis.modules.calendar.scope import request_scope
from jarvis.modules.identity.preferences import Preferences
from jarvis.modules.messaging import queue
from jarvis.modules.personal.service import handle_personal
from jarvis.modules.stickers.policy import (
    reaction_safe_reply,
    recent_social_context,
    social_candidate,
)


class ChatModel(Protocol):
    def reply(self, messages: list[dict[str, str]]) -> str: ...


class ConversationalModel(ChatModel, Protocol):
    def converse(
        self, messages: list[dict[str, str]], *, allow_sticker: bool
    ) -> ConversationalReply: ...


def refresh(engine, text):
    scope = request_scope(text, query=True)
    return [] if scope.problem else refresh_for_query(engine, scope.providers)


def respond(engine, turn: queue.Turn, model: ChatModel, clock):
    """Um turno gera no máximo uma resposta principal e uma mutação transacional."""
    linguistic = turn.kind in {"audio", "text"}
    candidate = linguistic and calendar_candidate(turn.text)
    personal_read = linguistic and personal_candidate(turn.text)
    web = research_query(turn.text) if linguistic and not personal_read else None
    unavailable = (
        refresh(engine, turn.text)
        if linguistic and not personal_read and web is None and is_query(turn.text)
        else []
    )
    google_linked = google_connected()
    now: datetime = clock()
    original_reply = None
    try_planning = False
    use_web = False
    try_personal = False

    def route(db: Session):
        nonlocal original_reply, try_planning, use_web, try_personal
        draft = (
            db.get(MessageDraft, turn.chat_id)
            if candidate or personal_read or web is not None
            else None
        )
        if web is not None and not draft:
            use_web = True
            return None
        reply = handle(db, turn, now, unavailable=unavailable, google_linked=google_linked)
        if personal_read and not draft and reply is None:
            try_personal = True
            return None
        if candidate and not draft and retryable_parse_reply(reply):
            original_reply, try_planning = reply, True
            return None
        return reply

    if queue.apply_command(engine, turn, route, now=now):
        return
    if use_web:
        queue.complete_turn(engine, turn, research_answer(web or "", model), now=clock())
        return

    generate = getattr(model, "structured", None)
    reader = cast(CalendarJSON, generate) if callable(generate) else None
    received = turn.received_at or now
    if try_personal:
        try:
            read = plan_personal(turn.text, received, reader)
        except ModelUnavailable as exc:
            read = plan_personal(turn.text, received, None)
            if not read.command:
                queue.fail_turn(engine, turn, issue=exc.issue, now=clock())
                return
        if read.command:
            current = clock()
            local_query = replace(turn, text=read.command)
            queue.apply_command(
                engine,
                turn,
                lambda db: (
                    handle_personal(db, local_query, current)
                    or "Pode detalhar o que deseja consultar?"
                ),
                now=current,
            )
            return
        if read.clarification:
            queue.complete_turn(engine, turn, read.clarification, now=clock())
            return

    if try_planning:
        try:
            decision = plan_calendar(turn.text, received, reader)
        except ModelUnavailable as exc:
            decision = plan_calendar(turn.text, received, None)
            if not decision.command:
                queue.fail_turn(engine, turn, issue=exc.issue, now=clock())
                return
        if decision.command:
            canonical = replace(turn, text=decision.command)
            unavailable = refresh(engine, canonical.text) if decision.is_query else []
            current = clock()

            def execute_plan(db: Session):
                return (
                    handle(
                        db, canonical, current, unavailable=unavailable, google_linked=google_linked
                    )
                    or "Qual é a data e o assunto do compromisso?"
                )

            queue.apply_command(engine, turn, execute_plan, now=current)
            return
        clarification = original_reply or decision.clarification
        if clarification:
            queue.complete_turn(engine, turn, clarification, now=clock())
            return

    if is_private_lookup(turn.text):
        queue.complete_turn(
            engine,
            turn,
            "Em qual fonte devo procurar? Diga, por exemplo, «minha agenda desta semana», «o que te contei sobre o projeto?» ou «quanto gastei este mês?».",
            now=clock(),
        )
        return
    history = queue.history_for_turn(engine, turn)
    social = recent_social_context(history)
    greeting = None
    if linguistic and history[0]["role"] == "system" and (label := standalone_greeting(turn.text)):
        with Session(engine) as db:
            if owner := db.get(Owner, 1):
                greeting = next_greeting(db, turn, label, Preferences.for_owner(owner), social=social)
    conversational = callable(getattr(type(model), "converse", None))
    allow_sticker = (
        conversational
        and linguistic
        and social
        and social_candidate(turn.text)
        and queue.can_react(engine, turn, now=clock())
    )
    if greeting:
        if not allow_sticker:
            queue.complete_turn(engine, turn, greeting, now=clock())
            return
        # O texto já está escolhido. O modelo decide se uma figurinha combina com ele
        # no contexto, usando a mesma chamada que faz no restante da conversa.
        history[0]["content"] += (
            " Neste turno, o texto da resposta já foi escolhido: "
            + json.dumps(greeting, ensure_ascii=False)
            + ". Use esse texto e escolha a reação opcional com base nele."
        )
    reaction = None
    try:
        if conversational:
            reply = cast(ConversationalModel, model).converse(history, allow_sticker=allow_sticker)
            answer = greeting or reply.text
            if allow_sticker and reply.sticker_key and reaction_safe_reply(answer):
                opposite = {"bom-dia": "Boa noite", "boa-noite": "Bom dia"}.get(reply.sticker_key)
                if not greeting or not opposite or not greeting.startswith(opposite):
                    reaction = reply.sticker_key
        else:
            answer = model.reply(history)
        if not queue.valid_text(answer, 4000):
            raise ModelUnavailable()
    except ModelUnavailable as exc:
        if greeting:
            queue.complete_turn(engine, turn, greeting, now=clock())
        else:
            queue.fail_turn(engine, turn, issue=exc.issue, now=clock())
    else:
        queue.complete_turn(engine, turn, answer, reaction_key=reaction, now=clock())
