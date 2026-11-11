import json
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_commands import input_message, process

from jarvis.adapters import web_search
from jarvis.adapters.ai.ollama import CONVERSATION_ONLY, ModelUnavailable
from jarvis.adapters.ai.provider import HostedChat, chat_from_env
from jarvis.bootstrap.settings import ConfigurationError
from jarvis.entrypoints.worker import process_one
from jarvis.modules.assistant.arithmetic import arithmetic
from jarvis.modules.assistant.research import research_query
from jarvis.modules.messaging.models import Outbound


def test_calendar_claim_from_conversation_is_never_a_receipt():
    model = HostedChat("groq", "exemplo", SecretStr("fake"))
    with patch.object(model, "_generate", return_value="Agenda atualizada: reunião às 10h"):
        assert model.reply([]) == CONVERSATION_ONLY


def test_hosted_contract_key_is_sent_only_to_selected_fixed_host(monkeypatch):
    monkeypatch.setenv("JARVIS_CHAT_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_MODEL", "modelo-sintetico")
    monkeypatch.setenv("GROQ_API_KEY", "teste-groq")
    model = chat_from_env()
    assert isinstance(model, HostedChat)
    with patch("jarvis.adapters.ai.provider.http.client.HTTPSConnection") as https:
        conn = https.return_value
        response = conn.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"ok":true}'},
                        "finish_reason": "stop",
                    }
                ]
            }
        ).encode()
        assert json.loads(
            model.structured([{"role": "user", "content": "teste"}], {"type": "object"})
        ) == {"ok": True}
        https.assert_called_once_with("api.groq.com", timeout=45)
        method, path, raw, headers = conn.request.call_args.args
        assert (method, path, headers["Authorization"]) == (
            "POST",
            "/openai/v1/chat/completions",
            "Bearer teste-groq",
        )
        assert json.loads(raw)["response_format"] == {"type": "json_object"}
        response.status = 301
        with pytest.raises(ModelUnavailable) as err:
            model.reply([])
        assert err.value.retryable is False
        conn.close.assert_called()
    monkeypatch.setenv("JARVIS_CHAT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(ConfigurationError):
        chat_from_env()


@pytest.mark.parametrize(
    ("phrase", "value"),
    [
        ("Quanto é 0,1 mais 0,2?", "0,3"),
        ("Jarvis, calcule 12,5% de R$ 1.200,00", "150"),
        ("(7+3)*8", "80"),
        ("Calcule 1 dividido por zero", "divisor"),
        ("Calcule vinte e cinco por cento de duzentos reais", "50"),
        ("25/11", None),
        ("Calcule 0.125 mais 0.125", "0,25"),
        ("Calcule 1.2000 mais 2", "3,2"),
        ("Calcule 1,2,3 mais 3", "Confira"),
        ("Quanto é 1/0?", "divisor"),
        ("Gastei R$ 40 ontem", None),
    ],
)
def test_exact_arithmetic(phrase, value):
    result = arithmetic(phrase)
    if value is None:
        assert result is None
    else:
        assert result and value in result


def test_web_turn_answers_from_provided_sources_only(signed, engine, clock):
    assert process(
        engine, clock, msg_id="fact", text="Guarde na memória que o projeto se chama Ipê"
    )
    model = Mock()
    model.structured.return_value = {
        "findings": [{"text": "A feira abre às 9h.", "source": 1, "evidence": "Abre às 9h."}]
    }
    query = "horário da feira de tecnologia"
    assert input_message(engine, clock, msg_id="research", text="Pesquise na internet " + query)
    with patch.object(
        web_search,
        "search",
        return_value=[web_search.SearchHit("Feira", "https://example.org/feira", "Abre às 9h.")],
    ) as search:
        assert process_one(engine, model, clock=clock)
    search.assert_called_once_with(query)
    assert "Ipê" not in str(model.structured.call_args)
    with Session(engine) as db:
        result = db.scalars(select(Outbound.text).order_by(Outbound.id.desc())).first()
        assert result == "A feira abre às 9h.\nhttps://example.org/feira"
    model.reply.assert_not_called()


def test_unavailable_web_never_becomes_a_model_guess(engine, clock, signed):
    with patch.object(
        web_search, "search", side_effect=web_search.SearchUnavailable("Buscador indisponível.")
    ):
        assert (
            process(engine, clock, msg_id="web", text="Quais as últimas notícias da aviação?")
            == "Buscador indisponível."
        )
    assert research_query("Qual a cotação atual da minha carteira?") is None
    assert research_query("Diga para Bia que pesquise na web sobre aviação") is None


def test_old_preference_reaches_chat_and_can_be_removed(engine, clock, signed):
    assert process(engine, clock, msg_id="pref", text="Eu prefiro respostas curtas")
    clock.now += timedelta(days=732)
    model = Mock()
    model.reply.return_value = "Bora."
    assert input_message(engine, clock, msg_id="hello", text="Oi, tudo certo?")
    process_one(engine, model, clock=clock)
    history = model.reply.call_args.args[0]
    assert any("respostas curtas" in m["content"] for m in history)
    assert history[-1]["content"] == "Oi, tudo certo?"
