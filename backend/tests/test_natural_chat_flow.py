"""Aceitação do diálogo privado atravessando gateway, transcrição, IA e entrega."""

import json
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from neonize.client import NewClient
from test_whatsapp_gateway import BOT, OWNER, msg

from jarvis.adapters.ai.ollama import OllamaChat
from jarvis.adapters.whatsapp.gateway import Gateway
from jarvis.adapters.whatsapp.profile import Profile
from jarvis.entrypoints.worker import process_one
from jarvis.modules.stickers.catalog import sticker_bytes

VICTORY = "Passei na prova! Estou feliz demais!"
CONGRATS = "Aí sim! Qual foi a parte mais difícil?"


@pytest.fixture
def channel(engine, signed, tmp_path):
    profile = Profile(tmp_path)
    profile.connected(BOT)
    assert profile.verify(OWNER, profile.challenge(OWNER)["phrase"])
    client = Mock()
    client.build_sticker_message = MethodType(NewClient.build_sticker_message, client)
    client.upload.side_effect = lambda data: SimpleNamespace(
        url="https://example.invalid/media",
        DirectPath="/synthetic",
        FileLength=len(data),
        MediaKey=b"0" * 32,
        FileEncSHA256=b"1" * 32,
        FileSHA256=b"2" * 32,
    )
    client.send_message.return_value = SimpleNamespace(ID="id-aceito")
    return Gateway(engine, profile, client)


class ModelTransport:
    """Use o contrato HTTP do Ollama. Só o servidor de inferência é simulado."""

    def __init__(self, monkeypatch):
        self.model = OllamaChat("http://127.0.0.1:11434", "modelo-local")
        self.calls = []
        self.text = CONGRATS
        self.sticker: str | None = "boa"

        def generate(_method, _path, body, _headers):
            data = json.loads(body)
            self.calls.append(data)
            content = self.text
            if data.get("format"):
                content = json.dumps({"text": self.text, "sticker_key": self.sticker})
            response = connection.getresponse.return_value
            response.status = 200
            response.read.return_value = json.dumps(
                {"done": True, "message": {"role": "assistant", "content": content}}
            ).encode()

        connection = Mock()
        connection.request.side_effect = generate
        monkeypatch.setattr(
            "jarvis.adapters.ai.ollama.http.client.HTTPConnection", lambda *a, **k: connection
        )


def exchange(channel, model, text, mid):
    channel.receive(None, msg(OWNER, text, mid))
    assert process_one(channel.engine, model.model)
    assert channel.send_next()
    return channel.client.send_message.call_args.args[1]


@pytest.mark.parametrize("voice", [False, True])
def test_model_can_follow_accepted_conversation_with_one_separate_sticker(
    channel, monkeypatch, voice
):
    ai = ModelTransport(monkeypatch)
    event = msg(OWNER, "" if voice else VICTORY, "conquista")
    if voice:
        event.Message.audioMessage.fileLength = 32
        event.Message.audioMessage.seconds = 4
        channel.client.download_any.return_value = b"audio sintetico de quatro segundos"
    channel.receive(None, event)
    with patch("jarvis.adapters.ai.speech.transcribe", return_value=VICTORY) as speech:
        assert process_one(channel.engine, ai.model)
        assert speech.call_count == int(voice)
    assert len(ai.calls) == 1 and ai.calls[0]["messages"][-1]["content"] == VICTORY
    assert channel.send_next()
    channel.client.upload.assert_not_called()
    channel.client.send_message.assert_called_once()
    destination, text = channel.client.send_message.call_args.args
    assert destination.User == OWNER[1:] and text == CONGRATS
    assert channel.send_next()
    next_destination, media = channel.client.send_message.call_args.args
    assert next_destination == destination and media.HasField("stickerMessage")
    assert media.stickerMessage.mimetype == "image/webp"
    channel.client.upload.assert_called_once_with(sticker_bytes("boa"))
    channel.receive(None, event)
    assert not process_one(channel.engine, ai.model) and not channel.send_next()
    assert len(ai.calls) == 1 and channel.client.send_message.call_count == 2


def test_serious_context_and_tool_results_stay_in_text(channel, monkeypatch):
    ai = ModelTransport(monkeypatch)
    ai.text = "Que perda, Álvaro. Quer me contar como ele era?"
    message = exchange(channel, ai, "Meu pai morreu ontem. Hoje estou de luto.", "luto")
    assert message == ai.text and not channel.send_next()
    assert len(ai.calls) == 1
    message = exchange(channel, ai, "Gastei 35 reais no mercado.", "despesa")
    assert "35,00" in message and "mercado" in message.casefold()
    assert len(ai.calls) == 1 and not channel.send_next()
    channel.client.upload.assert_not_called()


def test_pause_persists_and_explicit_sticker_still_works(channel, monkeypatch):
    ai = ModelTransport(monkeypatch)
    ack = exchange(channel, ai, "Sem figurinhas por enquanto", "pausa")
    assert isinstance(ack, str) and "figurinha" in ack.casefold()
    assert not ai.calls and not channel.send_next()

    channel.engine.dispose()
    assert exchange(channel, ai, VICTORY, "respeitar-pausa") == CONGRATS
    assert "Sem figurinhas por enquanto" in [
        item["content"] for item in ai.calls[-1]["messages"] if item["role"] == "user"
    ]
    assert not channel.send_next()
    channel.client.upload.assert_not_called()

    sticker = exchange(channel, ai, "Me manda uma figurinha de bom dia!", "explicita")
    assert sticker.HasField("stickerMessage") and not channel.send_next()
    channel.client.upload.assert_called_once_with(sticker_bytes("bom-dia"))
    assert len(ai.calls) == 1


def test_no_optional_sticker_if_text_has_no_whatsapp_acceptance(channel, monkeypatch):
    ai = ModelTransport(monkeypatch)
    channel.client.send_message.return_value = SimpleNamespace(ID="")
    exchange(channel, ai, VICTORY, "aceite-incerto")
    assert len(ai.calls) == 1
    assert not channel.send_next()
    channel.client.upload.assert_not_called()
    channel.client.send_message.assert_called_once()


def test_ordinary_conversation_can_stay_in_words_and_preserve_paragraphs(channel, monkeypatch):
    ai = ModelTransport(monkeypatch)
    ai.text = "Eu começaria pelo café da manhã.\n\nDepois vocês podem escolher o passeio juntos."
    ai.sticker = None
    assert exchange(channel, ai, "O que a gente podia inventar no domingo?", "ideias") == ai.text
    assert len(ai.calls) == 1
    assert not channel.send_next()
    channel.client.upload.assert_not_called()


def test_new_message_suppresses_reaction_to_previous_topic(channel, monkeypatch):
    ai = ModelTransport(monkeypatch)
    assert exchange(channel, ai, VICTORY, "comemorar") == CONGRATS
    ai.text = "Que tristeza, Álvaro. Estou aqui pra te ouvir."
    message = exchange(channel, ai, "Mudando de assunto, meu tio faleceu.", "noticia")
    assert message == ai.text
    assert not channel.send_next()
    channel.client.upload.assert_not_called()
