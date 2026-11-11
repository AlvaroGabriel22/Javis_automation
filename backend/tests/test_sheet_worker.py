from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Event
from unittest.mock import Mock

import pytest
from openpyxl import load_workbook
from sqlalchemy.orm import Session

from jarvis.modules.errors import Problem
from jarvis.modules.spreadsheets import exports
from jarvis.modules.spreadsheets.export_models import SheetRevision
from jarvis.modules.spreadsheets.exports import (
    download_path,
    request_sync,
    set_google_enabled,
    statuses,
    sync_one,
)
from jarvis.modules.spreadsheets.service import create_note, remove_note

OWNER = "test-owner-first"
OTHER = "test-owner-second"


@pytest.fixture
def linked(monkeypatch):
    route = {"route_key": OWNER}
    monkeypatch.setattr(exports.ipc, "call", lambda op: dict(route))
    return route


@pytest.fixture
def google(monkeypatch):
    settings = {"configured": True, "destination": "a" * 64}
    monkeypatch.setattr(exports.sheets, "google_configured", lambda: settings["configured"])
    monkeypatch.setattr(exports.sheets, "google_destination_id", lambda: settings["destination"])
    recorder = Mock()
    monkeypatch.setattr(exports.sheets, "write_google", recorder)
    return settings, recorder


def note(engine, route, label):
    with Session(engine) as db, db.begin():
        return create_note(db, route, date(2031, 1, 1), label, "2", request_key=label).id


def test_excel_writes_only_current_route_and_refuses_stale_download(
    engine, signed, linked, monkeypatch
):
    note(engine, OWNER, "Compra pessoal")
    note(engine, OTHER, "Outro vínculo")
    with Session(engine) as db:
        assert statuses(db, OWNER)["excel"]["state"] == "pending"
    assert sync_one(engine, "excel")
    with Session(engine) as db:
        state = statuses(db, OWNER)
        assert state["excel_available"] and state["excel"]["state"] == "ready"
        path = download_path(db, OWNER)
        with pytest.raises(Problem):
            download_path(db, OTHER)
    book = load_workbook(path, read_only=True)
    try:
        assert [item[3] for item in list(book["Jarvis"].values)[1:]] == ["Compra pessoal"]
    finally:
        book.close()
    assert path.stat().st_mode & 0o077 == 0
    assert not sync_one(engine, "excel")
    number = note(engine, OWNER, "Nova anotação")
    with Session(engine) as db:
        with pytest.raises(Problem):
            download_path(db, OWNER)
    assert sync_one(engine, "excel")
    with Session(engine) as db, db.begin():
        remove_note(db, OWNER, number)
    assert sync_one(engine, "excel")
    # Revogar o vínculo não deve eleger outro dono nem consultar qualquer linha.
    linked["route_key"] = None
    monkeypatch.setattr(exports, "snapshot", Mock(side_effect=AssertionError("sem vínculo")))
    assert not sync_one(engine, "excel")
    with Session(engine) as db:
        assert statuses(db, None)["excel"]["state"] == "idle"


def test_uncommitted_change_does_not_advance_revision(engine, signed, linked):
    with Session(engine) as db:
        create_note(db, OWNER, date(2031, 1, 1), "privado", request_key="abort")
        assert db.get_one(SheetRevision, OWNER).revision > 0
        db.rollback()
    with Session(engine) as db:
        assert db.get(SheetRevision, OWNER) is None


def test_opt_in_pins_google_file_and_protects_old_binding(engine, signed, linked, google):
    settings, write = google
    note(engine, OWNER, "Item um")
    assert not sync_one(engine, "google")
    with Session(engine) as db, db.begin():
        set_google_enabled(db, OWNER, True)
    assert sync_one(engine, "google")
    assert write.call_count == 1 and write.call_args.kwargs == {
        "expected_destination": "a" * 64,
        "binding": OWNER,
    }
    assert not sync_one(engine, "google")
    linked["route_key"] = OTHER
    assert not sync_one(engine, "google")
    with Session(engine) as db, pytest.raises(Problem, match="outro vínculo"):
        set_google_enabled(db, OTHER, True)
    linked["route_key"] = OWNER
    settings["destination"] = "b" * 64
    with Session(engine) as db:
        assert statuses(db, OWNER)["google"]["state"] == "error"
        with pytest.raises(Problem):
            request_sync(db, OWNER, "google")
    note(engine, OWNER, "Mais um")
    assert sync_one(engine, "google")
    write.assert_called_once()
    with Session(engine) as db, db.begin():
        set_google_enabled(db, OWNER, True)
    assert sync_one(engine, "google")
    assert write.call_args.kwargs == {"expected_destination": "b" * 64, "binding": OWNER}


def test_repeated_failure_backs_off_and_manual_retry_reconciles(
    engine, signed, linked, google, clock
):
    _, write = google
    write.side_effect = [exports.sheets.SheetError("A planilha está em uso."), None]
    with Session(engine) as db, db.begin():
        set_google_enabled(db, OWNER, True)
    assert sync_one(engine, "google", now=clock())
    assert not sync_one(engine, "google", now=clock() + timedelta(seconds=5))
    # O Excel continua independente de uma falha no provedor remoto.
    assert sync_one(engine, "excel", now=clock())
    with Session(engine) as db, db.begin():
        assert statuses(db, OWNER)["google"]["state"] == "error"
        request_sync(db, OWNER, "google")
    assert sync_one(engine, "google", now=clock() + timedelta(seconds=6))
    with Session(engine) as db:
        assert statuses(db, OWNER)["google"]["state"] == "ready"


def test_worker_skips_overlapping_io_and_preserves_new_request(engine, signed, linked, google):
    _, write = google
    entered, release = Event(), Event()

    def wait_remote(rows, *, expected_destination, binding):
        entered.set()
        assert release.wait(10)

    write.side_effect = wait_remote
    with Session(engine) as db, db.begin():
        set_google_enabled(db, OWNER, True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(sync_one, engine, "google")
        try:
            assert entered.wait(10)
            assert not sync_one(engine, "google")
            with Session(engine) as db, pytest.raises(Problem, match="sincronização"):
                set_google_enabled(db, OWNER, False)
            with Session(engine) as db, db.begin():
                request_sync(db, OWNER, "google")
        finally:
            release.set()
        assert running.result()
    with Session(engine) as db:
        assert statuses(db, OWNER)["google"]["state"] == "pending"
    write.side_effect = None
    assert sync_one(engine, "google")
    assert write.call_count == 2


def test_external_edit_requires_reconciliation_before_download(engine, signed, linked, clock):
    note(engine, OWNER, "Mercado")
    assert sync_one(engine, "excel", now=clock())
    with Session(engine) as db:
        book_path = download_path(db, OWNER)
    workbook = load_workbook(book_path)
    try:
        workbook["Jarvis"]["D2"] = "Modificado no editor externo"
        workbook.save(book_path)
    finally:
        workbook.close()
    with Session(engine) as db:
        assert statuses(db, OWNER)["excel"]["state"] == "pending"
        with pytest.raises(Problem):
            download_path(db, OWNER)
    assert sync_one(engine, "excel", now=clock())
    with Session(engine) as db:
        assert "editada fora" in statuses(db, OWNER)["excel"]["detail"]
    verify = load_workbook(book_path, read_only=True)
    try:
        assert verify["Jarvis"]["D2"].value == "Modificado no editor externo"
    finally:
        verify.close()
