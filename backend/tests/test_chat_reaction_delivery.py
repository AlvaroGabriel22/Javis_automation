from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_conversation_queue import CHAT, OTHER, accept, race
from test_sticker_delivery import channel  # noqa: F401
from test_whatsapp_gateway import OWNER, msg

from jarvis.adapters.persistence.db import utcnow
from jarvis.modules.messaging import queue as q
from jarvis.modules.messaging import reactions
from jarvis.modules.messaging.models import ChatReaction, DeliveryAttempt, Inbound, Outbound
from jarvis.modules.stickers.preferences import StickerPreference


def plan(engine, clock, *, reaction="combinado"):
    assert accept(engine, clock)
    turn = q.claim_turn(engine, now=clock())
    assert turn and q.can_react(engine, turn, now=clock())
    assert q.complete_turn(engine, turn, "Boa, cara!", reaction_key=reaction, now=clock())
    return turn


def accept_text(engine, clock):
    text = q.claim_delivery(engine, chat_id=CHAT, now=clock())
    assert text and text.sticker_key is None
    assert q.finish_delivery(engine, text, outcome="accepted", external_id="wa-text", now=clock())
    return text


def test_two_gateways_reserve_followup_only_after_text_receipt_and_keep_separate_audit(
    engine, clock
):
    turn = plan(engine, clock)
    assert not q.complete_turn(engine, turn, "Outra", reaction_key="valeu", now=clock())
    with Session(engine) as db:
        assert db.get_one(ChatReaction, turn.id).delivery_id is None
    primary = accept_text(engine, clock)
    assert not q.finish_delivery(
        engine, primary, outcome="accepted", external_id="second", now=clock()
    )
    assert not q.claim_delivery(engine, chat_id=OTHER, now=clock())
    claims = [d for d in race(lambda: q.claim_delivery(engine, chat_id=CHAT, now=clock())) if d]
    assert len(claims) == 1
    sticker = claims[0]
    assert sticker.reaction_to == turn.id and sticker.sticker_key == "combinado"
    assert q.finish_delivery(
        engine, sticker, outcome="accepted", external_id="wa-sticker", now=clock()
    )
    engine.dispose()
    assert not q.claim_delivery(engine, chat_id=CHAT, now=clock())
    with Session(engine) as db:
        assert db.get_one(Outbound, primary.id).external_id == "wa-text"
        assert db.get_one(Outbound, sticker.id).external_id == "wa-sticker"
        assert db.get_one(Inbound, sticker.id).kind == "notice"
        assert [
            a.external_id
            for a in db.scalars(select(DeliveryAttempt).order_by(DeliveryAttempt.outbound_id))
        ] == ["wa-text", "wa-sticker"]


@pytest.mark.parametrize("lost_ack", [False, True])
def test_failed_or_unconfirmed_text_never_generates_a_sticker(engine, clock, lost_ack):
    turn = plan(engine, clock)
    for _ in range(1 if lost_ack else q.MAX_ATTEMPTS):
        text = q.claim_delivery(engine, chat_id=CHAT, now=clock())
        assert text and text.id == turn.id and text.sticker_key is None
        if lost_ack:
            clock.now += q.DELIVERY_LEASE
            assert q.recover_deliveries(engine, now=clock()) == 1
        else:
            assert q.finish_delivery(engine, text, outcome="not_sent", now=clock())
            clock.now += timedelta(seconds=21)
    with Session(engine) as db:
        assert db.get_one(ChatReaction, turn.id).delivery_id is None
    assert not q.claim_delivery(engine, chat_id=CHAT, now=clock())


def test_expiration_and_new_input_suppress_queued_reaction_while_preserving_new_response(
    engine, clock
):
    turn = plan(engine, clock)
    accept_text(engine, clock)
    assert accept(engine, clock, "update")
    next_turn = q.claim_turn(engine, now=clock())
    assert next_turn and q.complete_turn(engine, next_turn, "Novo assunto", now=clock())
    next_reply = q.claim_delivery(engine, chat_id=CHAT, now=clock())
    assert next_reply and next_reply.id == next_turn.id and next_reply.sticker_key is None
    with Session(engine) as db:
        sticker_id = db.get_one(ChatReaction, turn.id).delivery_id
        assert sticker_id and db.get_one(Outbound, sticker_id).state == "failed"


@pytest.mark.parametrize("before_acceptance", [False, True])
def test_old_turn_never_sends_a_sticker_after_reconnection(engine, clock, before_acceptance):
    turn = plan(engine, clock)
    if not before_acceptance:
        accept_text(engine, clock)
    clock.now += reactions.LIFETIME
    if before_acceptance:
        accept_text(engine, clock)
    assert not q.claim_delivery(engine, chat_id=CHAT, now=clock())
    with Session(engine) as db:
        assert db.get_one(Outbound, turn.id).state == "accepted"


def test_bad_keys_stale_workers_and_opt_out_preserve_the_text(engine, clock):
    first = plan(engine, clock, reaction="../../any.webp")
    accept_text(engine, clock)
    with Session(engine) as db:
        assert db.get(ChatReaction, first.id) is None
    assert accept(engine, clock, "disable")
    turn = q.claim_turn(engine, now=clock())
    assert turn
    with Session(engine) as db, db.begin():
        db.add(StickerPreference(route=CHAT, enabled=False, source_id=turn.id))
    assert not q.can_react(engine, turn, now=clock())
    assert q.complete_turn(engine, turn, "Texto", reaction_key="valeu", now=clock())
    accept_text(engine, clock)
    assert not q.claim_delivery(engine, chat_id=CHAT, now=clock())


def test_cooldown_requires_elapsed_time_and_other_turns_and_is_per_binding(engine, clock):
    first = plan(engine, clock)
    for i in range(reactions.INTERVENING_TURNS + 1):
        assert accept(engine, clock, f"p{i}")
        turn = q.claim_turn(engine, now=clock())
        assert turn and not q.can_react(engine, turn, now=clock())
        assert q.complete_turn(engine, turn, "Palavras", reaction_key="combinado", now=clock())
    assert accept(engine, clock, "other", chat=OTHER)
    other = q.claim_turn(engine, now=clock())
    assert other and q.can_react(engine, other, now=clock())
    assert q.complete_turn(engine, other, "Beleza!", now=clock())
    clock.now += reactions.COOLDOWN
    assert accept(engine, clock, "next")
    allowed = q.claim_turn(engine, now=clock())
    assert allowed and allowed.id != first.id and q.can_react(engine, allowed, now=clock())


def test_long_pause_starts_a_fresh_conversation_without_four_intervening_turns(engine, clock):
    first = plan(engine, clock)
    clock.now += reactions.COOLDOWN
    assert accept(engine, clock, "cedo")
    still_same = q.claim_turn(engine, now=clock())
    assert still_same and not q.can_react(engine, still_same, now=clock())
    assert q.complete_turn(engine, still_same, "Falou!", now=clock())
    clock.now += reactions.CONVERSATION_BREAK - reactions.COOLDOWN
    assert accept(engine, clock, "outra-conversa")
    after_break = q.claim_turn(engine, now=clock())
    assert after_break and first.chat_id == after_break.chat_id
    assert q.can_react(engine, after_break, now=clock())


def test_preference_disabled_after_primary_ack_skips_upload_and_preserves_text(channel):  # noqa: F811
    channel.receive(None, msg(OWNER, "Valeu!", "m1"))
    turn = q.claim_turn(channel.engine)
    assert turn and q.complete_turn(channel.engine, turn, "Tamo junto!", reaction_key="valeu")
    assert channel.send_next()
    with Session(channel.engine) as db, db.begin():
        db.add(StickerPreference(route=turn.chat_id, enabled=False, source_id=turn.id))
    assert channel.send_next()
    channel.client.upload.assert_not_called()
    assert channel.client.send_message.call_count == 1


def test_gateway_abandons_missing_asset_silently_after_text(channel):  # noqa: F811
    channel.receive(None, msg(OWNER, "Valeu!", "m1"))
    turn = q.claim_turn(channel.engine)
    assert turn and q.complete_turn(channel.engine, turn, "Tamo junto!", reaction_key="valeu")
    assert channel.send_next()
    with patch("jarvis.adapters.whatsapp.gateway.sticker_bytes", side_effect=FileNotFoundError):
        assert channel.send_next()
    assert not channel.send_next()
    assert channel.client.send_message.call_count == 1
    assert channel.client.send_message.call_args.args[1] == "Tamo junto!"


def test_gateway_rechecks_new_message_between_upload_and_send(channel):  # noqa: F811
    channel.receive(None, msg(OWNER, "Valeu!", "m1"))
    turn = q.claim_turn(channel.engine)
    assert turn and q.complete_turn(channel.engine, turn, "Tamo junto!", reaction_key="valeu")
    assert channel.send_next()
    build = channel.client.build_sticker_message

    def delayed(*args, **kwargs):
        packet = build(*args, **kwargs)
        channel.receive(None, msg(OWNER, "Agora preciso de ajuda.", "m2"))
        return packet

    channel.client.build_sticker_message = delayed
    assert channel.send_next()
    assert channel.client.send_message.call_count == 1
    with Session(channel.engine) as db:
        sticker = db.get_one(ChatReaction, turn.id).delivery_id
        assert sticker and db.get_one(Outbound, sticker).state == "failed"
        assert db.get_one(Outbound, turn.id).state == "accepted"


def test_lost_sticker_receipt_does_not_retry_or_change_primary(channel):  # noqa: F811
    channel.receive(None, msg(OWNER, "Obrigado!", "m1"))
    turn = q.claim_turn(channel.engine, now=utcnow())
    assert turn and q.complete_turn(channel.engine, turn, "Até mais!", reaction_key="valeu")
    assert channel.send_next()
    channel.client.send_message.side_effect = TimeoutError()
    assert channel.send_next()
    assert not channel.send_next()
    with Session(channel.engine) as db:
        child = db.get_one(ChatReaction, turn.id).delivery_id
        assert child and db.get_one(Outbound, child).state == "uncertain"
        assert db.get_one(Outbound, turn.id).state == "accepted"
    assert channel.client.send_message.call_count == 2
