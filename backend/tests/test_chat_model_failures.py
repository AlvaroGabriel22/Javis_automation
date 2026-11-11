"""O WhatsApp deve explicar a falha da IA sem repetir segredos ou respostas externas."""

from unittest.mock import patch

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_commands import input_message

from jarvis.adapters.ai.ollama import OllamaChat
from jarvis.adapters.ai.provider import ChatConfigurationError, HostedChat, UnavailableChat
from jarvis.entrypoints.worker import process_one
from jarvis.modules.assistant.model_errors import ModelIssue
from jarvis.modules.messaging import queue
from jarvis.modules.messaging.models import Inbound, Outbound


@pytest.mark.parametrize(
    ("local", "status", "issue"),
    [
        (True, 403, ModelIssue.LOCAL_ACCESS),
        (True, 404, ModelIssue.LOCAL_MODEL),
        (True, None, ModelIssue.LOCAL_CONNECTION),
        (False, 401, ModelIssue.ACCESS_DENIED),
        (False, 403, ModelIssue.ACCESS_DENIED),
        (False, 404, ModelIssue.HOSTED_MODEL),
        (False, 400, ModelIssue.REQUEST_REJECTED),
        (False, 429, ModelIssue.RATE_LIMIT),
        (False, 503, ModelIssue.UNAVAILABLE),
    ],
)
def test_provider_error_survives_worker_and_retry_without_disclosing_http_body(
    engine, clock, signed, local, status, issue
):
    model = (
        OllamaChat("http://127.0.0.1:11434", "modelo-sintetico")
        if local
        else HostedChat("openai", "modelo-sintetico", SecretStr("chave-sintetica"))
    )
    transport = "http.client.HTTPConnection" if local else "http.client.HTTPSConnection"
    assert input_message(engine, clock, msg_id="diagnostico", text="Tudo bem?")
    with patch(transport) as constructor:
        conn = constructor.return_value
        response = conn.getresponse.return_value
        if status is None:
            conn.request.side_effect = ConnectionRefusedError()
        else:
            response.status = status
            response.read.return_value = b'{"error":"dados pessoais do provedor e da conta"}'
        for attempt in range(1, (queue.MAX_ATTEMPTS if issue.retryable else 1) + 1):
            assert process_one(engine, model, clock=clock)
            with Session(engine) as db:
                row = db.scalars(select(Inbound)).one()
                assert row.error_code == issue.code and row.attempts == attempt
                if row.state == "pending":
                    assert db.get(Outbound, row.id) is None
                    clock.now += queue.BACKOFF[attempt - 1]
                else:
                    assert row.state == "failed"
                    assert db.get_one(Outbound, row.id).text == issue.message
        response.read.assert_not_called()
    assert not process_one(engine, model, clock=clock)


@pytest.mark.parametrize(
    ("model_name", "key", "issue"),
    [("modelo", None, ModelIssue.KEY_MISSING), ("", SecretStr("teste"), ModelIssue.MODEL_MISSING)],
)
def test_incomplete_provider_configuration_is_distinct_and_actionable(model_name, key, issue):
    with pytest.raises(ChatConfigurationError) as failure:
        HostedChat("groq", model_name, key)
    assert failure.value.issue is issue
    assert ".env" in str(failure.value)


def test_unavailable_semantic_planner_reports_provider_issue(engine, clock, signed):
    assert input_message(
        engine, clock, msg_id="planejar", text="Encaixe amanhã o almoço com Bia às 13h"
    )
    assert process_one(engine, UnavailableChat(ModelIssue.KEY_MISSING), clock=clock)
    with Session(engine) as db:
        row = db.scalars(select(Inbound)).one()
        assert row.error_code == ModelIssue.KEY_MISSING.code
        assert db.get_one(Outbound, row.id).text == ModelIssue.KEY_MISSING.message
