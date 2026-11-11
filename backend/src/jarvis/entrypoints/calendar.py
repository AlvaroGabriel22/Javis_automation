"""Sincronizador independente da fila e da inferência."""

import argparse
import fcntl
import logging
import os
from threading import Thread
from time import sleep

from sqlalchemy.exc import SQLAlchemyError

from jarvis.adapters.calendar_refresh import refresh_google
from jarvis.adapters.google.client import ConsentError, Google, GoogleError, connect, root
from jarvis.adapters.icloud.account import sync as sync_icloud
from jarvis.adapters.persistence.db import new_engine


def icloud_loop(engine):
    while True:
        try:
            sync_icloud(engine)
        except (OSError, SQLAlchemyError) as exc:
            logging.warning("Falha na sincronização iCloud (%s).", type(exc).__name__)
        sleep(60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--connect", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if args.connect:
        try:
            connect()
        except (GoogleError, KeyError, ValueError, OSError) as err:
            print(
                str(err)
                if isinstance(err, (GoogleError, ConsentError))
                else "Não foi possível concluir a autorização."
            )
            return 1
        print("Google Agenda autorizado. O painel atualizará as agendas na próxima sincronização.")
        return 0
    folder = root()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (folder / "sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 1
        engine = new_engine()
        Thread(target=icloud_loop, args=(engine,), name="icloud-sync", daemon=True).start()
        google = Google()
        while True:
            if (folder / "token.json").exists():
                try:
                    if refresh_google(engine, google):
                        (folder / "error").unlink(missing_ok=True)
                except (GoogleError, KeyError, ValueError, OSError, SQLAlchemyError) as exc:
                    (folder / "error").write_text(
                        str(exc) if isinstance(exc, GoogleError) else "Sincronização indisponível."
                    )
                    logging.warning("Falha na sincronização de agendas (%s).", type(exc).__name__)
            sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
