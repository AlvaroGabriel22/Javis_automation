"""Estado privado de um único aparelho. QR e desafios expiram exclusivamente em memória."""

import json
import os
import re
import secrets
import tempfile
from collections.abc import Callable
from pathlib import Path
from threading import Event, RLock
from time import monotonic
from typing import Literal

import segno

from jarvis.modules.errors import Problem

PHONE = re.compile(r"\+[1-9][0-9]{6,14}\Z")
PREFIX = "VINCULAR "
BindingStatus = Literal[
    "waiting",
    "wrong_code",
    "different_number",
    "unverified_chat",
    "expired",
    "save_failed",
    "interrupted",
]


def binding_code(text: str) -> str | None:
    parts = text.split(maxsplit=1)
    if not parts or parts[0].upper() != PREFIX.strip():
        return None
    return parts[1].strip().upper() if len(parts) == 2 else ""


def state_dir() -> Path:
    base = Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local/share") / "jarvis"
    return (base / "demo" if os.getenv("JARVIS_PROFILE") == "demo" else base) / "whatsapp"


def write_private[T](path: Path, value: dict[str, T]) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as f:
        try:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
            os.replace(f.name, path)
        finally:
            Path(f.name).unlink(missing_ok=True)


class Profile:
    def __init__(self, root: Path, clock: Callable[[], float] = monotonic):
        self.root = root
        self.clock = clock
        self.lock = RLock()
        self.start = Event()
        self.restart = Event()
        self.state = "idle"
        self.account = ""
        self.qr = ""
        self.qr_until = 0.0
        self.pending: tuple[str, str, float] | None = None
        self.binding_status: BindingStatus | None = None
        self.observed_phone: str | None = None
        self.binding_file = root / "owner.json"
        self.saved: dict[str, str] = {}
        if self.binding_file.exists():
            try:
                data = json.loads(self.binding_file.read_text())
                if (
                    not isinstance(data, dict)
                    or not PHONE.fullmatch(data.get("account", ""))
                    or not PHONE.fullmatch(data.get("phone", ""))
                    or not re.fullmatch(r"[0-9a-f]{24}", data.get("binding_id", ""))
                ):
                    raise ValueError
                self.saved = data
            except (TypeError, ValueError):
                # Falha fechada. O proprietário poderá realizar uma nova prova pelo painel.
                self.saved = {}
        if (root / "paired").exists():
            self.start.set()

    def pairing(self) -> dict:
        with self.lock:
            if self.state != "connected":
                self.start.set()
                if self.state == "pairing" and self.clock() >= self.qr_until:
                    self.restart.set()
                    self.state = "connecting"
                if self.state == "idle":
                    self.state = "connecting"
            return self.snapshot()

    def _clear_challenge(self, status: BindingStatus | None = None) -> None:
        self.pending, self.observed_phone, self.binding_status = None, None, status

    def on_qr(self, data: bytes) -> None:
        with self.lock:
            seconds = 20 if self.state == "pairing" else 60
            self.restart.clear()
            self.state = "pairing"
            self.account = ""
            if self.pending:
                self._clear_challenge("interrupted")
            self.qr = segno.make_qr(data).png_data_uri(scale=5)
            self.qr_until = self.clock() + seconds

    def connected(self, account: str) -> None:
        if not PHONE.fullmatch(account):
            raise ValueError("A conta conectada não tem identidade telefônica verificável.")
        with self.lock:
            if self.pending and self.account != account:
                self._clear_challenge("interrupted")
            self.restart.clear()
            self.state, self.account, self.qr = "connected", account, ""
            (self.root / "paired").touch(mode=0o600)

    def disconnected(self, *, logout: bool = False) -> None:
        with self.lock:
            self.account, self.qr = "", ""
            if self.pending:
                self._clear_challenge("interrupted")
            if logout:
                self.start.clear()
                (self.root / "paired").unlink(missing_ok=True)
                self.revoke()
                self.state = "idle"
            else:
                self.state = "disconnected"

    def snapshot(self) -> dict:
        with self.lock:
            now = self.clock()
            bound = self._owner()
            if self.pending and self.pending[2] <= now:
                self._clear_challenge("expired")
            return {
                "state": self.state,
                "account": self.account or None,
                "owner_phone": bound or None,
                "qr": self.qr if self.qr_until > now and self.state == "pairing" else None,
                "pending_phone": self.pending[0] if self.pending else None,
                "binding_status": self.binding_status,
                "observed_phone": self.observed_phone
                if self.binding_status == "different_number"
                else None,
                "channel": "conversation",
            }

    def challenge(self, phone: str) -> dict:
        with self.lock:
            if not PHONE.fullmatch(phone):
                raise Problem(422, "invalid_phone", "Use +, código do país, DDD e número.")
            if self.state != "connected":
                raise Problem(409, "not_connected", "Conecte o WhatsApp do Jarvis primeiro.")
            if self._owner():
                raise Problem(409, "already_bound", "Remova o vínculo atual antes de criar outro.")
            if phone == self.account:
                raise Problem(
                    422, "same_account", "Use seu WhatsApp pessoal, diferente do número do Jarvis."
                )
            code = secrets.token_hex(5).upper()
            self.pending = (phone, code, self.clock() + 300)
            self.binding_status = "waiting"
            self.observed_phone = None
            return {"phrase": PREFIX + code, "valid_for_seconds": 300}

    def _owner(self) -> str:
        return (
            self.saved.get("phone", "")
            if self.account and self.saved.get("account") == self.account
            else ""
        )

    def verify(self, sender: str | None, text: str) -> bool:
        candidate = binding_code(text)
        if candidate is None:
            return False
        with self.lock:
            if self.state != "connected" or not self.pending:
                return False
            phone, code, expiry = self.pending
            if self.clock() >= expiry:
                return False
            if not secrets.compare_digest(candidate.encode(), code.encode()):
                if sender == phone:
                    self.binding_status = "wrong_code"
                return False
            if sender != phone:
                self.binding_status = "different_number" if sender else "unverified_chat"
                self.observed_phone = sender
                return False
            saved = {"account": self.account, "phone": phone, "binding_id": secrets.token_hex(12)}
            try:
                write_private(self.binding_file, saved)
            except OSError:
                self.binding_status = "save_failed"
                raise
            self.saved = saved
            self._clear_challenge()
            return True

    def revoke(self) -> None:
        with self.lock:
            self.binding_file.unlink(missing_ok=True)
            self.saved = {}
            self._clear_challenge()

    def route(self) -> tuple[str, str] | None:
        with self.lock:
            owner = self._owner()
            if self.state != "connected" or not owner:
                return None
            key = "|".join((self.account, owner, self.saved["binding_id"]))
            return key, owner
