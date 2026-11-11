import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.db import new_engine
from jarvis.api import create_app
from jarvis.modules.identity.service import issue_code

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2031, 1, 11, 12, tzinfo=UTC)


def migrate(engine: Engine) -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
def isolated_channel_profile(monkeypatch, tmp_path):
    # Uma suíte local nunca consulta o gateway ou a sessão WhatsApp da máquina.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("JARVIS_PROFILE", "demo")


@pytest.fixture
def engine(tmp_path):
    url = os.getenv("JARVIS_TEST_DATABASE_URL")
    if not url:
        db = new_engine("sqlite:///" + str(tmp_path / "case.sqlite3"))
        try:
            migrate(db)
            yield db
        finally:
            db.dispose()
        return
    # Cada caso tem schema exclusivo, inclusive em paralelo. Nunca esvaziar o banco do usuário.
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("JARVIS_TEST_DATABASE_URL deve apontar para um PostgreSQL de testes.")
    admin = create_engine(parsed.set(drivername="postgresql+psycopg"))
    schema = "jarvis_test_" + uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    db = create_engine(admin.url, connect_args={"options": "-csearch_path=" + schema})
    try:
        migrate(db)
        yield db
    finally:
        db.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


class Clock:
    now = START

    def __call__(self):
        return self.now


def client_for(engine, clock, peer="127.0.0.1"):
    return TestClient(
        create_app(engine, clock),
        base_url="http://127.0.0.1:8787",
        client=(peer, 5151),
        backend_options={"use_uvloop": sys.platform == "linux"},
    )


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client(engine, clock):
    with client_for(engine, clock) as c:
        yield c


@pytest.fixture
def code(engine, clock):
    with Session(engine) as db:
        return issue_code(db, "Dono de teste", clock())


@pytest.fixture
def signed(client, code):
    auth = client.post("/api/v1/auth/login", json={"code": code})
    assert auth.status_code == 200, auth.text
    client.headers["X-CSRF-Token"] = auth.json()["csrf_token"]
    return client
