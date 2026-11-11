from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_personal_skills import OTHER, ROUTE, TODAY

from jarvis.modules.errors import Problem
from jarvis.modules.messaging import queue
from jarvis.modules.messaging.models import Inbound
from jarvis.modules.personal.models import LedgerEntry
from jarvis.modules.spreadsheets.service import (
    correct_amount,
    create_note,
    record_expense,
    remove_ledger,
    remove_note,
    reserve_cents,
    set_reserve,
    snapshot,
)

pytest_plugins = ["test_personal_skills"]


def test_concurrent_note_requests_create_only_one_exportable_row(engine, run):
    together = Barrier(2)

    def submit():
        with Session(engine) as db, db.begin():
            together.wait(timeout=10)
            return create_note(db, ROUTE, TODAY.date(), "Canetas", "2", request_key="dupla").id

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit)
        second = pool.submit(submit)
        assert first.result() == second.result()
    with Session(engine) as db:
        assert len(snapshot(db, ROUTE)) == 1


def test_concurrent_edit_rejects_stale_version(engine, run):
    _, turn = run("Gastei R$ 30 no almoço")
    together = Barrier(2)

    def update_amount(value):
        with Session(engine) as db, db.begin():
            together.wait(timeout=10)
            try:
                correct_amount(db, ROUTE, turn.id, value, expected_version=1)
            except Problem as exc:
                return exc.status
            return 200

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(update_amount, 2400)
        second = pool.submit(update_amount, 2000)
        assert sorted([first.result(), second.result()]) == [200, 409]
    with Session(engine) as db:
        final = db.get_one(LedgerEntry, turn.id)
        assert final.version == 2 and final.amount in (2400, 2000)


def test_ledger_correction_deletion_and_export_do_not_duplicate(engine, run):
    _, turn = run("Gastei R$ 80,00 no mercado")
    run("Meu orçamento mensal é R$ 900")
    run("Recebi R$ 10 de salário", route=OTHER)
    with Session(engine) as db:
        (original,) = snapshot(db, ROUTE)
        assert (original.key, original.amount_cents, original.deleted) == (
            f"F{turn.id}",
            8000,
            False,
        )
    message, _ = run(f"Corrija o gasto F{turn.id} para R$ 67,40")
    assert "R$ 67,40" in message and "67,40" in run("Quanto gastei hoje?")[0]
    with Session(engine) as db, db.begin():
        (updated,) = snapshot(db, ROUTE)
        assert updated.key == original.key and updated.amount_cents == 6740
        assert db.get_one(LedgerEntry, turn.id).version == 2
        with pytest.raises(Problem) as stale:
            correct_amount(db, ROUTE, turn.id, 25, expected_version=1)
        assert stale.value.status == 409
        with pytest.raises(Problem) as hidden:
            correct_amount(db, OTHER, turn.id, 25)
        assert hidden.value.status == 404
        updated = correct_amount(db, ROUTE, turn.id, 5010, expected_version=2)
        assert updated.amount == 5010 and updated.version == 3
    assert "Excluí" in run(f"Exclua o gasto F{turn.id}")[0]
    with Session(engine) as db:
        (deleted,) = snapshot(db, ROUTE)
        assert deleted.key == original.key and deleted.deleted
        assert deleted.amount_cents is None and not deleted.description
        assert not any(r.key == original.key for r in snapshot(db, OTHER))
    assert "R$ 0,00" in run("Quanto gastei hoje?")[0]


def test_reserve_affordability_including_temporal_changes_and_isolation(engine, run):
    run("Meu orçamento mensal é R$ 600")
    run("Gastei R$ 50 no café")
    assert "R$ 100,00" in run("Minha reserva mensal é de R$ 100")[0]
    assert "Reserva protegida: R$ 100,00" in run("Posso gastar R$ 50?")[0]
    assert "deixaria R$ 400,00" in run("Posso gastar R$ 50?")[0]
    assert "faltariam R$ 0,01" in run("Posso gastar R$ 450,01?")[0]
    run("Reserve mensalmente R$ 300", at=TODAY.replace(month=5))
    with Session(engine) as db, db.begin():
        assert reserve_cents(db, ROUTE, TODAY.date()) == 10000
        assert reserve_cents(db, ROUTE, TODAY.replace(month=6).date()) == 30000
        assert reserve_cents(db, ROUTE, TODAY.replace(month=3).date()) == 0
        assert reserve_cents(db, OTHER, TODAY.date()) == 0
        with pytest.raises(Problem):
            set_reserve(db, ROUTE, TODAY.date(), -1)
    assert "R$ 0,00" in run("Minha reserva mensal é zero", at=TODAY.replace(month=6))[0]
    with Session(engine) as db:
        assert reserve_cents(db, ROUTE, TODAY.replace(month=7).date()) == 0


def test_general_sheet_notes_are_typed_and_scoped_and_accept_literal_formulas(engine, run):
    reply, turn = run("Salve na planilha: =1+1; quantidade: 2 caixas")
    assert "Anotação P" in reply
    with Session(engine) as db, db.begin():
        (row,) = snapshot(db, ROUTE)
        assert row.kind == "note" and row.amount_cents is None and row.description == "=1+1"
        assert row.quantity == "2 caixas" and not row.deleted
        assert not snapshot(db, OTHER)
        note = create_note(db, ROUTE, TODAY.date(), "=1+1", "2 caixas", request_key=f"wa:{turn.id}")
        assert row.key == f"P{note.id}"
        with pytest.raises(Problem, match="outro conteúdo"):
            create_note(db, ROUTE, TODAY.date(), "Arroz", "2 caixas", request_key=f"wa:{turn.id}")
        with pytest.raises(Problem) as hidden:
            remove_note(db, OTHER, note.id)
        assert hidden.value.status == 404
        remove_note(db, ROUTE, note.id)
        remove_note(db, ROUTE, note.id)
        (tombstone,) = snapshot(db, ROUTE)
        assert tombstone.deleted and not tombstone.description and not tombstone.quantity
        assert create_note(
            db, ROUTE, TODAY.date(), "=1+1", "2 caixas", request_key=f"wa:{turn.id}"
        ).deleted
        with pytest.raises(Problem, match="legível"):
            create_note(db, ROUTE, TODAY.date(), "bad\x00", request_key="bad")


def test_receipt_confirmation_can_repeat_and_never_resurrects_a_deleted_expense(engine, run):
    assert queue.accept_inbound(
        engine,
        chat_id=ROUTE,
        message_id="photo",
        kind="image",
        media=b"photo",
        text="Registre este cupom",
        now=TODAY,
    )
    with Session(engine) as db, db.begin():
        inbound_id = db.scalars(select(Inbound.id).where(Inbound.message_id == "photo")).one()

        def confirm(route):
            return record_expense(
                db,
                route=route,
                inbound_id=inbound_id,
                day=date(2030, 4, 13),
                amount_cents=1510,
                description="Mercado",
            )

        with pytest.raises(Problem, match="neste vínculo"):
            confirm(OTHER)
        assert confirm(ROUTE).amount == 1510
        correct_amount(db, ROUTE, inbound_id, 1300)
        assert confirm(ROUTE).amount == 1300
        remove_ledger(db, ROUTE, inbound_id)
        with pytest.raises(Problem) as gone:
            confirm(ROUTE)
        assert gone.value.status == 409
        (row,) = snapshot(db, ROUTE)
        assert row.deleted
