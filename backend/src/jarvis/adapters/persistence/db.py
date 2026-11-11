from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL, make_url

from jarvis.bootstrap.settings import ConfigurationError, read_settings

REVISION = "0019_owner_tone"


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_engine(url: str | URL | None = None) -> Engine:
    parsed = make_url(url) if url else read_settings().database_url()
    if parsed.get_backend_name() == "sqlite":
        # SQLite ignora permissões na URL. Crie o arquivo da demonstração privado.
        if (
            parsed.database
            and parsed.database != ":memory:"
            and not parsed.database.startswith("file:")
        ):
            try:
                Path(parsed.database).touch(mode=0o600, exist_ok=False)
            except FileExistsError:
                pass
            except OSError:
                raise ConfigurationError(
                    "Não foi possível criar o banco de demonstração."
                ) from None
        engine = create_engine(parsed, connect_args={"check_same_thread": False, "timeout": 10})

        @event.listens_for(engine, "connect")
        def sqlite_init(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(parsed, pool_pre_ping=True, connect_args={"connect_timeout": 3})


def ready(engine: Engine) -> bool:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [
            REVISION
        ]
