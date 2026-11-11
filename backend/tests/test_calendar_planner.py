from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from jarvis.modules.assistant.planner import (
    calendar_candidate,
    plan_calendar,
    retryable_parse_reply,
)
from jarvis.modules.calendar.scope import request_scope

# Depois da meia-noite em UTC, ainda é domingo em Manaus.
RECEIVED = datetime(2026, 11, 9, 2, tzinfo=UTC)


def test_comments_about_a_finished_meeting_can_stay_in_conversation():
    assert not calendar_candidate("Hoje a reunião foi boa kkk")
    assert not calendar_candidate("O evento de ontem deu certo!")
    assert calendar_candidate("A reunião de ontem deu certo. Marque outra para amanhã às 11h.")
    assert calendar_candidate("O evento de ontem foi bom?")


def extraction(intent="create", title=None, temporal=()):
    return {"intent": intent, "title": title, "temporal": list(temporal)}


def test_semantic_extracts_discontinuous_audio_and_preserves_provider():
    spoken = "Jarvis, encaixe amanhã o almoço com Bia às 13h na agenda do iCloud"
    generate = Mock(return_value=extraction(title="almoço com Bia", temporal=["amanhã", "às 13h"]))
    result = plan_calendar(spoken, RECEIVED, generate)
    assert result.command == "marque almoço com Bia amanhã às 13h no iCloud"
    messages, schema = generate.call_args.args
    assert "2026-11-08T22:00-04:00" in messages[1]["content"]
    assert "Google" not in messages[1]["content"]
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    ("raw", "day", "title", "providers"),
    [
        (
            "No dia 25 de novembro haverá a confraternização da equipe. Marque na agenda",
            "25 de novembro",
            "confraternização da equipe",
            set(),
        ),
        (
            "Em 25/11 às 11h30 teremos o almoço de equipe. Coloque na agenda do iCloud",
            "25/11 às 11h30",
            "almoço de equipe",
            {"icloud"},
        ),
        (
            "Na quarta-feira, 11/11/2026 terei avaliação de rotina. Marque no Google",
            "quarta-feira, 11/11/2026",
            "avaliação de rotina",
            {"google"},
        ),
    ],
)
def test_declaration_with_explicit_final_order_survives_model_outage(raw, day, title, providers):
    generate = Mock(side_effect=AssertionError("o comando literal dispensa inferência"))
    decision = plan_calendar(raw, RECEIVED, generate)
    assert decision.command and title in decision.command and day in decision.command
    assert request_scope(decision.command).providers == providers
    assert not decision.is_query


def test_declaration_with_conflicting_dates_asks_before_writing():
    raw = "Na quarta-feira, 12/11/2026 haverá a avaliação de rotina. Marque no Google"
    decision = plan_calendar(raw, RECEIVED, None)
    assert not decision.command and decision.clarification


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("Encaixe jantar amanhã", extraction(title="cirurgia", temporal=["amanhã"])),
        ("Encaixe jantar amanhã", extraction(title="jantar", temporal=["sexta-feira"])),
        ("Encaixe jantar amanhã às 19h30", extraction(title="jantar", temporal=["amanhã"])),
        (
            "Encaixe jantar amanhã e almoço na sexta",
            extraction(title="jantar", temporal=["amanhã"]),
        ),
        (
            "Encaixe reunião do projeto Alfa amanhã",
            extraction(title="reunião", temporal=["amanhã"]),
        ),
        (
            "Encaixe jantar no Google ou no iCloud amanhã",
            extraction(title="jantar", temporal=["amanhã"]),
        ),
        (
            "Encaixe jantar no Google amanhã ou na terça",
            extraction(title="jantar", temporal=["amanhã"]),
        ),
        (
            "Estou pensando em encaixar jantar amanhã na agenda",
            extraction(title="jantar", temporal=["amanhã"]),
        ),
        (
            "Você consegue mesmo reservar reunião amanhã?",
            extraction(title="reunião", temporal=["amanhã"]),
        ),
        ("Qual é a agenda de amanhã?", extraction(title="agenda", temporal=["amanhã"])),
        ("Encaixe jantar amanhã", "Marquei seu jantar!"),
        (
            "Encaixe jantar amanhã",
            {**extraction(title="jantar", temporal=["amanhã"]), "provider": "google"},
        ),
    ],
)
def test_untrusted_plan_cannot_invent_arguments_or_drop_constraints(text, answer):
    decision = plan_calendar(text, RECEIVED, Mock(return_value=answer))
    assert not decision.command and decision.clarification


@pytest.mark.parametrize(
    "text",
    [
        "Envie para Gabi que é pra ela reservar almoço no iCloud amanhã",
        "Um exemplo seria: encaixe reunião amanhã",
        "Não quero que marque reunião amanhã",
        "A Gabi disse que preciso marcar uma reunião amanhã",
        'Traduza: "Reserve almoço no iCloud amanhã"',
        "Quanto gastei no mercado ontem?",
    ],
)
def test_quotes_third_party_requests_and_other_domains_do_not_reach_model(text):
    generate = Mock()
    assert not calendar_candidate(text)
    decision = plan_calendar(text, RECEIVED, generate)
    assert not decision.command
    generate.assert_not_called()


def test_query_preserves_period_and_uses_only_named_source():
    text = "Me dá um panorama da minha programação da semana de 25/11 só no iCloud"
    result = plan_calendar(text, RECEIVED, Mock(return_value=extraction("list")))
    assert result.command and result.is_query
    assert "semana de 25/11" in result.command
    assert request_scope(result.command, query=True).providers == {"icloud"}


def test_retry_does_not_reinterpret_provider_or_validation_failures():
    assert retryable_parse_reply(None)
    assert retryable_parse_reply("Não consegui identificar a data ou o horário. Repita o pedido.")
    assert not retryable_parse_reply("Para gravar no iCloud, conecte a conta no painel.")
    assert not retryable_parse_reply("As datas informadas são diferentes.")
    assert not retryable_parse_reply("Compromisso registrado.")


def test_worker_distinguishes_final_instruction_from_day_first_query(engine, signed, clock):
    from unittest.mock import patch

    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from test_assistant_commands import process

    from jarvis.modules.calendar.models import Entry

    with patch("jarvis.modules.assistant.agent.refresh_for_query", return_value=[]) as refresh:
        reply = process(
            engine,
            clock,
            msg_id="create",
            text="Tenho dentista amanhã às 15h. Coloque na minha agenda.",
        )
        assert "registrado · dentista" in reply
        refresh.assert_not_called()
        for idx, question in enumerate(
            ("Amanhã tenho alguma coisa?", "Amanhã o que eu tenho na agenda")
        ):
            answer = process(engine, clock, msg_id=f"query{idx}", text=question)
            assert "15:00–16:00 · dentista" in answer
            refresh.assert_called_once_with(engine, frozenset())
            refresh.reset_mock()
        answer = process(engine, clock, msg_id="statement", text="Amanhã tenho alguma coisa")
        assert answer.startswith("Conversa:")
        refresh.assert_not_called()
    with Session(engine) as db:
        assert db.scalars(select(Entry.title)).all() == ["dentista"]


def test_worker_polite_calendar_question_accepts_explicit_write_only(engine, signed, clock):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from test_assistant_commands import input_message

    from jarvis.entrypoints.worker import process_one
    from jarvis.modules.calendar.models import Entry

    model = Mock()
    model.structured.return_value = extraction(title="dentista", temporal=["amanhã", "às 15h"])
    model.reply.return_value = "Conversa de teste."
    for idx, phrase in enumerate(
        (
            "Tem vaga para reservar o horário do dentista amanhã às 15h?",
            "Você consegue mesmo reservar o horário do dentista amanhã às 15h?",
            "Pode reservar o horário do dentista amanhã às 15h se houver vaga?",
            "Tenho dentista amanhã às 15h?",
            "Tenho um exemplo: dentista amanhã às 15h. Coloque na minha agenda.",
            "Tenho a transcrição: “dentista amanhã às 15h”. Coloque na minha agenda.",
            "Tenho dentista amanhã às 15h. Coloque na minha agenda só se eu confirmar.",
        )
    ):
        assert input_message(engine, clock, msg_id=str(idx), text=phrase)
        assert process_one(engine, model, clock=clock)
    with Session(engine) as db:
        assert not db.scalar(select(Entry.id))
    model.structured.reset_mock()
    phrase = "Pode reservar o horário do dentista amanhã às 15h?"
    assert input_message(engine, clock, msg_id="pedido", text=phrase)
    assert process_one(engine, model, clock=clock)
    model.structured.assert_called_once()
    with Session(engine) as db:
        assert db.scalars(select(Entry.title)).all() == ["dentista"]


def test_oversized_literal_event_asks_for_a_shorter_title():
    decision = plan_calendar("Amanhã haverá " + "a" * 301 + ". Marque na agenda", RECEIVED, None)
    assert not decision.command and decision.clarification and "300" in decision.clarification


def test_fallback_worker_refreshes_only_icloud_and_commits_one_voice_request(engine, signed, clock):
    from unittest.mock import patch

    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from test_assistant_commands import input_message, process
    from test_calendar import provider
    from test_icloud import MemoryDAV, link

    from jarvis.adapters.icloud import account
    from jarvis.entrypoints.worker import process_one
    from jarvis.modules.calendar.models import Entry
    from jarvis.modules.calendar.scope import LEGEND

    provider(engine)
    dav = MemoryDAV()
    sid = link(signed, engine, dav)
    spoken = "Dá para encaixar amanhã o almoço com Bia às treze horas no iCloud"
    model = Mock()
    model.reply.side_effect = AssertionError("o resultado deve vir da ferramenta")
    model.structured.return_value = extraction(
        title="almoço com Bia", temporal=["amanhã", "às treze horas"]
    )

    def receive():
        return input_message(
            engine, clock, msg_id="fallback_voz", text="[Áudio]", kind="audio", media=b"audio"
        )

    with patch("jarvis.adapters.ai.speech.transcribe", return_value=spoken):
        assert receive()
        assert process_one(engine, model, clock=clock)
        assert not receive()
        model.structured.assert_called_once()
    with Session(engine) as db:
        (item,) = db.scalars(select(Entry).where(Entry.title == "almoço com Bia")).all()
        assert item.source_id == sid
    with (
        patch.object(account, "CalDAV", return_value=dav),
        patch(
            "jarvis.adapters.calendar_refresh.Google",
            side_effect=AssertionError("Google fora do escopo da consulta"),
        ),
    ):
        answer = process(
            engine, clock, msg_id="fallback_icloud", text="Minha agenda do iCloud de amanhã"
        )
    assert "almoço com Bia" in answer and "13:00" in answer and answer.endswith(LEGEND)
    assert len([raw for _, raw in dav.files.values() if "SUMMARY:almoço com Bia" in raw]) == 1
    model.reply.assert_not_called()


def test_fallback_worker_does_not_override_a_conditional_or_compound_request(engine, signed, clock):
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from test_assistant_commands import input_message

    from jarvis.entrypoints.worker import process_one
    from jarvis.modules.calendar.models import Entry

    model = Mock()
    model.structured.side_effect = AssertionError("a validação local não delega este pedido")
    model.reply.side_effect = AssertionError("a validação local já respondeu")
    for i, text in enumerate(
        (
            "Marque dentista amanhã se houver vaga",
            "Marque dentista amanhã e depois cancele o evento",
        )
    ):
        assert input_message(engine, clock, msg_id=f"fallback_guard_{i}", text=text)
        assert process_one(engine, model, clock=clock)
    with Session(engine) as db:
        assert not db.scalar(select(Entry.id))


def test_spoken_day_date_and_year_cover_both_command_and_question():
    from jarvis.modules.assistant.intent import fold
    from jarvis.modules.calendar.chat import parse_event_input
    from jarvis.modules.calendar.periods import requested_period

    item = parse_event_input(
        "vinte e cinco de novembro de dois mil e vinte e sete às dez apresentação do projeto vinte e dois",
        RECEIVED,
    )
    assert item.title == "apresentação do projeto vinte e dois"
    assert item.starts_at.isoformat() == "2027-11-25T10:00:00-04:00"
    now = RECEIVED.date()
    day, period = requested_period(fold("Qual a agenda do dia vinte e cinco do próximo mês?"), now)
    assert (day.isoformat(), period) == ("2026-12-25", "day")
    month, period = requested_period(fold("Minha agenda de março de dois mil e vinte e sete"), now)
    assert (month.isoformat(), period) == ("2027-03-01", "month")
    assert (
        parse_event_input("entrega no dia vinte e cinco do próximo mês", RECEIVED).starts_at.date()
        == day
    )


def test_spoken_numbers_in_contact_and_payload_keep_their_meaning():
    from jarvis.modules.assistant.intent import parse_command
    from jarvis.modules.assistant.timing import delivery_time

    order = parse_command(
        "Envie para Equipe Vinte e Dois no dia vinte e cinco de novembro às dez que a peça é lote vinte e nove"
    )
    assert (
        order
        and order["recipient"] == "Equipe Vinte e Dois"
        and order["body"] == "a peça é lote vinte e nove"
    )
    assert delivery_time(order["when"], RECEIVED).instant.isoformat() == "2026-11-25T10:00:00-04:00"
