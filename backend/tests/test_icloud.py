from unittest.mock import Mock, patch

import pytest
from sqlalchemy.orm import Session
from test_calendar import CAL, EVENTS, add, listing, provider

from jarvis.adapters.google.sync import discover as google_discover
from jarvis.adapters.icloud import account, ical
from jarvis.adapters.icloud.client import (
    BASE,
    AppleError,
    CalDAV,
    RemoteCalendar,
    member_url,
    trusted,
)
from jarvis.adapters.icloud.sync import sync_source
from jarvis.modules.calendar.models import ICloudLink

HOME = "https://p01-caldav.icloud.com/123/calendars/"
URL = HOME + "private/"
EMAIL = "owner@example.invalid"
PASSWORD = "synthetic-only"
BODY = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:trip
DTSTART;TZID=America/Manaus:20310113T090000
DTEND;TZID=America/Manaus:20310113T100000
SUMMARY:Revisão
LOCATION:Oficina
DESCRIPTION:Uma informação longa que foi
 dobrada e deve ser preservada
BEGIN:VALARM
ACTION:DISPLAY
TRIGGER:-PT15M
END:VALARM
END:VEVENT
END:VCALENDAR
"""


class MemoryDAV:
    def __init__(self):
        self.files = {"trip.ics": ('"1"', BODY)}
        self.seq = 1

    def calendars(self):
        return [
            RemoteCalendar(URL, "Pessoal", True),
            RemoteCalendar(HOME + "read/", "Compartilhada", False),
        ]

    def members(self, url):
        return {key: tag for key, (tag, _) in self.files.items()} if url == URL else {}

    def get(self, url):
        try:
            return self.files[url.removeprefix(URL)]
        except KeyError:
            raise AppleError(404) from None

    def put(self, url, body, etag):
        key = url.removeprefix(URL)
        assert (key not in self.files and etag is None) or self.files[key][0] == etag
        self.seq += 1
        self.files[key] = f'"{self.seq}"', body.decode()

    def delete(self, url, etag):
        key = url.removeprefix(URL)
        assert self.files[key][0] == etag
        del self.files[key]


def link(signed, engine, dav):
    with patch.object(account, "CalDAV", return_value=dav):
        reply = signed.put(CAL + "/icloud", json={"email": EMAIL, "password": PASSWORD})
        assert reply.is_success and reply.json()["account"] == EMAIL
        assert PASSWORD not in reply.text
        account.sync(engine)
    return next(
        s["id"]
        for s in signed.get(CAL).json()["sources"]
        if s["provider"] == "icloud" and s["writable"]
    )


def test_two_providers_and_private_apple_crud(engine, signed, clock):
    google = provider(engine)
    dav = MemoryDAV()
    sid = link(signed, engine, dav)
    google_discover(engine, google)
    sources = signed.get(CAL).json()["sources"]
    assert [s["id"] for s in sources if s["write_default"]] == ["principal"]
    assert all(s["available"] for s in sources)
    readonly = next(s["id"] for s in sources if not s["writable"])
    assert add(signed, "readonly", source_id=readonly).status_code == 409
    assert add(signed, "local", source_id=None).json()["source_id"] is None
    with Session(engine) as db:
        assert PASSWORD not in db.get_one(ICloudLink, 1).encrypted
    apple = next(r for r in listing(signed) if r["source_id"] == sid)
    result = signed.patch(
        EVENTS + "/" + apple["id"],
        params={"version": apple["version"]},
        json={"title": "Revisão atualizada", "starts_at": apple["starts_at"]},
    )
    assert result.is_success
    new = add(signed, "apple-write", title="Criado no Jarvis", source_id=sid).json()
    assert new["state"] == "create"
    assert signed.delete(CAL + "/icloud").status_code == 409
    sync_source(engine, dav, sid, clock)
    assert (
        "SUMMARY:Revisão atualizada" in dav.files["trip.ics"][1]
        and "BEGIN:VALARM" in dav.files["trip.ics"][1]
    )
    assert "LOCATION:Oficina" in dav.files["trip.ics"][1]
    assert len(dav.files) == 2
    # Segunda leitura após o aceite e edição posterior no aparelho.
    sync_source(engine, dav, sid, clock)
    tag, raw = dav.files["trip.ics"]
    dav.files["trip.ics"] = ('"iphone"', raw.replace("Revisão atualizada", "Mudou no celular"))
    sync_source(engine, dav, sid, clock)
    rows = listing(signed)
    assert any(r["title"] == "Mudou no celular" for r in rows)
    row = next(r for r in rows if r["id"] == new["id"])
    assert signed.delete(EVENTS + "/" + row["id"], params={"version": row["version"]}).is_success
    sync_source(engine, dav, sid, clock)
    assert len(dav.files) == 1
    assert signed.delete(CAL + "/icloud").is_success
    assert signed.get(CAL).json()["icloud"]["account"] is None
    assert all(r["source_id"] != sid for r in listing(signed))
    assert signed.get(CAL).json()["sources"][0]["provider"] in {"icloud", "google"}


def test_recurrence_conflict_and_deletion_from_phone(engine, signed, clock):
    dav = MemoryDAV()
    dav.files["trip.ics"] = ('"1"', BODY.replace("UID:trip", "UID:trip\nRRULE:FREQ=WEEKLY;COUNT=3"))
    sid = link(signed, engine, dav)
    rows = listing(signed, period="month")
    assert len(rows) == 3
    second = rows[1]
    response = signed.patch(
        EVENTS + "/" + second["id"],
        params={"version": second["version"], "occurrence": second["starts_at"]},
        json={"title": "Exceção local", "starts_at": second["starts_at"]},
    )
    assert response.is_success
    sync_source(engine, dav, sid, clock)
    assert "RECURRENCE-ID" in dav.files["trip.ics"][1]
    assert [r["title"] for r in listing(signed, period="month")] == [
        "Revisão",
        "Exceção local",
        "Revisão",
    ]
    row = listing(signed, period="month")[0]
    response = signed.patch(
        EVENTS + "/" + row["id"],
        params={"version": row["version"]},
        json={"title": "Edição pendente", "starts_at": row["starts_at"], "recurrence": "weekly"},
    )
    assert response.is_success
    dav.files["trip.ics"] = (
        '"conflict"',
        dav.files["trip.ics"][1].replace("SUMMARY:Revisão", "SUMMARY:Do celular"),
    )
    sync_source(engine, dav, sid, clock)
    assert listing(signed, period="month")[0]["state"] == "conflict"
    assert "Do celular" in dav.files["trip.ics"][1]
    restored = ical.parse(dav.files["trip.ics"][1])
    restored.children = [c for c in restored.children if not c.get("RECURRENCE-ID")]
    dav.files["trip.ics"] = ('"restored"', ical.serialize(restored).decode())
    sync_source(engine, dav, sid, clock)
    assert [r["title"] for r in listing(signed, period="month")] == ["Do celular"] * 3
    dav.files.clear()
    sync_source(engine, dav, sid, clock)
    assert listing(signed, period="month") == []


def test_unauthorized_icloud_and_untrusted_redirect(engine, signed):
    with patch.object(CalDAV, "calendars", side_effect=AppleError(401)):
        r = signed.put(CAL + "/icloud", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 422 and PASSWORD not in r.text
    assert signed.get(CAL).json()["icloud"]["account"] is None
    assert (
        signed.put(
            CAL + "/icloud",
            headers={"X-CSRF-Token": "wrong"},
            json={"email": EMAIL, "password": PASSWORD},
        ).status_code
        == 403
    )
    for url in (
        "http://caldav.icloud.com",
        "https://outside.invalid/",
        "https://caldav.icloud.com@outside.invalid",
    ):
        with pytest.raises(AppleError):
            trusted(BASE, url)
    with pytest.raises(AppleError):
        member_url(URL, "..%2Fother.ics")


def test_caldav_discovery_namespaces_permissions_and_etags():
    def response(path, props):
        return f"<D:response><D:href>{path}</D:href><D:propstat><D:prop>{props}</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>"

    def multistatus(text):
        return (
            '<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
            + text
            + "</D:multistatus>"
        ).encode()

    client = CalDAV(EMAIL, PASSWORD)
    principal = (
        "<D:current-user-principal><D:href>/123/principal/</D:href></D:current-user-principal>"
    )
    home = "<C:calendar-home-set><D:href>" + HOME + "</D:href></C:calendar-home-set>"
    cal = "<D:resourcetype><D:collection/><C:calendar/></D:resourcetype><D:displayname>Pessoal</D:displayname><D:current-user-privilege-set><D:privilege><D:write/></D:privilege></D:current-user-privilege-set>"
    results = [
        (BASE, {}, multistatus(response("/", principal))),
        (BASE, {}, multistatus(response("/123/principal/", home))),
        (HOME, {}, multistatus(response(URL, cal))),
        (
            URL,
            {},
            multistatus(
                response(URL, cal) + response(URL + "abc.ics", '<D:getetag>"123"</D:getetag>')
            ),
        ),
    ]
    with patch.object(client, "request", Mock(side_effect=results)) as req:
        assert client.calendars() == [RemoteCalendar(URL, "Pessoal", True)]
        assert client.members(URL) == {"abc.ics": '"123"'}
        assert all(call.args[0] == "PROPFIND" for call in req.call_args_list)


def test_all_day_and_utf8_ical_round_trip():
    raw = (
        BODY.replace("DTSTART;TZID=America/Manaus:20310113T090000", "DTSTART;VALUE=DATE:20310113")
        .replace("DTEND;TZID=America/Manaus:20310113T100000", "DTEND;VALUE=DATE:20310115")
        .replace("SUMMARY:Revisão", "SUMMARY:" + ("São Paulo\\, " * 12))
    )
    encoded = ical.serialize(ical.parse(raw))
    assert all(len(line) <= 75 for line in encoded.split(b"\r\n"))
    (ev,) = ical.events(ical.parse(encoded.decode()), "a.ics", '"x"', "America/Manaus")
    assert ev["start"] == {"date": "2031-01-13"} and ev["end"] == {"date": "2031-01-15"}
    assert ev["summary"] == "São Paulo, " * 12


def test_legacy_google_settings_survive_upgrade(tmp_path):
    from alembic import command
    from alembic.config import Config
    from conftest import ROOT
    from sqlalchemy import text

    from jarvis.adapters.persistence.db import new_engine, ready
    from jarvis.modules.calendar.models import CalendarSource

    engine = new_engine("sqlite:///" + str(tmp_path / "upgrade.sqlite3"))
    cfg = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0007_calendar")
        conn.execute(
            text(
                "INSERT INTO calendar_sources (id, name, timezone, writable, selected, write_default, available, cursor) VALUES ('original', 'Agenda', 'America/Manaus', true, true, true, true, 'cursor-google')"
            )
        )
        command.upgrade(cfg, "head")
    assert ready(engine)
    with Session(engine) as db:
        source = db.get_one(CalendarSource, "original")
        assert source.provider == "google" and source.write_default and source.selected
        assert source.cursor == "cursor-google" and source.remote_url is None
    engine.dispose()


def test_icloud_recovers_after_lost_write_response(engine, signed, clock):
    dav = MemoryDAV()
    sid = link(signed, engine, dav)
    event = add(signed, "retry", title="Transmissão", source_id=sid).json()
    url = URL + event["id"].replace("-", "") + ".ics"

    def interrupted(url, body, etag):
        original(url, body, etag)
        raise AppleError()

    original = dav.put
    with patch.object(dav, "put", side_effect=interrupted), pytest.raises(AppleError):
        sync_source(engine, dav, sid, clock)
    tag, _ = dav.get(url)
    with patch.object(dav, "put", side_effect=AssertionError("Não deve duplicar o PUT")):
        sync_source(engine, dav, sid, clock)
    assert dav.get(url)[0] == tag
    row = next(r for r in listing(signed) if r["id"] == event["id"])
    assert row["state"] == "clean"


def test_caldav_writes_conditionally_and_keeps_auth_on_valid_hosts_only():
    client = CalDAV(EMAIL, PASSWORD)
    with patch.object(client, "request") as req:
        client.put(URL + "abc.ics", b"data", None)
        assert req.call_args.kwargs["headers"]["If-None-Match"] == "*"
        client.put(URL + "abc.ics", b"new", '"old"')
        assert req.call_args.kwargs["headers"]["If-Match"] == '"old"'
        client.delete(URL + "abc.ics", '"new"')
        assert req.call_args.kwargs["headers"]["If-Match"] == '"new"'
    from unittest.mock import MagicMock

    redirect = MagicMock()
    redirect.__enter__.return_value = redirect
    redirect.status_code = 302
    redirect.headers = {"Location": "https://example.invalid/caldav"}
    with (
        patch("jarvis.adapters.icloud.client.requests.request", return_value=redirect) as net,
        pytest.raises(AppleError),
    ):
        client.request("GET", BASE)
    assert net.call_count == 1
