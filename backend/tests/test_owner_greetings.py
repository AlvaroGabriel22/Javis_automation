from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_commands import ROUTE, process
from test_natural_chat_flow import ModelTransport, channel, exchange  # noqa: F401

from jarvis.modules.assistant.greetings import standalone_greeting
from jarvis.modules.calendar.notices import tick
from jarvis.modules.calendar.service import ZONE
from jarvis.modules.identity.preferences import CASUAL_VOCATIVES
from jarvis.modules.messaging.models import Inbound, Outbound


@pytest.mark.parametrize(
    ("words", "label"),
    [
        ("Oi!", "Olá"),
        ("OLÁ, Jarvis!", "Olá"),
        ("Jarvis?", None),
        ("Jarvis, bom dia!", "Bom dia"),
        ("Boa noite, Jarvis.", "Boa noite"),
        ("Oi, Jarvis! Me lembra de beber água às 15h.", None),
        ("Bom dia, estou preocupado com a conta.", None),
        ("Manda uma figurinha de boa noite", None),
        ("Repita literalmente: oi", None),
        ("Avise a Lia que eu disse boa noite", None),
    ],
)
def test_greeting_matches_only_a_salutation(words, label):
    assert standalone_greeting(words) == label


def test_audio_and_text_share_the_greeting_sequence_and_keep_the_chosen_vocative(
    engine, clock, signed
):
    with patch("jarvis.adapters.ai.speech.transcribe", return_value="Oi, Jarvis!"):
        assert (
            process(engine, clock, msg_id="voz", kind="audio", media=b"audio", text="[Áudio]")
            == "Opa, meu rei!"
        )
    assert (
        process(engine, clock, msg_id="ola", text="Olá!") == "Opa, meu rei, o que o senhor manda?"
    )
    profile = {"preferred_name": "Bia", "gender": "Feminino", "address_as": "nome"}
    assert signed.put("/api/v1/profile", json=profile).is_success
    assert process(engine, clock, msg_id="nome", text="Bom dia!") == "Bom dia, Bia."


def test_interleaved_morning_and_evening_advance_the_vocative(engine, clock, signed):
    morning = process(engine, clock, msg_id="amanheceu", text="Bom dia")
    night = process(engine, clock, msg_id="anoiteceu", text="Boa noite")
    following = process(engine, clock, msg_id="amanheceu-de-novo", text="Bom dia")
    assert morning.startswith("Bom dia, fofuxo!")
    assert night.startswith("Boa noite, gatinho!")
    assert following.startswith("Bom dia, patrão!")


def test_greeting_after_distress_stays_quiet(engine, clock, signed):
    at = clock().astimezone(ZONE).replace(hour=21)
    clock.now = at - timedelta(minutes=15)
    process(engine, clock, msg_id="luto", text="Meu pai faleceu ontem.")
    clock.now = at
    tick(engine, ROUTE, at)
    with Session(engine) as db:
        scheduled = db.scalar(select(Outbound.text).join(Inbound).where(Inbound.kind == "notice"))
        assert scheduled == "Boa noite, senhor."
    assert process(engine, clock, msg_id="noite", text="Boa noite.") == "Boa noite, senhor."


def test_model_can_attach_a_morning_sticker_to_the_selected_private_greeting(
    channel,  # noqa: F811
    monkeypatch,
):
    ai = ModelTransport(monkeypatch)
    ai.text, ai.sticker = "Bom dia!", "bom-dia"
    answer = exchange(channel, ai, "Bom dia!", "dia")
    assert answer == "Bom dia, fofuxo!"
    assert len(ai.calls) == 1 and answer in ai.calls[0]["messages"][0]["content"]
    assert channel.send_next()
    sticker = channel.client.send_message.call_args.args[1]
    assert sticker.HasField("stickerMessage") and not channel.send_next()


def test_enabled_routines_rotate_by_local_date_with_one_delivery_per_time(engine, signed):
    start = datetime(2031, 1, 29, 7, tzinfo=ZONE)
    for offset in range(len(CASUAL_VOCATIVES)):
        day = start + timedelta(days=offset)
        for hour in (7, 21):
            at = day.replace(hour=hour)
            tick(engine, ROUTE, at)
            tick(engine, ROUTE, at + timedelta(seconds=8))
    with Session(engine) as db:
        replies = list(db.scalars(select(Outbound.text).join(Inbound).order_by(Inbound.id)))
    for i, prefix in enumerate(("Bom dia", "Boa noite")):
        period = replies[i::2]
        assert len(period) == len(set(period)) == len(CASUAL_VOCATIVES)
        assert all(
            any(line.startswith(f"{prefix}, {v}!") for line in period) for v in CASUAL_VOCATIVES
        )

    profile = signed.get("/api/v1/profile").json()
    assert signed.put("/api/v1/profile", json={**profile, "tone": "neutro"}).is_success
    assert signed.put("/api/v1/calendar/routines", json={"morning": False}).is_success
    later = start + timedelta(days=len(CASUAL_VOCATIVES))
    tick(engine, ROUTE, later)
    tick(engine, ROUTE, later.replace(hour=21))
    with Session(engine) as db:
        assert db.scalars(
            select(Outbound.text).join(Inbound).where(Inbound.received_at >= later)
        ).all() == ["Boa noite, senhor."]
