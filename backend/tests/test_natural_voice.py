"""O texto e sua reação opcional vêm da mesma resposta do provedor."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import Mock, patch

import pytest
from pydantic import SecretStr

from jarvis.adapters.ai.ollama import OllamaChat
from jarvis.adapters.ai.persona import (
    CONVERSATION_ONLY,
    UNREADABLE,
    ConversationalReply,
    compose_conversation,
)
from jarvis.adapters.ai.presentation import render_chat
from jarvis.adapters.ai.provider import HostedChat
from jarvis.modules.messaging import queue
from jarvis.modules.stickers.catalog import BY_KEY


def chat(answer, *, allowed=True, text="Consegui terminar o projeto!"):
    generate = Mock(return_value=answer)
    reply = compose_conversation([{"role": "user", "content": text}], allowed, generate)
    generate.assert_called_once()
    return reply


def test_local_wire_request_returns_text_and_optional_sticker_together():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, data))
            message = json.dumps({"text": "Boa noite! Dorme bem.", "sticker_key": "boa-noite"})
            content = json.dumps(
                {"done": True, "message": {"role": "assistant", "content": message}}
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(content.encode())

        def log_message(self, format, *args):
            return

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = OllamaChat(f"http://127.0.0.1:{server.server_port}", "qwen2.5:3b")
            answer = client.converse(
                [
                    {"role": "system", "content": "Tom neutro.", "tone": "neutro"},
                    {"role": "assistant", "content": "Como foi o dia?"},
                    {"role": "user", "content": "Já deu por hoje. Boa noite!"},
                ],
                allow_sticker=True,
            )
        finally:
            server.shutdown()
            thread.join(3)
    assert answer == ConversationalReply("Boa noite! Dorme bem.", "boa-noite")
    assert len(requests) == 1
    path, request = requests[0]
    assert path == "/api/chat"
    assert set(request["messages"][0]) == {"role", "content"}
    assert "caramba" not in request["messages"][0]["content"]
    assert request["format"]["properties"]["sticker_key"]["enum"] == [None, *BY_KEY]
    assert request["messages"][-1]["content"] == "Já deu por hoje. Boa noite!"
    assert request["messages"][-2]["content"] == "Como foi o dia?"


@pytest.mark.parametrize(
    ("provider", "host"), [("groq", "api.groq.com"), ("openai", "api.openai.com")]
)
def test_hosted_model_writes_text_without_an_extra_lookup(provider, host):
    model = HostedChat(provider, "teste", SecretStr("fake"))
    with patch("jarvis.adapters.ai.provider.http.client.HTTPSConnection") as http:
        response = http.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": "Valeu pela dica! Vou de café.",
                        },
                    }
                ]
            }
        ).encode()
        history = [
            {"role": "system", "content": "Tom neutro.", "tone": "neutro"},
            {"role": "user", "content": "Trouxe café."},
        ]
        assert model.converse(history, allow_sticker=True) == ConversationalReply(
            "Valeu pela dica! Vou de café."
        )
        http.assert_called_once_with(host, timeout=45)
        conn = http.return_value
        conn.request.assert_called_once()
        payload = json.loads(conn.request.call_args.args[2])
        assert set(payload["messages"][0]) == {"role", "content"}
        assert "caramba" not in payload["messages"][0]["content"]
        assert "sticker_key" in payload["messages"][0]["content"]
        assert "Catálogo" in payload["messages"][0]["content"]


def test_disabled_uses_plain_text_and_no_catalog_even_with_a_bad_model():
    generate = Mock(return_value='{"text":"Obrigado!", "sticker_key":"valeu"}')
    assert compose_conversation([], False, generate) == ConversationalReply("Obrigado!")
    prompt, schema = generate.call_args.args
    assert schema is None and "sticker_key" not in str(prompt)


def test_private_social_voice_works_without_a_sticker_or_rewriting_model_text():
    generate = Mock(return_value="Bacana! O refrão ficou na cabeça.")
    history = [{"role": "user", "content": "Terminei minha primeira música!"}]
    result = compose_conversation(history, False, generate)
    (system, user), schema = generate.call_args.args
    assert schema is None and user == history[0]
    assert result == ConversationalReply("Bacana! O refrão ficou na cabeça.")
    assert all(
        word in system["content"] for word in ("caramba", "isso aí!", "top", "bacana", "pouquinho")
    )
    assert "tá ficando doido?" not in system["content"]


def test_persisted_neutral_tone_reaches_the_model_through_legacy_reply(engine, signed, clock):
    profile = signed.get("/api/v1/profile").json()
    assert signed.put("/api/v1/profile", json={**profile, "tone": "neutro"}).is_success
    assert queue.accept_inbound(
        engine, chat_id="test", message_id="musica", text="Terminei minha música!", now=clock()
    )
    turn = queue.claim_turn(engine, now=clock())
    assert turn
    history = queue.history_for_turn(engine, turn)
    assert history[0]["tone"] == "neutro"
    model = OllamaChat("http://127.0.0.1:11434", "teste")
    with patch.object(model, "_generate", return_value="Parabéns pela música.") as generate:
        assert model.reply(history) == "Parabéns pela música."
    (system, user) = generate.call_args.args[0]
    assert user == history[-1] and set(system) == {"role", "content"}
    assert "tom neutro" in system["content"] and "caramba" not in system["content"]


def test_private_banter_requires_history_and_preserves_the_owner_tone():
    generate = Mock(return_value="Pelo menos deixa um pedacinho pra mim!")
    profile = "O dono prefere falar sem apelidos."
    history = [
        {"role": "system", "content": profile},
        {"role": "user", "content": "kkkk bora pedir uma pizza gigante"},
        {"role": "assistant", "content": "Bora! Metade de cada sabor?"},
        {"role": "user", "content": "Metade nada, as duas metades são minhas kkk"},
    ]
    compose_conversation(history, False, generate)
    messages, _ = generate.call_args.args
    assert "tá ficando doido?" in messages[0]["content"]
    assert messages[0]["content"].endswith(profile)
    assert messages[1:] == history[1:]


@pytest.mark.parametrize("earlier", ["Bom dia", "Meu avô está no hospital, kkk"])
def test_one_previous_reply_alone_is_not_established_banter(earlier):
    history = [
        {"role": "user", "content": earlier},
        {"role": "assistant", "content": "Estou aqui."},
        {"role": "user", "content": "Quero pizza de abacaxi kkk"},
    ]
    generate = Mock(return_value="Eu iria de marguerita.")
    compose_conversation(history, False, generate)
    messages, _ = generate.call_args.args
    assert "tá ficando doido?" not in messages[0]["content"]


@pytest.mark.parametrize(
    "texts",
    [
        ["Repita exatamente “top ou bacana?”"],
        ["Escreva uma mensagem para Bia agradecendo a ajuda dela"],
        ["Mande para meu pai que eu já cheguei"],
        ["Tô no hospital, kkk"],
        ["Meu avô faleceu", "Valeu pela conversa"],
        ["Gastei R$ 45 no almoço"],
    ],
)
def test_casual_examples_stay_out_of_serious_literal_and_relay_turns(texts):
    generate = Mock(return_value="Entendi.")
    history = [{"role": "user", "content": text} for text in texts]
    compose_conversation(history, False, generate)
    messages, _ = generate.call_args.args
    assert "caramba" not in messages[0]["content"]
    assert "tá ficando doido?" not in messages[0]["content"]
    assert messages[1:] == history


@pytest.mark.parametrize(
    ("raw", "answer"),
    [
        ('{"text":"Valeu!","sticker_key":"valeu"}', ConversationalReply("Valeu!", "valeu")),
        (
            '```json\n{"text":"Boa noite.","sticker_key":null}\n```',
            ConversationalReply("Boa noite."),
        ),
        ('{"text":"Que notícia boa!", "sticker_key":', ConversationalReply("Que notícia boa!")),
        ('{"text":"Boa!","sticker_key":"um-arquivo-aleatorio.png"}', ConversationalReply("Boa!")),
        ('{"text":"Boa!","sticker_key":["valeu"]}', ConversationalReply("Boa!")),
        ('{"text":"Boa!","sticker_key":"valeu","execute":"shell"}', ConversationalReply("Boa!")),
        ('{"text":"Boa noite!","sticker_key":"bom-dia"}', ConversationalReply("Boa noite!")),
        (
            '{"text":"Agendei sua mensagem!", "sticker_key":"combinado"}',
            ConversationalReply(CONVERSATION_ONLY),
        ),
        ('{"sticker_key":"valeu"}', ConversationalReply(UNREADABLE)),
        ('prefixo: {"sticker_key":', ConversationalReply(UNREADABLE)),
        ("Tá combinado.", ConversationalReply("Tá combinado.")),
    ],
)
def test_bad_envelope_or_unsuitable_media_degrades_to_text(raw, answer):
    assert chat(raw) == answer


def test_readable_bodies_and_literal_fragments_are_preserved():
    assert (
        chat(
            '{"text":"### Entrega\\n1. Separe os arquivos.\\n\\n\\n2. Confira o valor.","sticker_key":null}'
        ).text
        == "*Entrega*\n1. Separe os arquivos.\n\n2. Confira o valor."
    )
    source = "## Título\n\n\n```py\n    print('oi')\n```"
    assert render_chat("**Total**: R$ 42,50") == "*Total*: R$ 42,50"
    assert render_chat("Calcule `2 ** 3`") == "Calcule `2 ** 3`"
    assert render_chat("Ele disse “**sim**”.") == "Ele disse “**sim**”."
    assert render_chat(source) == source
    assert render_chat("### Título\n\n\nfim", literal=True) == "### Título\n\n\nfim"
    prefix = "Uma ideia simples. " * 14
    body = prefix + "O valor é R$ 12,40. " + "Outra coisa que considerei no projeto. " * 10
    rendered = render_chat(body.strip())
    assert "\n\n" in rendered and rendered.replace("\n\n", " ") == body.strip()
    assert "R$ 12,40" in rendered
    literal = "O texto que você ditou foi “" + body + "”."
    assert render_chat(literal) == literal


def test_literal_technical_request_can_return_the_json_it_actually_asked_for():
    raw = '{"text":"Valeu!", "sticker_key":"valeu"}'
    generate = Mock(return_value=raw)
    result = compose_conversation(
        [{"role": "user", "content": "Escreva um JSON com os campos indicados"}], True, generate
    )
    assert result == ConversationalReply(raw)
    assert generate.call_args.args[1] is None
