from datetime import date, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_commands import ROUTE, input_message, process
from test_calendar import CAL, EVENTS, listing, provider, remote
from test_icloud import BODY, MemoryDAV, link

from jarvis.adapters import calendar_refresh
from jarvis.adapters.google.client import GoogleError, root
from jarvis.adapters.icloud import account
from jarvis.adapters.icloud.client import AppleError
from jarvis.modules.assistant.intent import fold
from jarvis.modules.calendar.models import CalendarChoice, CalendarSource, Entry
from jarvis.modules.calendar.notices import tick
from jarvis.modules.calendar.periods import command_text, is_query, requested_period
from jarvis.modules.calendar.service import ZONE
from jarvis.modules.messaging.models import Inbound, Outbound


class GoogleAgenda:
    def __init__(self):
        self.events = {}
        self.posts = []
        self.offline = False

    def pages(self, path, params):
        if self.offline:
            raise GoogleError(503)
        if path == "users/me/calendarList":
            return [
                {"id": "principal", "summary": "Pessoal", "accessRole": "owner", "primary": True}
            ], None
        return list(self.events.values()), "cursor"

    def request(self, method, path, *, body=None, etag=None):
        if method == "POST":
            assert body is not None
            assert body["id"] not in self.events
            self.posts.append(body)
            self.events[body["id"]] = {**body, "etag": "1"}
            return self.events[body["id"]]
        raise AssertionError(method)


@pytest.fixture
def calendars(signed, engine):
    provider(engine)
    g = GoogleAgenda()
    dav = MemoryDAV()
    sid = link(signed, engine, dav)
    root().mkdir(parents=True, exist_ok=True)
    (root() / "token.json").touch()
    with (
        patch.object(calendar_refresh, "Google", return_value=g),
        patch.object(account, "CalDAV", return_value=dav),
    ):
        yield g, dav, sid


def notices(engine):
    with Session(engine) as db:
        return db.scalars(
            select(Outbound.text)
            .join(Inbound)
            .where(Inbound.message_id.startswith("calendar-pair:"))
        ).all()


def test_voice_dual_writes_independent_retries_and_fresh_query(engine, signed, clock, calendars):
    g, dav, _ = calendars
    message = "Jarvis, insira na agenda consulta no dia 15 de janeiro às 14h30"
    with patch("jarvis.adapters.ai.speech.transcribe", return_value=message):
        initial = process(
            engine, clock, msg_id="voice", text="[Áudio]", kind="audio", media=b"synthetic"
        )
    assert (
        "15/01/2031 às 14:30" in initial and "Aguardando confirmação de Google e iCloud" in initial
    )
    assert "Conflito" not in initial
    assert not input_message(engine, clock, msg_id="voice", text=message)
    with patch.object(dav, "calendars", side_effect=AppleError(503)):
        first = process(engine, clock, msg_id="query1", text="O que tenho no dia 15 de janeiro?")
    assert (
        "Consulta parcial" in first
        and "iCloud (pendente)" in first
        and first.count("consulta ·") == 1
    )
    assert len(g.posts) == 1 and g.posts[0]["start"]["dateTime"] == "2031-01-15T14:30:00-04:00"
    tick(engine, ROUTE, clock())
    assert not notices(engine)
    # Eventos inseridos nos aplicativos depois da leitura anterior.
    g.events["remote"] = remote(
        {"date": "2031-01-15"}, {"date": "2031-01-16"}, summary="Evento do Google"
    )
    dav.files["mobile.ics"] = (
        '"2"',
        BODY.replace("20310113", "20310115").replace("Revisão", "Exclusivo do iPhone"),
    )
    second = process(
        engine, clock, msg_id="query2", text="Quais são meus compromissos de 15/01/2031?"
    )
    assert all(
        t in second
        for t in (
            "Agendas consultadas",
            "Google e iCloud",
            "Evento do Google",
            "Exclusivo do iPhone",
        )
    )
    assert second.count("consulta ·") == 1 and "pendente" not in second
    assert len(g.posts) == 1
    apple = [raw for _, raw in dav.files.values() if "SUMMARY:consulta" in raw]
    assert len(apple) == 1 and "20310115T183000Z" in apple[0]
    tick(engine, ROUTE, clock())
    tick(engine, ROUTE, clock())
    assert len(notices(engine)) == 1 and "Confirmado no Google e no iCloud" in notices(engine)[0]
    (item,) = [r for r in listing(signed, "2031-01-15", "day") if r["title"] == "consulta"]
    assert len(item["copies"]) == 2 and all(c["state"] == "clean" for c in item["copies"])
    assert signed.patch(
        EVENTS + "/" + item["id"],
        params={"version": item["version"]},
        json={"title": "Alterado", "starts_at": "2031-01-16T16:00:00-04:00"},
    ).is_success
    (changed,) = listing(signed, "2031-01-16", "day")
    assert len(changed["copies"]) == 2 and changed["title"] == "Alterado"
    assert signed.delete(
        EVENTS + "/" + item["id"], params={"version": changed["version"]}
    ).is_success
    with Session(engine) as db:
        assert (
            len(
                db.scalars(
                    select(Entry).where(Entry.state == "delete", Entry.pair_id.is_not(None))
                ).all()
            )
            == 2
        )


def test_day_without_hour_uses_exclusive_dates_in_both_services(engine, signed, clock, calendars):
    g, dav, _ = calendars
    result = process(
        engine,
        clock,
        msg_id="allday",
        text="Coloque entrega do projeto na minha agenda para 31 de janeiro",
    )
    assert "31/01/2031 · dia inteiro" in result
    answer = process(
        engine, clock, msg_id="week", text="Me fale sobre a agenda da semana de 31/01/2031"
    )
    assert "27/01 a 02/02/2031" in answer and "dia inteiro · entrega do projeto" in answer
    body = g.posts[0]
    assert (body["start"], body["end"]) == ({"date": "2031-01-31"}, {"date": "2031-02-01"})
    content = next(raw for _, raw in dav.files.values() if "SUMMARY:entrega do projeto" in raw)
    assert "DTSTART;VALUE=DATE:20310131" in content and "DTEND;VALUE=DATE:20310201" in content
    assert "entrega do projeto" not in process(
        engine, clock, msg_id="next", text="Minha agenda do próximo mês"
    )
    g.offline = True
    with patch.object(dav, "calendars", side_effect=AppleError(503)):
        empty = process(engine, clock, msg_id="offline", text="Minha agenda do dia 01/02")
    assert (
        "Consulta parcial" in empty and "dados disponíveis" in empty and "última leitura" in empty
    )


def test_ambiguous_destination_is_atomic_and_can_be_configured(engine, signed, clock, calendars):
    _, _, sid = calendars
    with Session(engine) as db, db.begin():
        db.add(
            CalendarSource(
                id="icloud:other",
                provider="icloud",
                name="Outra",
                writable=True,
                selected=True,
                write_default=False,
            )
        )
    text = "Agende jantar amanhã às 19"
    reply = process(engine, clock, msg_id="a", text=text)
    assert "Destinos do WhatsApp" in reply
    with Session(engine) as db:
        assert not db.scalar(select(Entry.id).where(Entry.title == "jantar"))
    assert signed.put(
        CAL + "/sources",
        json={
            "read": ["principal", sid, "icloud:other"],
            "write": "principal",
            "chat_targets": ["principal", sid],
        },
    ).is_success
    assert "Google e iCloud" in process(engine, clock, msg_id="b", text=text)


@pytest.mark.parametrize(
    ("question", "day", "period"),
    [
        ("O que tenho depois de amanhã?", "2031-01-13", "day"),
        ("Quais são os meus compromissos no dia 20?", "2031-01-20", "day"),
        ("Como está a minha agenda de sexta-feira?", "2031-01-17", "day"),
        ("Minha agenda na semana de 02/02/2031", "2031-02-02", "week"),
        ("Me mostre o calendário de março de 2032", "2032-03-01", "month"),
        ("Como está meu dia?", "2031-01-11", "day"),
        ("Minha agenda de sexta da semana que vem", "2031-01-17", "day"),
        ("Minha agenda de segunda passada", "2031-01-06", "day"),
    ],
)
def test_specific_period(question, day, period):
    assert is_query(question)
    assert requested_period(fold(command_text(question)), date(2031, 1, 11)) == (
        date.fromisoformat(day),
        period,
    )


def test_context_and_specific_times_follow_the_requested_date(engine, clock, signed):
    result = process(
        engine, clock, msg_id="range", text="Marque reunião dia 15 do próximo mês das 14h às 16h30"
    )
    assert "15/02/2031 às 14:00" in result and "Término às 16:30" in result
    answer = process(engine, clock, msg_id="context", text="E naquela semana?")
    assert "10/02 a 16/02/2031" in answer and "14:00–16:30 · reunião" in answer
    assert "Qual período" in process(engine, clock, msg_id="unclear", text="E naquele dia?")
    one = process(
        engine, clock, msg_id="specific", text="Qual é a minha agenda do dia 15 do próximo mês?"
    )
    assert one.splitlines()[0] == "Sua agenda de 15/02/2031 (Manaus)"
    monthly = process(engine, clock, msg_id="specificmonth", text="Minha agenda daquele mês")
    assert "01/02 a 28/02/2031" in monthly


def test_independent_events_with_identical_titles_stay_distinct(engine, clock, signed, calendars):
    g, dav, _ = calendars
    g.events["remote"] = remote(
        {"dateTime": "2031-01-13T09:00:00-04:00"},
        {"dateTime": "2031-01-13T10:00:00-04:00"},
        summary="Revisão",
    )
    answer = process(engine, clock, msg_id="same", text="Qual é minha agenda do dia 13?")
    assert answer.count(" · Revisão · ") == 2
    assert len(listing(signed, "2031-01-13", "day")) == 2


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("Marque amanhã a consulta", "12/01/2031 · dia inteiro"),
        ("Marque visita daqui a dois dias", "13/01/2031 · dia inteiro"),
        ("Marque visita daqui a dois dias às 14h", "13/01/2031 às 14:00"),
        ("Marque almoço hoje", "11/01/2031 · dia inteiro"),
        ("Marque consulta amanhã às 29h", "Não consegui identificar"),
    ],
)
def test_date_only_and_unusable_time(engine, clock, signed, command, expected):
    assert expected in process(engine, clock, msg_id="one", text=command)
    if "29h" in command:
        with Session(engine) as db:
            assert not db.scalar(select(Entry.id))


def test_explicit_both_requires_connected_accounts(engine, clock, signed):
    provider(engine)
    command = "Marque a consulta nos dois calendários para o dia 16 às nove"
    answer = process(engine, clock, msg_id="dual", text=command)
    assert "conecte iCloud" in answer and not listing(signed, "2031-01-16", "day")


def test_external_change_of_one_copy_is_visible(engine, clock, signed, calendars):
    g, _, _ = calendars
    command = "Marque plantão no Google e no iCloud dia 15 de janeiro"
    assert "Aguardando confirmação" in process(engine, clock, msg_id="schedule", text=command)
    query = "Minha agenda no dia 15"
    assert process(engine, clock, msg_id="initial", text=query).count(" · plantão · ") == 1
    event = g.events[g.posts[0]["id"]]
    event.update(etag="edit", summary="Plantão alterado no Google")
    updated = process(engine, clock, msg_id="updated", text=query)
    assert "Plantão alterado no Google" in updated and " · plantão · " in updated
    copies = listing(signed, "2031-01-15", "day")
    assert len(copies) == 2 and all(len(row["copies"]) == 1 for row in copies)


def test_calendar_commands_do_not_become_text_of_a_pending_message(engine, clock, signed):
    from jarvis.adapters.persistence.models import MessageDraft, Schedule

    process(engine, clock, msg_id="draft", text="Envie para +5592999990000 que cheguei")
    with Session(engine) as db:
        assert db.get(MessageDraft, ROUTE) is not None
    answer = process(engine, clock, msg_id="calendar", text="Marque dentista amanhã")
    assert "Compromisso registrado" in answer
    agenda = process(engine, clock, msg_id="read", text="Quero saber o que tenho amanhã")
    assert "dia inteiro · dentista" in agenda
    with Session(engine) as db:
        assert db.get(MessageDraft, ROUTE) is not None
        assert not db.scalar(select(Schedule.id))


@pytest.mark.parametrize(
    ("message", "day", "hour"),
    [
        (
            "Coloque na agenda do iCloud. Dia 25/11 - reunião de resultados Projeto Aurora",
            "2031-11-25",
            None,
        ),
        (
            "Coloque na agenda do iCloud dia 25 às 10h reunião de resultados Projeto Aurora",
            "2031-01-25",
            10,
        ),
        (
            "Coloque na agenda do iCloud dia 25 de novembro às 10h reunião de resultados Projeto Aurora",
            "2031-11-25",
            10,
        ),
    ],
)
def test_real_icloud_requests_reach_only_selected_apple_calendar(
    engine, clock, signed, calendars, message, day, hour
):
    g, dav, sid = calendars
    result = process(engine, clock, msg_id="apple", text=message)
    assert "Aguardando confirmação de iCloud." in result and "Google" not in result
    with Session(engine) as db:
        saved = db.scalars(
            select(Entry).where(Entry.title == "reunião de resultados Projeto Aurora")
        ).one()
        assert saved.source_id == sid and saved.all_day == (hour is None)
        assert saved.starts_at.astimezone(ZONE).isoformat().startswith(day)
        assert saved.starts_at.astimezone(ZONE).hour == (hour or 0)
    query = process(
        engine, clock, msg_id="apple-query", text=f"Como está minha agenda do iCloud no dia {day}?"
    )
    assert query.endswith("🟢 - Google\n🔵 - iCloud")
    assert "🔵 " in query and "reunião de resultados Projeto Aurora" in query
    assert not g.posts and any(
        "SUMMARY:reunião de resultados Projeto Aurora" in raw for _, raw in dav.files.values()
    )
    tick(engine, ROUTE, clock())
    assert len(notices(engine)) == 1 and "Confirmado no iCloud" in notices(engine)[0]


def test_provider_read_hides_other_source_outage_local_reminders_and_pending_pairs(
    engine, clock, signed, calendars
):
    g, _, _ = calendars
    process(engine, clock, msg_id="default", text="Agende reunião espelhada dia 13 às 16h")
    process(engine, clock, msg_id="google", text="Agende só no Google partida dia 13 às 18h")
    process(engine, clock, msg_id="local", text="Me lembre de estudar dia 13 às 22h")
    everything = process(engine, clock, msg_id="all", text="minha agenda para o dia 13")
    assert "🟢🔵 13/01" in everything and "🟢 13/01" in everything and "⚪ 13/01" in everything
    assert everything.count(" · reunião espelhada · ") == 1 and everything.endswith("⚪ - Local")
    g.offline = True
    apple = process(
        engine, clock, msg_id="apple", text="Como está minha agenda do iCloud esse mês?"
    )
    assert "Consulta parcial" not in apple and "Agendas consultadas: iCloud." in apple
    assert "estudar" not in apple and "partida" not in apple and "🔵 13/01" in apple


def test_choose_icloud_calendar_over_whatsapp_then_remember_it(engine, clock, signed, calendars):
    _, _, sid = calendars
    with Session(engine) as db, db.begin():
        for index in range(4):
            db.add(
                CalendarSource(
                    id=f"extra{index}",
                    name=f"Calendário {index}",
                    provider="icloud",
                    selected=True,
                    available=True,
                    writable=True,
                    write_default=index == 0,
                )
            )
    result = process(
        engine, clock, msg_id="amb", text="Coloque na agenda do iCloud dia 25 às 10h reunião"
    )
    assert (
        "Em qual agenda do iCloud" in result
        and "5. Pessoal" in result
        and "25/01/2031 às 10:00" in result
    )
    with Session(engine) as db:
        assert not db.scalar(select(Entry.id).where(Entry.title == "reunião"))
    assert "Em qual agenda" in process(engine, clock, msg_id="bad", text="9")
    clock.now += timedelta(hours=1)
    selected = process(engine, clock, msg_id="choice", text="sempre 5")
    assert "25/01/2031 às 10:00" in selected and "Aguardando confirmação de iCloud" in selected
    assert not input_message(engine, clock, msg_id="choice", text="sempre 5")
    assert "Aguardando confirmação de iCloud" in process(
        engine, clock, msg_id="later", text="Marque no iCloud consulta dia 26 às 11h"
    )
    with Session(engine) as db:
        actual = db.scalars(
            select(Entry.source_id).where(Entry.title.in_(["reunião", "consulta"]))
        ).all()
        assert actual == [sid, sid]
        assert db.get_one(CalendarSource, sid).chat_target
        assert not db.get(CalendarChoice, ROUTE)


def test_real_postposed_create_and_alternative_provider(engine, clock, signed, calendars):
    command = "No dia 25 de novembro haverá a confraternização da equipe. Marque na agenda"
    answer = process(engine, clock, msg_id="post", text=command)
    assert (
        "a confraternização da equipe\n25/11/2031 · dia inteiro" in answer
        and "Google e iCloud" in answer
    )
    unclear = process(engine, clock, msg_id="or", text="Marque festa amanhã no Google ou no iCloud")
    assert "Você quer o Google, o iCloud ou os dois?" in unclear
    with Session(engine) as db:
        assert not db.scalar(select(Entry.id).where(Entry.title == "festa"))


def test_provider_only_followup_keeps_previously_requested_month(engine, clock, signed, calendars):
    g, _, _ = calendars
    g.events["remote"] = remote(
        {"date": "2031-04-09"}, {"date": "2031-04-10"}, summary="Projeto de abril"
    )
    april = process(engine, clock, msg_id="month_apr", text="Qual é a agenda de abril?")
    assert "Projeto de abril" in april
    filtered = process(engine, clock, msg_id="just_google", text="Mostre só do Google")
    assert "01/04 a 30/04" in filtered and "🟢 09/04" in filtered
    apple = process(engine, clock, msg_id="and_apple", text="E no iCloud?")
    assert "01/04 a 30/04" in apple and "Projeto de abril" not in apple


def test_single_provider_failure_notified_once_until_receipt(engine, clock, signed, calendars):
    g, dav, _ = calendars
    process(engine, clock, msg_id="create", text="Marque só no iCloud plantão dia 12 às 19h")
    with patch.object(dav, "calendars", side_effect=AppleError(401)):
        query = process(engine, clock, msg_id="offline", text="Minha agenda do iCloud amanhã")
        assert "Consulta parcial" in query
    tick(engine, ROUTE, clock())
    tick(engine, ROUTE, clock())
    assert not notices(engine)
    with Session(engine) as db:
        text = db.scalars(
            select(Outbound.text)
            .join(Inbound)
            .where(Inbound.message_id.startswith("calendar-issue:"))
        ).one()
        assert "Ainda não consegui confirmar" in text and "iCloud" in text
    process(engine, clock, msg_id="back", text="Minha agenda do iCloud amanhã")
    tick(engine, ROUTE, clock())
    assert "Confirmado no iCloud" in notices(engine)[0] and not g.posts


def test_provider_in_subject_does_not_expand_explicit_destination(engine, clock, signed, calendars):
    g, _, sid = calendars
    reply = process(
        engine, clock, msg_id="subject", text="Marque reunião da Apple no Google amanhã às 15h"
    )
    assert "reunião da Apple" in reply and "Aguardando confirmação de Google." in reply
    apple = process(
        engine, clock, msg_id="appleonly", text="Marque aula do Google Meet no iCloud amanhã às 19h"
    )
    assert "aula do Google Meet" in apple and "Aguardando confirmação de iCloud." in apple
    with Session(engine) as db:
        assert (
            db.scalars(select(Entry.source_id).where(Entry.title == "reunião da Apple")).one()
            == "principal"
        )
        assert (
            db.scalars(select(Entry.source_id).where(Entry.title == "aula do Google Meet")).one()
            == sid
        )
