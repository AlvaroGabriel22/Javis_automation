import errno
import stat
import tempfile
from threading import Thread

import pytest

from jarvis.adapters.whatsapp import ipc
from jarvis.adapters.whatsapp.profile import Profile, state_dir


def test_real_local_control_socket(monkeypatch):
    # Caminho curto para respeitar o limite de endereço AF_UNIX do Linux.
    with tempfile.TemporaryDirectory(prefix="jarvis-ipc-") as root:
        monkeypatch.setenv("XDG_DATA_HOME", root)
        monkeypatch.setenv("JARVIS_PROFILE", "demo")
        profile_path = state_dir()
        profile_path.mkdir(parents=True, mode=0o700)
        profile = Profile(profile_path)
        path = profile_path / "control.sock"
        try:
            server = ipc.Control(path, profile)
        except PermissionError as exc:
            if exc.errno == errno.EPERM:
                pytest.skip("O ambiente bloqueia bind em sockets Unix.")
            raise
        with server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                assert stat.S_IMODE(path.stat().st_mode) == 0o600
                assert ipc.call("pair")["state"] == "connecting"
                profile.on_qr(b"synthetic-test")
                assert ipc.call("status")["qr"].startswith("data:image/png")
                assert ipc.call("health") == {"connected": False}
            finally:
                server.shutdown()
                thread.join(3)
