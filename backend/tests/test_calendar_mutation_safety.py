"""O conteúdo de um exemplo, uma condição ou uma desistência não autoriza gravar na agenda."""

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_commands import process
from test_calendar import add

from jarvis.modules.calendar.models import CalendarChoice, Entry


@pytest.mark.parametrize(
    "instruction",
    [
        "Marque amanhã às 10h a consulta só se a Gabi confirmar",
        "Marque amanhã às 10h a visita, mas não faça isso ainda",
        "Marque amanhã a consulta; na verdade, não marque.",
        "Marque amanhã às 10h a reunião e cancele o almoço",
        "Marque como exemplo revisão amanhã às dez",
        "Quero saber marcar exame amanhã às 10h",
        "Não marque consulta amanhã às 10h",
        "O que acontece se eu disser “marque o exame amanhã às 10h”?",
        "Amanhã às 14h teremos reunião se houver quórum. Marque isso na agenda.",
    ],
)
def test_ambiguous_or_retracted_instructions_do_not_write(engine, clock, signed, instruction):
    process(engine, clock, msg_id="instruction", text=instruction)
    with Session(engine) as db:
        assert not db.scalar(select(Entry.id))
        assert not db.scalar(select(CalendarChoice.route))


@pytest.mark.parametrize(
    "suffix",
    [
        "apenas se eu confirmar",
        "ou não, ainda estou decidindo",
        "e agende outro para amanhã às 9h",
    ],
)
def test_qualified_cancellation_preserves_original(engine, clock, signed, suffix):
    item = add(signed).json()
    phrase = f"Cancele o compromisso #{item['id'][:8]} {suffix}"
    process(engine, clock, msg_id="conditional_cancel", text=phrase)
    with Session(engine) as db:
        row = db.get_one(Entry, UUID(item["id"]))
        assert not row.deleted
        assert row.version == item["version"]


def test_negative_in_reminder_subject_is_part_of_authorized_content(engine, clock, signed):
    process(
        engine,
        clock,
        msg_id="reminder",
        text="Me lembre de não esquecer o documento amanhã às nove",
    )
    with Session(engine) as db:
        item = db.scalars(select(Entry)).one()
        assert item.kind == "reminder" and item.title == "não esquecer o documento"
