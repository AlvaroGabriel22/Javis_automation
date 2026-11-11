from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

import pytest
from neonize.proto.Neonize_pb2 import IsOnWhatsAppResponse
from neonize.utils.jid import build_jid
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import Schedule, ScheduleAttempt
from jarvis.adapters.whatsapp import ipc
from jarvis.adapters.whatsapp.contacts import replace_directory
from jarvis.adapters.whatsapp.gateway import Gateway
from jarvis.adapters.whatsapp.profile import Profile
from jarvis.entrypoints.worker import process_one
from jarvis.modules.messaging import queue
from jarvis.modules.messaging.models import Inbound, Outbound
from jarvis.modules.scheduling import dispatch

API = "/api/v1"
OWNER = "+5511000000011"
BOT = "+5511000000012"
TARGET = "+5511000000013"


@pytest.fixture
def channel(signed, engine, clock, tmp_path, monkeypatch):
    profile = Profile(tmp_path)
    profile.connected(BOT)
    profile.verify(OWNER, profile.challenge(OWNER)["phrase"])
    monkeypatch.setattr(ipc, "call", lambda op, **kw: ipc.dispatch(profile, {"op": op, **kw}))
    sdk = Mock()
    sdk.is_on_whatsapp.return_value = [
        SimpleNamespace(IsIn=True, Query=TARGET, JID=build_jid(TARGET[1:]))
    ]
    sdk.send_message.return_value = SimpleNamespace(ID="synthetic-receipt")
    response = signed.post(
        API + "/contacts",
        headers={"Idempotency-Key": "contact"},
        json={"name": "Lia", "phone": TARGET, "aliases": ["Lili"]},
    )
    assert response.status_code == 201, response.text
    contact_id = response.json()["id"]

    def create(recurrence="none", key="schedule"):
        data = {
            "contact_id": contact_id,
            "scheduled_at": (clock() + timedelta(minutes=1)).isoformat(),
            "text": "A sala mudou para o segundo andar.",
            "recurrence": recurrence,
        }
        result = signed.post(API + "/schedules", json=data, headers={"Idempotency-Key": key})
        assert result.status_code == 201, result.text
        return UUID(result.json()["id"])

    return Gateway(engine, profile, sdk), sdk, create


def test_due_while_model_busy_and_after_gateway_restart(channel, engine, clock, signed):
    gateway, sdk, create = channel
    sid = create("weekly")
    original = gateway.profile.route()
    assert original
    assert not gateway.send_scheduled(clock)
    queue.accept_inbound(
        engine,
        chat_id=original[0],
        message_id="conversation",
        text="Me conta uma história.",
        now=clock(),
    )
    entered, release = Event(), Event()
    model = Mock()

    def infer(_messages):
        entered.set()
        assert release.wait(5)
        return "Oi!"

    model.reply.side_effect = infer
    worker = Thread(target=process_one, args=(engine, model), kwargs={"clock": clock})
    worker.start()
    try:
        assert entered.wait(4)
        next_profile = Profile(gateway.profile.root)
        next_profile.connected(BOT)
        gateway = Gateway(engine, next_profile, sdk)
        clock.now += timedelta(minutes=1)
        assert gateway.send_scheduled(clock)
        assert not gateway.send_scheduled(clock)
    finally:
        release.set()
        worker.join(6)
    jid, text = sdk.send_message.call_args.args
    assert jid.User == TARGET[1:] and text == "A sala mudou para o segundo andar."
    with Session(engine) as db:
        assert db.get_one(Schedule, sid).status == "accepted"
        recurrence = db.scalar(select(Schedule).where(Schedule.status == "scheduled"))
        assert recurrence and recurrence.scheduled_at == clock() + timedelta(days=7)
        assert db.scalar(select(ScheduleAttempt.external_id)) == "synthetic-receipt"
        assert (
            db.scalar(select(Outbound.text).where(Outbound.text.contains("WhatsApp aceitou")))
            is not None
        )


def test_late_and_uncertain_do_not_send_again(channel, engine, clock, signed):
    gateway, sdk, create = channel
    sid = create()
    route = gateway.profile.route()
    assert route
    clock.now += timedelta(minutes=7)
    dispatch.recover(engine, route[0], clock())
    assert not gateway.send_scheduled(clock)
    with Session(engine) as db:
        assert db.get_one(Schedule, sid).status == "needs_review"
    new_time = clock() + timedelta(minutes=1)
    assert (
        signed.patch(
            API + f"/schedules/{sid}", json={"scheduled_at": new_time.isoformat()}
        ).status_code
        == 200
    )
    clock.now = new_time
    sdk.send_message.side_effect = TimeoutError
    assert gateway.send_scheduled(clock)
    assert not gateway.send_scheduled(clock)
    assert sdk.send_message.call_count == 1
    with Session(engine) as db:
        assert db.get_one(Schedule, sid).status == "uncertain"
    assert signed.delete(API + f"/schedules/{sid}").status_code == 409


def test_concurrent_claim_and_lost_receipt(channel, engine, clock):
    gateway, _, create = channel
    sid = create()
    route = gateway.profile.route()
    assert route
    clock.now += timedelta(minutes=1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: dispatch.claim(engine, route[0], clock()), range(2)))
    assert sum(job is not None for job in jobs) == 1
    clock.now += dispatch.IN_FLIGHT
    dispatch.recover(engine, route[0], clock())
    with Session(engine) as db:
        assert db.get_one(Schedule, sid).status == "uncertain"
    assert not gateway.send_scheduled(clock)


def test_replaced_binding_and_unregistered_number(channel, engine, clock):
    gateway, sdk, create = channel
    sid = create()
    clock.now += timedelta(minutes=1)
    sdk.is_on_whatsapp.return_value = [IsOnWhatsAppResponse(Query=TARGET, IsIn=False)]
    assert gateway.send_scheduled(clock)
    sdk.send_message.assert_not_called()
    with Session(engine) as db:
        assert db.get_one(Schedule, sid).status == "failed"
    create(key="new")
    gateway.profile.revoke()
    gateway.profile.verify(OWNER, gateway.profile.challenge(OWNER)["phrase"])
    clock.now += timedelta(minutes=1)
    assert not gateway.send_scheduled(clock)
    sdk.send_message.assert_not_called()


def test_directory_card_and_audio_after_verifying_sender(channel, engine, clock, signed):
    from neonize.events import MessageEv

    gateway, sdk, _ = channel
    route = gateway.profile.route()
    assert route
    sdk.contact.get_all_contacts.return_value = [
        SimpleNamespace(
            JID=build_jid(TARGET[1:]), Info=SimpleNamespace(FullName="Lia", FirstName="")
        )
    ]
    gateway.sync_contacts(route)
    assert signed.get(API + "/whatsapp/directory?q=Lia").json() == [
        {"phone": TARGET, "name": "Lia"}
    ]
    event = MessageEv()
    event.Info.ID = "voice"
    src = event.Info.MessageSource
    src.Chat.CopyFrom(build_jid(OWNER[1:]))
    src.Sender.CopyFrom(src.Chat)
    event.Message.audioMessage.fileLength = 4
    sdk.download_any.return_value = b"OggS"
    src.IsGroup = True
    gateway.receive(None, event)
    sdk.download_any.assert_not_called()
    src.IsGroup = False
    gateway.receive(None, event)
    gateway.receive(None, event)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Inbound)) == 1
        row = db.scalars(select(Inbound)).one()
        assert row.kind == "audio" and row.media == b"OggS"
        row.media = None
        row.text = "Conteúdo de teste"
        db.commit()
    assert signed.get(API + "/whatsapp/latest-transcript").json()["text"] == "Conteúdo de teste"
    gateway.profile.revoke()
    assert signed.get(API + "/whatsapp/latest-transcript").status_code == 404


def test_offline_schedules_require_explicit_activation(channel, clock, signed, monkeypatch):
    gateway, _, create = channel
    online = ipc.call
    monkeypatch.setattr(ipc, "call", lambda op, **kw: {"route_key": None})
    sid = create()
    monkeypatch.setattr(ipc, "call", online)
    clock.now += timedelta(minutes=1)
    assert not gateway.send_scheduled(clock)
    instant = clock() + timedelta(minutes=1)
    assert signed.patch(
        API + f"/schedules/{sid}", json={"scheduled_at": instant.isoformat()}
    ).json()["armed"]
    clock.now = instant
    assert gateway.send_scheduled(clock)


def test_independent_destinations_and_preflight_retry(channel, clock, engine, signed):
    gateway, sdk, create = channel
    sid = create()
    second_phone = "+5511000000022"
    result = signed.post(
        API + "/contacts",
        headers={"Idempotency-Key": "new-person"},
        json={"name": "Renato", "phone": second_phone},
    )
    second_due = clock() + timedelta(minutes=3)
    data = {
        "contact_id": result.json()["id"],
        "text": "Traga o livro azul.",
        "scheduled_at": second_due.isoformat(),
    }
    assert (
        signed.post(
            API + "/schedules", headers={"Idempotency-Key": "second"}, json=data
        ).status_code
        == 201
    )
    clock.now += timedelta(minutes=1)
    sdk.is_on_whatsapp.side_effect = TimeoutError
    assert gateway.send_scheduled(clock)
    assert not gateway.send_scheduled(clock)
    sdk.send_message.assert_not_called()

    def registered(phone):
        return [SimpleNamespace(IsIn=True, Query=phone, JID=build_jid(phone[1:]))]

    sdk.is_on_whatsapp.side_effect = registered
    clock.now += timedelta(seconds=5)
    assert gateway.send_scheduled(clock)
    clock.now = second_due
    assert gateway.send_scheduled(clock)
    assert [(args[0].User, args[1]) for args, _ in sdk.send_message.call_args_list] == [
        (TARGET[1:], "A sala mudou para o segundo andar."),
        (second_phone[1:], "Traga o livro azul."),
    ]
    with Session(engine) as db:
        assert db.get_one(Schedule, sid).attempts == 2


def test_old_schedules_stay_unarmed_after_upgrade(tmp_path):
    from pathlib import Path
    from uuid import uuid4

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from jarvis.adapters.persistence.db import new_engine, utcnow

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = new_engine("sqlite:///" + str(tmp_path / "upgrade.sqlite3"))
    cid, sid = uuid4(), uuid4()
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "0002_s01")
            conn.execute(text("INSERT INTO owners(id,name) VALUES(1,'Teste')"))
            conn.execute(
                text("INSERT INTO contacts(id,owner_id,name,phone) VALUES(:cid,1,'Teste',:phone)"),
                {"cid": cid.hex, "phone": TARGET},
            )
            conn.execute(
                text(
                    "INSERT INTO schedules(id,owner_id,contact_id,text,scheduled_at,created_at,status) VALUES(:sid,1,:cid,'Rascunho',:dt,:dt,'scheduled')"
                ),
                {"sid": sid.hex, "cid": cid.hex, "dt": "2031-01-01 12:00:00"},
            )
            command.upgrade(cfg, "head")
        with Session(engine) as db:
            row = db.get_one(Schedule, sid)
            assert (row.armed, row.recurrence, row.status, row.text) == (
                False,
                "none",
                "scheduled",
                "Rascunho",
            )
        assert dispatch.claim(engine, "new-route", utcnow()) is None
    finally:
        engine.dispose()


def test_saved_contact_uses_the_paired_devices_exact_mapping(channel, engine, clock):
    gateway, sdk, create = channel
    route = gateway.profile.route()
    assert route
    replace_directory(engine, route[0], {TARGET: "Contato"})
    lid = build_jid("74999904111", "lid")
    sdk.get_lid_from_pn.return_value = lid
    sdk.get_pn_from_lid.return_value = build_jid(TARGET[1:])
    sid = create()
    clock.now += timedelta(minutes=1)
    assert gateway.send_scheduled(clock)
    sdk.is_on_whatsapp.assert_not_called()
    sdk.get_lid_from_pn.assert_called_once_with(build_jid(TARGET[1:]))
    sdk.get_pn_from_lid.assert_called_once_with(lid)
    assert sdk.send_message.call_args.args[0] == lid
    with Session(engine) as db:
        assert db.get_one(Schedule, sid).status == "accepted"


@pytest.mark.parametrize(
    "server,echo",
    [
        ("lid", TARGET),
        ("lid", ""),
        ("s.whatsapp.net", ""),
        ("lid", TARGET[1:] + "@c.us"),
    ],
)
def test_lookup_accepts_registered_private_addresses(channel, clock, server, echo):
    gateway, sdk, create = channel
    address = build_jid("847777799" if server == "lid" else TARGET[1:], server)
    sdk.is_on_whatsapp.return_value = [IsOnWhatsAppResponse(Query=echo, IsIn=True, JID=address)]
    sdk.get_pn_from_lid.return_value = build_jid(TARGET[1:])
    create()
    clock.now += timedelta(minutes=1)
    assert gateway.send_scheduled(clock)
    sdk.send_message.assert_called_once_with(address, "A sala mudou para o segundo andar.")


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [IsOnWhatsAppResponse(Query=OWNER, IsIn=True, JID=build_jid(TARGET[1:]))],
        [IsOnWhatsAppResponse(IsIn=True, JID=build_jid(OWNER[1:]))],
        [IsOnWhatsAppResponse(IsIn=True, JID=build_jid("123", "lid"))],
        [IsOnWhatsAppResponse(IsIn=True, Query=TARGET, JID=build_jid("123", "g.us"))],
        [IsOnWhatsAppResponse(IsIn=True, Query=TARGET, JID=build_jid(TARGET[1:]))] * 2,
    ],
)
def test_inconclusive_lookup_never_sends_even_with_unrelated_saved_contact(
    channel, engine, clock, rows
):
    gateway, sdk, create = channel
    route = gateway.profile.route()
    assert route
    replace_directory(engine, route[0], {TARGET: "Contato"})
    sdk.get_lid_from_pn.return_value = build_jid("123", "lid")
    sdk.get_pn_from_lid.return_value = build_jid(OWNER[1:])
    sdk.is_on_whatsapp.return_value = rows
    sid = create()
    for seconds in (60, 5, 10, 20):
        clock.now += timedelta(seconds=seconds)
        assert gateway.send_scheduled(clock)
    sdk.send_message.assert_not_called()
    with Session(engine) as db:
        row = db.get_one(Schedule, sid)
        assert row.status == "failed" and row.attempts == 4
        assert row.result_detail and "consulta" in row.result_detail
        assert set(db.scalars(select(ScheduleAttempt.outcome))) == {"lookup_incomplete"}


def test_scheduled_delivery_retains_destination_after_contact_removal(channel, clock, signed):
    gateway, sdk, create = channel
    create()
    row = signed.get(API + "/schedules").json()["items"][0]
    assert signed.delete(API + "/contacts/" + row["contact_id"]).status_code == 204
    assert signed.get(API + "/contacts").json()["items"] == []
    clock.now += timedelta(minutes=1)
    assert gateway.send_scheduled(clock)
    sdk.send_message.assert_called_once_with(build_jid(TARGET[1:]), row["text"])
    result = signed.get(API + "/schedules").json()["items"][0]
    assert result["status"] == "accepted" and result["recipient"] == row["recipient"]


def test_voice_schedules_once_confirms_to_owner_and_delivers_at_deadline(
    channel, engine, clock, monkeypatch
):
    from neonize.events import MessageEv

    from jarvis.adapters.persistence.models import Contact, ContactAlias, Owner

    gateway, sdk, _ = channel
    route = gateway.profile.route()
    assert route
    with Session(engine) as db, db.begin():
        db.get_one(Owner, 1).name = "Álvaro"
        recipient = db.scalars(select(Contact)).one()
        db.add(ContactAlias(contact_id=recipient.id, alias="Gabi"))
    event = MessageEv()
    event.Info.ID = "voz-unica"
    source = event.Info.MessageSource
    source.Chat.CopyFrom(build_jid(OWNER[1:]))
    source.Sender.CopyFrom(source.Chat)
    event.Message.audioMessage.fileLength = 4
    sdk.download_any.return_value = b"OggS"
    monkeypatch.setattr("jarvis.adapters.whatsapp.gateway.utcnow", clock)
    gateway.receive(None, event)
    transcript = "daqui 1 minuto vc avisa a gabi que eu já estou indo dormir. Dê boa noite"

    def recognize(_data):
        clock.now += timedelta(seconds=4)
        return transcript

    monkeypatch.setattr("jarvis.adapters.ai.speech.transcribe", recognize)
    monkeypatch.setattr(queue, "utcnow", clock)
    model = Mock()
    assert process_one(engine, model, clock=clock)
    model.reply.assert_not_called()
    gateway.receive(None, event)
    assert not process_one(engine, model, clock=clock)
    assert gateway.send_next()
    jid, ack = sdk.send_message.call_args.args
    assert (
        jid.User == OWNER[1:]
        and "Registrei o envio #" in ack
        and "08:01:00" in ack
        and "eu já estou indo dormir. Dê boa noite" in ack
    )
    assert "boa noite" in ack
    clock.now += timedelta(seconds=55)
    assert not gateway.send_scheduled(clock)
    clock.now += timedelta(seconds=1)
    engine.dispose()
    assert gateway.send_scheduled(clock)
    jid, msg = sdk.send_message.call_args.args
    assert jid.User == TARGET[1:]
    assert msg == "Jarvis, a pedido de Álvaro:\n\neu já estou indo dormir. Dê boa noite"
    assert not gateway.send_scheduled(clock)
    with Session(engine) as db:
        assert db.scalars(select(Schedule.status)).all() == ["accepted"]


def test_self_delivery_uses_verified_owner_chat_and_waits_for_actual_receipt(
    channel, engine, clock
):
    gateway, sdk, _ = channel
    route = gateway.profile.route()
    assert route
    for mid, words in (
        ("req", "Envie para mim que hoje eu saio às 19h"),
        ("answer", "Pode mandar agora"),
    ):
        queue.accept_inbound(engine, chat_id=route[0], message_id=mid, text=words, now=clock())
        assert process_one(engine, Mock(), clock=clock)
        if mid == "req":
            assert not gateway.send_scheduled(clock)
    with Session(engine) as db:
        row = db.scalars(select(Schedule)).one()
        assert row.status == "scheduled"
        sid = row.id
    sdk.is_on_whatsapp.side_effect = AssertionError("O vínculo já verificou este contato")
    assert gateway.send_scheduled(clock)
    sdk.send_message.assert_called_once_with(
        build_jid(OWNER[1:]), "Jarvis, a pedido de Dono de teste:\n\nhoje eu saio às 19h"
    )
    assert not gateway.send_scheduled(clock)
    with Session(engine) as db:
        assert db.get_one(Schedule, sid).status == "accepted"
        receipt = db.scalars(
            select(Outbound.text).where(Outbound.text.contains("WhatsApp aceitou"))
        ).one()
        assert receipt.startswith("#" + str(sid)[:8])
