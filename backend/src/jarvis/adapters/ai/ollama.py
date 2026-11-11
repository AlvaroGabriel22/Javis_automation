"""Cliente síncrono do /api/chat da instalação local do Ollama."""

import http.client
import ipaddress
import json
import os
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.adapters.ai.persona import (
    ACTION_CLAIM,
    CONVERSATION_ONLY,
    ConversationalReply,
    compose_conversation,
)
from jarvis.bootstrap.settings import ROOT, ConfigurationError
from jarvis.modules.assistant.model_errors import ModelIssue, ModelUnavailable
from jarvis.modules.messaging.queue import valid_text

__all__ = ["ACTION_CLAIM", "CONVERSATION_ONLY", "ModelSettings", "ModelUnavailable", "OllamaChat"]


class ModelSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_file_encoding="utf-8")
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:3b"
    ollama_timeout_seconds: int = Field(default=45, ge=1, le=120)


class OllamaChat:
    def __init__(self, base_url: str, model: str, timeout_seconds: int = 45):
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "localhost"
        if (
            not local
            or parsed.scheme != "http"
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigurationError("OLLAMA_BASE_URL deve indicar o servidor Ollama em loopback.")
        if not valid_text(model, 128) or "cloud" in model.lower() or "/" in model or "://" in model:
            raise ConfigurationError("Escolha um modelo instalado localmente.")
        self.host, self.port = host, parsed.port or 11434
        self.model, self.timeout_seconds = model, timeout_seconds

    @classmethod
    def from_env(cls):
        cfg = ModelSettings(_env_file=os.getenv("JARVIS_ENV_FILE", str(ROOT / ".env")))  # pyright: ignore[reportCallIssue]
        return cls(cfg.ollama_base_url, cfg.ollama_model, cfg.ollama_timeout_seconds)

    def reply(self, messages: list[dict[str, str]]) -> str:
        return self.converse(messages, allow_sticker=False).text

    def converse(
        self, messages: list[dict[str, str]], *, allow_sticker: bool
    ) -> ConversationalReply:
        def generate(chat: list[dict[str, str]], schema: dict | None) -> str:
            return self._generate(chat, schema=schema, conversation=True)

        return compose_conversation(messages, allow_sticker, generate)

    def structured(self, messages: list[dict[str, str]], schema: dict) -> str:
        return self._generate(messages, schema=schema)

    def _generate(
        self,
        messages: list[dict[str, str]],
        *,
        schema: dict | None = None,
        conversation: bool = False,
    ) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": messages,
            "options": {
                "num_predict": 768 if conversation and schema else 512,
                "temperature": 0.65 if conversation else 0,
            },
        }
        if schema:
            payload["format"] = schema
        body = json.dumps(payload, ensure_ascii=False).encode()
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_seconds)
        try:
            connection.request("POST", "/api/chat", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                # O transporte direto não usa proxy e não segue redirecionamentos.
                raise ModelUnavailable.for_http(response.status, local=True)
            data = response.read(65537)
            if len(data) > 65536:
                raise ModelUnavailable()
            result = json.loads(data)
            if not isinstance(result, dict) or result.get("done") is not True:
                raise ModelUnavailable()
            message = result.get("message")
            if (
                not isinstance(message, dict)
                or message.get("role") != "assistant"
                or message.get("tool_calls")
            ):
                raise ModelUnavailable()
            answer = message.get("content")
            if not isinstance(answer, str) or not valid_text(
                answer, 16_000 if conversation and schema else 4000
            ):
                raise ModelUnavailable()
            return answer.strip()
        except ConnectionError:
            raise ModelUnavailable(ModelIssue.LOCAL_CONNECTION) from None
        except (OSError, http.client.HTTPException, UnicodeError, ValueError) as exc:
            raise ModelUnavailable() from exc
        finally:
            connection.close()
