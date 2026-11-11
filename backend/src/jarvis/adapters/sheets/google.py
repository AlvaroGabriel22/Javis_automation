"""Aba administrada por ID de linha estável, com reconciliação antes da escrita."""

import hashlib
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import quote

import requests

from jarvis.adapters.sheets.google_auth import access_token, settings
from jarvis.adapters.sheets.layout import (
    HEADERS,
    TITLE,
    Cell,
    SheetError,
    checked_values,
    exclusive,
    records,
)
from jarvis.adapters.whatsapp.profile import state_dir
from jarvis.modules.spreadsheets.rows import SheetRow

METADATA = "jarvis.row.v1"
BINDING_METADATA = "jarvis.binding.v1"
CHUNK = 150


def _metadata_id(sid: int, key: str) -> int:
    # O Sheets só aceita IDs positivos de 31 bits. Para os IDs sequenciais
    # usuais, pares/ímpares distinguem finanças e notas sem colisão de hash.
    maximum = 2**31 - 1
    salt = 65537 * sid
    if key == BINDING_METADATA:
        return 1 + salt % maximum
    if key.startswith(("F", "P")) and key[1:].isascii() and key[1:].isdigit():
        sequence = 2 * int(key[1:]) + (key[0] == "P")
        return 1 + (salt + sequence) % maximum
    raise SheetError("O identificador interno da linha do Sheets é inválido.")


class SheetClient:
    def __init__(self, session: requests.Session, spreadsheet: str, token: str, binding: str):
        self.binding = hashlib.sha256(binding.encode()).hexdigest()
        self.session = session
        self.url = "https://sheets.googleapis.com/v4/spreadsheets/" + spreadsheet
        self.headers = {"Authorization": "Bearer " + token}

    def call(self, method: str, path: str, **kwargs):
        res = self.session.request(
            method,
            self.url + path,
            headers=self.headers,
            timeout=(5, 25),
            allow_redirects=False,
            **kwargs,
        )
        if not 200 <= res.status_code < 300:
            if res.status_code in {401, 403, 404}:
                raise SheetError(
                    "Confira o ID, a API do Sheets e o acesso de edição da conta de serviço."
                )
            raise SheetError(
                "O Google não confirmou a operação. O Jarvis vai conferir as linhas antes de repetir."
            )
        return res.json()

    def batch(self, requests_list: list[dict]):
        return self.call("POST", ":batchUpdate", json={"requests": requests_list})

    def owner_marker(self, sid: int) -> dict:
        return {
            "metadataId": _metadata_id(sid, BINDING_METADATA),
            "metadataKey": BINDING_METADATA,
            "metadataValue": self.binding,
            "visibility": "DOCUMENT",
            "location": {"sheetId": sid},
        }

    def tab(self) -> int:
        tabs = self.call(
            "GET", "", params={"fields": "sheets(properties(sheetId,title,sheetType))"}
        )["sheets"]
        for item in tabs:
            tab = item["properties"]
            if tab["title"] == TITLE:
                if tab.get("sheetType", "GRID") != "GRID":
                    raise SheetError("A aba Jarvis precisa ser uma grade editável.")
                expected = self.owner_marker(tab["sheetId"])
                found = self.call(
                    "POST",
                    "/developerMetadata:search",
                    json={
                        "dataFilters": [
                            {"developerMetadataLookup": {"metadataId": expected["metadataId"]}}
                        ]
                    },
                ).get("matchedDeveloperMetadata", [])
                actual = found[0].get("developerMetadata", {}) if len(found) == 1 else {}
                if (
                    any(
                        actual.get(k) != expected[k]
                        for k in ("metadataId", "metadataKey", "metadataValue", "visibility")
                    )
                    or actual.get("location", {}).get("sheetId") != tab["sheetId"]
                ):
                    raise SheetError(
                        "A aba Jarvis não pertence a este vínculo. Escolha uma planilha dedicada."
                    )
                return tab["sheetId"]
        # Reserva o nome da aba e insere o cabeçalho no mesmo lote. Um criador
        # concorrente receberá conflito e relerá os dados na próxima tentativa.
        used = {s["properties"]["sheetId"] for s in tabs}
        sid = 1
        while sid in used:
            sid += 1
        self.batch(
            [
                {
                    "addSheet": {
                        "properties": {
                            "sheetId": sid,
                            "title": TITLE,
                            "gridProperties": {"frozenRowCount": 1, "columnCount": len(HEADERS)},
                        }
                    }
                },
                {
                    "updateCells": {
                        "start": {"sheetId": sid, "rowIndex": 0, "columnIndex": 0},
                        "rows": [
                            {"values": [{"userEnteredValue": {"stringValue": v}} for v in HEADERS]}
                        ],
                        "fields": "userEnteredValue",
                    }
                },
                {"createDeveloperMetadata": {"developerMetadata": self.owner_marker(sid)}},
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": sid,
                            "dimension": "COLUMNS",
                            "startIndex": 7,
                            "endIndex": 8,
                        },
                        "properties": {"hiddenByUser": True},
                        "fields": "hiddenByUser",
                    }
                },
            ]
        )
        return sid

    def state(self, sid: int):
        data = self.call(
            "GET",
            "/values/" + quote(f"'{TITLE}'!A:H", safe=""),
            params={"valueRenderOption": "FORMULA"},
        ).get("values", [])
        if not data or tuple(data[0]) != HEADERS:
            raise SheetError(
                "A aba Jarvis existente tem outro cabeçalho. Preserve-a com outro nome."
            )
        rows: dict[str, list] = {}
        for cells in data[1:]:
            if cells and isinstance(cells[0], str) and cells[0]:
                key = cells[0]
                if key in rows and key[:1] in {"P", "F"} and key[1:].isdigit():
                    raise SheetError(
                        f"O identificador {key} aparece mais de uma vez na aba Jarvis."
                    )
                rows[key] = cells
        matches = self.call(
            "POST",
            "/developerMetadata:search",
            json={
                "dataFilters": [
                    {"developerMetadataLookup": {"metadataKey": METADATA, "visibility": "DOCUMENT"}}
                ]
            },
        ).get("matchedDeveloperMetadata", [])
        # A própria linha é o alvo do filtro de escrita, mesmo quando o usuário
        # insere outras linhas enquanto uma chamada ao Google está em andamento.
        anchors: dict[str, int] = {}
        for item in matches:
            meta = item["developerMetadata"]
            place = meta["location"].get("dimensionRange", {})
            if place.get("sheetId", 0) != sid or place.get("dimension") != "ROWS":
                continue
            key = meta["metadataValue"]
            pos = place.get("startIndex", 0)
            if (
                key in anchors
                or meta["metadataId"] != _metadata_id(sid, key)
                or not 0 < pos < len(data)
                or data[pos][:1] != [key]
            ):
                raise SheetError(
                    "A identificação das linhas da aba Jarvis mudou. Confira a planilha."
                )
            anchors[key] = meta["metadataId"]
        return rows, anchors

    def reserve(self, sid: int, keys: Sequence[str]):
        # Uma inserção por linha sempre cabe mesmo em grades com apenas duas linhas.
        # Não toca nas linhas manuais existentes, que se deslocam com seus metadados.
        reqs: list[dict] = []
        for key in keys:
            dimension = {"sheetId": sid, "dimension": "ROWS", "startIndex": 1, "endIndex": 2}
            reqs.extend(
                [
                    {"insertDimension": {"range": dimension, "inheritFromBefore": False}},
                    {
                        "updateCells": {
                            "start": {"sheetId": sid, "rowIndex": 1, "columnIndex": 0},
                            "rows": [{"values": [{"userEnteredValue": {"stringValue": key}}]}],
                            "fields": "userEnteredValue",
                        }
                    },
                    {
                        "createDeveloperMetadata": {
                            "developerMetadata": {
                                "metadataId": _metadata_id(sid, key),
                                "metadataKey": METADATA,
                                "metadataValue": key,
                                "visibility": "DOCUMENT",
                                "location": {"dimensionRange": dimension},
                            }
                        }
                    },
                ]
            )
        self.batch(reqs)

    def put(self, anchors: dict[str, int], updates: list[list[Cell]]) -> None:
        filters = [
            {
                "dataFilter": {"developerMetadataLookup": {"metadataId": anchors[str(row[0])]}},
                "majorDimension": "ROWS",
                "values": [row],
            }
            for row in updates
        ]
        self.call(
            "POST",
            "/values:batchUpdateByDataFilter",
            json={"valueInputOption": "RAW", "data": filters},
        )


def sync(client: SheetClient, desired: dict[str, list[Cell]]) -> None:
    sid = client.tab()
    current, anchors = client.state(sid)
    missing = []
    for key, values in desired.items():
        if key not in anchors:
            if key in current:
                raise SheetError(
                    f"O identificador {key} já pertence a uma linha sem controle do Jarvis."
                )
            if values[6] != "Removido":
                missing.append(key)
        elif current[key] != [key]:
            checked_values(current[key], key)
    for start in range(0, len(missing), CHUNK):
        client.reserve(sid, missing[start : start + CHUNK])
    if missing:
        current, anchors = client.state(sid)
        if any(key not in anchors for key in missing):
            raise SheetError("O Google não confirmou as novas linhas da aba Jarvis.")
    updates = []
    for key, values in desired.items():
        if key in anchors and (
            current[key] == [key] or checked_values(current[key], key) != values
        ):
            updates.append(values)
    for start in range(0, len(updates), CHUNK):
        client.put(anchors, updates[start : start + CHUNK])
    if updates:
        latest, meta = client.state(sid)
        if any(
            str(row[0]) not in meta or checked_values(latest[str(row[0])], str(row[0])) != row
            for row in updates
        ):
            raise SheetError("O Google não confirmou o conteúdo das linhas exportadas.")


def write_google(
    rows: Sequence[SheetRow], *, expected_destination: str | None = None, binding: str | None = None
) -> None:
    cfg = settings()
    destination = cfg.signature
    if not destination or expected_destination is not None and expected_destination != destination:
        raise SheetError("Confira e habilite o destino Google Sheets no painel.")
    if not binding:
        raise SheetError("O vínculo do proprietário da planilha está indisponível.")
    desired = records(rows)
    try:
        lock = state_dir().parent / "planilhas" / ("." + destination + ".lock")
        with exclusive(lock), requests.Session() as session:
            token = access_token(session, Path(cfg.jarvis_google_sheets_credentials))
            sync(SheetClient(session, cfg.jarvis_google_sheets_id, token, binding), desired)
    except (requests.RequestException, OSError, KeyError, TypeError, ValueError) as error:
        raise SheetError(
            "O Google não confirmou a exportação. As linhas serão conferidas antes de repetir."
        ) from error
