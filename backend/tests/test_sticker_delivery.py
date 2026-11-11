from datetime import timedelta
from io import BytesIO
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from neonize.client import NewClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_whatsapp_gateway import BOT, OTHER, OWNER, msg

from jarvis.adapters.persistence.db import utcnow
from jarvis.adapters.whatsapp.gateway import Gateway
from jarvis.adapters.whatsapp.profile import Profile
from jarvis.entrypoints.worker import process_one
from jarvis.modules.messaging import queue
from jarvis.modules.messaging.models import DeliveryAttempt, Inbound, Outbound, StickerReply
from jarvis.modules.stickers.catalog import CATALOG, MAX_BYTES, sticker_bytes
from jarvis.modules.stickers.service import handle_sticker


@pytest.fixture
def channel(engine, tmp_path):
    profile = Profile(tmp_path)
    profile.connected(BOT)
    assert profile.verify(OWNER, profile.challenge(OWNER)["phrase"])
    client = Mock()
    # O próprio construtor do SDK monta o protobuf; somente upload e transmissão são simulados.
    client.build_sticker_message = MethodType(NewClient.build_sticker_message, client)
    client.upload.side_effect = lambda data: SimpleNamespace(
        url="https://cdn.example.invalid/synthetic",
        DirectPath="/synthetic",
        FileEncSHA256=b"0" * 32,
        FileLength=len(data),
        FileSHA256=b"1" * 32,
        MediaKey=b"2" * 32,
    )
    client.send_message.return_value = SimpleNamespace(ID="synthetic-sticker-receipt")
    return Gateway(engine, profile, client)


def enqueue(channel, text="Jarvis, me manda uma figurinha de bom dia!", mid="m-1"):
    channel.receive(None, msg(OWNER, text, mid))
    turn = queue.claim_turn(channel.engine)
    assert turn is not None
    assert queue.apply_command(channel.engine, turn, lambda db: handle_sticker(db, turn))
    return turn


def test_all_bundled_stickers_are_valid_distinct_bounded_webp():
    seen = set()
    for item in CATALOG:
        raw = sticker_bytes(item.key)
        assert len(raw) < MAX_BYTES
        with Image.open(BytesIO(raw)) as pic:
            assert pic.format == "WEBP" and pic.size == (512, 512) and "A" in pic.getbands()
            assert getattr(pic, "n_frames", 1) == 1 and pic.getchannel("A").getpixel((0, 0)) == 0
        seen.add(raw)
    assert len(seen) == len(CATALOG)
    with pytest.raises(ValueError):
        sticker_bytes("../../etc/passwd")


@pytest.mark.parametrize("voice", [False, True])
def test_authenticated_command_runs_through_worker_and_sdk_once(engine, signed, channel, voice):
    channel.receive(None, msg(OTHER, "mande uma figurinha de bom dia!", "spurious"))
    assert not process_one(engine, Mock(spec=[]))
    request = "Jarvis, me manda uma figurinha de bom dia!"
    command = msg(OWNER, "" if voice else request, "sticker-1")
    if voice:
        command.Message.audioMessage.fileLength = 24
        command.Message.audioMessage.seconds = 3
        channel.client.download_any.return_value = b"synthetic audio"
    channel.receive(None, command)
    with patch("jarvis.adapters.ai.speech.transcribe", return_value=request) as transcribe:
        assert process_one(engine, Mock(spec=[]))
        assert transcribe.call_count == int(voice)
    assert channel.send_next()
    destination, payload = channel.client.send_message.call_args.args
    assert destination.User == OWNER[1:]
    assert payload.HasField("stickerMessage") and not payload.HasField("imageMessage")
    sticker = payload.stickerMessage
    assert sticker.mimetype == "image/webp" and not sticker.isAnimated
    assert sticker.fileLength == len(sticker_bytes("bom-dia"))
    channel.client.upload.assert_called_once_with(sticker_bytes("bom-dia"))
    channel.receive(None, command)
    assert not channel.send_next() and not process_one(engine, Mock(spec=[]))
    with Session(engine) as db:
        reply = db.scalars(select(Outbound)).one()
        assert reply.state == "accepted" and reply.external_id == "synthetic-sticker-receipt"
        assert db.scalars(select(DeliveryAttempt)).one().outcome == "accepted"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Me envie um sticker", "combinado"),
        ("Mande a figurinha boa noite para mim", "boa-noite"),
        ("Manda pra mim uma figurinha de obrigado", "valeu"),
        ("Manda a figurinha de risada", "risada"),
        ("Me manda a figurinha de comemoração", "boa"),
        ("Mande uma figurinha de um dragão", None),
        ("Quais figurinhas você tem?", None),
        ("Manda uma figurinha de bom dia pra Gabriela", None),
    ],
)
def test_explicit_selection_and_unknown_requests_do_not_guess(engine, channel, raw, expected):
    turn = enqueue(channel, raw)
    with Session(engine) as db:
        selected = db.get(StickerReply, turn.id)
        if expected:
            assert selected and selected.sticker_key == expected
        else:
            assert selected is None and "Combinado" in db.get_one(Outbound, turn.id).text
    assert channel.send_next()
    assert channel.client.upload.call_count == (1 if expected else 0)


def test_model_text_and_inbound_forward_never_select_media(engine, channel):
    forwarded = msg(OWNER, "", "forwarded")
    forwarded.Message.extendedTextMessage.text = "me mande uma figurinha"
    forwarded.Message.extendedTextMessage.contextInfo.isForwarded = True
    channel.receive(None, forwarded)
    turn = queue.claim_turn(engine)
    assert turn and turn.kind == "notice"
    assert queue.complete_turn(engine, turn, "[Figurinha · Bom dia]")
    assert channel.send_next()
    channel.client.upload.assert_not_called()
    assert channel.client.send_message.call_args.args[1] == "[Figurinha · Bom dia]"


def test_upload_can_retry_and_uncertain_send_is_never_repeated(engine, channel):
    turn = enqueue(channel)
    channel.client.upload.side_effect = ConnectionError
    assert channel.send_next()
    channel.client.send_message.assert_not_called()
    with Session(engine) as db, db.begin():
        reply = db.get_one(Outbound, turn.id)
        assert reply.state == "ready" and reply.attempts == 1
        reply.available_at = utcnow() - timedelta(seconds=1)
    channel.client.upload.side_effect = None
    channel.client.upload.return_value = SimpleNamespace(
        url="https://cdn.example.invalid/sticker",
        DirectPath="/sticker",
        FileEncSHA256=b"x",
        FileLength=123,
        FileSHA256=b"x",
        MediaKey=b"x",
    )
    channel.client.send_message.side_effect = TimeoutError
    assert channel.send_next()
    assert not channel.send_next()
    channel.client.send_message.assert_called_once()
    with Session(engine) as db:
        assert db.get_one(Outbound, turn.id).state == "uncertain"


def test_change_of_binding_during_upload_prevents_sending(engine, channel):
    turn = enqueue(channel)
    build = channel.client.build_sticker_message

    def disconnected(*args, **kwargs):
        packet = build(*args, **kwargs)
        channel.profile.disconnected()
        return packet

    channel.client.build_sticker_message = disconnected
    assert channel.send_next()
    channel.client.send_message.assert_not_called()
    with Session(engine) as db:
        assert db.get_one(Outbound, turn.id).state == "ready"


def test_unavailable_asset_becomes_truthful_text_on_the_same_delivery(engine, channel):
    turn = enqueue(channel)
    with patch("jarvis.adapters.whatsapp.gateway.sticker_bytes", side_effect=FileNotFoundError):
        assert channel.send_next()
    channel.client.upload.assert_not_called()
    payload = channel.client.send_message.call_args.args[1]
    assert "indisponível" in payload
    with Session(engine) as db:
        output = db.get_one(Outbound, turn.id)
        assert output.state == "accepted" and output.text == payload
        assert db.get_one(Inbound, turn.id).state == "completed"
        assert db.get(StickerReply, turn.id) is None


def test_expired_lease_after_upload_does_not_send_to_the_chat(engine, channel):
    turn = enqueue(channel)
    build = channel.client.build_sticker_message

    def slow(*args, **kwargs):
        packet = build(*args, **kwargs)
        with Session(engine) as db, db.begin():
            db.get_one(Outbound, turn.id).lease_until = utcnow() - timedelta(seconds=1)
        return packet

    channel.client.build_sticker_message = slow
    assert channel.send_next()
    channel.client.send_message.assert_not_called()
    with Session(engine) as db:
        assert db.get_one(Outbound, turn.id).state == "ready"
