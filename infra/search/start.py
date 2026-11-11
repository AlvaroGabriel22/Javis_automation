"""Inicia o buscador privado do Jarvis e aguarda sua API em loopback."""

import os
import secrets
import subprocess
import sys
import time
from http.client import HTTPConnection
from pathlib import Path
from tempfile import NamedTemporaryFile


def instance_secret():
    data = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share"))
    folder = data / "jarvis" / "search"
    folder.mkdir(parents=True, mode=0o700, exist_ok=True)
    path = folder / "secret"
    if not path.exists():
        # Uma segunda inicialização pode ter criado o segredo enquanto este era preparado.
        with NamedTemporaryFile(dir=folder) as temporary:
            temporary.write(secrets.token_hex(32).encode())
            temporary.flush()
            try:
                os.link(temporary.name, path)
            except FileExistsError:
                pass
    return path.read_text().strip()


def main():
    os.umask(0o077)
    try:
        env = dict(os.environ, SEARXNG_SECRET=instance_secret())
        compose = Path(__file__).with_name("compose.yaml").resolve()
        subprocess.run(
            ["docker", "compose", "-f", str(compose), "up", "-d"], env=env, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        print(
            "Confira se o Docker Compose está instalado e se seu usuário tem acesso ao Docker.",
            file=sys.stderr,
        )
        return 1
    for _ in range(25):
        conn = HTTPConnection("127.0.0.1", 8888, timeout=2)
        try:
            conn.request("GET", "/healthz")
            response = conn.getresponse()
            if response.status == 200:
                print(
                    "SearXNG pronto em http://127.0.0.1:8888. No WhatsApp, peça ao Jarvis para pesquisar."
                )
                return 0
        except OSError:
            pass
        finally:
            conn.close()
        time.sleep(1)
    print(
        "O contêiner iniciou, mas o SearXNG ainda não respondeu em 127.0.0.1:8888.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
