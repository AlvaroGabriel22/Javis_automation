"""Run the local panel from a source checkout with the compiled React assets."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Painel local do Jarvis")
    parser.add_argument("command", choices=("serve", "login-code", "doctor"))
    parser.add_argument(
        "--demo", action="store_true", help="usar dados dedicados em SQLite"
    )
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    os.umask(0o077)
    os.chdir(ROOT)
    if args.demo:
        os.environ["JARVIS_PROFILE"] = "demo"
        state = (
            Path(os.getenv("XDG_DATA_HOME") or Path.home() / ".local/share")
            / "jarvis/demo"
        )
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.environ["JARVIS_DATABASE_URL"] = "sqlite:///" + str(
            state.resolve() / "jarvis.sqlite3"
        )
    if args.command != "serve":
        os.execv(sys.executable, [sys.executable, "-m", "jarvis.cli", args.command])

    assets = ROOT / "frontend/dist"
    if not (assets / "index.html").is_file():
        print("O painel precisa ser compilado. Execute make build.", file=sys.stderr)
        return 1

    import uvicorn
    from alembic import command
    from alembic.config import Config
    from fastapi.staticfiles import StaticFiles
    from jarvis.api import create_app

    if args.demo:
        command.upgrade(Config(str(ROOT / "backend/alembic.ini")), "head")
    app = create_app()
    app.mount("/", StaticFiles(directory=assets, html=True), name="painel")
    print(f"Painel local em http://127.0.0.1:{args.port}", flush=True)
    print(
        "Em outro terminal, gere o código com make "
        + (
            "demo-code"
            if args.demo or os.getenv("JARVIS_PROFILE") == "demo"
            else "login-code"
        ),
        flush=True,
    )
    uvicorn.run(
        app, host="127.0.0.1", port=args.port, proxy_headers=False, access_log=False
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
