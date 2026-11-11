from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_assistant_commands import input_message, process

from jarvis.modules.assistant.personal_planner import personal_candidate, plan_personal
from jarvis.modules.personal.models import Fact, LedgerEntry

AT = datetime(2026, 11, 9, 2, tzinfo=UTC)


def proposal(intent, *, topic=None, value=None, period=None):
    return {"intent": intent, "topic": topic, "value": value, "period": period}


def test_common_requests_run_without_inference_and_keep_original_arguments():
    no_model = Mock(side_effect=AssertionError("sem inferência"))
    assert (
        plan_personal("sobraria dinheiro se eu comprasse algo de 80 reais?", AT, no_model).command
        == "Posso gastar 80 reais?"
    )
    assert (
        plan_personal(
            "qual era aquele projeto de orquídeas que te contei faz tempo?", AT, no_model
        ).command
        == "O que te contei sobre projeto de orquídeas?"
    )
    no_model.assert_not_called()


def test_structured_expense_read_preserves_period_category_and_currency():
    question = "Como ficaram as minhas despesas com transporte no mês passado?"
    model = Mock(return_value=proposal("expenses", topic="transporte", period="no mês passado"))
    assert (
        plan_personal(question, AT, model).command == "Quanto gastei com transporte no mês passado?"
    )
    args = model.call_args.args
    assert (
        args[1]["additionalProperties"] is False
        and "2026-11-08T22:00-04:00" in args[0][1]["content"]
    )


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        (
            "Como ficaram as minhas despesas com transporte no mês passado?",
            proposal("expenses", topic="transporte"),
        ),
        (
            "Como ficaram as minhas despesas com transporte no mês passado?",
            proposal("expenses", period="no mês passado"),
        ),
        (
            "Como ficaram as minhas despesas no mês passado?",
            proposal("expenses", period="em julho"),
        ),
        (
            "Como ficaram as minhas despesas no mês passado?",
            proposal("budget", period="no mês passado"),
        ),
        ("Qual meu limite para gastar 60 euros?", proposal("purchase", value="60")),
        ("Qual meu limite para gastar 60 ou 90 reais?", proposal("purchase", value="90 reais")),
        ("Qual foi o balanço das minhas despesas?", {**proposal("expenses"), "execute": "envie"}),
        (
            "Recorda o que comentei nas nossas conversas sobre o cultivo de orquídeas?",
            proposal("history", topic="cultivo de rosas"),
        ),
        (
            "Recorda o que comentei nas nossas conversas sobre o cultivo de orquídeas?",
            proposal("history", topic="orquídeas"),
        ),
    ],
)
def test_proposal_cannot_introduce_or_silently_remove_constraints(question, answer):
    decision = plan_personal(question, AT, Mock(return_value=answer))
    assert decision.command is None and decision.clarification


@pytest.mark.parametrize(
    "text",
    [
        "E aí, tudo bem?",
        "Quem inventou o rádio?",
        "Qual o orçamento da prefeitura?",
        "Envie para Gabi perguntando se eu gastasse 80 reais sobraria dinheiro?",
        'Minha amiga disse: "Quanto eu gastei neste mês?"',
        "Apague o que te contei antes",
        "Gastei 80 reais ontem",
        "Avisa a Gabi do que te contei sobre a reunião?",
    ],
)
def test_secondary_router_skips_chat_writes_and_third_party_messages(text):
    generate = Mock()
    assert not personal_candidate(text)
    assert not plan_personal(text, AT, generate).command
    generate.assert_not_called()


def test_worker_returns_exact_remaining_money_and_sources_without_creating_new_entries(
    engine, signed, clock
):
    process(engine, clock, msg_id="limit", text="Meu orçamento mensal é R$ 400")
    process(engine, clock, msg_id="expense", text="Gastei 70 reais no mercado")
    process(
        engine, clock, msg_id="fact", text="Guarde que o projeto de orquídeas chama-se Estufa 42"
    )
    purchase = process(
        engine, clock, msg_id="hypo", text="Sobraria dinheiro se eu comprasse algo de 80 reais?"
    )
    recall = process(
        engine,
        clock,
        msg_id="recall",
        text="Qual era aquele projeto de orquídeas que te contei faz tempo?",
    )
    assert "R$ 250,00" in purchase and "Estufa 42" in recall and "memória M" in recall
    assert not input_message(engine, clock, msg_id="hypo", text="repetição")
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 2
        assert db.scalar(select(func.count()).select_from(Fact)) == 1
