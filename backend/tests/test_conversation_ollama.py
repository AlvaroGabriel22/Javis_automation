import json
from unittest.mock import patch

import pytest

from jarvis.adapters.ai.ollama import CONVERSATION_ONLY, ModelUnavailable, OllamaChat
from jarvis.bootstrap.settings import ConfigurationError


def model():
    return OllamaChat("http://127.0.0.1:11434", "qwen2.5:3b")


def test_sends_local_chat_request_and_returns_valid_response(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://203.0.113.4:9999")
    with patch("jarvis.adapters.ai.ollama.http.client.HTTPConnection") as ctor:
        conn = ctor.return_value
        response = conn.getresponse.return_value
        response.status = 200
        response.read.return_value = (
            b'{"done":true,"message":{"role":"assistant","content":"Bom dia!"}}'
        )
        assert model().reply([{"role": "user", "content": "Oi"}]) == "Bom dia!"
        ctor.assert_called_once_with("127.0.0.1", 11434, timeout=45)
        method, path, body, _ = conn.request.call_args.args
        data = json.loads(body)
        assert (method, path, data["model"], data["stream"]) == (
            "POST",
            "/api/chat",
            "qwen2.5:3b",
            False,
        )
        assert [m["role"] for m in data["messages"]] == ["system", "user"]
        assert "America/Manaus" in data["messages"][0]["content"]
        assert "-04:00" in data["messages"][0]["content"]
        conn.close.assert_called_once()


@pytest.mark.parametrize(
    "status,retryable",
    [(307, False), (401, False), (404, False), (408, True), (429, True), (503, True)],
)
def test_http_status_controls_retries_without_following_redirects(status, retryable):
    with patch("jarvis.adapters.ai.ollama.http.client.HTTPConnection") as ctor:
        conn = ctor.return_value
        conn.getresponse.return_value.status = status
        with pytest.raises(ModelUnavailable) as exc:
            model().reply([])
        assert exc.value.retryable is retryable
        assert ctor.call_count == conn.request.call_count == 1


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"[]",
        b'{"done":false}',
        b'{"done":true,"message":{"role":"user","content":"oi"}}',
        b'{"done":true,"message":{"role":"assistant","content":"   "}}',
        b"X" * 65537,
        b'{"done":true,"message":{"role":"assistant","tool_calls":[{}],"content":"oi"}}',
    ],
)
def test_invalid_response_does_not_become_user_reply(body):
    with patch("jarvis.adapters.ai.ollama.http.client.HTTPConnection") as ctor:
        response = ctor.return_value.getresponse.return_value
        response.status, response.read.return_value = 200, body
        with pytest.raises(ModelUnavailable):
            model().reply([])


def test_timeout_and_invented_action():
    with patch("jarvis.adapters.ai.ollama.http.client.HTTPConnection") as ctor:
        conn = ctor.return_value
        conn.getresponse.side_effect = TimeoutError()
        with pytest.raises(ModelUnavailable) as exc:
            model().reply([])
        assert exc.value.retryable
        conn.getresponse.side_effect = None
        response = conn.getresponse.return_value
        response.status = 200
        response.read.return_value = (
            b'{"done":true,"message":{"role":"assistant","content":"Agendei sua mensagem!"}}'
        )
        assert model().reply([]) == CONVERSATION_ONLY
        response.read.return_value = json.dumps(
            {
                "done": True,
                "message": {"role": "assistant", "content": "Será enviado daqui a dois minutos."},
            }
        ).encode()
        assert model().reply([]) == CONVERSATION_ONLY


@pytest.mark.parametrize(
    "url,tag", [("https://api.example", "qwen2.5:3b"), ("http://127.0.0.1", "modelo-cloud")]
)
def test_refuses_external_model(url, tag):
    with pytest.raises(ConfigurationError):
        OllamaChat(url, tag)
