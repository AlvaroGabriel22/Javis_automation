from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from conftest import client_for
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.db import new_engine
from jarvis.adapters.persistence.models import Command, Owner, Schedule, WebSession
from jarvis.api import COOKIE
from jarvis.modules.identity.service import issue_code

PREFIX = "/api/v1"


def post(client, resource, data, key=None):
    return client.post(
        f"{PREFIX}/{resource}", json=data, headers={"Idempotency-Key": key or uuid4().hex}
    )


def contact(client, name="Destinatário", phone="+5592999990100"):
    response = post(client, "contacts", {"name": name, "phone": phone})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def payload(cid, when="2031-01-12T09:52:00-04:00", text="  Cheguei.\nPode subir!  "):
    return {"contact_id": cid, "scheduled_at": when, "text": text}


def test_status_has_no_private_data(signed):
    response = signed.get(PREFIX + "/status")
    assert response.json() == {
        "status": "ok",
        "whatsapp": "not_connected",
        "delivery_enabled": False,
    }
    assert response.headers["cache-control"] == "no-store"


def test_unmigrated_db_unavailable(tmp_path, clock):
    engine = new_engine("sqlite:///" + str(tmp_path / "empty.sqlite3"))
    with client_for(engine, clock) as c:
        status = c.get(PREFIX + "/status")
        assert status.status_code == 503
        assert status.json()["status"] == "unavailable"
    engine.dispose()


def test_credential_is_restricted_to_local_host_and_origin(engine, clock, client, code):
    with client_for(engine, clock, "192.0.2.10") as remote:
        assert remote.post(PREFIX + "/auth/login", json={"code": code}).status_code == 403
    for header in [{"Host": "untrusted.example"}, {"Origin": "https://untrusted.example"}]:
        assert (
            client.post(PREFIX + "/auth/login", json={"code": code}, headers=header).status_code
            == 403
        )
    login = client.post(PREFIX + "/auth/login", json={"code": code})
    assert login.status_code == 200
    assert (
        "HttpOnly" in login.headers["set-cookie"]
        and "samesite=strict" in login.headers["set-cookie"].lower()
    )
    raw = client.cookies.get(COOKIE)
    with Session(engine) as db:
        session = db.scalar(select(WebSession))
        owner = db.get_one(Owner, 1)
        assert session and session.token_hash != raw
        assert owner.code_hash is None and owner.code_expires is None
    assert client.post(PREFIX + "/auth/login", json={"code": code}).status_code == 401


def test_new_code_invalidates_previous_and_expires(engine, clock, client, code):
    with Session(engine) as db:
        new_code = issue_code(db, None, clock())
        assert db.get_one(Owner, 1).code_hash not in (new_code, None)
    assert client.post(PREFIX + "/auth/login", json={"code": code}).status_code == 401
    clock.now += timedelta(minutes=10)
    assert client.post(PREFIX + "/auth/login", json={"code": new_code}).status_code == 401


def test_auth_csrf_logout_and_expiry(engine, clock, client, code):
    assert client.get(PREFIX + "/schedules").status_code == 401
    login = client.post(PREFIX + "/auth/login", json={"code": code})
    token = login.json()["csrf_token"]
    assert client.get(PREFIX + "/auth/session").json() == login.json()
    assert post(client, "contacts", {"name": "Ana", "phone": "+5511999990001"}).status_code == 403
    assert client.post(PREFIX + "/auth/logout").status_code == 403
    captured = client.cookies.get(COOKIE)
    assert client.post(PREFIX + "/auth/logout", headers={"X-CSRF-Token": token}).status_code == 204
    client.cookies.set(COOKIE, captured)
    assert client.get(PREFIX + "/contacts").status_code == 401
    client.cookies.clear()
    with Session(engine) as db:
        fresh = issue_code(db, None, clock())
    assert client.post(PREFIX + "/auth/login", json={"code": fresh}).status_code == 200
    clock.now += timedelta(hours=12)
    assert client.get(PREFIX + "/auth/session").status_code == 401


def test_brute_force_limited(client):
    for _ in range(20):
        assert client.post(PREFIX + "/auth/login", json={"code": "errado"}).status_code == 401
    assert client.post(PREFIX + "/auth/login", json={"code": "errado"}).status_code == 429


def test_concurrent_login_is_consumed_once(engine, clock, code):
    gate = Barrier(2)

    clients = [client_for(engine, clock) for _ in range(2)]

    def compete(index):
        with clients[index] as c:
            gate.wait(timeout=5)
            return c.post(PREFIX + "/auth/login", json={"code": code}).status_code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(compete, range(2))) == [200, 401]


def test_restart_preserves_actual_record_and_never_sends(engine, clock, signed):
    cid = contact(signed)
    data = payload(cid)
    first = post(signed, "schedules", data, "same")
    assert first.status_code == 201, first.text
    row = first.json()
    assert row["scheduled_at"] == "2031-01-12T13:52:00Z"
    assert row["status"] == "scheduled" and row["text"] == data["text"]
    clock.now += timedelta(days=3)
    engine.dispose()  # descarta as conexões; abre outra instância da API sobre o mesmo banco.
    with client_for(engine, clock) as new:
        with Session(engine) as db:
            code = issue_code(db, None, clock())
        token = new.post(PREFIX + "/auth/login", json={"code": code}).json()["csrf_token"]
        new.headers["X-CSRF-Token"] = token
        assert new.get(PREFIX + "/schedules").json()["items"] == [row]
        equivalent = dict(data, scheduled_at="2031-01-12T13:52:00Z")
        replay = post(new, "schedules", equivalent, "same")
        assert replay.status_code == 200 and replay.json() == row
        for change in [dict(data, text="Outro conteúdo"), dict(data, contact_id=str(uuid4()))]:
            assert (
                post(new, "schedules", change, "same").json()["detail"]["code"]
                == "idempotency_conflict"
            )
        for _ in range(2):
            cancel = new.delete(PREFIX + "/schedules/" + row["id"])
            assert cancel.status_code == 200 and cancel.json()["status"] == "cancelled"
        assert post(new, "schedules", equivalent, "same").json()["status"] == "cancelled"


def test_concurrent_replay_creates_one_schedule(engine, clock, signed):
    cid = contact(signed)
    cookie, csrf = signed.cookies.get(COOKIE), signed.headers["X-CSRF-Token"]
    gate = Barrier(2)

    clients = [client_for(engine, clock) for _ in range(2)]

    def compete(index):
        with clients[index] as c:
            c.cookies.set(COOKIE, cookie)
            c.headers["X-CSRF-Token"] = csrf
            gate.wait(timeout=5)
            r = post(c, "schedules", payload(cid), "parallel")
            return r.status_code, r.json().get("id")

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(compete, range(2)))
    assert sorted(row[0] for row in results) == [200, 201]
    assert len({row[1] for row in results}) == 1
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Schedule)) == 1


@pytest.mark.parametrize(
    "when,expected_error",
    [
        ("2031-01-12T11:40:00", "invalid_input"),
        ("2030-01-12T11:40:00Z", "schedule_in_past"),
        (1936110000, "invalid_input"),
    ],
)
def test_invalid_schedule_releases_key(engine, signed, when, expected_error):
    cid = contact(signed)
    bad = post(signed, "schedules", payload(cid, when=when), "retry")
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == expected_error
    with Session(engine) as db:
        assert db.get(Command, (1, "retry")) is None
    assert post(signed, "schedules", payload(cid), "retry").status_code == 201
    missing = post(signed, "schedules", payload(str(uuid4())))
    assert missing.status_code == 404
    assert post(signed, "schedules", payload(cid, text="  ")).status_code == 422


def test_homonyms_contact_dedup_and_key_scope(signed):
    first = {"name": "Ana", "phone": "+5592999990100"}
    resp = post(signed, "contacts", first, "key")
    assert post(signed, "contacts", first, "key").json() == resp.json()
    assert post(signed, "contacts", first, "different").json()["detail"]["code"] == "phone_exists"
    assert post(signed, "schedules", payload(resp.json()["id"]), "key").status_code == 409
    second = contact(signed, "Ana", "+5592999990101")
    assert second != resp.json()["id"]
    assert len(signed.get(PREFIX + "/contacts").json()["items"]) == 2


def test_stable_pagination_of_homonyms_and_simultaneous_schedules(signed):
    cids = [contact(signed, "Ana", f"+559299999010{n}") for n in range(3)]
    sids = [post(signed, "schedules", payload(cid)).json()["id"] for cid in cids]
    for entity, expected in [("contacts", cids), ("schedules", sids)]:
        path = PREFIX + "/" + entity
        a = signed.get(path, params={"limit": 2}).json()
        b = signed.get(path, params={"limit": 2, "cursor": a["next_cursor"]}).json()
        assert b["next_cursor"] is None
        assert sorted(row["id"] for row in [*a["items"], *b["items"]]) == sorted(expected)
        assert signed.get(path, params={"cursor": str(uuid4())}).status_code == 422


@pytest.mark.parametrize("has_schedule", [False, True])
def test_delete_contact_and_register_same_phone_again(signed, engine, has_schedule):
    from uuid import UUID

    from jarvis.adapters.persistence.models import Contact, ContactAlias
    from jarvis.modules.assistant.recipients import candidates

    data = {"name": "Lia (anterior)", "phone": "+5592000000101", "aliases": ["Lili"]}
    original = post(signed, "contacts", data, "first-contact").json()
    cid = original["id"]
    if has_schedule:
        assert post(signed, "schedules", payload(cid)).status_code == 201
    path = f"{PREFIX}/contacts/{cid}"
    assert signed.delete(path, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert signed.delete(path).status_code == 204
    assert signed.delete(path).status_code == 204
    assert signed.get(PREFIX + "/contacts").json()["items"] == []
    assert post(signed, "schedules", payload(cid)).status_code == 404
    with Session(engine) as db:
        assert db.get(ContactAlias, (UUID(cid), "Lili")) is None
        assert not candidates(db, "teste", "Lili")
        assert (db.get(Contact, UUID(cid)) is not None) == has_schedule
    new = post(signed, "contacts", {**data, "name": "Lia", "aliases": []})
    assert new.status_code == 201 and new.json()["id"] != cid
    stale = post(signed, "contacts", data, "first-contact")
    assert stale.status_code == 410 and stale.json()["detail"]["code"] == "contact_removed"
    assert signed.delete(path).status_code == 204
    assert signed.get(PREFIX + "/contacts").json()["items"] == [new.json()]
    if has_schedule:
        saved = signed.get(PREFIX + "/schedules").json()["items"][0]
        assert saved["status"] == "scheduled" and saved["recipient"] == original


def test_owner_preferences_persist_and_reach_chat(signed, engine, clock):
    from unittest.mock import Mock

    from jarvis.entrypoints.worker import process_one
    from jarvis.modules.messaging import queue

    url = PREFIX + "/profile"
    assert signed.get(url).json()["tone"] == "proximo"
    legacy = {"preferred_name": "Bia", "gender": "Feminino", "address_as": "Sra."}
    saved = {**legacy, "tone": "neutro"}
    assert signed.put(url, json=saved, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert signed.put(url, json={**saved, "preferred_name": "  "}).status_code == 422
    assert signed.put(url, json={**saved, "tone": "qualquer"}).status_code == 422
    assert signed.put(url, json=saved).json() == saved
    assert signed.put(url, json=legacy).json() == saved
    assert signed.get(url).json() == saved
    assert signed.get(PREFIX + "/auth/session").json()["profile"] == saved
    engine.dispose()
    queue.accept_inbound(
        engine, chat_id="verified", message_id="greet", text="Como vai?", now=clock()
    )
    model = Mock()
    model.reply.return_value = "Tudo certo, senhora."
    assert process_one(engine, model, clock=clock)
    identity = model.reply.call_args.args[0][0]
    assert identity["role"] == "system" and all(
        value in identity["content"] for value in ["Jarvis", *saved.values()]
    )
    assert "tom neutro" in identity["content"] and "fofuxo" not in identity["content"]


def test_existing_owner_keeps_profile_and_gets_default_tone(engine):
    from alembic import command
    from alembic.config import Config
    from conftest import ROOT
    from sqlalchemy import text

    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.downgrade(config, "0018_sticker_preference")
        conn.execute(
            text(
                "INSERT INTO owners (id, name, preferred_name, gender, address_as)"
                " VALUES (1, 'Beatriz', 'Bia', 'Feminino', 'Sra.')"
            )
        )
        command.upgrade(config, "0019_owner_tone")
        assert conn.execute(
            text("SELECT name, preferred_name, gender, address_as, tone FROM owners")
        ).one() == ("Beatriz", "Bia", "Feminino", "Sra.", "proximo")
