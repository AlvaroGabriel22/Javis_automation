from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_commands import ROUTE, Conversation, input_message, process
from test_calendar import provider

from jarvis.entrypoints.worker import process_one
from jarvis.modules.calendar.models import Entry
from jarvis.modules.calendar.service import ZONE
from jarvis.modules.messaging import queue

START = "Dia 28/11 marque o evento"


@pytest.mark.parametrize(
    ("follow", "day", "hour", "all_day"),
    [
        ("Dia 28/11 às 19:30 Apresentação do projeto", 28, 19, False),
        ("Dia 29/11 às 19:30 Apresentação do projeto", 29, 19, False),
        ("Às 19:30, Apresentação do projeto", 28, 19, False),
        ("Título: Apresentação do projeto", 28, 0, True),
    ],
)
def test_incomplete_dated_event_uses_next_qualified_detail(
    engine, signed, clock, follow, day, hour, all_day
):
    assert "Qual é o título" in process(engine, clock, msg_id="pedido", text=START)
    engine.dispose()
    assert "registrado" in process(engine, clock, msg_id="dados", text=follow)
    assert not input_message(engine, clock, msg_id="dados", text=follow)
    with Session(engine) as db:
        item = db.scalars(select(Entry)).one()
        local = item.starts_at.astimezone(ZONE)
        assert (item.title, local.month, local.day, local.hour, item.all_day) == (
            "Apresentação do projeto",
            11,
            day,
            hour,
            all_day,
        )
        assert local.minute == (0 if all_day else 30)


def test_followup_keeps_requested_calendar_and_uses_audio_transcription(engine, signed, clock):
    provider(engine)
    process(engine, clock, msg_id="inicio", text=START + " no Google")
    with patch("jarvis.adapters.ai.speech.transcribe", return_value="Às 10h, Encontro"):
        process(engine, clock, msg_id="voz", kind="audio", media=b"audio", text="[Áudio]")
    with Session(engine) as db:
        item = db.scalars(select(Entry)).one()
        assert item.source_id is not None
        assert item.title == "Encontro"


@pytest.mark.parametrize(
    "interruption", ["expire", "other_chat", "oi", "esquece", "absent", "sem autorização"]
)
def test_calendar_details_need_a_recent_uninterrupted_request(engine, signed, clock, interruption):
    if interruption not in {"absent", "sem autorização"}:
        assert "Qual é o título" in process(engine, clock, msg_id="inicio", text=START)
    if interruption == "expire":
        clock.now += timedelta(minutes=16)
    elif interruption == "other_chat":
        queue.accept_inbound(
            engine, chat_id=ROUTE + "|outro", message_id="b", text="às 19h, Encontro", now=clock()
        )
        assert process_one(engine, Conversation(), clock=clock)
        with Session(engine) as db:
            assert db.scalars(select(Entry)).all() == []
        return
    elif interruption not in {"absent", "sem autorização"}:
        process(engine, clock, msg_id="interrupcao", text=interruption)
    details = (
        "às 19h, Encontro"
        if interruption != "sem autorização"
        else "Dia 28/11 às 19:30 Apresentação do projeto"
    )
    process(engine, clock, msg_id="solto", text=details)
    with Session(engine) as db:
        assert db.scalars(select(Entry)).all() == []


@pytest.mark.parametrize(
    "details",
    [
        "Até logo",
        "às 19h não marque",
        "às 19h cancele a visita",
        "às 19h talvez eu possa",
        "Às 19h estou livre?",
    ],
)
def test_followup_does_not_turn_a_new_subject_or_retraction_into_an_event(
    engine, signed, clock, details
):
    process(engine, clock, msg_id="pedido", text=START)
    process(engine, clock, msg_id="outra", text=details)
    with Session(engine) as db:
        assert not db.scalar(select(Entry.id))
