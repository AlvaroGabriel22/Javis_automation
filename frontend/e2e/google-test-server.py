"""Inicia o painel real com os dois endpoints externos de OAuth substituídos no teste."""

import runpy
from unittest.mock import patch

from jarvis.adapters.google import client


def response(method, url, **options):
    if (
        method == "POST"
        and url == client.TOKEN
        and options["data"]["code"] == "test-code"
    ):
        return {
            "access_token": "synthetic",
            "refresh_token": "synthetic-refresh",
            "scope": client.SCOPES,
        }
    if (
        method == "GET"
        and url
        == "https://www.googleapis.com/calendar/v3/users/me/calendarList/primary"
    ):
        return {"id": "agenda@example.invalid"}
    raise AssertionError("Pedido inesperado ao provedor simulado.")


with patch.object(client, "http", response):
    runpy.run_path("scripts/panel.py", run_name="__main__")
