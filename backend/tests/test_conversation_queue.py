from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from jarvis.adapters.ai.ollama import ModelUnavailable
from jarvis.entrypoints.worker import process_one
from jarvis.modules.messaging import queue as q
from jarvis.modules.messaging.models import DeliveryAttempt, Inbound, Outbound

CHAT = "5592000000001|5592000000002"
OTHER = "5592000000011|5592000000002"


def accept(engine, clock, message_id="m1", chat=CHAT, text="Olá"):
    return q.accept_inbound(engine, chat_id=chat, message_id=message_id, text=text, now=clock())


def race(fn):
    both = Barrier(2)

    def start(_):
        both.wait(timeout=5)
        return fn()

    with ThreadPoolExecutor(2) as workers:
        return list(workers.map(start, range(2)))


class Assistant:
    def __init__(self):
        self.calls = []

    def reply(self, messages):
        self.calls.append(messages)
        return "Resposta a: " + messages[-1]["content"]


def test_replay_after_restart_during_ingress_and_reply_is_unique(engine, clock):
    assert sorted(race(lambda: accept(engine, clock))) == [False, True]
    assert accept(engine, clock, message_id="m2", text="Outro assunto")
    in_flight = race(lambda: q.claim_turn(engine, now=clock()))
    turns = [t for t in in_flight if t is not None]
    assert len(turns) == 1
    first = turns[0]
    assert first.text == "Olá"
    assert q.complete_turn(engine, first, "Bom dia!", now=clock())
    engine.dispose()
    assert not accept(engine, clock, text="Outra versão da duplicata")
    assert q.claim_delivery(engine, chat_id=OTHER, now=clock()) is None
    sent = q.claim_delivery(engine, chat_id=CHAT, now=clock())
    assert sent and sent.text == "Bom dia!"
    assert q.finish_delivery(engine, sent, outcome="accepted", external_id="canal-1", now=clock())
    assert not q.finish_delivery(engine, sent, outcome="not_sent", now=clock())
    assistant = Assistant()
    assert process_one(engine, assistant, clock=clock)
    assert assistant.calls == [
        [
            {"role": "user", "content": "Olá"},
            {"role": "assistant", "content": "Bom dia!"},
            {"role": "user", "content": "Outro assunto"},
        ]
    ]
    assert not process_one(engine, assistant, clock=clock)
    assert q.queue_status(engine) == {
        "inbox": {"completed": 2},
        "outbox": {"accepted": 1, "ready": 1},
    }
    with Session(engine) as db:
        assert db.get_one(Outbound, first.id).external_id == "canal-1"


def test_equal_ids_in_different_accounts_are_isolated_and_can_run_together(engine, clock):
    assert accept(engine, clock)
    assert accept(engine, clock, message_id="m2", text="Segunda da primeira conta")
    assert accept(engine, clock, chat=OTHER, text="Segredo de outra conta")
    active = race(lambda: q.claim_turn(engine, now=clock()))
    assert {t.chat_id for t in active if t} == {CHAT, OTHER}
    for t in active:
        assert t and q.history_for_turn(engine, t) == [{"role": "user", "content": t.text}]
        assert q.complete_turn(engine, t, t.text, now=clock())
    d = q.claim_delivery(engine, chat_id=OTHER, now=clock())
    assert d and d.text == "Segredo de outra conta"
    assert q.claim_delivery(engine, chat_id="não vinculado", now=clock()) is None


def test_stale_worker_cannot_renew_or_finish_after_another_worker_reclaims(engine, clock):
    assert accept(engine, clock)
    old = q.claim_turn(engine, now=clock())
    assert old
    clock.now += q.TURN_LEASE / 2
    assert q.renew_turn(engine, old, now=clock())
    clock.now += q.TURN_LEASE / 2
    assert q.claim_turn(engine, now=clock()) is None
    clock.now += q.TURN_LEASE
    replacement = q.claim_turn(engine, now=clock())
    assert replacement and replacement.token != old.token and replacement.id == old.id
    assert not q.renew_turn(engine, old, now=clock())
    assert not q.complete_turn(engine, old, "Atrasada", now=clock())
    assert not q.fail_turn(engine, old, now=clock())
    assert q.complete_turn(engine, replacement, "Atual", now=clock())
    assert q.queue_status(engine)["outbox"] == {"ready": 1}


def test_worker_crashing_on_last_attempt_yields_one_honest_fallback(engine, clock):
    assert accept(engine, clock)
    for i in range(q.MAX_ATTEMPTS):
        t = q.claim_turn(engine, now=clock())
        assert t and t.attempt == i + 1
        clock.now += q.TURN_LEASE
        engine.dispose()
    assert q.claim_turn(engine, now=clock()) is None
    assert q.claim_turn(engine, now=clock()) is None
    d = q.claim_delivery(engine, chat_id=CHAT, now=clock())
    assert d and d.text == q.MODEL_UNAVAILABLE
    assert q.queue_status(engine)["inbox"] == {"failed": 1}


@pytest.mark.parametrize("retryable,count", [(True, 3), (False, 1)])
def test_model_failures_distinguish_transient_from_configuration(engine, clock, retryable, count):
    class Down:
        def reply(self, messages):
            raise ModelUnavailable(retryable=retryable)

    assert accept(engine, clock)
    for i in range(count):
        assert process_one(engine, Down(), clock=clock)
        assert not process_one(engine, Down(), clock=clock)
        if i < count - 1:
            assert q.queue_status(engine)["outbox"] == {}
            clock.now += q.BACKOFF[i]
    assert q.queue_status(engine) == {"inbox": {"failed": 1}, "outbox": {"ready": 1}}
    with Session(engine) as db:
        row = db.scalars(select(Inbound)).one()
        assert row.attempts == count
        assert row.error_code == ("model_unavailable" if retryable else "model_configuration")


def test_concurrent_senders_and_crash_after_possible_transmission(engine, clock):
    assert accept(engine, clock)
    assert process_one(engine, Assistant(), clock=clock)
    ds = [d for d in race(lambda: q.claim_delivery(engine, chat_id=CHAT, now=clock())) if d]
    assert len(ds) == 1
    d = ds[0]
    engine.dispose()  # Possível aceite externo, sem confirmação gravada pelo gateway.
    assert q.recover_deliveries(engine, now=clock()) == 0
    clock.now += q.DELIVERY_LEASE
    assert sorted(race(lambda: q.recover_deliveries(engine, now=clock()))) == [0, 1]
    assert q.claim_delivery(engine, chat_id=CHAT, now=clock()) is None
    assert not q.finish_delivery(engine, d, outcome="not_sent", now=clock())
    assert q.queue_status(engine)["outbox"] == {"uncertain": 1}
    with Session(engine) as db:
        attempts = db.scalars(select(DeliveryAttempt)).all()
        assert len(attempts) == 1 and attempts[0].outcome == "uncertain"


def test_definitely_not_sent_retries_in_order_and_stops(engine, clock):
    assert accept(engine, clock)
    assert accept(engine, clock, message_id="m2", text="Outra")
    for _ in range(2):
        assert process_one(engine, Assistant(), clock=clock)
    for i in range(q.MAX_ATTEMPTS):
        d = q.claim_delivery(engine, chat_id=CHAT, now=clock())
        assert d and d.attempt == i + 1 and d.text == "Resposta a: Olá"
        assert q.finish_delivery(engine, d, outcome="not_sent", now=clock())
        if i < q.MAX_ATTEMPTS - 1:
            assert q.claim_delivery(engine, chat_id=CHAT, now=clock()) is None
            clock.now += q.BACKOFF[i]
    next_reply = q.claim_delivery(engine, chat_id=CHAT, now=clock())
    assert next_reply and next_reply.text.endswith("Outra")
    # Sem o ID de aceite não se pode anunciar sucesso.
    with pytest.raises(ValueError):
        q.finish_delivery(engine, next_reply, outcome="accepted", now=clock())
    assert q.finish_delivery(engine, next_reply, outcome="uncertain", now=clock())
    assert q.queue_status(engine)["outbox"] == {"failed": 1, "uncertain": 1}
    with Session(engine) as db:
        assert [
            a.outcome
            for a in db.scalars(
                select(DeliveryAttempt).order_by(
                    DeliveryAttempt.outbound_id, DeliveryAttempt.attempt
                )
            )
        ] == ["not_sent"] * 3 + ["uncertain"]
