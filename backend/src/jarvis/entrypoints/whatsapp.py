"""Uma instância exclusiva por sessão. Controle local do pareamento e saída da conversa."""

import fcntl
import logging
import os
import signal
from datetime import UTC, datetime
from threading import Event, Thread
from time import monotonic

from neonize.client import NewClient
from sqlalchemy.exc import SQLAlchemyError

from jarvis.adapters.persistence.db import new_engine, ready
from jarvis.adapters.whatsapp.gateway import Gateway
from jarvis.adapters.whatsapp.ipc import Control
from jarvis.adapters.whatsapp.profile import Profile, state_dir
from jarvis.modules.calendar.notices import tick
from jarvis.modules.messaging.queue import recover_deliveries
from jarvis.modules.scheduling import dispatch

log = logging.getLogger(__name__)


def main() -> int:
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO)
    # O SDK tem logs de depuração que podem conter dados de protocolo.
    logging.getLogger("neonize").setLevel(logging.CRITICAL)
    root = state_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.stat().st_mode & 0o077:
        raise RuntimeError("O diretório do WhatsApp precisa de permissão 0700.")
    with (root / "gateway.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.error("Outro processo já possui a sessão do WhatsApp.")
            return 1
        engine = new_engine()
        if not ready(engine):
            log.error("Aplique as migrações deste perfil antes de iniciar o WhatsApp.")
            return 1
        profile = Profile(root)
        client = NewClient(str(root / "session.sqlite3"))
        gateway = Gateway(engine, profile, client)
        gateway.register()
        stop = Event()
        connecting = Event()

        def shutdown(*_args):
            stop.set()
            if connecting.is_set():
                client.stop()

        def directory():
            last = None
            next_sync = 0.0
            while not stop.is_set():
                route = profile.route()
                if route and (route != last or monotonic() >= next_sync):
                    last, next_sync = route, monotonic() + 120
                    try:
                        gateway.sync_contacts(route)
                    except Exception as err:
                        log.warning("Agenda do aparelho indisponível (%s).", type(err).__name__)
                stop.wait(1)

        def personal_notices():
            while not stop.is_set():
                if route := profile.route():
                    try:
                        tick(engine, route[0], datetime.now(UTC))
                    except (SQLAlchemyError, ValueError, OverflowError) as exc:
                        log.warning("Avisos pessoais indisponíveis (%s).", type(exc).__name__)
                stop.wait(5)

        def transmit():
            next_recovery = 0.0
            while not stop.is_set():
                try:
                    if profile.restart.is_set():
                        profile.restart.clear()
                        if connecting.is_set():
                            client.stop()
                    if monotonic() >= next_recovery:
                        recover_deliveries(engine)
                        if route := profile.route():
                            dispatch.recover(engine, route[0], datetime.now(UTC))
                        next_recovery = monotonic() + 10
                    answered = gateway.send_next()
                    if gateway.send_scheduled() or answered:
                        continue
                except SQLAlchemyError as err:
                    log.error("Fila indisponível (%s).", type(err).__name__)
                stop.wait(0.5)

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
        path = root / "control.sock"
        with Control(path, profile) as ipc:
            server = Thread(target=ipc.serve_forever, daemon=True)
            sender = Thread(target=transmit, daemon=True)
            server.start()
            sender.start()
            catalog = Thread(target=directory, daemon=True)
            catalog.start()
            Thread(target=personal_notices, daemon=True).start()
            log.info("Gateway pronto. Abra WhatsApp no painel para iniciar o pareamento.")
            try:
                while not stop.is_set():
                    if not profile.start.wait(0.5) or stop.is_set():
                        continue
                    try:
                        connecting.set()
                        if not stop.is_set():
                            client.connect()
                    except Exception as err:
                        log.warning("Conexão interrompida (%s).", type(err).__name__)
                    finally:
                        connecting.clear()
                    if profile.start.is_set():
                        profile.disconnected()
                    stop.wait(3)
            finally:
                stop.set()
                ipc.shutdown()
                server.join(3)
                sender.join(3)
                catalog.join(3)
                path.unlink(missing_ok=True)
                engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
