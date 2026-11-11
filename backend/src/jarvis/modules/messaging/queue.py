"""Transações curtas do canal. Nenhum lock SQL cobre chamadas ao Ollama ou WhatsApp."""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import Engine, and_, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, aliased

from jarvis.adapters.persistence.db import utcnow
from jarvis.adapters.persistence.models import Owner
from jarvis.modules.assistant.model_errors import ModelIssue
from jarvis.modules.identity.preferences import Preferences
from jarvis.modules.messaging import reactions
from jarvis.modules.messaging.models import (
    Chat,
    ChatReaction,
    DeliveryAttempt,
    Inbound,
    Outbound,
    StickerReply,
)

MAX_ATTEMPTS = 3
MAX_MEDIA_BYTES = 8 * 1024 * 1024
LOTTIE_PREVIEW = "[Miniatura de figurinha Lottie]"
InboundKind = Literal["text", "audio", "card", "notice", "image", "sticker"]
TURN_LEASE = timedelta(seconds=90)
DELIVERY_LEASE = timedelta(minutes=2)
BACKOFF = (timedelta(seconds=5), timedelta(seconds=20))
MODEL_UNAVAILABLE = ModelIssue.UNAVAILABLE.message


@dataclass(frozen=True)
class Turn:
    id: int
    token: str
    chat_id: str
    text: str
    attempt: int
    received_at: datetime | None = None
    kind: str = "text"
    media: bytes | None = None


@dataclass(frozen=True)
class Delivery:
    id: int
    token: str
    chat_id: str
    text: str
    attempt: int
    sticker_key: str | None = None
    reaction_to: int | None = None


def valid_text(value: str, limit: int) -> bool:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= limit
        or not value.strip()
        or "\0" in value
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def already_received(engine: Engine, chat_id: str, message_id: str) -> bool:
    # Evita refazer o download no replay. A restrição única continua cobrindo ingressos concorrentes.
    with Session(engine) as db:
        return (
            db.scalar(
                select(Inbound.id).where(
                    Inbound.chat_id == chat_id, Inbound.message_id == message_id
                )
            )
            is not None
        )


def accept_inbound(
    engine: Engine,
    *,
    chat_id: str,
    message_id: str,
    text: str,
    now: datetime | None = None,
    kind: InboundKind = "text",
    media: bytes | None = None,
) -> bool:
    """O gateway já verificou dono, conta, conversa privada e origem da mensagem."""
    if not (valid_text(chat_id, 192) and valid_text(message_id, 192) and valid_text(text, 16000)):
        raise ValueError("Evento de texto inválido.")
    if kind not in {"text", "audio", "card", "notice", "image", "sticker"}:
        raise ValueError("Tipo de mensagem inválido.")
    if kind == "notice" and not valid_text(text, 4000):
        raise ValueError("Aviso longo demais.")
    if kind in {"audio", "image", "sticker"}:
        if not isinstance(media, bytes) or not 0 < len(media) <= MAX_MEDIA_BYTES:
            raise ValueError("A mídia deve ter até 8 MiB.")
    elif media is not None:
        raise ValueError("Conteúdo binário inesperado.")
    at = now or utcnow()
    insert = pg_insert if engine.dialect.name == "postgresql" else sqlite_insert
    with Session(engine) as db, db.begin():
        # Serializa ingressos do mesmo chat antes de atribuir o número de chegada.
        # Assim uma transação mais lenta não revela uma sequência menor tardiamente.
        db.execute(
            insert(Chat)
            .values(chat_id=chat_id)
            .on_conflict_do_update(index_elements=[Chat.chat_id], set_={"chat_id": chat_id})
        )
        action = (
            insert(Inbound)
            .values(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                kind=kind,
                media=media,
                received_at=at,
                available_at=at,
                attempts=0,
                state="pending",
            )
            .on_conflict_do_nothing(index_elements=[Inbound.chat_id, Inbound.message_id])
            .returning(Inbound.id)
        )
        return db.scalar(action) is not None


def _reply(db: Session, inbound_id: int, text: str, at: datetime) -> None:
    db.add(Outbound(id=inbound_id, text=text, state="ready", attempts=0, available_at=at))


def _recover_exhausted_turns(db: Session, at: datetime) -> None:
    exhausted = (
        Inbound.state == "processing",
        Inbound.lease_until <= at,
        Inbound.attempts >= MAX_ATTEMPTS,
    )
    ids = (
        select(Inbound.id)
        .where(*exhausted)
        .order_by(Inbound.id)
        .limit(32)
        .with_for_update(skip_locked=True)
    )
    updated = (
        update(Inbound)
        .where(Inbound.id.in_(ids), *exhausted)
        .values(
            state="failed",
            token=None,
            lease_until=None,
            error_code="worker_expired",
            media=None,
        )
        .returning(Inbound.id)
    )
    for inbound_id in db.scalars(updated):
        _reply(db, inbound_id, MODEL_UNAVAILABLE, at)


def claim_turn(engine: Engine, *, now: datetime | None = None) -> Turn | None:
    at = now or utcnow()
    older = aliased(Inbound)
    eligible = or_(
        and_(Inbound.state == "pending", Inbound.available_at <= at),
        and_(Inbound.state == "processing", Inbound.lease_until <= at),
    )
    prior_pending = exists(
        select(older.id).where(
            older.chat_id == Inbound.chat_id,
            older.id < Inbound.id,
            older.state.in_(("pending", "processing")),
        )
    ).correlate(Inbound)
    with Session(engine) as db, db.begin():
        _recover_exhausted_turns(db, at)
        candidate = (
            select(Inbound.id)
            .where(eligible, Inbound.attempts < MAX_ATTEMPTS, ~prior_pending)
            .order_by(Inbound.id)
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )
        token = secrets.token_urlsafe(24)
        row = db.execute(
            update(Inbound)
            .where(Inbound.id == candidate, eligible)
            .values(
                state="processing",
                token=token,
                lease_until=at + TURN_LEASE,
                attempts=Inbound.attempts + 1,
            )
            .returning(
                Inbound.id,
                Inbound.chat_id,
                Inbound.text,
                Inbound.attempts,
                Inbound.received_at,
                Inbound.kind,
                Inbound.media,
            )
        ).one_or_none()
        return (
            Turn(
                row.id,
                token,
                row.chat_id,
                row.text,
                row.attempts,
                row.received_at,
                row.kind,
                row.media,
            )
            if row
            else None
        )


def _held_turn(turn: Turn, at: datetime):
    return (
        Inbound.id == turn.id,
        Inbound.state == "processing",
        Inbound.token == turn.token,
        Inbound.lease_until > at,
    )


def renew_turn(engine: Engine, turn: Turn, *, now: datetime | None = None) -> bool:
    at = now or utcnow()
    with Session(engine) as db, db.begin():
        return (
            db.scalar(
                update(Inbound)
                .where(*_held_turn(turn, at))
                .values(lease_until=at + TURN_LEASE)
                .returning(Inbound.id)
            )
            is not None
        )


def can_react(engine: Engine, turn: Turn, *, now: datetime | None = None) -> bool:
    with Session(engine) as db:
        return reactions.eligible(db, turn.chat_id, turn.id, now or utcnow())


def complete_turn(
    engine: Engine,
    turn: Turn,
    text: str,
    *,
    now: datetime | None = None,
    reaction_key: str | None = None,
) -> bool:
    if not valid_text(text, 4000):
        raise ValueError("Resposta inválida.")
    at = now or utcnow()
    with Session(engine) as db, db.begin():
        changed = db.scalar(
            update(Inbound)
            .where(*_held_turn(turn, at))
            .values(state="completed", token=None, lease_until=None, error_code=None, media=None)
            .returning(Inbound.id)
        )
        if changed is None:
            return False
        _reply(db, changed, text, at)
        if reaction_key:
            reactions.plan(db, turn.chat_id, changed, reaction_key, at)
        return True


def apply_command(
    engine: Engine,
    turn: Turn,
    handler: Callable[[Session], str | None],
    *,
    now: datetime | None = None,
) -> bool:
    """Confirma uma alteração local, a resposta e o turno juntos. O handler não faz I/O externo."""
    at = now or utcnow()
    with Session(engine) as db:
        # UPDATE também reserva a escrita no SQLite. PostgreSQL trava apenas este turno.
        held = db.scalar(
            update(Inbound)
            .where(*_held_turn(turn, at))
            .values(state="processing")
            .returning(Inbound.id)
        )
        if held is None:
            return True  # Outro worker já assumiu a responsabilidade.
        response = handler(db)
        if response is None:
            return False  # O encerramento da sessão desfaz alterações especulativas.
        if not valid_text(response, 4000):
            raise ValueError("Resposta inválida.")
        db.execute(
            update(Inbound)
            .where(Inbound.id == held)
            .values(state="completed", token=None, lease_until=None, error_code=None, media=None)
        )
        _reply(db, held, response, at)
        db.commit()
        return True


def store_transcription(
    engine: Engine, turn: Turn, text: str, *, now: datetime | None = None
) -> bool:
    """Libera o binário após guardar o texto. Um retry não precisa transcrever novamente."""
    if not valid_text(text, 16000):
        raise ValueError("Transcrição vazia ou longa demais.")
    at = now or utcnow()
    with Session(engine) as db, db.begin():
        return (
            db.scalar(
                update(Inbound)
                .where(*_held_turn(turn, at), Inbound.kind == "audio")
                .values(text=text, media=None)
                .returning(Inbound.id)
            )
            is not None
        )


def fail_turn(
    engine: Engine,
    turn: Turn,
    *,
    retryable: bool = True,
    issue: ModelIssue | None = None,
    now: datetime | None = None,
) -> bool:
    at = now or utcnow()
    issue = issue or (ModelIssue.UNAVAILABLE if retryable else ModelIssue.CONFIGURATION)
    terminal = not issue.retryable or turn.attempt >= MAX_ATTEMPTS
    with Session(engine) as db, db.begin():
        changed = db.scalar(
            update(Inbound)
            .where(*_held_turn(turn, at))
            .values(
                state="failed" if terminal else "pending",
                token=None,
                lease_until=None,
                error_code=issue.code,
                available_at=at if terminal else at + BACKOFF[turn.attempt - 1],
            )
            .returning(Inbound.id)
        )
        if changed is None:
            return False
        if terminal:
            db.execute(update(Inbound).where(Inbound.id == changed).values(media=None))
            _reply(db, changed, issue.message, at)
        return True


def history_for_turn(engine: Engine, turn: Turn) -> list[dict[str, str]]:
    """Contexto do vínculo com evidências relevantes e respostas aceitas pelo transporte."""
    from jarvis.modules.personal.service import excluded_history_ids, memory_context

    with Session(engine) as db:
        owner = db.get(Owner, 1)
        profile = Preferences.for_owner(owner) if owner else None
        memories = memory_context(db, turn) if owner else ""
        recent = db.execute(
            select(Inbound.text, Outbound.text, Outbound.state)
            .outerjoin(Outbound, Outbound.id == Inbound.id)
            .where(
                Inbound.chat_id == turn.chat_id,
                Inbound.id < turn.id,
                Inbound.id.not_in(excluded_history_ids(turn.chat_id)),
                Inbound.kind.in_(("text", "audio", "image", "sticker")),
            )
            .order_by(Inbound.id.desc())
            .limit(10)
        ).all()
    history: list[dict[str, str]] = (
        [{"role": "system", "content": profile.chat_context(), "tone": profile.tone}]
        if profile
        else []
    )
    # Limita o prompt, sem truncar o texto original guardado na caixa de entrada.
    for user_text, assistant_text, state in reversed(recent):
        history.append({"role": "user", "content": user_text[:1000]})
        if state == "accepted":
            history.append({"role": "assistant", "content": assistant_text[:1000]})
    if memories:
        history.append(
            {
                "role": "user",
                "content": "Dados recuperados da minha conversa para referência factual e de tom: "
                + memories,
            }
        )
    history.append({"role": "user", "content": turn.text})
    return history


def claim_delivery(engine: Engine, *, chat_id: str, now: datetime | None = None) -> Delivery | None:
    """Reserva e grava a tentativa antes de devolver o texto ao escritor do WhatsApp."""
    at = now or utcnow()
    earlier = aliased(Outbound)
    source = aliased(Inbound)
    blocked = exists(
        select(earlier.id)
        .join(source, source.id == earlier.id)
        .where(
            source.chat_id == chat_id,
            earlier.id < Outbound.id,
            earlier.state.in_(("ready", "transmitting")),
        )
    ).correlate(Outbound)
    eligible = (
        Outbound.state == "ready",
        Outbound.available_at <= at,
        Outbound.attempts < MAX_ATTEMPTS,
    )
    candidate = (
        select(Outbound.id)
        .join(Inbound, Inbound.id == Outbound.id)
        .where(Inbound.chat_id == chat_id, *eligible, ~blocked)
        .order_by(Outbound.id)
        .limit(1)
        .with_for_update(of=Outbound, skip_locked=True)
        .scalar_subquery()
    )

    token = secrets.token_urlsafe(24)
    with Session(engine) as db, db.begin():
        db.execute(
            update(Outbound)
            .where(Outbound.state == "ready", Outbound.expires_at <= at)
            .values(state="failed")
        )
        reactions.drop_superseded(db, chat_id)
        row = db.execute(
            update(Outbound)
            .where(Outbound.id == candidate, *eligible)
            .values(
                state="transmitting",
                token=token,
                lease_until=at + DELIVERY_LEASE,
                attempts=Outbound.attempts + 1,
            )
            .returning(Outbound.id, Outbound.text, Outbound.attempts)
        ).one_or_none()
        if not row:
            return None
        db.add(
            DeliveryAttempt(
                outbound_id=row.id, attempt=row.attempts, started_at=at, outcome="transmitting"
            )
        )
        media = db.execute(
            select(
                StickerReply.sticker_key,
                ChatReaction.sticker_key.label("reaction_key"),
                ChatReaction.turn_id,
            )
            .select_from(Inbound)
            .outerjoin(StickerReply, StickerReply.inbound_id == Inbound.id)
            .outerjoin(ChatReaction, ChatReaction.delivery_id == Inbound.id)
            .where(Inbound.id == row.id)
        ).one()
        return Delivery(
            row.id,
            token,
            chat_id,
            row.text,
            row.attempts,
            media.sticker_key or media.reaction_key,
            media.turn_id,
        )


def renew_delivery(engine: Engine, delivery: Delivery) -> bool:
    """Reconfere a reserva após um upload que pode ter demorado."""
    now = utcnow()
    with Session(engine) as db, db.begin():
        if delivery.reaction_to is not None and not reactions.pending_is_current(
            db, delivery.chat_id, delivery.reaction_to, delivery.id
        ):
            return False
        return (
            db.scalar(
                update(Outbound)
                .where(
                    Outbound.id == delivery.id,
                    Outbound.state == "transmitting",
                    Outbound.token == delivery.token,
                    Outbound.lease_until > now,
                    or_(Outbound.expires_at.is_(None), Outbound.expires_at > now),
                )
                .values(lease_until=now + DELIVERY_LEASE)
                .returning(Outbound.id)
            )
            is not None
        )


def replace_missing_sticker(engine: Engine, delivery: Delivery, notice: str) -> bool:
    """Antes da transmissão, preserva no histórico o aviso que o dono receberá."""
    with Session(engine) as db, db.begin():
        changed = db.scalar(
            update(Outbound)
            .where(
                Outbound.id == delivery.id,
                Outbound.state == "transmitting",
                Outbound.token == delivery.token,
            )
            .values(text=notice)
            .returning(Outbound.id)
        )
        if changed is None:
            return False
        db.execute(delete(StickerReply).where(StickerReply.inbound_id == changed))
        return True


def finish_delivery(
    engine: Engine,
    delivery: Delivery,
    *,
    outcome: Literal["accepted", "not_sent", "uncertain"],
    external_id: str | None = None,
    retry: bool = True,
    now: datetime | None = None,
) -> bool:
    at = now or utcnow()
    if outcome not in ("accepted", "not_sent", "uncertain"):
        raise ValueError("Resultado de envio inválido.")
    if outcome == "accepted" and not valid_text(external_id or "", 192):
        raise ValueError("O aceite requer o identificador de mensagem do canal.")
    if external_id is not None and not valid_text(external_id, 192):
        raise ValueError("ID externo inválido.")
    state = outcome
    due = at
    # Após upload demorado, uma falha comprovada antes do envio ainda pode liberar
    # o mesmo token. A recuperação anterior já terá invalidado esse token.
    lease_condition = () if outcome == "not_sent" else (Outbound.lease_until > at,)
    if outcome == "not_sent":
        if retry and delivery.attempt < MAX_ATTEMPTS:
            state, due = "ready", at + BACKOFF[delivery.attempt - 1]
        else:
            state = "failed"
    with Session(engine) as db, db.begin():
        changed = db.scalar(
            update(Outbound)
            .where(
                Outbound.id == delivery.id,
                Outbound.state == "transmitting",
                Outbound.token == delivery.token,
                *lease_condition,
                Outbound.attempts == delivery.attempt,
            )
            .values(
                state=state, available_at=due, token=None, lease_until=None, external_id=external_id
            )
            .returning(Outbound.id)
        )
        if changed is None:
            return False
        db.execute(
            update(DeliveryAttempt)
            .where(
                DeliveryAttempt.outbound_id == changed, DeliveryAttempt.attempt == delivery.attempt
            )
            .values(outcome=outcome, external_id=external_id, ended_at=at)
        )
        if outcome == "accepted" and delivery.reaction_to is None and delivery.sticker_key is None:
            reactions.after_acceptance(db, delivery.id, at)
        return True


def recover_deliveries(engine: Engine, *, now: datetime | None = None) -> int:
    at = now or utcnow()
    expired = (Outbound.state == "transmitting", Outbound.lease_until <= at)
    ids = (
        select(Outbound.id)
        .where(*expired)
        .order_by(Outbound.id)
        .limit(64)
        .with_for_update(skip_locked=True)
    )
    with Session(engine) as db, db.begin():
        rows = db.execute(
            update(Outbound)
            .where(Outbound.id.in_(ids), *expired)
            .values(state="uncertain", token=None, lease_until=None)
            .returning(Outbound.id, Outbound.attempts)
        ).all()
        for oid, attempt in rows:
            db.execute(
                update(DeliveryAttempt)
                .where(
                    DeliveryAttempt.outbound_id == oid,
                    DeliveryAttempt.attempt == attempt,
                )
                .values(outcome="uncertain", ended_at=at)
            )
        return len(rows)


def queue_status(engine: Engine) -> dict[str, dict[str, int]]:
    with Session(engine) as db:
        return {
            "inbox": {
                state: n
                for state, n in db.execute(
                    select(Inbound.state, func.count()).group_by(Inbound.state)
                )
            },
            "outbox": {
                state: n
                for state, n in db.execute(
                    select(Outbound.state, func.count()).group_by(Outbound.state)
                )
            },
        }
