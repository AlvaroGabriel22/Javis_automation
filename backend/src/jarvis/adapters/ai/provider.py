"""Seleção explícita do modelo de conversa. Uma falha local não muda o destino dos dados."""

import http.client
import json
import os
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.adapters.ai.ollama import ModelUnavailable, OllamaChat
from jarvis.adapters.ai.persona import ConversationalReply, compose_conversation
from jarvis.bootstrap.settings import ROOT, ConfigurationError
from jarvis.modules.assistant.model_errors import ModelIssue
from jarvis.modules.messaging.queue import valid_text


class ChatSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_file_encoding="utf-8")
    jarvis_chat_provider: Literal["ollama", "groq", "openai"] = "ollama"
    openai_model: str = ""
    groq_model: str = ""
    openai_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None


class ChatConfigurationError(ConfigurationError):
    def __init__(self, issue: ModelIssue):
        super().__init__(issue.message)
        self.issue = issue


class HostedChat:
    def __init__(self, provider: Literal["groq", "openai"], model: str, key: SecretStr | None):
        self.host, self.path = (
            ("api.groq.com", "/openai/v1/chat/completions")
            if provider == "groq"
            else ("api.openai.com", "/v1/chat/completions")
        )
        if not key or not key.get_secret_value().strip():
            raise ChatConfigurationError(ModelIssue.KEY_MISSING)
        if not valid_text(model.strip(), 128):
            raise ChatConfigurationError(ModelIssue.MODEL_MISSING)
        self.key, self.model = key, model.strip()

    def reply(self, messages: list[dict[str, str]]) -> str:
        return self.converse(messages, allow_sticker=False).text

    def converse(
        self, messages: list[dict[str, str]], *, allow_sticker: bool
    ) -> ConversationalReply:
        def generate(chat: list[dict[str, str]], schema: dict | None) -> str:
            # A conversa também funciona em modelos hospedados sem modo JSON nativo.
            # O contrato de resposta vai no prompt e o texto é validado localmente.
            return self._generate(chat, structured=False, content_limit=16_000 if schema else 4000)

        return compose_conversation(messages, allow_sticker, generate)

    def structured(self, messages: list[dict[str, str]], schema: dict) -> str:
        contract = {
            "role": "system",
            "content": "Retorne somente um objeto JSON que corresponda ao esquema: "
            + json.dumps(schema, ensure_ascii=False),
        }
        return self._generate([contract, *messages], structured=True)

    def _generate(self, messages, *, structured, content_limit=4000):
        payload = {"model": self.model, "messages": messages, "max_completion_tokens": 1200}
        if structured:
            payload["response_format"] = {"type": "json_object"}
        conn = http.client.HTTPSConnection(self.host, timeout=45)
        try:
            conn.request(
                "POST",
                self.path,
                json.dumps(payload).encode(),
                {
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self.key.get_secret_value(),
                },
            )
            response = conn.getresponse()
            if response.status != 200:
                raise ModelUnavailable.for_http(response.status, local=False)
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ModelUnavailable()
            choice = json.loads(raw)["choices"][0]
            message = choice["message"]
            if (
                choice.get("finish_reason") != "stop"
                or message.get("role") != "assistant"
                or message.get("tool_calls")
                or not valid_text(message.get("content"), content_limit)
            ):
                raise ModelUnavailable()
            return message["content"].strip()
        except (
            OSError,
            http.client.HTTPException,
            UnicodeError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
        ) as exc:
            raise ModelUnavailable() from exc
        finally:
            conn.close()


class UnavailableChat:
    def __init__(self, issue: ModelIssue = ModelIssue.CONFIGURATION):
        self.issue = issue

    def reply(self, messages):
        raise ModelUnavailable(self.issue)

    def structured(self, messages, schema):
        raise ModelUnavailable(self.issue)


def chat_from_env():
    cfg = ChatSettings(_env_file=os.getenv("JARVIS_ENV_FILE", str(ROOT / ".env")))  # pyright: ignore[reportCallIssue]
    if cfg.jarvis_chat_provider == "ollama":
        return OllamaChat.from_env()
    if cfg.jarvis_chat_provider == "groq":
        return HostedChat("groq", cfg.groq_model, cfg.groq_api_key)
    return HostedChat("openai", cfg.openai_model, cfg.openai_api_key)
