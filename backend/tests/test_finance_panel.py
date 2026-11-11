"""Exercita o painel pelo HTTP e as mesmas transações usadas pelo WhatsApp."""

import pytest
from test_assistant_commands import OTHER, ROUTE, process

from jarvis.modules.spreadsheets import exports

API = "/api/v1/finance"
MONTH = "2031-01"


@pytest.fixture
def route(monkeypatch, signed):
    current = [ROUTE]
    monkeypatch.setattr(exports, "active_route", lambda: current[0])
    signed.headers["X-Jarvis-Binding"] = signed.get(API).json()["binding_token"]
    return current


def test_panel_updates_same_ledger_used_in_voice_and_whatsapp(engine, clock, signed, route):
    process(engine, clock, msg_id="budget", text="Meu orçamento mensal é de R$ 500")
    process(engine, clock, msg_id="purchase", text="Gastei R$ 120 no mercado")
    process(engine, clock, msg_id="salary", text="Recebi 100 reais de comissão")
    panel = signed.get(API, params={"month": MONTH})
    assert panel.status_code == 200, panel.text
    first = panel.json()
    assert first["bound"] and first["totals"]["available_cents"] == 38000
    expense = next(i for i in first["entries"] if i["kind"] == "expense")
    assert (
        signed.put(API + "/reserve", json={"month": MONTH, "amount_cents": 9000}).status_code == 204
    )
    correction = {"expected_version": expense["version"], "amount_cents": 10985}
    assert signed.patch(f"{API}/entries/{expense['id']}", json=correction).status_code == 204
    stale = signed.patch(f"{API}/entries/{expense['id']}", json=correction)
    assert stale.status_code == 409
    totals = signed.get(API, params={"month": MONTH}).json()["totals"]
    assert totals == {
        "budget_cents": 50000,
        "reserve_cents": 9000,
        "expense_cents": 10985,
        "income_cents": 10000,
        "available_cents": 30015,
    }
    assert not signed.get(API, params={"month": "2030-12"}).json()["entries"]
    route[0] = OTHER
    refreshed = signed.get(API).json()
    assert not refreshed["entries"]
    stale_binding = signed.put(API + "/reserve", json={"month": MONTH, "amount_cents": 555})
    assert (
        stale_binding.status_code == 409
        and stale_binding.json()["detail"]["code"] == "finance_binding_changed"
    )
    assert signed.get(API + "/exports/excel/file").status_code == 409
    assert refreshed["binding_token"] != signed.headers["X-Jarvis-Binding"]
    signed.headers["X-Jarvis-Binding"] = refreshed["binding_token"]
    assert signed.get(API).json()["totals"]["reserve_cents"] == 0
    assert signed.patch(f"{API}/entries/{expense['id']}", json=correction).status_code == 404


def test_notes_replay_and_changed_binding_do_not_disclose(signed, route):
    data = {"day": "2031-01-11", "description": "=literal", "quantity": "2 unidades"}
    headers = {"Idempotency-Key": "jan-note"}
    first = signed.post(API + "/notes", json=data, headers=headers)
    assert first.status_code == 201
    assert signed.post(API + "/notes", json=data, headers=headers).json() == first.json()
    assert (
        signed.post(API + "/notes", json={**data, "quantity": "3"}, headers=headers).status_code
        == 409
    )
    notes = signed.get(API, params={"month": MONTH}).json()["notes"]
    assert len(notes) == 1 and notes[0]["id"] == first.json()["id"]
    route[0] = None
    empty = signed.get(API, params={"month": MONTH}).json()
    assert empty["notes"] == [] and empty["bound"] is False
    for path, method, kwargs in [
        ("/notes", "post", {"json": data, "headers": headers}),
        ("/exports/excel", "post", {}),
        ("/exports/excel/file", "get", {}),
        ("/exports/google", "put", {"json": {"enabled": True}}),
    ]:
        assert getattr(signed, method)(API + path, **kwargs).status_code == 409


@pytest.mark.parametrize("month", ["", "2026-13", "2026-11-09", "9999-12", "202601"])
def test_month_validation(signed, route, month):
    assert signed.get(API, params={"month": month}).status_code == 422


def test_finance_requires_owner_and_csrf(client, code):
    assert client.get(API).status_code == 401
    assert client.get(API + "/exports/excel/file").status_code == 401
    assert client.post("/api/v1/auth/login", json={"code": code}).status_code == 200
    assert client.post(API + "/exports/excel").status_code == 403
    assert client.put(API + "/exports/google", json={"enabled": True}).status_code == 403
