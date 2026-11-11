"""Atualização atômica da pasta de trabalho privativa do vínculo."""

import fcntl
import os
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.worksheet import Worksheet

from jarvis.adapters.sheets.layout import (
    HEADERS,
    TITLE,
    Cell,
    SheetError,
    checked_values,
    exclusive,
    records,
)
from jarvis.modules.spreadsheets.rows import SheetRow


def _fingerprint(path: Path):
    if path.is_symlink():
        raise SheetError("O destino do Excel deve ser um arquivo local regular.")
    try:
        item = path.stat()
    except FileNotFoundError:
        return None
    if not path.is_file():
        raise SheetError("O destino do Excel deve ser um arquivo local regular.")
    return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns


@contextmanager
def _lock_current_file(path: Path) -> Iterator[None]:
    # Respeita também um programa que usa flock diretamente no arquivo XLSX.
    try:
        handle = os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb")
    except FileNotFoundError:
        yield
        return
    with handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SheetError("A planilha está em uso. A exportação ficará pendente.") from error
        yield


def _office_lock(path: Path):
    if any(
        (path.parent / name).exists() for name in ("~$" + path.name, ".~lock." + path.name + "#")
    ):
        raise SheetError("Feche a planilha no editor para concluir a exportação.")


def _open(path: Path):
    with ZipFile(path) as z:
        entries = z.infolist()
        if len(entries) > 2000 or sum(e.file_size for e in entries) > 64 * 1024 * 1024:
            raise SheetError("A pasta de trabalho excede o limite desta integração.")
        if any(
            e.filename.startswith(("xl/activeX/", "xl/embeddings/", "customXml/", "xl/pivot"))
            or e.filename.endswith(".bin")
            for e in entries
        ):
            raise SheetError(
                "Use uma pasta de trabalho dedicada ao Jarvis, sem objetos incorporados."
            )
    book = load_workbook(path, keep_links=True, rich_text=True)
    if TITLE not in book.sheetnames:
        raise SheetError("O arquivo existente não contém a aba gerenciada pelo Jarvis.")
    return book


def _new():
    book = Workbook()
    tab = book.active
    assert isinstance(tab, Worksheet)
    tab.title = TITLE
    tab.append(HEADERS)
    for cell in tab[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="193953")
    for i, width in enumerate((15, 14, 16, 52, 18, 18, 13), 1):
        tab.column_dimensions[get_column_letter(i)].width = width
    tab.column_dimensions["H"].hidden = True
    tab.freeze_panes = "B2"
    return book


def _edit(tab: Worksheet, desired: dict[str, list[Cell]]) -> bool:
    if tuple(c.value for c in tab[1][: len(HEADERS)]) != HEADERS:
        raise SheetError("As colunas da aba Jarvis foram alteradas.")
    if tab.max_row > 100_000 or tab.max_column > 200:
        raise SheetError("A aba Jarvis excede o limite de linhas ou colunas.")
    positions: dict[str, int] = {}
    for number, cells in enumerate(tab.iter_rows(min_row=2, max_col=len(HEADERS)), 2):
        first, marker = cells[0].value, cells[-1].value
        if first is None and marker is None:
            continue
        if isinstance(marker, str) and len(marker) == 64:
            # Uma edição do próprio ID também deve bloquear a gravação.
            checked_values([c.value for c in cells], str(first))
        if first not in desired:
            continue
        key = str(first)
        if key in positions:
            raise SheetError(f"Há duas linhas com o identificador {key} na aba Jarvis.")
        positions[key] = number

    next_row = tab.max_row + 1
    changed = False
    for key, values in desired.items():
        number = positions.get(key)
        if number is None:
            if values[6] == "Removido":
                continue
            number, next_row = next_row, next_row + 1
        else:
            cells = [tab.cell(number, col) for col in range(1, len(HEADERS) + 1)]
            if any(c.data_type == "f" for c in cells):
                raise SheetError(
                    f"A linha {key} da aba Jarvis contém uma fórmula inserida no editor."
                )
            if checked_values([c.value for c in cells], key) == values:
                continue
        for col, value in enumerate(values, 1):
            cell = tab.cell(number, col)
            if isinstance(cell, MergedCell):
                raise SheetError("Desfaça a mesclagem das células de dados na aba Jarvis.")
            cell.value = value if value != "" else None
            if isinstance(value, str) and value:
                cell.data_type = "s"
            if col == 5:
                cell.number_format = '"R$" #,##0.00'
        changed = True
    return changed


def write_excel(path: Path, rows: Sequence[SheetRow]) -> None:
    desired = records(rows)
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise SheetError("O arquivo deve ter extensão .xlsx.")
    try:
        with exclusive(path.with_name("." + path.name + ".lock")), _lock_current_file(path):
            _office_lock(path)
            before = _fingerprint(path)
            book = _open(path) if before else _new()
            try:
                changed = _edit(book[TITLE], desired)
                if before and not changed:
                    _office_lock(path)
                    if _fingerprint(path) != before:
                        raise SheetError("O Excel mudou durante a conferência. Tente novamente.")
                    return
                with tempfile.NamedTemporaryFile(
                    dir=path.parent, suffix=".xlsx", delete=False
                ) as output:
                    temp = Path(output.name)
                try:
                    book.save(temp)
                    with temp.open("rb") as saved:
                        os.fsync(saved.fileno())
                    _office_lock(path)
                    if _fingerprint(path) != before:
                        raise SheetError(
                            "O Excel foi alterado durante a exportação. Tente novamente."
                        )
                    os.replace(temp, path)
                    directory = os.open(path.parent, os.O_DIRECTORY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                finally:
                    temp.unlink(missing_ok=True)
            finally:
                book.close()
    except (
        OSError,
        BadZipFile,
        InvalidFileException,
        KeyError,
        ValueError,
        TypeError,
        SyntaxError,
        RuntimeError,
    ) as error:
        raise SheetError(
            "Não foi possível atualizar o Excel. Confira o arquivo e o acesso à pasta."
        ) from error
