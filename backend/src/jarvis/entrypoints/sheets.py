"""Worker de planilhas, isolado do processamento de conversa e agendamentos."""

import argparse
import logging
import os
import signal
from threading import Event

from sqlalchemy.exc import SQLAlchemyError

from jarvis.adapters.persistence.db import new_engine
from jarvis.modules.spreadsheets.exports import TARGETS, sync_one


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda _sig, _frame: stop.set())
    engine = new_engine()
    try:
        while not stop.is_set():
            failed = False
            for target in TARGETS:
                try:
                    sync_one(engine, target)
                except (OSError, SQLAlchemyError) as exc:
                    logging.warning("Planilha %s indisponível (%s).", target, type(exc).__name__)
                    failed = True
            if args.once:
                return int(failed)
            stop.wait(10)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
