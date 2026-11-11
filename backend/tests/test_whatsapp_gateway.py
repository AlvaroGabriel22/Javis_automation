from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from neonize.events import MessageEv
from neonize.utils.jid import build_jid

from jarvis.adapters.whatsapp import ipc
from jarvis.adapters.whatsapp.gateway import Gateway, private_sender
from jarvis.adapters.whatsapp.profile import Profile
from jarvis.entrypoints.worker import process_one
from jarvis.modules.errors import Problem
from jarvis.modules.messaging import queue

OWNER = "+5592000000001"
BOT = "+5592000000002"
OTHER = "+5592000000003"
API = "/api/v1/whatsapp"


def msg(phone=OWNER, text="Oi", mid="m-1"):
    event = MessageEv()
    event.Info.ID = mid
    event.Info.MessageSource.Chat.CopyFrom(build_jid(phone[1:]))
    event.Info.MessageSource.Sender.CopyFrom(build_jid(phone[1:]))
    event.Message.conversation = text
    return event


def test_expiry_account_scope_and_private_files(tmp_path):
    now = [0.0]
    profile = Profile(tmp_path, lambda: now[0])
    profile.pairing()
    profile.on_qr(b"synthetic qr")
    assert profile.snapshot()["qr"].startswith("data:image/png")
    assert not list(tmp_path.iterdir())
    now[0] += 65
    assert profile.snapshot()["qr"] is None
    profile.pairing()
    assert profile.restart.is_set()
    profile.connected(BOT)
    assert not profile.restart.is_set()
    with pytest.raises(Problem):
        profile.challenge(BOT)
    old = profile.challenge(OWNER)["phrase"]
    now[0] += 301
    assert not profile.verify(OWNER, old)
    assert profile.snapshot()["binding_status"] == "expired"
    valid = profile.challenge(OWNER)["phrase"]
    assert profile.snapshot()["binding_status"] == "waiting"
    assert not profile.verify(OTHER, valid)
    assert not profile.verify(OWNER, old)
    assert profile.verify(OWNER, valid)
    key = profile.route()
    assert key
    assert profile.binding_file.stat().st_mode & 0o077 == 0
    assert valid not in profile.binding_file.read_text()
    restored = Profile(tmp_path)
    assert restored.start.is_set()
    restored.connected(OTHER)
    assert restored.route() is None
    restored.connected(BOT)
    assert restored.route() == key
    restored.disconnected(logout=True)
    assert not restored.start.is_set()
    restored.connected(BOT)
    assert restored.route() is None


@pytest.mark.parametrize(
    "ignored", ["group", "self", "edited", "broadcast", "different_sender", "foreign_chat"]
)
def test_reject_group_self_edit_spoofed_or_third_party(ignored):
    event = msg()
    src = event.Info.MessageSource
    if ignored == "group":
        src.IsGroup = True
    if ignored == "self":
        src.IsFromMe = True
    if ignored == "edited":
        event.IsEdit = True
    if ignored == "broadcast":
        src.Chat.Server = "broadcast"
    if ignored == "different_sender":
        src.Sender.User = OTHER[1:]
    if ignored == "foreign_chat":
        src.Chat.Server = "newsletter"
    assert private_sender(event, Mock()) is None


def test_verified_lid_and_conflict():
    event = msg()
    src = event.Info.MessageSource
    src.Sender.User = src.Chat.User = "747474"
    src.Sender.Server = src.Chat.Server = "lid"
    src.SenderAlt.CopyFrom(build_jid(OWNER[1:]))
    resolve = Mock(side_effect=ValueError)
    assert private_sender(event, resolve) == OWNER
    src.Chat.User = "123123"
    assert private_sender(event, resolve) is None
    src.Chat.CopyFrom(build_jid(OTHER[1:]))
    assert private_sender(event, resolve) is None


def test_private_chat_identified_by_senders_alternate_lid():
    event = msg()
    src = event.Info.MessageSource
    src.Chat.User, src.Chat.Server = "747474", "lid"
    src.SenderAlt.CopyFrom(src.Chat)
    resolve = Mock(side_effect=ValueError)
    assert private_sender(event, resolve) == OWNER
    src.Chat.User = "123123"
    assert private_sender(event, resolve) is None


def test_pending_code_invalidated_after_reconnect(tmp_path):
    profile = Profile(tmp_path)
    profile.connected(BOT)
    phrase = profile.challenge(OWNER)["phrase"]
    profile.disconnected()
    profile.connected(BOT)
    assert not profile.verify(OWNER, phrase)
    assert profile.snapshot()["binding_status"] == "interrupted"
    assert profile.verify(OWNER, profile.challenge(OWNER)["phrase"])


def test_binding_feedback_requires_the_expected_sender_or_a_valid_code(engine, tmp_path):
    profile = Profile(tmp_path)
    profile.connected(BOT)
    phrase = profile.challenge(OWNER)["phrase"]
    gateway = Gateway(engine, profile, Mock())
    gateway.receive(None, msg(OTHER, "VINCULAR errado"))
    assert profile.snapshot()["binding_status"] == "waiting"
    invalid = msg(OWNER, phrase)
    invalid.Info.MessageSource.IsFromMe = True
    gateway.receive(None, invalid)
    assert profile.snapshot()["binding_status"] == "unverified_chat"
    assert profile.route() is None
    gateway.receive(None, msg(OWNER, "VINCULAR"))
    assert profile.snapshot()["binding_status"] == "wrong_code"
    gateway.receive(None, msg(OTHER, phrase))
    assert profile.snapshot()["observed_phone"] == OTHER
    profile.challenge(OWNER)
    assert profile.snapshot()["observed_phone"] is None
    assert not profile.binding_file.exists()
    assert queue.queue_status(engine)["inbox"] == {}


def test_panel_gateway_worker_outbox_with_real_protocol(
    engine, signed, client, tmp_path, monkeypatch
):
    profile = Profile(tmp_path)
    monkeypatch.setattr(
        ipc, "call", lambda op, phone="": ipc.dispatch(profile, {"op": op, "phone": phone})
    )
    assert signed.post(API + "/pairing").json()["state"] == "connecting"
    profile.on_qr(b"pairing demo")
    assert signed.get(API).json()["qr"].startswith("data:image/png")
    assert "qr" not in signed.get("/api/v1/status").json()
    profile.connected(BOT)
    proof = signed.post(API + "/binding", json={"phone": OWNER}).json()["phrase"]
    client_neon = Mock()
    client_neon.send_message.return_value = SimpleNamespace(ID="receipt-123")
    gateway = Gateway(engine, profile, client_neon)
    gateway.receive(None, msg(OTHER, proof))
    assert profile.route() is None
    assert signed.get(API).json()["binding_status"] == "different_number"
    gateway.receive(None, msg(OWNER, " \n" + proof.lower().replace(" ", "\t\xa0") + " "))
    status = signed.get(API).json()
    assert status["owner_phone"] == OWNER
    assert status["binding_status"] is None
    assert status["observed_phone"] is None
    gateway.receive(None, msg(OTHER, "vazar histórico"))
    event = msg(OWNER, "Me conte uma piada curta.", "user-message-42")
    gateway.receive(None, event)
    gateway.receive(None, event)
    assert queue.queue_status(engine)["inbox"] == {"pending": 1}
    model = Mock()
    model.reply.return_value = "Oi! Como posso ajudar?"
    assert process_one(engine, model)
    assert gateway.send_next()
    assert not gateway.send_next()
    target, body = client_neon.send_message.call_args.args
    assert (target.User, target.Server, body) == (
        OWNER[1:],
        "s.whatsapp.net",
        model.reply.return_value,
    )
    assert signed.get(API).json()["queues"]["outbox"] == {"accepted": 1}
    assert signed.delete(API + "/binding").json()["owner_phone"] is None
    gateway.receive(None, msg(OWNER, "ainda aí?", "after-revoke"))
    assert not process_one(engine, model)


def test_private_endpoints_reject_unauthenticated_and_csrf(client, signed):
    signed.headers.pop("X-CSRF-Token")
    assert signed.post(API + "/pairing").status_code == 403
    assert signed.post(API + "/binding", json={"phone": OWNER}).status_code == 403
    assert signed.delete(API + "/binding").status_code == 403
    client.cookies.clear()
    assert client.get(API).status_code == 401


def test_unknown_transport_result_is_not_retried(engine, tmp_path):
    profile = Profile(tmp_path)
    profile.connected(BOT)
    profile.verify(OWNER, profile.challenge(OWNER)["phrase"])
    client = Mock()
    client.send_message.side_effect = TimeoutError
    gateway = Gateway(engine, profile, client)
    gateway.receive(None, msg())
    model = Mock()
    model.reply.return_value = "Resposta para teste"
    assert process_one(engine, model)
    assert gateway.send_next()
    assert not gateway.send_next()
    assert client.send_message.call_count == 1
    assert queue.queue_status(engine)["outbox"] == {"uncertain": 1}


def test_definite_pre_send_failure_is_retriable(engine, tmp_path):
    from neonize.exc import SendMessageError

    profile = Profile(tmp_path)
    profile.connected(BOT)
    assert profile.verify(OWNER, profile.challenge(OWNER)["phrase"])
    client = Mock()
    client.send_message.side_effect = SendMessageError("client is nil")
    gateway = Gateway(engine, profile, client)
    gateway.receive(None, msg())
    model = Mock()
    model.reply.return_value = "Olá!"
    assert process_one(engine, model)
    assert gateway.send_next()
    assert queue.queue_status(engine)["outbox"] == {"ready": 1}
    assert not gateway.send_next()
