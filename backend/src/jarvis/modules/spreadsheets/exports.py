"""Coordena a projeção do livro local em destinos de planilha independentes."""

import fcntl
import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from sqlalchemy import Engine, event, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, SessionTransaction

from jarvis.adapters import sheets
from jarvis.adapters.persistence.db import utcnow
from jarvis.adapters.whatsapp import ipc
from jarvis.adapters.whatsapp.profile import state_dir
from jarvis.modules.errors import Problem
from jarvis.modules.spreadsheets.export_models import ExportState, GoogleDestination, SheetRevision
from jarvis.modules.spreadsheets.service import snapshot

Target = Literal["excel", "google"]
TARGETS: tuple[Target, Target] = ("excel", "google")
NEW_DESTINATION = (
    "O destino do Google mudou. Confira a planilha configurada e ative a sincronização novamente."
)


def active_route() -> str | None:
    route = ipc.call("route").get("route_key")
    return route if isinstance(route, str) and 0 < len(route) <= 192 else None


def _folder(route: str) -> Path:
    scope = hashlib.sha256(route.encode("utf-8")).hexdigest()
    return state_dir().parent / "planilhas" / scope


def _book(route: str) -> Path:
    return _folder(route) / "Jarvis.xlsx"


def _file_identity(route: str) -> str | None:
    path = _book(route)
    try:
        file = path.lstat()
        if (
            stat.S_ISREG(file.st_mode)
            and file.st_size > 0
            and not path.parent.is_symlink()
            and not path.parent.parent.is_symlink()
        ):
            tag = (file.st_dev, file.st_ino, file.st_size, file.st_mtime_ns, file.st_ctime_ns)
            return hashlib.sha256(str(tag).encode("ascii")).hexdigest()
    except OSError:
        pass
    return None


def _revision(db: Session, route: str) -> int:
    return db.scalar(select(SheetRevision.revision).where(SheetRevision.route == route)) or 0


def _done(state: ExportState, revision: int, identity: str | None) -> bool:
    return (
        state.applied_revision == revision
        and state.applied_id == state.request_id
        and (state.target != "excel" or identity is not None and state.file_identity == identity)
    )


def statuses(db: Session, route: str | None) -> dict:
    configured = sheets.google_configured()
    identity = _file_identity(route) if route else None
    version = _revision(db, route) if route else 0
    result: dict = {
        "google_configured": configured,
        "google_enabled": False,
        "excel_available": False,
    }
    for target in TARGETS:
        detail = None
        state = db.get(ExportState, (route, target)) if route else None
        if not route:
            name = "idle"
        elif target == "google" and (state is None or not state.enabled):
            name = "idle"
        elif target == "google" and (
            not configured or state is None or sheets.google_destination_id() != state.destination
        ):
            name, detail = (
                "error",
                NEW_DESTINATION if configured else "Confira a configuração do Google Sheets.",
            )
        elif state and state.error:
            name, detail = "error", state.error
        elif state and _done(state, version, identity):
            name = "ready"
        else:
            name = "pending"
        result[target] = {"state": name, "detail": detail}
        if target == "google":
            result["google_enabled"] = bool(state and state.enabled)
        else:
            result["excel_available"] = name == "ready"
    return result


def _insert(db: Session):
    return pg_insert if db.get_bind().dialect.name == "postgresql" else sqlite_insert


def _ensure(db: Session, route: str, target: Target) -> None:
    db.execute(
        _insert(db)(ExportState)
        .values(route=route, target=target, enabled=(target == "excel"), request_id=uuid4().hex)
        .on_conflict_do_nothing()
    )


def _reset_values() -> dict:
    return {"request_id": uuid4().hex, "failures": 0, "retry_at": None, "error": None}


@event.listens_for(Session, "after_transaction_end")
def _release_options_lock(db: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        for guard in db.info.pop("jarvis_sheet_guards", {}).values():
            guard.__exit__(None, None, None)


def _lock_options(db: Session, route: str) -> None:
    # O commit da opção e a primeira chamada ao provedor têm uma ordem definida
    # mesmo se a opção mudar durante uma chamada de rede demorada.
    locks = db.info.setdefault("jarvis_sheet_guards", {})
    if route in locks:
        return
    guard = _exclusive(route, "google")
    try:
        acquired = guard.__enter__()
    except OSError as exc:
        raise Problem(503, "export_storage", "Confira o diretório privado de planilhas.") from exc
    if not acquired:
        guard.__exit__(None, None, None)
        raise Problem(
            409, "sheet_busy", "Uma sincronização está em andamento. Aguarde para mudar o destino."
        )
    locks[route] = guard


def set_google_enabled(db: Session, route: str, enabled: bool) -> None:
    db.connection()
    _lock_options(db, route)
    destination = sheets.google_destination_id() if enabled else None
    if enabled:
        if not destination or not sheets.google_configured():
            raise Problem(
                409, "sheets_config", "Configure a planilha e a conta de serviço antes de ativar."
            )
        db.execute(
            _insert(db)(GoogleDestination)
            .values(destination=destination, route=route)
            .on_conflict_do_nothing()
        )
        binding = db.get_one(GoogleDestination, destination)
        if binding.route != route:
            raise Problem(
                409,
                "sheets_other_binding",
                "Esse arquivo Google já pertence a outro vínculo. Configure uma planilha dedicada a este vínculo.",
            )
    _ensure(db, route, "google")
    current = db.get_one(ExportState, (route, "google"))
    if current.enabled == enabled and current.destination == destination:
        return
    current.enabled, current.destination = enabled, destination
    current.request_id = uuid4().hex
    current.retry_at, current.error, current.failures = None, None, 0
    db.flush()


def request_sync(db: Session, route: str, target: Target) -> None:
    if target not in TARGETS:
        raise Problem(422, "sheet_target", "Escolha Excel ou Google Sheets.")
    _ensure(db, route, target)
    current = db.get_one(ExportState, (route, target))
    if not current.enabled:
        raise Problem(409, "sheets_disabled", "Ative o Google Sheets para esse vínculo no painel.")
    if target == "google" and (
        not sheets.google_configured() or current.destination != sheets.google_destination_id()
    ):
        raise Problem(409, "sheets_config", NEW_DESTINATION)
    db.execute(
        update(ExportState)
        .where(ExportState.route == route, ExportState.target == target)
        .values(**_reset_values())
    )


def download_path(db: Session, route: str) -> Path:
    state = db.get(ExportState, (route, "excel"))
    if not state or state.error or not _done(state, _revision(db, route), _file_identity(route)):
        raise Problem(
            409, "excel_pending", "Aguarde a atualização da planilha Excel antes de baixar."
        )
    return _book(route)


@contextmanager
def _exclusive(route: str, target: Target) -> Iterator[bool]:
    folder = _folder(route)
    for path in (folder.parent, folder):
        path.mkdir(mode=0o700, exist_ok=True, parents=True)
        if path.is_symlink() or path.stat().st_uid != os.getuid():
            raise OSError("Diretório de exportação inválido.")
    with os.fdopen(
        os.open(folder / (target + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "r+b"
    ) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def _sync_locked(engine: Engine, route: str, target: Target, now: datetime) -> bool:
    # Ler a versão antes das linhas. Uma alteração concorrente exigirá outra projeção.
    with Session(engine) as db, db.begin():
        state = db.get(ExportState, (route, target))
        if state is None:
            if target == "google":
                return False
            _ensure(db, route, target)
            state = db.get_one(ExportState, (route, target))
        if not state.enabled or state.retry_at and state.retry_at > now:
            return False
        revision = _revision(db, route)
        if not state.error and _done(
            state, revision, _file_identity(route) if target == "excel" else None
        ):
            return False
        destination, request, failures = state.destination, state.request_id, state.failures
        rows = snapshot(db, route)
    # O gateway pode ter mudado de vínculo durante o carregamento das linhas.
    if active_route() != route:
        return False
    error = None
    identity = None
    try:
        if target == "excel":
            sheets.write_excel(_book(route), rows)
            identity = _file_identity(route)
            if identity is None:
                error = "O arquivo Excel ficou indisponível durante a sincronização."
        elif not destination or destination != sheets.google_destination_id():
            error = NEW_DESTINATION
        else:
            sheets.write_google(rows, expected_destination=destination, binding=route)
    except sheets.SheetError as exc:
        error = str(exc)[:300] or "Falha ao atualizar a planilha."
    except OSError:
        error = "Não foi possível acessar o arquivo de planilha. Confira o espaço e as permissões."
    if error:
        delay = min(900, 15 * 2 ** min(failures, 6))
        values = dict(error=error, failures=failures + 1, retry_at=now + timedelta(seconds=delay))
    else:
        values = dict(
            applied_revision=revision,
            applied_id=request,
            file_identity=identity,
            error=None,
            failures=0,
            retry_at=None,
        )
    with engine.begin() as conn:
        # Uma mudança de opção durante a I/O conserva o novo pedido pendente.
        conn.execute(
            update(ExportState)
            .where(
                ExportState.route == route,
                ExportState.target == target,
                ExportState.enabled,
                ExportState.request_id == request,
            )
            .values(**values)
        )
    return True


def sync_one(engine: Engine, target: Target, *, now: datetime | None = None) -> bool:
    if target not in TARGETS:
        raise ValueError("Destino desconhecido.")
    route = active_route()
    if not route:
        return False
    current = now or utcnow()
    try:
        with _exclusive(route, target) as locked:
            return bool(locked and _sync_locked(engine, route, target, current))
    except OSError:
        # Falha antes de abrir o destino (diretório sem acesso, por exemplo).
        # Registre no painel mesmo quando o arquivo de trava não pode ser criado.
        with Session(engine) as db, db.begin():
            if target == "excel":
                _ensure(db, route, target)
            state = db.get(ExportState, (route, target))
            if not state or not state.enabled or state.retry_at and state.retry_at > current:
                return False
            state.error = "Confira o espaço e as permissões do diretório privado de planilhas."
            state.retry_at = current + timedelta(seconds=30)
        return True
