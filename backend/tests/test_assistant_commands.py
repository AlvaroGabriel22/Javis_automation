import json
from datetime import timedelta
from typing import Literal
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jarvis.adapters.ai.speech import SpeechError
from jarvis.adapters.persistence.models import Contact, DeviceContact, Owner, Schedule
from jarvis.entrypoints.worker import process_one
from jarvis.modules.assistant.intent import parse_command
from jarvis.modules.assistant.recipients import candidates
from jarvis.modules.messaging import queue
from jarvis.modules.messaging.models import Inbound, Outbound
from jarvis.modules.scheduling.service import stage_schedule

ROUTE = "+5592000000001|+5592000000002"
OTHER = "+5592000000003|+5592000000002"


class Conversation:
    def reply(self, messages):
        return "Conversa: " + messages[-1]["content"]


def input_message(
    engine,
    clock,
    *,
    msg_id,
    text,
    kind: Literal["text", "audio", "card", "notice"] = "text",
    media=None,
):
    return queue.accept_inbound(
        engine, chat_id=ROUTE, message_id=msg_id, text=text, kind=kind, media=media, now=clock()
    )


def process(engine, clock, **kwargs):
    assert input_message(engine, clock, **kwargs)
    assert process_one(engine, Conversation(), clock=clock)
    with Session(engine) as db:
        return db.scalars(
            select(Outbound.text)
            .join(Inbound, Inbound.id == Outbound.id)
            .where(Inbound.chat_id == ROUTE, Inbound.message_id == kwargs["msg_id"])
        ).one()


@pytest.fixture
def owned(engine):
    with Session(engine) as db, db.begin():
        db.add(Owner(id=1, name="Dono sintético"))
    return engine


def test_ambiguous_cancel_requires_an_explicit_schedule_and_does_not_cross_routes(owned, clock):
    ids = []
    with Session(owned) as db, db.begin():
        for ix, route in enumerate((ROUTE, ROUTE, OTHER)):
            c = Contact(owner_id=1, name="Rafa", phone=f"+559299999000{ix}", source_route=route)
            db.add(c)
            db.flush()
            row = stage_schedule(
                db, 1, c.id, clock() + timedelta(hours=ix + 1), f"recado {ix}", clock(), route
            )
            ids.append(row.id)
    listing = process(owned, clock, msg_id="list", text="listar agendamentos")
    assert "recado 0" in listing and "recado 1" in listing and "recado 2" not in listing
    ask = process(owned, clock, msg_id="amb", text="Cancela o aviso do Rafa")
    assert "mais de um" in ask
    first = str(ids[0])[:8]
    untouched = process(owned, clock, msg_id="cond", text="Cancele #" + first + " se chover")
    assert "Cancelado" not in untouched
    assert parse_command("Hoje recebi uma mensagem dizendo: cancele #" + first) is None
    response = process(owned, clock, msg_id="stop", text="Cancele #" + first)
    assert first in response and "Cancelado" in response
    assert not input_message(owned, clock, msg_id="stop", text="Cancele #" + first)
    invalid = process(owned, clock, msg_id="other", text="Cancele #" + str(ids[2])[:8])
    assert "Não encontrei" in invalid
    with Session(owned) as db:
        assert [db.get_one(Schedule, sid).status for sid in ids] == [
            "cancelled",
            "scheduled",
            "scheduled",
        ]


def test_first_use_phone_contact_card_device_alias_and_account_boundaries(owned, clock):
    card = json.dumps({"name": "Lúcia", "phones": ["+5592999990000"]})
    assert "Lúcia" in process(owned, clock, msg_id="card", kind="card", text=card)
    assert "Salvei o apelido" in process(owned, clock, msg_id="alias", text="Chame Lúcia de Lu")
    assert "Salvei o apelido" in process(
        owned, clock, msg_id="new", text="Chame +5592999990077 de Recepção"
    )
    with Session(owned) as db, db.begin():
        db.add(DeviceContact(route_key=OTHER, name="Somente aparelho B", phone="+5592999990088"))
        db.flush()
        assert [r.phone for r in candidates(db, ROUTE, "lu")] == ["+5592999990000"]
        assert [r.phone for r in candidates(db, ROUTE, "Recepção")] == ["+5592999990077"]
        assert not candidates(db, OTHER, "Lu")
        assert not candidates(db, ROUTE, "Somente aparelho B")
        assert candidates(db, OTHER, "Somente aparelho B")[0].phone == "+5592999990088"
        assert candidates(db, OTHER, "+5592999990000")[0].name == "+5592999990000"


def test_transcription_survives_restart_and_runs_exactly_as_text(owned, clock):
    assert input_message(
        owned, clock, msg_id="voice", kind="audio", text="[Áudio]", media=b"fixture"
    )
    turn = queue.claim_turn(owned, now=clock())
    assert turn and turn.media == b"fixture" and turn.received_at == clock()
    assert queue.store_transcription(owned, turn, "listar agendamentos", now=clock())
    owned.dispose()
    clock.now += queue.TURN_LEASE
    with patch(
        "jarvis.adapters.ai.speech.transcribe", side_effect=AssertionError("transcrição repetida")
    ):
        assert process_one(owned, Conversation(), clock=clock)
    with Session(owned) as db:
        row = db.get_one(Inbound, turn.id)
        assert row.media is None and row.text == "listar agendamentos"
        assert "Não há agendamentos" in db.get_one(Outbound, turn.id).text
    with patch("jarvis.adapters.ai.speech.transcribe", return_value="Olá do áudio") as whisper:
        reply = process(
            owned, clock, msg_id="voice2", kind="audio", text="[Áudio]", media=b"fixture2"
        )
        assert reply == "Conversa: Olá do áudio"
        whisper.assert_called_once_with(b"fixture2")


def test_notice_and_failure_are_not_commands(owned, clock):
    assert "cancele" in process(
        owned, clock, msg_id="notice", kind="notice", text="cancele #11111111"
    )
    with patch(
        "jarvis.adapters.ai.speech.transcribe", side_effect=SpeechError("Não consegui ler o áudio.")
    ):
        assert (
            process(owned, clock, msg_id="error", kind="audio", text="[Áudio]", media=b"fixture")
            == "Não consegui ler o áudio."
        )
    with Session(owned) as db:
        assert (
            db.scalar(select(func.count()).select_from(Inbound).where(Inbound.media.is_not(None)))
            == 0
        )


def test_action_rollback_and_stale_worker_cannot_mutate(owned, clock):
    assert input_message(owned, clock, msg_id="action", text="pedido de teste")
    turn = queue.claim_turn(owned, now=clock())
    assert turn

    def change(db):
        db.add(Contact(owner_id=1, name="Aná", phone="+5592999990999"))
        db.flush()
        raise RuntimeError("crash antes da confirmação")

    with pytest.raises(RuntimeError):
        queue.apply_command(owned, turn, change, now=clock())
    with Session(owned) as db:
        assert db.scalar(select(func.count()).select_from(Contact)) == 0
        assert not db.get(Outbound, turn.id)
        assert db.get_one(Inbound, turn.id).state == "processing"
    clock.now += queue.TURN_LEASE
    assert queue.apply_command(owned, turn, change, now=clock())  # handler antigo nunca roda


def test_content_and_quoted_commands_are_not_executed():
    cmd = parse_command("Amanhã às 22h17, manda pra Ana exatamente assim: ‘Já cheguei às 12:05.’")
    assert cmd and cmd["recipient"] == "Ana" and cmd["body"] == "Já cheguei às 12:05."
    assert cmd["when"] == "Amanhã às 22h17"
    assert parse_command("Recebi a seguinte frase: cancele o aviso do João") is None


@pytest.mark.parametrize("lead", ["Dá um toque na", "Manda um zap pra", "Envie um WhatsApp para"])
def test_colloquial_message_reaches_the_same_contact_and_preserves_body(owned, clock, lead):
    with Session(owned) as db, db.begin():
        db.add(Contact(owner_id=1, name="Gabi", phone="+5592999991234"))
    body = "Dá um toque na hora que chegar. Boa noite!"
    reply = process(
        owned, clock, msg_id="recado", text=f"{lead} Gabi daqui a 2 minutos dizendo: {body}"
    )
    with Session(owned) as db:
        schedule = db.scalars(select(Schedule)).one()
        assert db.get_one(Contact, schedule.contact_id).name == "Gabi"
        assert schedule.text == "Jarvis, a pedido de Dono sintético:\n\n" + body
        assert schedule.scheduled_at == clock() + timedelta(minutes=2)
    assert "Registrei o envio" in reply


@pytest.mark.parametrize(
    "words",
    [
        "Dá um toque na tela.",
        "Se eu disser ‘dá um toque na Gabi agora dizendo oi’, o que acontece?",
        "Não dê um toque na Gabi agora dizendo oi.",
    ],
)
def test_mentioning_a_nudge_is_not_an_authorization(words):
    assert parse_command(words) is None


@pytest.mark.parametrize(
    ("words", "offset"),
    [
        ("daqui a meia hora", 30),
        ("em duas horas", 120),
        ("daqui quinze segundos", 0.25),
        ("daqui a vinte e cinco minutos", 25),
        ("amanhã às nove horas da manhã", 1500),
        ("às 23h17", 917),
        ("dia 12 às três e meia da tarde", 1890),
        ("domingo às 9:00", 1500),
        ("12/01/2031 às 09h", 1500),
    ],
)
def test_spoken_delivery_times(clock, words, offset):
    from jarvis.modules.assistant.timing import delivery_time

    assert delivery_time(words, clock()).instant == clock() + timedelta(minutes=offset)


def test_multiple_topics_literal_and_reported_preserve_meaning(owned, clock):
    from jarvis.modules.scheduling import dispatch

    with Session(owned) as db, db.begin():
        db.add(DeviceContact(route_key=ROUTE, name="Bianca", phone="+5592999990010"))
    text = "R$ 17,50. Encontre a Bia às 12:05. Dê boa noite."
    ack = process(
        owned,
        clock,
        msg_id="lit",
        text=f"Amanhã às 9h, mande para Bianca exatamente assim: “{text}”",
    )
    assert "12/01/2031 às 09:00" in ack and text in ack
    body = "minha tia disse que eu preciso buscar o pedido 402 às 15h"
    ack = process(owned, clock, msg_id="report", text=f"Em um minuto, fale para Bianca que {body}")
    assert body in ack
    clock.now += timedelta(minutes=3)
    job = dispatch.claim(owned, ROUTE, clock())
    assert (
        job
        and body in job.text
        and "Dono sintético" in job.text
        and job.text_at(clock()) == f"Jarvis, a pedido de Dono sintético:\n\n{body}"
    )
    clock.now += timedelta(minutes=1497)
    literal = dispatch.claim(owned, ROUTE, clock())
    assert literal and literal.text == "Jarvis, a pedido de Dono sintético:\n\n" + text


def test_incomplete_ambiguous_stale_and_unrelated_orders_do_not_schedule(owned, clock):
    with Session(owned) as db, db.begin():
        for ix, name in enumerate(("Ana Silva", "Ana Reis")):
            db.add(DeviceContact(route_key=ROUTE, name=name, phone=f"+559299999001{ix}"))
    cases = (
        ("avise Ana que eu cheguei às 10h", "mais de um"),
        ("avise Ana Silva amanhã às nove", "recado"),
        ("amanhã às nove avise Ana que eu cheguei", "mais de um"),
        ("daqui a um minuto avise Beto que eu cheguei", "Não encontrei"),
        ("avise Ana hoje às sete da manhã que eu cheguei", "já passou"),
        ("daqui a um minuto avise Ana amanhã às nove que chegou", "Não entendi"),
        ("avise Ana na próxima vez que eu chegar", "Não encontrei"),
        ("Recebi uma mensagem: daqui a um minuto avise a Ana que cheguei", "Conversa"),
    )
    for i, (message, expected) in enumerate(cases):
        assert expected in process(owned, clock, msg_id=f"invalid{i}", text=message)
    assert input_message(
        owned, clock, msg_id="expired", text="daqui um minuto avise Ana que eu cheguei"
    )
    clock.now += timedelta(minutes=2)
    assert process_one(owned, Conversation(), clock=clock)
    with Session(owned) as db:
        row = db.scalar(select(Inbound).where(Inbound.message_id == "expired"))
        assert row and "já passou" in db.get_one(Outbound, row.id).text
        assert db.scalar(select(func.count()).select_from(Schedule)) == 0


def test_unclear_audio_with_time_asks_again_without_creating(owned, clock):
    distorted = (
        "Aqui há um minuto, você é a Misa Gabi, que eu já estou indo do dormir. De boa noite."
    )
    with patch("jarvis.adapters.ai.speech.transcribe", return_value=distorted):
        result = process(
            owned, clock, msg_id="unclear", kind="audio", text="[Áudio]", media=b"OggS"
        )
    assert "Nada foi agendado" in result
    with Session(owned) as db:
        assert db.scalar(select(func.count()).select_from(Schedule)) == 0


@pytest.mark.parametrize(
    "verb", ["envie para", "avise", "fale para", "informe para a", "diga para a"]
)
def test_directed_speech_waits_for_timing_then_sends_exact_payload_once(owned, clock, verb):
    from jarvis.modules.scheduling import dispatch

    with Session(owned) as db, db.begin():
        owner = db.get_one(Owner, 1)
        owner.preferred_name, owner.address_as, owner.gender = "Ana", "Sra.", "Feminino"
        db.add(DeviceContact(route_key=ROUTE, name="Bia", phone="+5511000000010"))
    body = "eu não recebi R$ 19,70. Tô saindo às 10h. Tudo certo?"
    with patch(
        "jarvis.adapters.ai.speech.transcribe", return_value=f"Jarvis, {verb} Bia que {body}"
    ):
        question = process(
            owned, clock, msg_id="voice", kind="audio", text="[Áudio]", media=b"audio"
        )
    assert "Deseja enviar a mensagem agora ou agendar para mais tarde, senhora?" in question
    assert body in question and dispatch.claim(owned, ROUTE, clock()) is None
    owned.dispose()
    assert "Para qual dia e horário" in process(
        owned, clock, msg_id="later", text="Agendar para mais tarde"
    )
    assert "aguardando" in process(owned, clock, msg_id="unclear", text="sim")
    with Session(owned) as db:
        assert db.scalar(select(func.count()).select_from(Schedule)) == 0
    with patch("jarvis.adapters.ai.speech.transcribe", return_value="Daqui a dois minutos"):
        ack = process(owned, clock, msg_id="when", kind="audio", text="[Áudio]", media=b"resposta")
    assert "Registrei o envio #" in ack
    assert not input_message(owned, clock, msg_id="when", text="Daqui a dois minutos")
    clock.now += timedelta(seconds=119)
    assert dispatch.claim(owned, ROUTE, clock()) is None
    clock.now += timedelta(seconds=1)
    job = dispatch.claim(owned, ROUTE, clock())
    assert job and job.phone == "+5511000000010"
    assert job.text_at(clock()) == f"Jarvis, a pedido de Ana:\n\n{body}"


def test_transcription_pauses_asr_and_missing_body(owned, clock):
    from jarvis.modules.scheduling import dispatch

    with Session(owned) as db, db.begin():
        db.add(DeviceContact(route_key=ROUTE, name="Mae", phone="+5511000000040"))
    body = "Oi mãe, já tô saindo. Liga pra mim às nove, tá?"
    with patch(
        "jarvis.adapters.ai.speech.transcribe",
        return_value=f"Javis, daqui a dois minutos e vi para mãe. {body}",
    ):
        ack = process(owned, clock, msg_id="asr", kind="audio", text="[Áudio]", media=b"audio")
    assert "Registrei o envio #" in ack
    assert "Qual recado" in process(
        owned, clock, msg_id="header", text="Envie para mim daqui a 2 minutos"
    )
    assert "Registrei o envio #" in process(
        owned, clock, msg_id="dictated", text="Eu vou comprar café."
    )
    clock.now += timedelta(minutes=2)
    jobs = [dispatch.claim(owned, ROUTE, clock()) for _ in range(2)]
    assert {job.phone: job.text_at(clock()) for job in jobs if job} == {
        "+5511000000040": f"Jarvis, a pedido de Dono sintético:\n\n{body}",
        ROUTE.split("|")[1]: "Jarvis, a pedido de Dono sintético:\n\nEu vou comprar café.",
    }


def test_draft_cancel_and_foreign_route_reply_do_not_authorize_send(owned, clock):
    assert "agendar para mais tarde" in process(
        owned, clock, msg_id="draft", text="Diga para mim que já saí"
    )
    assert queue.accept_inbound(owned, chat_id=OTHER, message_id="wrong", text="agora", now=clock())
    assert process_one(owned, Conversation(), clock=clock)
    assert "Descartei" in process(owned, clock, msg_id="cancel", text="cancelar")
    process(owned, clock, msg_id="now", text="agora")
    with Session(owned) as db:
        assert db.scalar(select(func.count()).select_from(Schedule)) == 0


def test_two_numbers_differing_by_ninth_digit_require_choice_and_keep_original_deadline(
    owned, clock
):
    from jarvis.modules.scheduling import dispatch

    with Session(owned) as db, db.begin():
        db.add(Contact(owner_id=1, name="Mae", phone="+5592998765432"))
        db.add(DeviceContact(route_key=ROUTE, name="Mae", phone="+559298765432"))
    content = "Oi mãe! Tô saindo. O portão é o 3."
    with patch(
        "jarvis.adapters.ai.speech.transcribe",
        return_value=f"Javis, daqui a dois minutos e vi para mãe. {content}",
    ):
        prompt = process(owned, clock, msg_id="heard", kind="audio", text="[Áudio]", media=b"ogg")
    assert "Qual deles?" in prompt and "painel" in prompt and "agenda do WhatsApp" in prompt
    assert dispatch.claim(owned, ROUTE, clock()) is None
    with Session(owned) as db:
        assert db.scalar(select(func.count()).select_from(Schedule)) == 0
    owned.dispose()
    clock.now += timedelta(seconds=30)
    assert "Registrei o envio #" in process(
        owned, clock, msg_id="choose", text="O da agenda do WhatsApp."
    )
    clock.now += timedelta(seconds=89)
    assert dispatch.claim(owned, ROUTE, clock()) is None
    clock.now += timedelta(seconds=1)
    job = dispatch.claim(owned, ROUTE, clock())
    assert job and job.phone == "+559298765432" and job.text_at(clock()).endswith("\n\n" + content)


def test_missing_punctuation_keeps_content_and_unresolved_command_cannot_confirm(owned, clock):
    assert "Registrei o envio #" in process(
        owned,
        clock,
        msg_id="nosep",
        text="Envie para mim daqui a 2 minutos eu disse que a Ana já saiu, tá?",
    )
    with Session(owned) as db:
        assert (
            db.scalars(select(Schedule.text)).one().endswith("\n\neu disse que a Ana já saiu, tá?")
        )
    assert "Nenhum envio foi registrado" in process(
        owned, clock, msg_id="indirect", text="E se fosse possível enviar isso para Ana?"
    )
