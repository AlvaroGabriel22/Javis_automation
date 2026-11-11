"""Executa a API e o painel reais substituindo a descoberta do iCloud no teste."""

import runpy
from unittest.mock import patch

from jarvis.adapters.icloud.client import AppleError, CalDAV, RemoteCalendar


def calendars(client):
    if client.auth != ("example@example.invalid", "app-synthetic"):
        raise AppleError(401)
    return [
        RemoteCalendar(
            "https://p01-caldav.icloud.com/123/calendars/home/", "Pessoal", True
        )
    ]


with patch.object(CalDAV, "calendars", calendars):
    runpy.run_path("scripts/panel.py", run_name="__main__")
