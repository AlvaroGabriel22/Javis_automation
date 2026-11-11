"""RPC local sobre socket Unix privado. Nunca registra os payloads de pareamento."""

import json
import socket
import socketserver
from pathlib import Path

from jarvis.adapters.whatsapp.profile import Profile, state_dir
from jarvis.modules.errors import Problem

LIMIT = 128 * 1024


def dispatch(profile: Profile, packet: dict) -> dict:
    op = packet.get("op")
    if op == "health":
        return {
            "connected": profile.snapshot()["state"] == "connected",
            "delivery_enabled": bool(profile.route()),
        }
    if op == "route":
        route = profile.route()
        return {"route_key": route[0] if route else None}
    if op == "status":
        return profile.snapshot()
    if op == "pair":
        return profile.pairing()
    if op == "challenge":
        return profile.challenge(str(packet.get("phone", "")))
    if op == "revoke":
        profile.revoke()
        return profile.snapshot()
    raise Problem(400, "unknown_command", "Operação desconhecida.")


class Control(socketserver.UnixStreamServer):
    def __init__(self, path: Path, profile: Profile):
        self.profile = profile
        path.unlink(missing_ok=True)  # O chamador já possui o lock exclusivo do gateway.
        super().__init__(str(path), Handler)
        path.chmod(0o600)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(2)
        try:
            raw = self.rfile.readline(2049)
            if len(raw) > 2048 or not raw.endswith(b"\n"):
                return
            packet = json.loads(raw)
            if not isinstance(packet, dict) or not isinstance(self.server, Control):
                return
            try:
                res = {"data": dispatch(self.server.profile, packet)}
            except Problem as e:
                res = {"status": e.status, "code": e.code, "message": e.message}
            self.wfile.write(json.dumps(res).encode() + b"\n")
        except (OSError, ValueError):
            return


def call(op: str, *, phone: str = "") -> dict:
    path = state_dir() / "control.sock"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(path))
            client.sendall(json.dumps({"op": op, "phone": phone}).encode() + b"\n")
            with client.makefile("rb") as stream:
                raw = stream.readline(LIMIT + 1)
            if len(raw) > LIMIT or not raw.endswith(b"\n"):
                raise ValueError
            result = json.loads(raw)
            if "data" in result:
                return result["data"]
            raise Problem(result["status"], result["code"], result["message"])
    except (OSError, ValueError, KeyError):
        if op == "route":
            return {"route_key": None}
        if op == "health":
            return {"connected": False}
        if op == "status":
            return {
                "state": "offline",
                "account": None,
                "owner_phone": None,
                "pending_phone": None,
                "qr": None,
                "channel": "conversation",
            }
        raise Problem(
            503, "gateway_offline", "Inicie o processo WhatsApp do Jarvis na máquina."
        ) from None
