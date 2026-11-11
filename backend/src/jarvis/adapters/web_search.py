"""Pesquisa em provedores configurados pelo proprietário. Resultados são dados externos."""

import ipaddress
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from html import unescape
from typing import Literal
from urllib.parse import urlsplit

import requests
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from jarvis.bootstrap.settings import ROOT
from jarvis.modules.assistant.intent import LEAD, fold

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
MAX_RESPONSE = 1024 * 1024
MAX_HITS = 4


class SearchUnavailable(RuntimeError):
    """Mensagem sem dados da consulta, pronta para ser exibida no WhatsApp."""


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


class SearchSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", env_file_encoding="utf-8")
    jarvis_search_provider: Literal["searxng", "brave"] = "searxng"
    jarvis_searxng_url: str = "http://127.0.0.1:8888"
    brave_search_api_key: SecretStr | None = None


_PUBLIC_WEB = r"(?:na (?:internet|web)|(?:no|pelo) buscador|online)"
_WEB = rf"(?:{_PUBLIC_WEB}|(?:no|pelo) google(?! (?:agenda|calendar|drive|sheets|docs|fotos|tarefas)\b))"
_VERB = re.compile(
    r"^(?:(?:voce |vc )?(?:consegue|pode|poderia) |(?:que )?(?:voce |vc ))?"
    r"(?:(?:faca|faz|de|da) (?:uma? )?(?:pesquisa(?:da)?|busca)|"
    r"pesquis(?:ar|e|a)|busc(?:ar|a)|busque|procur(?:ar|e|a)|"
    r"(?:verifi(?:que|car)|confira|veja|consulte) (?=" + _WEB + r"\b))\b"
)
_QUALIFIER = re.compile(
    rf"^(?:{_WEB}|(?:para|pra) mim|por favor|sobre|por|a respeito de|informacoes sobre)\b[\s,:-]*"
)
_PERSONAL = re.compile(
    r"\b(?:minh[ao]s?|noss[ao]s?|meus?) (?:ultim[oa]s? )?(?:agenda|calendario|icloud|gmail|google|whatsapp|"
    r"conversa|mensage|compromisso|reuniao|evento|contato|despesa|gasto|receita|anotac|memoria|"
    r"lembrete|historico|planilha|arquivo|orcamento|saldo|fatura|carteira)"
    r"|\bo que (?:combinamos|conversamos|eu te (?:disse|falei))\b"
)
_CONNECTED = re.compile(
    r"\b(?:n[ao]s?|em) (?:agendas?|calendarios?|conversas?|mensagens?|contatos?|historico|"
    r"lembretes?|whatsapp|icloud|gmail|google agenda|google calendar|google drive)\b"
    r"|\b(?:eventos?|reunioes|compromissos?|agendas?|calendarios?|arquivos?|e-mails?|contatos?|mensagens?) "
    r"d[oa] (?:google|icloud|apple|gmail|whatsapp|drive)\b"
)


def _requested_lookup(text: str) -> tuple[str, bool] | None:
    raw = unicodedata.normalize("NFC", " ".join(text.split()))
    if lead := LEAD.match(fold(raw)):
        raw = raw[lead.end() :].lstrip()
    if not (match := _VERB.match(fold(raw))):
        return None
    query = raw[match.end() :].lstrip(" ,:-")
    explicit_web = False
    while qualifier := _QUALIFIER.match(fold(query)):
        explicit_web |= bool(re.match(_PUBLIC_WEB, qualifier[0]))
        query = query[qualifier.end() :]
    if tail := re.search(rf"[, ]+{_PUBLIC_WEB}[?.!]*$", fold(query)):
        explicit_web = True
        query = query[: tail.start()]
    normal = fold(query)
    private = not explicit_web and bool(_PERSONAL.search(normal) or _CONNECTED.search(normal))
    return query.strip(), private


def extract_query(text: str) -> str | None:
    """Extrai o termo público e impede que consultas internas sejam enviadas ao buscador."""
    lookup = _requested_lookup(text)
    return lookup[0] if lookup is not None and not lookup[1] else None


def is_private_lookup(text: str) -> bool:
    """Identifica buscas que devem seguir para as ferramentas pessoais ou para esclarecimento."""
    lookup = _requested_lookup(text)
    return lookup is not None and lookup[1]


def local_endpoint(base: str) -> str:
    try:
        parts = urlsplit(base)
        host = parts.hostname or ""
        # localhost é normalizado para o endereço literal. A consulta não usa DNS.
        address = ipaddress.ip_address("127.0.0.1" if host == "localhost" else host)
        if (
            parts.scheme != "http"
            or not address.is_loopback
            or parts.username is not None
            or parts.password is not None
            or parts.path not in ("", "/")
            or parts.query
            or parts.fragment
        ):
            raise ValueError
        port = parts.port or 80
    except ValueError:
        raise SearchUnavailable(
            "Confira JARVIS_SEARXNG_URL. Use a URL HTTP local do SearXNG."
        ) from None
    literal = f"[{address}]" if address.version == 6 else str(address)
    return f"http://{literal}:{port}/search"


def _plain(value: object, size: int) -> str:
    if not isinstance(value, str):
        return ""
    # Os provedores retornam fragmentos curtos com marcação de destaque.
    # A limpeza também aceita HTML parcial ou malformado enviado por um motor.
    plain = unescape(re.sub(r"</?[a-zA-Z][^>]{0,1000}>", "", value[:4000]))
    text = "".join(c for c in plain if not unicodedata.category(c).startswith("C") or c.isspace())
    return " ".join(text.split())[:size]


def _public_link(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 720 or "\\" in value:
        return None
    if any(c.isspace() or unicodedata.category(c).startswith("C") for c in value):
        return None
    try:
        url = urlsplit(value)
        host = (url.hostname or "").rstrip(".").lower()
        if (
            url.scheme not in ("https", "http")
            or not host
            or url.username is not None
            or url.password is not None
        ):
            return None
        if url.port not in (None, 80, 443):
            return None
        try:
            if not ipaddress.ip_address(host).is_global:
                return None
        except ValueError:
            if "." not in host or host.endswith((".localhost", ".local", ".internal")):
                return None
    except ValueError:
        return None
    return url._replace(fragment="").geturl()


def _items(payload: object, provider: str) -> list[SearchHit]:
    if not isinstance(payload, dict):
        raise SearchUnavailable("O buscador devolveu uma resposta ilegível.")
    if provider == "searxng":
        rows = payload.get("results")
        if rows == [] and payload.get("unresponsive_engines"):
            raise SearchUnavailable(
                "Os motores do SearXNG não concluíram a pesquisa. Tente novamente."
            )
        description = "content"
    else:
        web = payload.get("web")
        if web is None and payload.get("type") == "search":
            return []
        rows = web.get("results") if isinstance(web, dict) else None
        description = "description"
    if not isinstance(rows, list):
        raise SearchUnavailable("O buscador não devolveu resultados no formato esperado.")
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or not (url := _public_link(row.get("url"))) or url in seen:
            continue
        title = _plain(row.get("title"), 125)
        if not title:
            continue
        hits.append(SearchHit(title, url, _plain(row.get(description), 240)))
        seen.add(url)
        if len(hits) == MAX_HITS:
            break
    return hits


def search(query: str) -> list[SearchHit]:
    """GET em um endpoint fixo, sem redirecionamentos ou acesso às URLs retornadas."""
    query = " ".join(query.split())
    if not query:
        raise SearchUnavailable("O que você quer que eu pesquise na internet?")
    if (
        len(query) > 600
        or len(query.split()) > 75
        or any(unicodedata.category(c).startswith("C") for c in query)
    ):
        raise SearchUnavailable("Resuma os termos da pesquisa em até 600 caracteres e 75 palavras.")
    try:
        cfg = SearchSettings(_env_file=os.getenv("JARVIS_ENV_FILE", str(ROOT / ".env")))  # pyright: ignore[reportCallIssue]
    except ValidationError:
        raise SearchUnavailable(
            "Confira JARVIS_SEARCH_PROVIDER no .env. Escolha searxng ou brave."
        ) from None
    provider = cfg.jarvis_search_provider
    headers = {"Accept": "application/json", "User-Agent": "Jarvis/1.0"}
    if provider == "brave":
        token = (
            cfg.brave_search_api_key.get_secret_value().strip() if cfg.brave_search_api_key else ""
        )
        if not token:
            raise SearchUnavailable(
                "Configure BRAVE_SEARCH_API_KEY no .env para pesquisar pelo Brave."
            )
        endpoint = BRAVE_ENDPOINT
        headers["X-Subscription-Token"] = token
        params = {"q": query, "count": "6", "country": "BR", "text_decorations": "false"}
    else:
        endpoint = local_endpoint(cfg.jarvis_searxng_url)
        params = {"q": query, "format": "json", "language": "pt-BR", "categories": "general"}
    deadline = time.monotonic() + 16
    try:
        with requests.Session() as client:
            # Termos enviados a um loopback nunca são roteados por um proxy de ambiente.
            client.trust_env = provider == "brave"
            with client.get(
                endpoint,
                params=params,
                headers=headers,
                timeout=(3, 8),
                allow_redirects=False,
                stream=True,
            ) as response:
                if response.status_code == 429:
                    raise SearchUnavailable(
                        "O buscador atingiu o limite de consultas. Tente novamente mais tarde."
                    )
                if response.status_code in (401, 403):
                    message = (
                        "Habilite o formato JSON no SearXNG."
                        if provider == "searxng"
                        else "Confira a chave e as permissões da Brave Search API."
                    )
                    raise SearchUnavailable(message)
                if response.status_code != 200:
                    raise SearchUnavailable(
                        f"O buscador respondeu com HTTP {response.status_code}. A pesquisa não foi concluída."
                    )
                body = bytearray()
                for chunk in response.iter_content(32768):
                    if len(body) + len(chunk) > MAX_RESPONSE or time.monotonic() > deadline:
                        raise SearchUnavailable(
                            "O buscador excedeu o limite de tempo ou de tamanho da resposta."
                        )
                    body.extend(chunk)
    except requests.RequestException:
        hint = (
            " Inicie o serviço na pasta do Jarvis com python3 infra/search/start.py."
            if provider == "searxng"
            else " Confira o acesso da máquina à internet."
        )
        raise SearchUnavailable("Não consegui consultar o buscador." + hint) from None
    try:
        payload = json.loads(body)
    except (ValueError, RecursionError):
        raise SearchUnavailable("O buscador devolveu uma resposta ilegível.") from None
    return _items(payload, provider)


def format_results(hits: list[SearchHit]) -> str:
    if not hits:
        return "Não encontrei fontes para esses termos. Posso procurar com outras palavras."
    sections: list[str] = []
    for index, hit in enumerate(hits[:MAX_HITS], start=1):
        section = f"{index}. {hit.title}\n" + (f"{hit.snippet}\n" if hit.snippet else "") + hit.url
        if len("\n\n".join([*sections, section])) > 3950:
            break
        sections.append(section)
    return "\n\n".join(sections)
