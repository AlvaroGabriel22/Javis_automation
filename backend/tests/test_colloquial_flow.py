"""Aceitação do tom no WhatsApp privado, com o relógio e o transporte simulados."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from neonize.utils.jid import build_jid
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_whatsapp_gateway import BOT, OTHER, OWNER, msg

from jarvis.adapters.ai.provider import UnavailableChat
from jarvis.adapters.whatsapp.gateway import Gateway
from jarvis.adapters.whatsapp.profile import Profile
from jarvis.entrypoints.worker import process_one
from jarvis.modules.personal.models import LedgerEntry


@pytest.fixture
def channel(engine, signed, tmp_path, monkeypatch, clock):
    monkeypatch.setattr("jarvis.adapters.whatsapp.gateway.utcnow", clock)
    monkeypatch.setattr("jarvis.modules.messaging.queue.utcnow", clock)
    profile = Profile(tmp_path)
    profile.connected(BOT)
    assert profile.verify(OWNER, profile.challenge(OWNER)["phrase"])
    sdk = Mock()
    sdk.send_message.return_value = SimpleNamespace(ID="aceite-sintetico")
    return Gateway(engine, profile, sdk)


def reply(channel, clock, mid, text):
    channel.receive(None, msg(OWNER, text, mid))
    assert process_one(channel.engine, UnavailableChat(), clock=clock)
    assert channel.send_next()
    destination, answer = channel.client.send_message.call_args.args
    assert destination == build_jid(OWNER[1:]) and isinstance(answer, str)
    return answer


def test_familiar_greetings_vary_and_respect_a_saved_change_of_tone(channel, clock, signed):
    first = reply(channel, clock, "oi-1", "Oi!")
    channel.engine.dispose()
    second = reply(channel, clock, "oi-2", "Oi!")
    assert second != first
    casual_morning = reply(channel, clock, "manha-1", "Bom dia!")

    url = "/api/v1/profile"
    profile = signed.get(url).json()
    assert profile["tone"] == "proximo"
    assert signed.put(url, json={**profile, "tone": "neutro"}).status_code == 200
    channel.engine.dispose()
    morning = reply(channel, clock, "manha-2", "Bom dia!")
    assert morning != casual_morning and "bom dia" in morning.casefold()
    assert "fofuxo" not in morning.casefold()


def test_purchase_warning_then_reported_expense_changes_the_ledger_once(channel, clock):
    reply(channel, clock, "limite", "Meu orçamento mensal é R$ 100")
    plan = reply(channel, clock, "compra", "Quero gastar R$ 180 num tênis")
    assert "tá ficando doido?" in plan.casefold() and "R$ 80,00" in plan
    expenses = select(LedgerEntry.amount).where(LedgerEntry.kind == "expense")
    with Session(channel.engine) as db:
        assert db.scalars(expenses).all() == []

    spent = "Já gastei R$ 180 num tênis"
    receipt = reply(channel, clock, "feito", spent)
    assert "te controla" in receipt.casefold() and "R$ 180,00" in receipt
    channel.receive(None, msg(OWNER, spent, "feito"))
    assert not process_one(channel.engine, UnavailableChat(), clock=clock)
    assert not channel.send_next()
    with Session(channel.engine) as db:
        assert db.scalars(expenses).all() == [18_000]


def test_greeting_in_a_relay_preserves_the_dictated_words(channel, clock, signed):
    contact = signed.post(
        "/api/v1/contacts",
        headers={"Idempotency-Key": "contato-gabi"},
        json={"name": "Gabi", "phone": OTHER, "aliases": []},
    )
    assert contact.status_code == 201
    body = "bom dia, fofuxo!"
    preview = reply(channel, clock, "aviso", f"Avise a Gabi que {body}")
    assert body in preview
    assert not channel.send_scheduled(clock)
    channel.receive(None, msg(OTHER, "Agora", "resposta-de-gabi"))
    assert not process_one(channel.engine, UnavailableChat(), clock=clock)
    assert not channel.send_scheduled(clock)

    reply(channel, clock, "horario", "Agora")
    channel.client.is_on_whatsapp.return_value = [
        SimpleNamespace(IsIn=True, Query=OTHER, JID=build_jid(OTHER[1:]))
    ]
    assert channel.send_scheduled(clock)
    destination, payload = channel.client.send_message.call_args.args
    owner_name = signed.get("/api/v1/profile").json()["preferred_name"]
    assert destination == build_jid(OTHER[1:])
    assert payload == f"Jarvis, a pedido de {owner_name}:\n\n{body}"
    assert not channel.send_scheduled(clock)
