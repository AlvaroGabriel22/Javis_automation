"""Fluxo do dono desde a foto até o arquivo real baixado no painel."""

import json
import re
from io import BytesIO
from unittest.mock import Mock

from openpyxl import load_workbook
from PIL import Image, ImageDraw
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_commands import ROUTE, process

from jarvis.adapters.ai import vision
from jarvis.entrypoints.worker import process_one
from jarvis.modules.messaging import queue
from jarvis.modules.messaging.models import Outbound
from jarvis.modules.spreadsheets import exports


def test_cupom_confirmado_chega_ao_excel_e_correcao_preserva_linha(
    engine, clock, signed, monkeypatch
):
    monkeypatch.setattr(exports, "active_route", lambda: ROUTE)
    connection = Mock()
    result = connection.return_value.getresponse.return_value
    result.status = 200
    result.read.return_value = json.dumps(
        {
            "done": True,
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "description": "Cupom de compra sintético.",
                        "receipt": {
                            "currency": "BRL",
                            "merchant": "Mercado Aurora",
                            "day": "11/01/2031",
                            "subtotal": "54,75",
                            "discount": "5,00",
                            "surcharge": None,
                            "total_candidates": ["49,75"],
                            "tendered": "50,00",
                            "change": "0,25",
                        },
                    }
                ),
            },
        }
    ).encode()
    monkeypatch.setattr(vision.http.client, "HTTPConnection", connection)
    picture = Image.new("RGB", (360, 160), "white")
    draw = ImageDraw.Draw(picture)
    draw.multiline_text((10, 10), "MERCADO AURORA\n11/01/2031\nTOTAL R$49,75", fill="black")
    image = BytesIO()
    picture.save(image, "JPEG")
    assert queue.accept_inbound(
        engine,
        chat_id=ROUTE,
        message_id="imagem",
        text="[Imagem]",
        kind="image",
        media=image.getvalue(),
        now=clock(),
    )
    assert process_one(engine, Mock(), clock=clock)
    with Session(engine) as db:
        proposal = db.scalars(select(Outbound.text)).one()
    match = re.search(r"C(\d+)", proposal)
    assert match, proposal
    number = match[1]
    overview = "/api/v1/finance"
    view = signed.get(overview).json()
    assert view["entries"] == []
    signed.headers["X-Jarvis-Binding"] = view["binding_token"]
    confirmation = process(engine, clock, msg_id="sim", text=f"confirmar cupom C{number}")
    assert f"F{number}" in confirmation
    process(engine, clock, msg_id="repeticao", text=f"confirmar cupom C{number}")

    def rows():
        assert exports.sync_one(engine, "excel", now=clock())
        reply = signed.get(overview + "/exports/excel/file")
        assert reply.status_code == 200, reply.text if reply.status_code != 200 else ""
        with BytesIO(reply.content) as content:
            book = load_workbook(content)
            try:
                return list(book["Jarvis"].values)
            finally:
                book.close()

    first = rows()
    assert len(first) == 2 and first[1][:5] == (
        f"F{number}",
        "2031-01-11",
        "Despesa",
        "Mercado Aurora",
        49.75,
    )
    entry = signed.get(overview).json()["entries"][0]
    corrected = signed.patch(
        overview + "/entries/" + entry["id"],
        json={"amount_cents": 4500, "expected_version": entry["version"]},
    )
    assert corrected.status_code == 204
    assert signed.get(overview + "/exports/excel/file").status_code == 409
    second = rows()
    assert len(second) == 2 and second[1][:4] == first[1][:4] and second[1][4] == 45
