from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_calendar import CAL, add, listing, provider, remote
from test_icloud import URL, MemoryDAV, link

from jarvis.adapters import calendar_refresh
from jarvis.adapters.google.sync import sync_source as pull_google
from jarvis.adapters.icloud import ical
from jarvis.adapters.icloud.client import AppleError
from jarvis.adapters.icloud.sync import sync_source as pull_apple
from jarvis.modules.calendar.models import CalendarImportIssue, CalendarSource, Entry


def test_google_keeps_healthy_events_and_recovers_a_partial_cursor(engine, clock, signed):
    google = provider(engine)
    start, end = {"date": "2031-01-13"}, {"date": "2031-01-14"}
    first = remote(start, end, id="a", summary="Versão válida")
    google.pages.return_value = ([first], "first-cursor")
    pull_google(engine, google, "principal", clock)

    # A atualização inválida do primeiro item não pode apagar sua versão boa
    # nem impedir que o segundo apareça. A próxima consulta usa apenas o delta.
    bad = dict(first, etag="new", start={"date": "2031-02-30"}, summary="Inválido")
    second = remote(start, end, id="b", summary="Segundo")
    google.pages.return_value = ([bad, second], "partial-cursor")
    pull_google(engine, google, "principal", clock)
    assert {r["title"] for r in listing(signed)} == {"Segundo", "Versão válida"}
    with Session(engine) as db:
        source = db.get_one(CalendarSource, "principal")
        assert (
            source.cursor == "partial-cursor"
            and source.error
            and "incompatíveis: 1" in source.error
        )
        assert db.scalars(select(CalendarImportIssue.etag)).all() == ["new"]
    google.pages.return_value = ([], "no-changes")
    pull_google(engine, google, "principal", clock)
    assert google.pages.call_args.args[1]["syncToken"] == "partial-cursor"
    with Session(engine) as db:
        assert db.get_one(CalendarSource, "principal").error
    google.pages.return_value = (
        [dict(first, etag="fixed", summary="Corrigido"), second],
        "complete",
    )
    pull_google(engine, google, "principal", clock)
    assert google.pages.call_args.args[1]["syncToken"] == "no-changes"
    assert {r["title"] for r in listing(signed)} == {"Segundo", "Corrigido"}
    with Session(engine) as db:
        source = db.get_one(CalendarSource, "principal")
        assert source.cursor == "complete" and source.error is None
        assert not db.scalar(select(CalendarImportIssue.remote_id))


@pytest.mark.parametrize("full", (True, False))
def test_google_removes_issue_for_external_deletion(engine, clock, signed, full):
    from datetime import timedelta

    google = provider(engine)
    google.pages.return_value = ([{"id": "broken", "etag": "1"}], "cursor")
    pull_google(engine, google, "principal", clock)
    if full:
        clock.now += timedelta(days=8)
        changes = []
    else:
        changes = [{"id": "broken", "status": "cancelled"}]
    google.pages.return_value = (changes, "complete")
    pull_google(engine, google, "principal", clock)
    with Session(engine) as db:
        assert not db.scalar(select(CalendarImportIssue.remote_id))
        assert db.get_one(CalendarSource, "principal").error is None


def test_unidentified_google_resource_preserves_local_snapshot_and_cursor(engine, clock, signed):
    from datetime import timedelta

    google = provider(engine)
    good = remote({"date": "2031-01-13"}, {"date": "2031-01-14"}, id="existing")
    google.pages.return_value = ([good], "baseline")
    pull_google(engine, google, "principal", clock)
    clock.now += timedelta(days=8)
    google.pages.return_value = ([{"etag": "missing-id"}], "unknown-cursor")
    pull_google(engine, google, "principal", clock)
    with Session(engine) as db:
        existing = db.scalars(select(Entry).where(Entry.remote_id == "existing")).one()
        assert not existing.deleted and existing.title == good["summary"]
        source = db.get_one(CalendarSource, "principal")
        assert source.cursor == "baseline" and source.error


def test_apple_can_confirm_lost_response_after_server_removes_custom_field(signed, engine, clock):
    dav = MemoryDAV()
    sid = link(signed, engine, dav)
    event = add(signed, "lost-reply", title="Reunião", source_id=sid).json()
    address = URL + event["id"].replace("-", "") + ".ics"
    original_put = dav.put

    def put_then_timeout(url, body, etag):
        stored = ical.parse(body.decode())
        stored.children[0].set("X-JARVIS-MUTATION", None)
        original_put(url, ical.serialize(stored), etag)
        raise AppleError(503)

    with patch.object(dav, "put", side_effect=put_then_timeout):
        pull_apple(engine, dav, sid, clock)
    assert "Reunião" in dav.get(address)[1]
    assert next(r for r in listing(signed) if r["id"] == event["id"])["state"] == "create"
    with patch.object(dav, "put", side_effect=AssertionError("O recurso já existe")):
        pull_apple(engine, dav, sid, clock)
    assert next(r for r in listing(signed) if r["id"] == event["id"])["state"] == "clean"


def test_apple_does_not_confirm_changed_event_on_readback(engine, signed, clock):
    dav = MemoryDAV()
    sid = link(signed, engine, dav)
    event = add(signed, "rewritten", title="Reunião", source_id=sid).json()
    original_put = dav.put

    def changed_by_remote(url, body, etag):
        original_put(url, body.replace(b"SUMMARY:", b"SUMMARY:Alterada "), etag)

    with patch.object(dav, "put", side_effect=changed_by_remote):
        pull_apple(engine, dav, sid, clock)
    item = next(r for r in listing(signed) if r["id"] == event["id"])
    assert item["state"] == "conflict" and item["title"] == "Alterada Reunião"
    assert signed.get(CAL).json()["conflicts"]


def test_targeted_refresh_excludes_other_provider(engine):
    with (
        patch.object(calendar_refresh, "google_connected", return_value=True),
        patch.object(
            calendar_refresh, "refresh_google", side_effect=AssertionError("Google fora do escopo")
        ),
    ):
        assert calendar_refresh.refresh_for_query(engine, frozenset({"icloud"})) == ["iCloud"]
