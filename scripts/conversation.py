"""Inicia o painel, o gateway e o worker do mesmo perfil e encerra os três juntos."""

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    os.chdir(ROOT)
    if not (ROOT / "frontend/dist/index.html").exists():
        print("Compile o painel com make build.", file=sys.stderr)
        return 1
    if args.demo:
        demo = (
            Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
            / "jarvis/demo"
        )
        demo.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.environ.update(
            JARVIS_PROFILE="demo",
            JARVIS_DATABASE_URL="sqlite:///" + str(demo.resolve() / "jarvis.sqlite3"),
        )
    else:
        os.environ["JARVIS_PROFILE"] = "local"
    py = sys.executable
    subprocess.run(
        [py, "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
        check=True,
    )
    children: list[subprocess.Popen] = []
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for cmd in (
            ["scripts/panel.py", "serve"],
            ["-m", "jarvis.entrypoints.whatsapp"],
            ["-m", "jarvis.entrypoints.worker"],
            ["-m", "jarvis.entrypoints.calendar"],
            ["-m", "jarvis.entrypoints.sheets"],
        ):
            children.append(subprocess.Popen([py, *cmd], start_new_session=True))
        while not stopping:
            for child in children:
                status = child.poll()
                if status is not None:
                    print(
                        "Um serviço encerrou. Confira a mensagem anterior.",
                        file=sys.stderr,
                    )
                    return status or 1
            time.sleep(0.4)
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    sys.exit(main())
