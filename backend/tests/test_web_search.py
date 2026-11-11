import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from jarvis.adapters import web_search
from jarvis.modules.messaging.queue import valid_text


@pytest.fixture(autouse=True)
def isolated_search(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ENV_FILE", str(tmp_path / "absent.env"))
    monkeypatch.setenv("JARVIS_SEARCH_PROVIDER", "searxng")
    monkeypatch.setenv("JARVIS_SEARXNG_URL", "http://127.0.0.1:8888")
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Jarvis, dá uma pesquisada na internet sobre a documentação do PostgreSQL",
            "a documentação do PostgreSQL",
        ),
        ("Pode pesquisar pra mim por SearXNG?", "SearXNG?"),
        ("Verifique na web o lançamento do modelo", "o lançamento do modelo"),
        ("Pesquise", ""),
        ("Consulte minha agenda de hoje", None),
        ("Busque no iCloud a revisão", None),
        ("Jarvis, procure na agenda o meu compromisso amanhã", None),
        ("Procure na minha agenda a consulta", None),
        ("Jarvis, pesquise nas minhas conversas sobre trabalho", None),
        ("Avise a Gabi que pesquise sobre o projeto", None),
        (
            "Pesquise na web como configurar minha agenda do iCloud",
            "como configurar minha agenda do iCloud",
        ),
        (
            "Procure documentação para sincronizar no Google Agenda na internet",
            "documentação para sincronizar no Google Agenda",
        ),
    ],
)
def test_explicit_search_and_personal_commands(text, expected):
    assert web_search.extract_query(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Procure reunião com a Gabi na minha agenda",
        "Pesquise o que combinamos no nosso histórico",
        "Busque os eventos do iCloud",
        "Busque na agenda do Google sobre auditoria",
        "Procure no Google Agenda a reunião de revisão",
        "Pesquise no Google meus compromissos desta semana",
        "Busque as mensagens com a Gabi no WhatsApp",
        "Pesquise o valor nos meus gastos do mês",
    ],
)
def test_internal_lookup_never_becomes_a_web_query(text):
    assert web_search.is_private_lookup(text)
    assert web_search.extract_query(text) is None


def response(body, status=200):
    from unittest.mock import MagicMock

    res = MagicMock()
    res.__enter__.return_value = res
    res.status_code = status
    data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
    res.iter_content.return_value = [data]
    return res


def test_brave_key_only_goes_to_fixed_endpoint(monkeypatch):
    monkeypatch.setenv("JARVIS_SEARCH_PROVIDER", "brave")
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "synthetic-key")
    body = {
        "web": {
            "results": [
                {
                    "title": "<b>PostgreSQL</b> &amp; SQL",
                    "description": "Conexões em <b>português</b>.",
                    "url": "https://www.postgresql.org/docs/",
                },
                {
                    "title": "Outra ocorrência",
                    "url": "https://www.postgresql.org/docs/#connections",
                },
                {"title": "Local", "url": "http://127.0.0.1/private"},
                {"title": "Host local", "url": "http://localhost/private"},
                {"title": "Com credencial", "url": "https://key@example.org/private"},
                {"title": "Script", "url": "javascript:alert(0)"},
            ]
        }
    }
    with patch("requests.Session.get", return_value=response(body)) as get:
        hits = web_search.search("site:postgresql.org conexão")
        args = get.call_args
        assert args.args == (web_search.BRAVE_ENDPOINT,)
        assert args.kwargs["headers"]["X-Subscription-Token"] == "synthetic-key"
        assert args.kwargs["params"]["q"] == "site:postgresql.org conexão"
        assert args.kwargs["allow_redirects"] is False
        assert hits == [
            web_search.SearchHit(
                "PostgreSQL & SQL", "https://www.postgresql.org/docs/", "Conexões em português."
            )
        ]


@pytest.mark.parametrize(
    "bad",
    [
        "http://example.org:8888",
        "http://127.0.0.1/admin",
        "https://localhost",
        "http://me@localhost:8888",
        "http://127.0.0.1:99999",
    ],
)
def test_untrusted_search_endpoints_never_receive_queries(monkeypatch, bad):
    monkeypatch.setenv("JARVIS_SEARXNG_URL", bad)
    with (
        patch("requests.Session.get") as get,
        pytest.raises(web_search.SearchUnavailable, match="JARVIS_SEARXNG_URL"),
    ):
        web_search.search("PostgreSQL")
    assert not get.called


@pytest.mark.parametrize(
    ("body", "status", "reason"),
    [
        ({}, 403, "Habilite o formato JSON"),
        ({}, 429, "limite de consultas"),
        ({}, 307, "HTTP 307"),
        ({}, 502, "HTTP 502"),
        (b"invalido", 200, "ilegível"),
        ({"malformed": 1}, 200, "formato esperado"),
        ({"unresponsive_engines": [["engine", "timeout"]], "results": []}, 200, "não concluíram"),
        (b"x" * (web_search.MAX_RESPONSE + 1), 200, "limite"),
    ],
)
def test_explicit_failures(body, status, reason):
    with (
        patch("requests.Session.get", return_value=response(body, status)),
        pytest.raises(web_search.SearchUnavailable, match=reason),
    ):
        web_search.search("assunto de teste")


def test_empty_cache_no_fake_answer():
    with patch("requests.Session.get", return_value=response({"results": []})):
        assert web_search.format_results(web_search.search("assunto raro")).startswith(
            "Não encontrei fontes"
        )
    with (
        patch("requests.Session.get", side_effect=requests.Timeout()),
        pytest.raises(web_search.SearchUnavailable, match="infra/search/start.py"),
    ):
        web_search.search("assunto de teste")


def test_plain_text_fits_whatsapp_and_preserves_urls():
    hits = [
        web_search.SearchHit("t" * 125, "https://example.org/" + ("a" * 700), "x" * 240)
        for _ in range(4)
    ]
    result = web_search.format_results(hits)
    assert valid_text(result, 4000) and result.endswith(hits[0].url)


@contextmanager
def searx_fixture():
    calls = []

    class API(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            calls.append(self.path)
            data = json.dumps(
                {
                    "results": [
                        {
                            "title": "Documentation",
                            "content": "Synthetic",
                            "url": "https://docs.python.org/3/",
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), API)
    except PermissionError:
        pytest.skip("O ambiente bloqueia escuta em loopback.")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_real_http_roundtrip_to_configured_loopback(monkeypatch):
    with searx_fixture() as (base_url, calls):
        monkeypatch.setenv("JARVIS_SEARXNG_URL", base_url)
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        hits = web_search.search("documentação oficial do Python")
    assert hits == [
        web_search.SearchHit("Documentation", "https://docs.python.org/3/", "Synthetic")
    ]
    assert len(calls) == 1
    path = urlsplit(calls[0])
    assert path.path == "/search" and parse_qs(path.query)["q"] == [
        "documentação oficial do Python"
    ]
