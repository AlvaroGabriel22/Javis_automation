from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_assistant_commands import ROUTE, process

from jarvis.adapters.google.client import Google, GoogleError
from jarvis.adapters.google.sync import discover, sync_source
from jarvis.modules.calendar.models import CalendarSource, Entry
from jarvis.modules.calendar.notices import birthday_today, tick
from jarvis.modules.calendar.service import ZONE
from jarvis.modules.messaging import queue
from jarvis.modules.messaging.models import Inbound, Outbound

EVENTS = "/api/v1/events"
CAL = "/api/v1/calendar"


def add(client, key="first", **extra):
    data = dict(title="Revisão", starts_at="2031-01-13T09:00:00-04:00")
    return client.post(EVENTS, json={**data, **extra}, headers={"Idempotency-Key": key})


def listing(client, day="2031-01-13", period="week"):
    response = client.get(EVENTS, params={"anchor": day, "period": period})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def provider(engine):
    g = Mock()
    g.pages.return_value = (
        [
            dict(
                id="principal",
                summary="Minha",
                primary=True,
                accessRole="owner",
                timeZone="America/Manaus",
            )
        ],
        None,
    )
    discover(engine, g)
    return g


def remote(start, end, **extra):
    return {
        "id": "remote",
        "etag": "v1",
        "summary": "Evento do celular",
        "start": start,
        "end": end,
        **extra,
    }


def test_periods_conflict_idempotency_and_versions(signed):
    original = add(signed).json()
    assert original["id"] == add(signed).json()["id"]
    assert add(signed, title="Outro assunto").status_code == 409
    assert add(signed, "overlap", title="Conflito").json()["conflicts"] == ["Revisão"]
    assert add(
        signed,
        "multi",
        starts_at="2030-12-31T00:00:00-04:00",
        ends_at="2031-01-02T00:00:00-04:00",
        all_day=True,
    ).is_success
    assert len(listing(signed, period="month")) == 3
    assert [r["all_day"] for r in listing(signed, "2031-01-01", "day")] == [True]
    assert listing(signed, "2031-01-02", "day") == []
    url = EVENTS + "/" + original["id"]
    body = dict(title="Alterado", starts_at="2031-01-14T10:30:00-04:00")
    assert signed.patch(url, params={"version": 1}, json=body).json()["version"] == 2
    assert signed.delete(url, params={"version": 1}).status_code == 409
    assert signed.delete(url, params={"version": 2}).is_success
    assert listing(signed, "2031-01-14", "day") == []


def test_single_occurrence_edit_and_master_delete(signed):
    first = add(signed, recurrence="weekly").json()
    items = listing(signed, period="month")
    assert len(items) == 3
    url = EVENTS + "/" + first["id"]
    params = dict(version=1, occurrence=items[1]["starts_at"])
    assert signed.patch(
        url, params=params, json=dict(title="Exceção", starts_at="2031-01-21T12:00:00-04:00")
    ).is_success
    assert listing(signed, "2031-01-20", "day") == []
    assert [r["title"] for r in listing(signed, period="month")] == [
        "Revisão",
        "Exceção",
        "Revisão",
    ]
    assert signed.delete(url, params=dict(version=2, occurrence=items[-1]["starts_at"])).is_success
    assert len(listing(signed, period="month")) == 2
    assert signed.delete(url, params=dict(version=3)).is_success
    assert listing(signed, period="month") == []


def test_audio_to_calendar_and_same_owner_chat(engine, clock, signed):
    with patch(
        "jarvis.adapters.ai.speech.transcribe",
        return_value="Jarvis, marque revisão amanhã às nove e me lembre 10 minutos antes",
    ):
        reply = process(
            engine, clock, msg_id="audio1", text="[Áudio]", kind="audio", media=b"synthetic"
        )
    assert "uma hora" in reply and "10 minutos antes" in reply
    row = listing(signed, "2031-01-12", "day")[0]
    assert row["title"] == "revisão" and row["alert_minutes"] == 10
    code = row["id"][:8]
    assert "Remarcado" in process(
        engine, clock, msg_id="edit", text=f"remarque o compromisso #{code} para amanhã às 11"
    )
    assert "11:00" in process(engine, clock, msg_id="list", text="Mostre minha agenda de janeiro")
    assert "Cancelamento registrado" in process(
        engine, clock, msg_id="cancel", text=f"cancele o compromisso #{code}"
    )
    assert not listing(signed, "2031-01-12", "day")
    assert "agendamentos pendentes" in process(
        engine, clock, msg_id="envios", text="listar agendamentos"
    )


def test_late_personal_reminder_and_cancel_outbox(engine, clock, signed):
    assert "Lembrete salvo" in process(
        engine, clock, msg_id="remember", text="Me lembre de pagar a conta daqui a dois minutos"
    )
    later = clock() + timedelta(days=3)
    tick(engine, ROUTE, later)
    tick(engine, ROUTE, later + timedelta(seconds=10))
    with Session(engine) as db:
        notices = db.scalars(select(Outbound).join(Inbound).where(Inbound.kind == "notice")).all()
        assert len(notices) == 1 and "11/01 às 08:02" in notices[0].text
    r = listing(signed, "2031-01-11", "day")[0]
    assert signed.delete(EVENTS + "/" + r["id"], params=dict(version=r["version"])).is_success
    with Session(engine) as db:
        assert (
            db.scalar(select(Outbound.state).join(Inbound).where(Inbound.kind == "notice"))
            == "failed"
        )


def test_owner_routines_leap_year_reconnect_and_expiry(engine, signed):
    assert signed.put(CAL + "/routines", json=dict(birthday="02-30")).status_code == 422
    assert signed.put(CAL + "/routines", json=dict(birthday="02-29", leap_day="03-01")).is_success
    at = datetime(2031, 3, 1, 7, 0, tzinfo=ZONE)
    tick(engine, ROUTE, at - timedelta(minutes=1))
    tick(engine, ROUTE, at)
    tick(engine, ROUTE, at + timedelta(minutes=1))
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Outbound)) == 2
    delivery = queue.claim_delivery(engine, chat_id=ROUTE, now=at + timedelta(minutes=16))
    assert delivery and "Feliz aniversário" in delivery.text
    queue.finish_delivery(
        engine, delivery, outcome="accepted", external_id="simulado", now=at + timedelta(minutes=16)
    )
    tick(engine, ROUTE, at.replace(hour=21))
    d = queue.claim_delivery(engine, chat_id=ROUTE, now=at.replace(hour=21))
    assert d and "Boa noite" in d.text
    assert birthday_today("02-29", "03-01", date(2032, 2, 29))
    assert not birthday_today("02-29", "03-01", date(2032, 3, 1))


def test_google_pagination():
    g = object.__new__(Google)
    with patch.object(
        g,
        "request",
        side_effect=[{"items": [1], "nextPageToken": "p"}, {"items": [2], "nextSyncToken": "sync"}],
    ) as req:
        assert g.pages("events", {"syncToken": "old"}) == ([1, 2], "sync")
        assert req.call_args.kwargs["params"] == {"syncToken": "old", "pageToken": "p"}


def test_google_sync_mobile_edit_invalidates_ready_alert(engine, clock, signed):
    g = provider(engine)
    item = add(
        signed, alert_minutes=10, starts_at=(clock() + timedelta(minutes=10)).isoformat()
    ).json()
    assert item["state"] == "create"
    guid = UUID(item["id"])
    g.request.return_value = {"id": guid.hex, "etag": "v1"}
    baseline = remote(
        {"dateTime": (clock() + timedelta(minutes=10)).isoformat()},
        {"dateTime": (clock() + timedelta(minutes=70)).isoformat()},
        id=guid.hex,
    )
    g.pages.return_value = ([baseline], "cursor1")
    sync_source(engine, g, "principal", clock)
    assert g.request.call_args.kwargs["body"]["id"] == guid.hex
    tick(engine, ROUTE, clock())
    with Session(engine) as db:
        assert (
            db.scalar(select(func.count()).select_from(Outbound).where(Outbound.state == "ready"))
            == 1
        )
    changed = remote(
        {"dateTime": (clock() + timedelta(hours=1)).isoformat()},
        {"dateTime": (clock() + timedelta(hours=2)).isoformat()},
        id=guid.hex,
        etag="v2",
    )
    g.pages.return_value = ([changed], "cursor2")
    sync_source(engine, g, "principal", clock)
    assert g.pages.call_args.args[1]["syncToken"] == "cursor1"
    assert not queue.claim_delivery(engine, chat_id=ROUTE, now=clock())
    assert listing(signed, "2031-01-11", "day")[0]["title"] == "Evento do celular"
    clock.now += timedelta(minutes=50)
    g.pages.return_value = ([], "cursor3")
    sync_source(engine, g, "principal", clock)
    tick(engine, ROUTE, clock())
    d = queue.claim_delivery(engine, chat_id=ROUTE, now=clock())
    assert d and "Evento do celular" in d.text and "09:00" in d.text


def test_google_recurring_all_day_exception_and_cursor_recovery(engine, clock, signed):
    g = provider(engine)
    master = remote(
        {"date": "2031-01-06"},
        {"date": "2031-01-07"},
        recurrence=[
            "RRULE:FREQ=WEEKLY;UNTIL=20310130",
            "RDATE;VALUE=DATE:20310125",
            "EXDATE;VALUE=DATE:20310127",
        ],
    )
    gone = {
        "id": "instance",
        "etag": "x",
        "status": "cancelled",
        "recurringEventId": "remote",
        "originalStartTime": {"date": "2031-01-13"},
    }
    g.pages.return_value = ([gone, master], "cursor")
    sync_source(engine, g, "principal", clock)
    rows = listing(signed, period="month")
    assert [r["starts_at"][:10] for r in rows] == ["2031-01-06", "2031-01-20", "2031-01-25"]
    g.pages.side_effect = [GoogleError(410), GoogleError(503)]
    with pytest.raises(GoogleError):
        sync_source(engine, g, "principal", clock)
    with Session(engine) as db:
        assert db.get_one(CalendarSource, "principal").cursor == "cursor"
    assert len(listing(signed, period="month")) == 3
    g.pages.side_effect = [GoogleError(410), ([], "reset")]
    sync_source(engine, g, "principal", clock)
    assert not listing(signed, period="month")


def test_google_if_match_conflict_keeps_mobile_version(engine, clock, signed):
    g = provider(engine)
    event = remote({"date": "2031-01-13"}, {"date": "2031-01-14"})
    g.pages.return_value = ([event], "cursor")
    sync_source(engine, g, "principal", clock)
    row = listing(signed)[0]
    res = signed.patch(
        EVENTS + "/" + row["id"],
        params=dict(version=row["version"]),
        json=dict(title="Edição no painel", starts_at="2031-01-14T09:00:00-04:00"),
    )
    assert res.json()["state"] == "update"
    current = {**event, "etag": "v2", "summary": "Edição no celular"}
    g.request.side_effect = [GoogleError(412), current]
    g.pages.return_value = ([current], "cursor2")
    sync_source(engine, g, "principal", clock)
    assert g.request.call_args_list[0].kwargs["etag"] == "v1"
    row = listing(signed)[0]
    assert row["state"] == "conflict" and row["title"] == "Edição no celular"
    assert signed.get(CAL).json()["conflicts"]


def test_sources_enforce_read_and_write(engine, signed):
    g = provider(engine)
    g.pages.return_value = ([{"id": "outra", "summary": "Leitura", "accessRole": "reader"}], None)
    discover(engine, g)
    assert signed.put(CAL + "/sources", json=dict(read=[], write="outra")).status_code == 422
    assert signed.put(CAL + "/sources", json=dict(read=["outra"], write=None)).is_success
    assert add(signed).json()["source_id"] is None
    with Session(engine) as db:
        assert db.scalar(select(Entry.kind)) == "event"


def test_google_recovers_creation_after_lost_ack_without_a_second_event(engine, clock, signed):
    g = provider(engine)
    old = add(signed).json()
    guid = UUID(old["id"])
    item = remote(
        {"dateTime": old["starts_at"]},
        {"dateTime": old["ends_at"]},
        id=guid.hex,
        summary="Alterado no celular após o aceite",
        extendedProperties={"private": {"jarvisMutation": f"{guid}:1"}},
    )
    g.request.side_effect = [GoogleError(0), GoogleError(409), item]
    g.pages.side_effect = [([], "cursor1"), ([item], "cursor2")]
    sync_source(engine, g, "principal", clock)
    assert listing(signed)[0]["state"] == "create"
    sync_source(engine, g, "principal", clock)
    assert [r["title"] for r in listing(signed)] == ["Alterado no celular após o aceite"]
    assert (
        g.request.call_args_list[0].kwargs["body"]["id"]
        == g.request.call_args_list[1].kwargs["body"]["id"]
    )


def test_long_month_is_delivered_in_order_by_week(engine, clock, signed):
    for i in range(1, 32):
        assert add(
            signed, str(i), title=f"Tarefa {i:02d}", starts_at=f"2031-01-{i:02d}T11:00:00-04:00"
        ).is_success
    first = process(engine, clock, msg_id="full", text="Toda a minha agenda de janeiro")
    assert "Tarefa 01" in first
    with Session(engine) as db:
        messages = list(db.scalars(select(Outbound.text).order_by(Outbound.id)))
    assert messages[0] == first and len(messages) > 1
    full = "\n".join(messages)
    assert [full.count(f"Tarefa {n:02d}") for n in range(1, 32)] == [1] * 31
    assert all(len(msg) <= 4000 for msg in messages)


def test_google_single_instance_edit_resolves_provider_id(engine, clock, signed):
    g = provider(engine)
    master = remote(
        {"dateTime": "2031-01-13T09:00:00-04:00"},
        {"dateTime": "2031-01-13T10:00:00-04:00"},
        recurrence=["RRULE:FREQ=WEEKLY"],
    )
    g.pages.return_value = ([master], "cursor1")
    sync_source(engine, g, "principal", clock)
    series = listing(signed)[0]
    r = signed.patch(
        EVENTS + "/" + series["id"],
        params={"version": series["version"], "occurrence": series["starts_at"]},
        json=dict(title="Exceção", starts_at="2031-01-14T09:00:00-04:00"),
    )
    assert r.is_success, r.text
    original = {"dateTime": "2031-01-13T09:00:00-04:00"}
    g.pages.side_effect = [
        ([{"id": "remote_instance", "etag": "previous", "originalStartTime": original}], None),
        ([], "cursor2"),
    ]
    g.request.return_value = {"etag": "new"}
    sync_source(engine, g, "principal", clock)
    assert g.request.call_args.args == ("PATCH", "calendars/principal/events/remote_instance")
    assert g.request.call_args.kwargs["etag"] == "previous"
    assert [(r["title"], r["state"]) for r in listing(signed)] == [("Exceção", "clean")]


def test_calendar_mutations_require_owner_session_and_csrf(client, signed):
    response = client.put(
        CAL + "/routines", json={"birthday": "11-09"}, headers={"X-CSRF-Token": "wrong"}
    )
    assert response.status_code == 403
    client.cookies.clear()
    assert client.get(CAL).status_code == 401
    assert client.get(EVENTS, params={"anchor": "2031-01-13"}).status_code == 401


def test_desktop_authorization_state_pkce_and_private_storage(monkeypatch, tmp_path):
    import base64
    import hashlib
    import io
    import json
    from urllib.parse import parse_qs, urlsplit

    from jarvis.adapters.google import client as oauth

    path = tmp_path / "desktop.json"
    path.write_text(json.dumps({"installed": {"client_id": "test", "client_secret": "demo"}}))
    monkeypatch.setenv("JARVIS_GOOGLE_CLIENT_FILE", str(path))
    params = {}
    statuses = []

    def browser(url):
        params.update(parse_qs(urlsplit(url).query))
        return True

    class Listener:
        server_port = 12345

        def __init__(self, address, handler):
            assert address == ("127.0.0.1", 0)
            self.handler = handler

        def __enter__(self):
            return self

        def __exit__(self, *ignored):
            pass

        def handle_request(self):
            state = "invalid" if not statuses else params["state"][0]
            raw = f"GET /?state={state}&code=ok HTTP/1.0\r\n\r\n".encode()
            sock = Mock()
            sock.makefile.return_value = io.BytesIO(raw)
            self.handler(sock, ("127.0.0.1", 12346), self)
            statuses.append(b"".join(c.args[0] for c in sock.sendall.call_args_list))

    monkeypatch.setattr(oauth, "HTTPServer", Listener)
    monkeypatch.setattr(oauth.webbrowser, "open", browser)
    net = Mock(
        side_effect=[
            {"refresh_token": "synthetic-refresh-token", "access_token": "short"},
            {"id": "calendar-A"},
            {"access_token": "short2", "expires_in": 3600},
            {"items": []},
            {"items": []},
        ]
    )
    monkeypatch.setattr(oauth, "http", net)
    oauth.connect()
    assert b"400" in statuses[0] and b"200" in statuses[1]
    verifier = net.call_args_list[0].kwargs["data"]["code_verifier"]
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    assert params["code_challenge"] == [challenge]
    token = oauth.root() / "token.json"
    assert "synthetic-refresh-token" not in token.read_text()
    assert token.stat().st_mode & 0o077 == 0 and (oauth.root() / "key").stat().st_mode & 0o077 == 0
    g = Google()
    g.request("GET", "users/me/calendarList")
    g.request("GET", "users/me/calendarList")
    assert net.call_args_list[2].kwargs["data"]["refresh_token"] == "synthetic-refresh-token"
    assert net.call_count == 5


@pytest.mark.parametrize(
    "phrase", ["as 10:30", "hoje as 10:30", "08/11 as 10:30", "hoje 08/11/2026 as 10:30"]
)
def test_reminder_time_before_subject_over_whatsapp(engine, clock, signed, phrase):
    clock.now = datetime(2026, 11, 8, 9, 12, tzinfo=ZONE)
    title = "preciso sair para buscar a encomenda"
    reply = process(engine, clock, msg_id="pedido", text=f"Jarvis, me lembre que {phrase} {title}")
    due = clock.now.replace(hour=10, minute=30)
    assert "Lembrete salvo" in reply and "08/11/2026 às 10:30" in reply
    (item,) = listing(signed, "2026-11-08", "day")
    assert item["title"] == title and item["kind"] == "reminder"
    assert datetime.fromisoformat(item["starts_at"]) == due
    assert "#" + item["id"][:8] in reply
    tick(engine, ROUTE, due - timedelta(seconds=1))
    with Session(engine) as db:
        notices = (
            select(Outbound.text)
            .join(Inbound)
            .where(Inbound.kind == "notice", Inbound.chat_id == ROUTE)
        )
        assert db.scalars(notices).all() == []
    tick(engine, ROUTE, due)
    tick(engine, ROUTE, due + timedelta(seconds=1))
    with Session(engine) as db:
        assert db.scalars(notices).all() == [f"Lembrete de 08/11 às 10:30: {title}"]


@pytest.mark.parametrize(
    ("command", "title", "kind", "day"),
    [
        (
            "Me lembre que preciso pagar as contas hoje às 10h30",
            "preciso pagar as contas",
            "reminder",
            8,
        ),
        (
            "Me lembre às dez e meia de amanhã de pegar o trem das onze",
            "pegar o trem das onze",
            "reminder",
            9,
        ),
        (
            "Marque no dia 9 de novembro às 10h30 a revisão do carro",
            "a revisão do carro",
            "event",
            9,
        ),
        ("Me lembre amanhã às 10h30min de sair", "sair", "reminder", 9),
        ("Agende para amanhã às 10h30 a revisão", "a revisão", "event", 9),
        ("Me lembre amanhã, às 10h30, que as notas vencem", "as notas vencem", "reminder", 9),
    ],
)
def test_calendar_subject_positions(engine, clock, signed, command, title, kind, day):
    clock.now = datetime(2026, 11, 8, 9, 12, tzinfo=ZONE)
    with patch("jarvis.adapters.ai.speech.transcribe", return_value=command):
        reply = process(
            engine, clock, msg_id="audio", text="[Áudio]", kind="audio", media=b"synthetic"
        )
    (item,) = listing(signed, f"2026-11-{day:02d}", "day")
    assert item["title"] == title and item["kind"] == kind
    assert datetime.fromisoformat(item["starts_at"]) == clock.now.replace(
        day=day, hour=10, minute=30
    )
    assert "#" + item["id"][:8] in reply


@pytest.mark.parametrize(
    ("phrase", "reason"),
    [
        ("hoje 09/11/2026 às 10:30 preciso sair", "datas informadas são diferentes"),
        ("31/11 às 10:30 preciso sair", "data ou o horário"),
        ("hoje às 09:00 preciso sair", "já passou"),
        ("hoje às 25:30 preciso sair", "data ou o horário"),
        ("às 10:30 ou às 11:00 preciso sair", "data ou o horário"),
        ("às 10:30", "Qual é o assunto"),
        ("preciso sair amanhã", "data ou o horário"),
    ],
)
def test_invalid_reminder_does_not_write(engine, clock, signed, phrase, reason):
    clock.now = datetime(2026, 11, 8, 9, 12, tzinfo=ZONE)
    response = process(engine, clock, msg_id="invalid", text="Jarvis, me lembre que " + phrase)
    assert reason in response
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Entry)) == 0


@pytest.mark.parametrize(
    "command",
    [
        "agende para +5592999990000 amanhã às nove que o documento chegou",
        "agende amanhã às nove para +5592999990000 que o documento chegou",
    ],
)
def test_addressed_schedule_stays_in_message_queue(engine, clock, signed, command):
    from jarvis.adapters.persistence.models import Schedule

    reply = process(engine, clock, msg_id="recado", text=command)
    assert "Registrei o envio" in reply
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Entry)) == 0
        row = db.scalars(select(Schedule)).one()
        assert "o documento chegou" in row.text
        assert row.scheduled_at == clock().astimezone(ZONE).replace(hour=9) + timedelta(days=1)
