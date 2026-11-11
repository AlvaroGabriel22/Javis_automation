"""Autorização iniciada pelo dono no painel local. O código OAuth só passa pelo loopback."""

import fcntl
import json
import os
from threading import Lock, Thread

from jarvis.adapters.google import client as google
from jarvis.adapters.whatsapp.profile import write_private
from jarvis.modules.errors import Problem

MAX_CLIENT_FILE = 32 * 1024


def sync_running():
    try:
        with (google.root() / "sync.lock").open("rb") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except FileNotFoundError:
        pass
    return False


class PanelLink:
    def __init__(self):
        self.lock = Lock()
        self.phase = "idle"
        self.message = None
        self.url = None

    def _status(self):
        try:
            google.credentials()
            configured = True
        except google.ConsentError:
            configured = False
        try:
            account = google.account()
        except google.ConsentError as err:
            raise Problem(409, "google_profile", str(err)) from None
        return dict(
            configured=configured,
            account=account,
            phase=self.phase,
            message=self.message,
            sync_running=sync_running(),
            demo=os.getenv("JARVIS_PROFILE") == "demo",
        )

    def status(self):
        with self.lock:
            return self._status()

    def import_client(self, raw: bytes):
        try:
            fields = google.desktop_client(json.loads(raw))
        except google.ConsentError as err:
            raise Problem(422, "google_client", str(err)) from None
        except (ValueError, RecursionError):
            raise Problem(
                422,
                "google_json",
                "Não consegui ler esse JSON. Selecione o arquivo baixado do Google Cloud.",
            ) from None
        with self.lock:
            if self.phase in ("waiting", "exchanging"):
                raise Problem(409, "google_pending", "Já existe uma autorização em andamento.")
            try:
                folder = google.root()
                folder.mkdir(mode=0o700, parents=True, exist_ok=True)
                write_private(folder / "client.json", {"installed": fields})
            except OSError:
                raise Problem(
                    503,
                    "google_save",
                    "Não consegui salvar a credencial no perfil local. Confira as permissões da pasta do Jarvis.",
                ) from None
            self.phase, self.message = "idle", None
            return self._status()

    def start(self):
        with self.lock:
            if self.phase in ("waiting", "exchanging"):
                return {"url": self.url, "phase": self.phase}
            try:
                flow = google.DesktopConsent(google.credentials())
            except google.ConsentError as err:
                raise Problem(422, "google_client", str(err)) from None
            except OSError:
                raise Problem(
                    503,
                    "google_loopback",
                    "Não consegui abrir a porta local de retorno. Tente novamente no computador do Jarvis.",
                ) from None
            self.url, self.phase, self.message = flow.url, "waiting", None
            Thread(target=self._complete, args=(flow,), daemon=True).start()
            return {"url": self.url, "phase": self.phase}

    def _exchanging(self):
        with self.lock:
            self.url, self.phase = None, "exchanging"

    def _complete(self, flow):
        error = "A autorização foi interrompida. Tente novamente."
        try:
            flow.finish(self._exchanging)
            error = None
        except google.ConsentError as exc:
            error = str(exc)
        except google.GoogleError as exc:
            error = {
                400: "O Google recusou o código ou a credencial. Confira o cliente OAuth e tente novamente.",
                401: "O Google recusou a credencial. Importe o JSON atual e tente novamente.",
                403: "O Google negou acesso. Confira se a Calendar API está ativada, se sua conta é usuária de teste e se você aceitou as permissões.",
            }.get(
                exc.status,
                "Não foi possível consultar o Google. Confira a conexão e tente novamente.",
            )
        except (OSError, ValueError, TypeError, KeyError):
            error = "Não consegui concluir o vínculo. Confira a credencial e a pasta de dados do Jarvis."
        finally:
            with self.lock:
                # Mesmo uma interrupção inesperada libera uma nova tentativa.
                self.url = None
                self.phase = "failed" if error else "complete"
                self.message = error
