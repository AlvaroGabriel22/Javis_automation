"""Verifica modelos e pesquisa do perfil; --smoke usa somente entradas sintéticas."""

import argparse
import os
from io import BytesIO

import requests
from jarvis.adapters import web_search
from jarvis.adapters.ai.ollama import ModelUnavailable, OllamaChat
from jarvis.adapters.ai.provider import (
    ChatConfigurationError,
    HostedChat,
    chat_from_env,
)
from jarvis.adapters.ai.vision import VisionError, VisionSettings, describe
from jarvis.bootstrap.settings import ROOT, ConfigurationError
from jarvis.modules.assistant.model_errors import ModelIssue
from PIL import Image
from pydantic import ValidationError

ENV_FILE = os.getenv("JARVIS_ENV_FILE", str(ROOT / ".env"))


def models_at(server):
    url = server.host if ":" not in server.host else f"[{server.host}]"
    with requests.Session() as client:
        client.trust_env = False
        response = client.get(
            f"http://{url}:{server.port}/api/tags", timeout=3, allow_redirects=False
        )
        response.raise_for_status()
        return {m["name"] for m in response.json()["models"]}


def check_chat(smoke: bool) -> bool:
    try:
        model = chat_from_env()
        if isinstance(model, OllamaChat):
            print("Conversa · Ollama · modelo:", model.model)
            tag = model.model if ":" in model.model else model.model + ":latest"
            if tag not in models_at(model):
                print(
                    "O modelo configurado não aparece no Ollama. Compare OLLAMA_MODEL com ollama list."
                )
                return False
        elif isinstance(model, HostedChat):
            print("Conversa ·", model.host, "· modelo:", model.model)
        if smoke:
            model.reply(
                [
                    {
                        "role": "user",
                        "content": "Responda em português com uma saudação curta.",
                    }
                ]
            )
            print("Conversa · o modelo respondeu ao teste.")
        return True
    except (ChatConfigurationError, ModelUnavailable) as exc:
        print(exc.issue.message)
    except (requests.ConnectionError, requests.Timeout):
        print(ModelIssue.LOCAL_CONNECTION.message)
    except (ConfigurationError, ValidationError):
        print(ModelIssue.CONFIGURATION.message)
    except (requests.RequestException, ValueError, KeyError, TypeError):
        print(
            "O serviço local não forneceu o catálogo de modelos. Confira OLLAMA_BASE_URL."
        )
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Pergunta pública ao buscador e testes de conversa e visão com dados sintéticos.",
    )
    parser.add_argument(
        "--chat-only", action="store_true", help="Testa apenas a conversa."
    )
    args = parser.parse_args()
    smoke = args.smoke
    errors = int(not check_chat(smoke))
    if args.chat_only:
        return errors
    try:
        cfg = VisionSettings(_env_file=ENV_FILE)  # pyright: ignore[reportCallIssue]
        server = OllamaChat(cfg.ollama_base_url, cfg.ollama_vision_model)
        if (
            server.model if ":" in server.model else server.model + ":latest"
        ) not in models_at(server):
            raise ConfigurationError("Tag de visão ausente.")
        print("Visão local · modelo disponível:", server.model)
        if smoke:
            buffer = BytesIO()
            Image.new("RGB", (80, 80), "red").save(buffer, format="PNG")
            result = describe(
                buffer.getvalue(), kind="image", caption="Qual a cor deste quadrado?"
            )
            print(
                "Visão · resposta ao quadrado vermelho sintético:",
                " ".join(result.split())[:180],
            )
    except (
        ConfigurationError,
        ValidationError,
        requests.RequestException,
        VisionError,
        ValueError,
        KeyError,
        TypeError,
    ):
        print("Visão indisponível · confira o Ollama e OLLAMA_VISION_MODEL.")
        errors += 1
    try:
        settings = web_search.SearchSettings(_env_file=ENV_FILE)  # pyright: ignore[reportCallIssue]
        if settings.jarvis_search_provider == "searxng":
            health = (
                web_search.local_endpoint(settings.jarvis_searxng_url).removesuffix(
                    "/search"
                )
                + "/healthz"
            )
            with requests.Session() as client:
                client.trust_env = False
                if (
                    client.get(health, timeout=3, allow_redirects=False).status_code
                    != 200
                ):
                    raise web_search.SearchUnavailable("SearXNG indisponível.")
            print("Pesquisa · SearXNG respondeu no loopback.")
        elif not settings.brave_search_api_key:
            raise web_search.SearchUnavailable("Chave ausente.")
        else:
            print("Pesquisa · credencial do Brave configurada.")
        if smoke:
            hits = web_search.search("SearXNG documentação oficial")
            if not hits:
                raise web_search.SearchUnavailable("Busca de teste sem fontes.")
            print("Pesquisa · fontes públicas no teste:", len(hits))
    except (ValidationError, requests.RequestException, web_search.SearchUnavailable):
        print(
            "Busca indisponível · inicie o SearXNG com python3 infra/search/start.py ou configure o Brave no .env."
        )
        errors += 1
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
