"""Create a private PostgreSQL configuration without modifying an existing one."""

import os
import secrets
from pathlib import Path

if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    os.umask(0o077)
    sample = (root / ".env.example").read_text()
    line = "POSTGRES_PASSWORD="
    assert sample.splitlines().count(line) == 1
    config = (
        "\n".join(
            line + secrets.token_urlsafe(32) if item == line else item
            for item in sample.splitlines()
        )
        + "\n"
    )
    try:
        with (root / ".env").open("x") as out:
            out.write(config)
        print("Configuração criada. A senha do banco está no .env privado.")
    except FileExistsError:
        print("A configuração local já existe.")
