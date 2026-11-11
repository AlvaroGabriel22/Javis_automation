import argparse
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.db import REVISION, new_engine, ready, utcnow
from jarvis.bootstrap.settings import ConfigurationError
from jarvis.modules.identity.service import issue_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Administração local do Jarvis")
    sub = parser.add_subparsers(dest="command", required=True)
    code = sub.add_parser("login-code", help="emitir código do proprietário, válido dez minutos")
    code.add_argument("--name", help="nome do primeiro proprietário")
    sub.add_parser("doctor", help="verificar instalação local")
    args = parser.parse_args()
    try:
        engine = new_engine()
        try:
            if not ready(engine):
                print("Migração pendente; execute alembic upgrade head.", file=sys.stderr)
                return 1
            if args.command == "login-code":
                with Session(engine) as db:
                    raw = issue_code(db, args.name, utcnow())
                print(
                    "Código válido por dez minutos; use-o somente no painel local.", file=sys.stderr
                )
                print(raw)
            else:
                now = datetime.now(UTC)
                print(f"Banco: {engine.dialect.name} · migração {REVISION} (OK)")
                print(f"Relógio UTC: {now.isoformat(timespec='seconds')}")
                print(
                    f"Manaus: {now.astimezone(ZoneInfo('America/Manaus')).isoformat(timespec='seconds')}"
                )
                from jarvis.adapters.whatsapp.ipc import call

                status = call("status")
                print(f"Conversa WhatsApp: {status['state']}. Envios agendados aguardam a S02.")
            return 0
        finally:
            engine.dispose()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
    except SQLAlchemyError:
        print(
            "Banco ou migração indisponível; confira a configuração e execute alembic upgrade head.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
