import fcntl
import json
from threading import Event
from time import monotonic, sleep
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from jarvis.adapters.google import client as google
from jarvis.adapters.google.panel import MAX_CLIENT_FILE

PATH = "/api/v1/calendar/google"
CLIENT = {"client_id": "test.apps.googleusercontent.com", "client_secret": "synthetic-secret"}
FILE = {"installed": CLIENT}


@pytest.fixture(autouse=True)
def private_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_GOOGLE_CLIENT_FILE", str(tmp_path / "external.json"))


def status(client):
    response = client.get(PATH)
    assert response.is_success and "synthetic-secret" not in response.text
    return response.json()


def wait_phase(client, phase):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        state = status(client)
        if state["phase"] == phase:
            return state
        sleep(0.01)
    pytest.fail("A autorização não atingiu " + phase)


def test_import_requires_owner_csrf_and_desktop_file(signed):
    assert not status(signed)["configured"]
    assert signed.post(PATH + "/connect").status_code == 422
    assert (
        signed.put(PATH + "/client", json=FILE, headers={"X-CSRF-Token": "invalid"}).status_code
        == 403
    )
    for raw in [
        b"{",
        b"null",
        b'{"type":"service_account"}',
        b'{"web":{}}',
        b'{"installed":{"client_id":"id"}}',
    ]:
        assert signed.put(PATH + "/client", content=raw).status_code == 422
    assert signed.put(PATH + "/client", content=b" " * (MAX_CLIENT_FILE + 1)).status_code == 413
    res = signed.put(PATH + "/client", json={**FILE, "unneeded": "discard"})
    assert res.is_success and res.json()["configured"] and CLIENT["client_secret"] not in res.text
    path = google.root() / "client.json"
    assert json.loads(path.read_text()) == FILE and path.stat().st_mode & 0o077 == 0
    assert not status(signed)["account"]
    signed.cookies.clear()
    assert signed.get(PATH).status_code == 401
    assert signed.post(PATH + "/connect").status_code == 401


def test_pending_duplicate_denial_retry_and_verified_account(signed, monkeypatch):
    signed.put(PATH + "/client", json=FILE).raise_for_status()
    release = Event()
    calls = []

    class Consent:
        url = "https://accounts.google.com/o/oauth2/v2/auth?state=synthetic"

        def __init__(self, client):
            calls.append(client)

        def finish(self, exchanging):
            assert release.wait(3)
            if len(calls) == 1:
                raise google.ConsentError("A autorização foi cancelada no Google.")
            exchanging()
            google.save_grant("dono@example.invalid", "refresh-A", CLIENT)

    monkeypatch.setattr(google, "DesktopConsent", Consent)
    url = signed.post(PATH + "/connect").json()["url"]
    assert url and signed.post(PATH + "/connect").json()["url"] == url and len(calls) == 1
    assert signed.put(PATH + "/client", json=FILE).status_code == 409
    assert status(signed)["phase"] == "waiting" and "url" not in status(signed)
    release.set()
    failed = wait_phase(signed, "failed")
    assert "cancelada" in failed["message"] and failed["account"] is None
    signed.post(PATH + "/connect").raise_for_status()
    assert wait_phase(signed, "complete")["account"] == "dono@example.invalid"
    assert signed.get("/api/v1/calendar").json()["authorized"]
    assert not status(signed)["sync_running"]
    with (google.root() / "sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        assert status(signed)["sync_running"]


def test_replacement_does_not_break_active_grant_or_mix_accounts(signed, monkeypatch):
    google.save_grant("dono@example.invalid", "refresh-A", CLIENT)
    original = (google.root() / "token.json").read_bytes()
    candidate = {"client_id": "new", "client_secret": "new-secret"}
    signed.put(PATH + "/client", json={"installed": candidate}).raise_for_status()
    assert google.credentials() == candidate
    with pytest.raises(google.ConsentError, match="outra conta"):
        google.save_grant("other@example.invalid", "refresh-B", candidate)
    assert (google.root() / "token.json").read_bytes() == original
    net = Mock(return_value={"access_token": "fake"})
    monkeypatch.setattr(google, "http", net)
    google.Google().refresh()
    assert net.call_args.kwargs["data"] == {
        **CLIENT,
        "refresh_token": "refresh-A",
        "grant_type": "refresh_token",
    }


def test_s03_grant_still_renews_with_original_client(signed, monkeypatch):
    google.legacy_path().write_text(json.dumps(FILE))
    assert status(signed)["configured"]
    folder = google.root()
    folder.mkdir(parents=True)
    key = Fernet.generate_key()
    (folder / "key").write_bytes(key)
    (folder / "token.json").write_text(
        json.dumps(
            {
                "calendar": "legacy@example.invalid",
                "refresh": Fernet(key).encrypt(b"original").decode(),
            }
        )
    )
    signed.put(
        PATH + "/client", json={"installed": {"client_id": "new", "client_secret": "new-secret"}}
    ).raise_for_status()
    assert status(signed)["account"] == "legacy@example.invalid"
    net = Mock(return_value={"access_token": "fake"})
    monkeypatch.setattr(google, "http", net)
    google.Google().refresh()
    assert net.call_args.kwargs["data"]["client_id"] == CLIENT["client_id"]
