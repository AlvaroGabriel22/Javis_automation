"""Painel e exportador reais em perfil temporário com um vínculo simulado."""

import os
import runpy
from contextlib import ExitStack
from datetime import UTC, datetime
from threading import Event, Thread
from unittest.mock import patch

import uvicorn
from jarvis.adapters.whatsapp import ipc

ROUTE = "+5592000000011|+5592000000012|finance-e2e"
OTHER = "+5592000000013|+5592000000014|outro"
clock = datetime(2031, 2, 10, 15, tzinfo=UTC)
original_call = ipc.call
serve = uvicorn.run


def fake_device(op, **kwargs):
    return {"route_key": ROUTE} if op == "route" else original_call(op, **kwargs)


def run(app, **kwargs):
    from sqlalchemy.orm import Session

    from jarvis.adapters.ai.provider import UnavailableChat
    from jarvis.adapters.persistence.db import new_engine
    from jarvis.adapters.persistence.models import Owner
    from jarvis.entrypoints.worker import process_one
    from jarvis.modules.messaging.queue import accept_inbound
    from jarvis.modules.spreadsheets.exports import sync_one

    engine = new_engine()
    with Session(engine) as db, db.begin():
        db.add(Owner(id=1, name="Proprietário de teste"))

    for index, (chat, message) in enumerate(
        (
            (ROUTE, "Meu orçamento mensal é R$ 500"),
            (ROUTE, "Gastei 20 reais no mercado"),
            (OTHER, "Gastei 80 reais no aeroporto"),
        )
    ):
        assert accept_inbound(
            engine, chat_id=chat, message_id=str(index), text=message, now=clock
        )
        assert process_one(engine, UnavailableChat(), clock=lambda: clock)
    stop = Event()

    def synchronize():
        while not stop.wait(0.2):
            sync_one(engine, "excel")
            if os.environ.get("JARVIS_E2E_SHEETS") == "1":
                sync_one(engine, "google")

    worker = Thread(target=synchronize, daemon=True)
    worker.start()
    try:
        serve(app, **kwargs)
    finally:
        stop.set()
        worker.join(5)
        engine.dispose()


# O servidor de testes não carrega credenciais ou destinos reais da instalação.
os.environ.update(
    JARVIS_ENV_FILE=os.devnull,
    JARVIS_GOOGLE_SHEETS_ID="",
    JARVIS_GOOGLE_SHEETS_CREDENTIALS="",
)
with ExitStack() as overrides:
    overrides.enter_context(patch.object(ipc, "call", fake_device))
    overrides.enter_context(patch.object(uvicorn, "run", run))
    if os.environ.get("JARVIS_E2E_SHEETS") == "1":
        from jarvis.adapters import sheets

        overrides.enter_context(
            patch.object(sheets, "google_configured", return_value=True)
        )
        overrides.enter_context(
            patch.object(sheets, "google_destination_id", return_value="synthetic-book")
        )
        overrides.enter_context(
            patch.object(
                sheets,
                "write_google",
                side_effect=[
                    sheets.SheetError("Acesso temporariamente recusado."),
                    None,
                ],
            )
        )
    runpy.run_path("scripts/panel.py", run_name="__main__")
