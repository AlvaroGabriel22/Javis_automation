"""OAuth Desktop com PKCE e callback exclusivo de loopback."""

import base64
import fcntl
import hashlib
import json
import os
import secrets
import time
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import requests
from cryptography.fernet import Fernet, InvalidToken

from jarvis.adapters.whatsapp.profile import state_dir, write_private

TOKEN = "https://oauth2.googleapis.com/token"
SCOPES = "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.calendarlist.readonly"


class GoogleError(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__(
            f"Google Agenda indisponível (HTTP {status})."
            if status
            else "Google Agenda indisponível. Confira conexão e credenciais."
        )


def root():
    return state_dir().parent / "google"


class ConsentError(ValueError):
    """Mensagem que pode ser exibida ao proprietário."""


def legacy_path():
    return Path(
        os.getenv("JARVIS_GOOGLE_CLIENT_FILE") or Path.home() / ".config/jarvis/google-client.json"
    )


def desktop_client(data):
    installed = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(installed, dict):
        raise ConsentError(
            "Selecione o JSON de um cliente OAuth do tipo Aplicativo para computador."
        )
    fields = {}
    for key in ("client_id", "client_secret"):
        value = installed.get(key)
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 2048
            or not value.isascii()
            or not value.isprintable()
            or any(c.isspace() for c in value)
        ):
            raise ConsentError(
                "O arquivo está incompleto. Baixe novamente o JSON do cliente OAuth no Google Cloud."
            )
        fields[key] = value
    return fields


def read_client(path):
    try:
        return desktop_client(json.loads(path.read_bytes()))
    except (OSError, ValueError, RecursionError):
        raise ConsentError("Importe o JSON do aplicativo para computador para continuar.") from None


def credentials():
    saved = root() / "client.json"
    return read_client(saved if saved.is_file() else legacy_path())


def account():
    try:
        value = json.loads((root() / "token.json").read_text())["calendar"]
        if not isinstance(value, str) or not value:
            raise ValueError
        return value
    except FileNotFoundError:
        return None
    except (KeyError, TypeError, ValueError):
        raise ConsentError("Não foi possível ler o vínculo Google deste perfil.") from None


def http(method, url, **kwargs):
    try:
        res = requests.request(method, url, timeout=(5, 20), allow_redirects=False, **kwargs)
        if not 200 <= res.status_code < 300:
            raise GoogleError(res.status_code)
        return res.json() if res.content else {}
    except (requests.RequestException, ValueError):
        raise GoogleError(0) from None


class DesktopConsent:
    def __init__(self, client):
        self.client = client
        self.verifier = secrets.token_urlsafe(64)
        state = secrets.token_urlsafe(32)
        self.result: str | None = None
        self.deadline = time.monotonic() + 300
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        flow = self

        class Callback(BaseHTTPRequestHandler):
            timeout = 4

            def log_message(self, format, *args):
                pass

            def do_GET(self):
                url = urlsplit(self.path)
                args = parse_qs(url.query)
                valid = url.path == "/" and secrets.compare_digest(
                    args.get("state", [""])[0].encode(), state.encode()
                )
                if valid:
                    flow.result = args.get("code", [""])[0] if not args.get("error") else ""
                self.send_response(200 if valid else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                message = (
                    "Volte à aba do Jarvis para conferir o resultado da autorização."
                    if valid
                    else "Pedido inválido. Inicie a conexão pelo Jarvis."
                )
                self.wfile.write(message.encode())

        self.server = HTTPServer(("127.0.0.1", 0), Callback)
        self.server.timeout = 1
        self.redirect = f"http://127.0.0.1:{self.server.server_port}/"
        self.url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            dict(
                client_id=client["client_id"],
                redirect_uri=self.redirect,
                response_type="code",
                scope=SCOPES,
                state=state,
                code_challenge=challenge,
                code_challenge_method="S256",
                access_type="offline",
                prompt="consent",
            )
        )

    def finish(self, exchanging: Callable[[], None] | None = None):
        with self.server:
            while self.result is None and time.monotonic() < self.deadline:
                self.server.handle_request()
        if self.result is None:
            raise ConsentError(
                "O prazo de cinco minutos terminou. Clique em Autorizar no Google para tentar novamente."
            )
        if not self.result:
            raise ConsentError(
                "O Google não autorizou o acesso. Se você cancelou, pode tentar novamente. "
                "Se apareceu “Acesso bloqueado”, confira os usuários de teste do projeto."
            )
        if exchanging:
            exchanging()
        tokens = http(
            "POST",
            TOKEN,
            data={
                **self.client,
                "code": self.result,
                "grant_type": "authorization_code",
                "redirect_uri": self.redirect,
                "code_verifier": self.verifier,
            },
        )
        refresh = tokens.get("refresh_token")
        if not isinstance(refresh, str) or not refresh:
            raise ConsentError(
                "O Google não liberou a renovação de acesso. Autorize novamente e aceite as permissões de agenda."
            )
        if "scope" in tokens and not set(SCOPES.split()).issubset(tokens["scope"].split()):
            raise ConsentError(
                "O Google concedeu acesso parcial. Autorize a leitura das agendas e a edição dos compromissos."
            )
        primary = http(
            "GET",
            "https://www.googleapis.com/calendar/v3/users/me/calendarList/primary",
            headers={"Authorization": "Bearer " + tokens["access_token"]},
        )["id"]
        if not isinstance(primary, str) or not primary:
            raise GoogleError(502)
        save_grant(primary, refresh, self.client)


def save_grant(primary, refresh, client):
    folder = root()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (folder / "grant.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        existing = account()
        if existing and existing != primary:
            raise ConsentError(
                "Este perfil já está vinculado a outra conta. Autorize novamente usando a conta exibida no painel."
            )
        keyfile = folder / "key"
        try:
            with open(keyfile, "xb", opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
                f.write(Fernet.generate_key())
        except FileExistsError:
            pass
        cipher = Fernet(keyfile.read_bytes())
        write_private(
            folder / "token.json",
            {
                "calendar": primary,
                "refresh": cipher.encrypt(refresh.encode()).decode(),
                "client": cipher.encrypt(json.dumps(client).encode()).decode(),
            },
        )


def connect():
    flow = DesktopConsent(credentials())
    print(
        "Autorize o Google Agenda no navegador deste computador. Se necessário, abra",
        flow.url,
        flush=True,
    )
    webbrowser.open(flow.url)
    flow.finish()


class Google:
    def __init__(self):
        self.access = ""
        self.valid_until = 0.0

    def refresh(self):
        try:
            token = json.loads((root() / "token.json").read_text())
            cipher = Fernet((root() / "key").read_bytes())
            refresh = cipher.decrypt(token["refresh"].encode()).decode()
            # Vínculos da S03 usam o JSON da instalação para renovar o token.
            client = (
                json.loads(cipher.decrypt(token["client"].encode()))
                if "client" in token
                else read_client(legacy_path())
            )
        except (OSError, KeyError, ValueError, InvalidToken):
            raise GoogleError(401) from None
        tokens = http(
            "POST", TOKEN, data={**client, "grant_type": "refresh_token", "refresh_token": refresh}
        )
        self.access = tokens["access_token"]
        self.valid_until = time.monotonic() + max(0, int(tokens.get("expires_in", 3600)) - 60)

    def request(self, method, path, *, params=None, body=None, etag=None):
        if time.monotonic() >= self.valid_until:
            self.refresh()
        headers = {"Authorization": "Bearer " + self.access}
        if etag:
            headers["If-Match"] = etag
        try:
            return http(
                method,
                "https://www.googleapis.com/calendar/v3/" + path,
                headers=headers,
                params=params,
                json=body,
            )
        except GoogleError as exc:
            if exc.status == 401:
                self.valid_until = 0.0
            raise

    def pages(self, path, params):
        items = []
        token = None
        seen = set()
        while True:
            data = self.request("GET", path, params=dict(params, pageToken=token))
            items.extend(data.get("items", []))
            token = data.get("nextPageToken")
            if not token:
                return items, data.get("nextSyncToken")
            if token in seen:
                raise GoogleError(502)
            seen.add(token)


def path_for(calendar: str, event: str | None = None):
    path = "calendars/" + quote(calendar, safe="") + "/events"
    return path if event is None else path + "/" + quote(event, safe="")
