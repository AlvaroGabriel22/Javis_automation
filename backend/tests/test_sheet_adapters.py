import base64
import fcntl
import json
import os
from dataclasses import replace
from datetime import date
from unittest.mock import Mock

import pytest
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from openpyxl import Workbook, load_workbook

from jarvis.adapters.sheets import (
    SheetError,
    SheetRow,
    excel,
    google_auth,
    google_destination_id,
    write_excel,
    write_google,
)
from jarvis.adapters.sheets.google import BINDING_METADATA, SheetClient, sync
from jarvis.adapters.sheets.layout import HEADERS, exclusive, records

FEE = SheetRow("F12", date(2026, 11, 9), "expense", "Café", 1095)
NOTE = SheetRow(
    "P14", date(2026, 11, 10), "note", '=HYPERLINK("https://example.invalid/")', quantity="duas"
)
CHANGED = replace(FEE, description="Café corrigido", amount_cents=1200)


def test_excel_updates_same_row_preserves_manually_added_data_and_uses_literals(tmp_path):
    path = tmp_path / "Jarvis.xlsx"
    write_excel(path, [FEE, NOTE])
    with path.open("rb") as stream:
        book = load_workbook(stream)
    tab = book["Jarvis"]
    assert tab["E2"].value == 10.95
    assert tab["D3"].value == NOTE.description and tab["D3"].data_type == "s"
    tab["I2"] = "conferido à mão"
    tab.append(["Anotação manual", "", "", "=SUM(2,3)"])
    book.create_sheet("Minha aba")["A1"] = 77
    book.save(path)
    book.close()
    write_excel(path, [NOTE, CHANGED])
    fingerprint = path.stat().st_mtime_ns
    write_excel(path, [CHANGED, NOTE])
    assert path.stat().st_mtime_ns == fingerprint and path.stat().st_mode & 0o077 == 0
    book = load_workbook(path)
    tab = book["Jarvis"]
    assert [r[0] for r in tab.values] == ["ID Jarvis", "F12", "P14", "Anotação manual"]
    assert tab["I2"].value == "conferido à mão" and tab["E2"].value == 12
    assert book["Minha aba"]["A1"].value == 77 and tab["D4"].data_type == "f"
    book.close()
    write_excel(path, [replace(FEE, deleted=True), NOTE, replace(FEE, key="F99", deleted=True)])
    book = load_workbook(path)
    assert [c.value for c in book["Jarvis"][2][:7]] == [
        "F12",
        None,
        None,
        None,
        None,
        None,
        "Removido",
    ]
    assert book["Jarvis"]["I2"].value == "conferido à mão" and book["Jarvis"].max_row == 4
    book.close()


@pytest.mark.parametrize(
    ("coordinate", "value"), [("A2", "F88"), ("E2", 42), ("D2", "=NOW()"), ("H2", "x")]
)
def test_excel_fails_without_overwriting_manual_changes(tmp_path, coordinate, value):
    path = tmp_path / "demo.xlsx"
    write_excel(path, [FEE])
    book = load_workbook(path)
    book["Jarvis"][coordinate] = value
    book.save(path)
    book.close()
    before = path.read_bytes()
    with pytest.raises(SheetError):
        write_excel(path, [CHANGED])
    assert path.read_bytes() == before


def test_excel_busy_corrupt_and_foreign_file(tmp_path):
    path = tmp_path / "data.xlsx"
    with (
        exclusive(path.with_name("." + path.name + ".lock")),
        pytest.raises(SheetError, match="uso"),
    ):
        write_excel(path, [FEE])
    for name in (".~lock.data.xlsx#", "~$data.xlsx"):
        marker = tmp_path / name
        marker.touch()
        with pytest.raises(SheetError, match="Feche"):
            write_excel(path, [FEE])
        marker.unlink()
    assert not path.exists()
    Workbook().save(path)
    before = path.read_bytes()
    with pytest.raises(SheetError, match="aba"):
        write_excel(path, [FEE])
    assert path.read_bytes() == before
    path.write_bytes(b"danificado")
    with pytest.raises(SheetError):
        write_excel(path, [FEE])
    assert path.read_bytes() == b"danificado"


def test_excel_detects_replacement_while_saving(tmp_path, monkeypatch):
    path = tmp_path / "data.xlsx"
    write_excel(path, [FEE])
    check = excel._fingerprint
    calls = 0

    def replace_file(target):
        nonlocal calls
        calls += 1
        if calls == 2:
            target.write_bytes(b"outra versao")
        return check(target)

    monkeypatch.setattr(excel, "_fingerprint", replace_file)
    with pytest.raises(SheetError, match="alterado"):
        write_excel(path, [CHANGED])
    assert path.read_bytes() == b"outra versao"


class FakeGoogle(SheetClient):
    """Exercita o payload real e a ordem das operações como se o servidor aplicasse cada lote."""

    def __init__(self):
        self.data = []
        self.ids = {}
        self.owner = None
        self.binding = "synthetic-route"
        self.batches = []
        self.accepted_timeout = None
        self.move_on_put = False

    def call(self, method, path, **kwargs):
        body = kwargs.get("json", {})
        if method == "GET" and path == "":
            sheets = [{"properties": {"sheetId": 1, "title": "Jarvis"}}] if self.data else []
            return {"sheets": sheets}
        if path.startswith("/values/"):
            return {"values": [r.copy() for r in self.data]}
        if path == "/developerMetadata:search":
            if "metadataId" in body["dataFilters"][0]["developerMetadataLookup"]:
                return {
                    "matchedDeveloperMetadata": [{"developerMetadata": self.owner}]
                    if self.owner
                    else []
                }
            return {
                "matchedDeveloperMetadata": [
                    {
                        "developerMetadata": {
                            "metadataId": mid,
                            "metadataValue": key,
                            "location": {
                                "dimensionRange": {
                                    "sheetId": 1,
                                    "startIndex": i,
                                    "dimension": "ROWS",
                                }
                            },
                        }
                    }
                    for i, row in enumerate(self.data)
                    for mid, (key, pointer) in self.ids.items()
                    if row is pointer
                ]
            }
        if path == ":batchUpdate":
            self.batches.append(body)
            for r in body["requests"]:
                if "addSheet" in r:
                    self.data.append([])
                if "insertDimension" in r:
                    self.data.insert(1, [])
                if "updateCells" in r:
                    obj = r["updateCells"]
                    self.data[obj["start"]["rowIndex"]] = [
                        c["userEnteredValue"]["stringValue"] for c in obj["rows"][0]["values"]
                    ]
                if "createDeveloperMetadata" in r:
                    meta = r["createDeveloperMetadata"]["developerMetadata"]
                    if meta["metadataKey"] == BINDING_METADATA:
                        self.owner = {
                            **meta,
                            "location": {**meta["location"], "locationType": "SHEET"},
                        }
                        continue
                    mid = meta["metadataId"]
                    assert mid not in self.ids
                    self.ids[mid] = (meta["metadataValue"], self.data[1])
            if self.accepted_timeout == "reserve" and any(
                "createDeveloperMetadata" in r for r in body["requests"]
            ):
                self.accepted_timeout = None
                raise requests.Timeout
            return {}
        if path == "/values:batchUpdateByDataFilter":
            assert method == "POST" and body["valueInputOption"] == "RAW"
            self.batches.append(body)
            if self.move_on_put:
                self.data.insert(1, ["Intercalada", "preservar"])
                self.move_on_put = False
            for item in body["data"]:
                mid = item["dataFilter"]["developerMetadataLookup"]["metadataId"]
                target = self.ids[mid][1]
                (row,) = item["values"]
                assert row[0] == target[0]
                target[:8] = row
            if self.accepted_timeout == "write":
                self.accepted_timeout = None
                raise requests.Timeout
            return {}
        raise AssertionError((method, path))


@pytest.mark.parametrize("stage", ["reserve", "write"])
def test_google_reconciles_timeout_and_keeps_manual_rows_and_columns(stage):
    client = FakeGoogle()
    client.tab()
    client.data.append(["manual", "=1+2"])
    client.accepted_timeout = stage
    with pytest.raises(requests.Timeout):
        sync(client, records([FEE, NOTE]))
    sync(client, records([FEE, NOTE]))
    assert len(client.ids) == 2
    assert len(client.data) == 4
    row = next(row for row in client.data if row[0] == "F12")
    row.append("conferido à mão")
    client.move_on_put = True
    sync(client, records([CHANGED, NOTE]))
    assert row[3:5] == ["Café corrigido", 12] and row[8] == "conferido à mão"
    assert client.data[1] == ["Intercalada", "preservar"] and client.data[-1] == ["manual", "=1+2"]
    note = next(row for row in client.data if row[0] == NOTE.key)
    assert note[3] == NOTE.description and note[5] == NOTE.quantity
    before = len(client.batches)
    sync(client, records([CHANGED, NOTE]))
    assert len(client.batches) == before
    sync(client, records([replace(FEE, deleted=True), NOTE]))
    assert row[:7] == ["F12", "", "", "", "", "", "Removido"] and row[8] == "conferido à mão"


def test_google_preserves_foreign_data_and_prevents_duplicate_ids():
    client = FakeGoogle()
    client.tab()
    client.data.append(["F12", "colisão"])
    with pytest.raises(SheetError, match="sem controle"):
        sync(client, records([FEE]))
    assert client.data[1][1] == "colisão"
    client.data.pop()
    sync(client, records([FEE]))
    client.data[1][4] = 999
    with pytest.raises(SheetError, match="editada"):
        sync(client, records([CHANGED]))
    assert client.data[1][4] == 999
    client.data.append(["F12"])
    with pytest.raises(SheetError, match="mais de uma vez"):
        sync(client, records([CHANGED]))


def test_google_rejects_foreign_header_and_wrong_destination_before_network(monkeypatch):
    client = FakeGoogle()
    client.tab()
    client.data = [["Usada pela família"]]
    with pytest.raises(SheetError, match="cabeçalho"):
        sync(client, records([FEE]))
    assert client.data == [["Usada pela família"]]
    monkeypatch.setenv("JARVIS_GOOGLE_SHEETS_ID", "IDdoarquivo1234")
    monkeypatch.setenv("JARVIS_GOOGLE_SHEETS_CREDENTIALS", "/sem-credencial")
    original = google_destination_id()
    monkeypatch.setenv("JARVIS_GOOGLE_SHEETS_ID", "outroarquivo1234")
    assert original and original != google_destination_id()
    with pytest.raises(SheetError, match="habilite"):
        write_google([FEE], expected_destination=original)


def test_signed_token_is_limited_to_sheets_and_fixed_google_endpoint(tmp_path):
    key = rsa.generate_private_key(65537, 2048)
    path = tmp_path / "private.json"
    identity = {
        "type": "service_account",
        "token_uri": google_auth.TOKEN_URL,
        "client_email": "jarvis@example.iam.gserviceaccount.com",
        "private_key": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    }
    path.write_text(json.dumps(identity))
    os.chmod(path, 0o600)
    session = Mock()
    session.post.return_value.status_code = 200
    session.post.return_value.json.return_value = {"access_token": "synthetic-only"}
    assert google_auth.access_token(session, path) == "synthetic-only"
    call = session.post.call_args
    assert call.args == (google_auth.TOKEN_URL,) and not call.kwargs["allow_redirects"]
    head, body, signed = call.kwargs["data"]["assertion"].split(".")

    def decode(b):
        return base64.urlsafe_b64decode(b + "=" * (-len(b) % 4))

    key.public_key().verify(
        decode(signed), (head + "." + body).encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    claims = json.loads(decode(body))
    assert claims["scope"] == google_auth.SCOPE and claims["aud"] == google_auth.TOKEN_URL
    identity["token_uri"] = "https://other.invalid/token"
    path.write_text(json.dumps(identity))
    with pytest.raises(SheetError):
        google_auth.access_token(session, path)
    path.chmod(0o644)
    with pytest.raises(SheetError, match="600"):
        google_auth.access_token(session, path)
    assert session.post.call_count == 1


def test_google_adapter_fixes_destination_and_sanitizes_error():
    http = Mock()
    http.request.return_value.status_code = 403
    http.request.return_value.json.return_value = {"error": "secret contents"}
    client = SheetClient(http, "spreadsheet", "secret", "route")
    with pytest.raises(SheetError) as result:
        client.tab()
    assert "secret" not in str(result.value)
    assert (
        http.request.call_args.args[1]
        == "https://sheets.googleapis.com/v4/spreadsheets/spreadsheet"
    )
    assert http.request.call_args.kwargs["allow_redirects"] is False


def test_record_validation_rejects_duplicate_ids_and_excel_control_characters():
    with pytest.raises(SheetError):
        records([FEE, FEE])
    with pytest.raises(SheetError):
        records([replace(NOTE, description="text\x00bad")])
    assert len(HEADERS) == 8


def test_google_checks_link_ownership_before_writing_data_to_a_shared_file():
    client = FakeGoogle()
    sync(client, records([FEE]))
    before = len(client.batches)
    client.binding = "other-route"
    with pytest.raises(SheetError, match="vínculo"):
        sync(client, records([CHANGED]))
    assert len(client.batches) == before


def test_excel_honors_exclusive_lock_held_by_another_editor(tmp_path):
    path = tmp_path / "jarvis.xlsx"
    write_excel(path, [FEE])
    with path.open("rb") as editor:
        fcntl.flock(editor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SheetError, match="uso"):
            write_excel(path, [CHANGED])
    write_excel(path, [CHANGED])
    book = load_workbook(path)
    assert book["Jarvis"]["E2"].value == 12
    book.close()
