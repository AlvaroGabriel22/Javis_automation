"""Contrato da aba privada do Jarvis, compartilhado pelos dois formatos."""

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from jarvis.modules.spreadsheets.rows import SheetRow

TITLE = "Jarvis"
HEADERS = (
    "ID Jarvis",
    "Data",
    "Tipo",
    "Descrição",
    "Valor (R$)",
    "Quantidade",
    "Estado",
    "Controle Jarvis",
)
TYPES = {"expense": "Despesa", "income": "Receita", "note": "Registro"}
Cell = str | float | int


class SheetError(Exception):
    """O snapshot fica pendente para reconciliação na próxima tentativa."""


@contextmanager
def exclusive(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o600), "r+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SheetError("A planilha está em uso. A exportação ficará pendente.") from error
        yield


def checksum(cells: Sequence[Cell]) -> str:
    # Excel/Sheets podem representar, por exemplo, 10,00 como inteiro.
    canonical = [
        str(Decimal(str(c)).normalize()) if isinstance(c, (float, int)) else c for c in cells
    ]
    data = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def records(rows: Sequence[SheetRow]) -> dict[str, list[Cell]]:
    planned = {}
    for row in rows:
        if row.key in planned or not re.fullmatch(r"[FP][1-9][0-9]{0,19}", row.key):
            raise SheetError("Identificador de planilha repetido ou inválido.")
        if row.deleted:
            cells: list[Cell] = [row.key, "", "", "", "", "", "Removido"]
        else:
            if row.kind not in TYPES:
                raise SheetError("Tipo de registro inválido para exportação.")
            cells = [
                row.key,
                row.day.isoformat(),
                TYPES[row.kind],
                row.description,
                row.amount_cents / 100 if row.amount_cents is not None else "",
                row.quantity,
                "Ativo",
            ]
        if any(
            isinstance(c, str)
            and (
                len(c) > 32767
                or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", c)
            )
            for c in cells
        ):
            raise SheetError("O registro contém texto incompatível com a planilha.")
        planned[row.key] = [*cells, checksum(cells)]
    return planned


def checked_values(raw: Sequence, key: str) -> list[Cell]:
    values = ["" if v is None else v for v in raw][: len(HEADERS)]
    values.extend([""] * (len(HEADERS) - len(values)))
    if (
        values[0] != key
        or any(type(c) not in (str, int, float) for c in values)
        or values[-1] != checksum(values[:-1])
    ):
        raise SheetError(
            f"A linha {key} foi editada fora do Jarvis. Confira a aba antes de exportar."
        )
    return values
