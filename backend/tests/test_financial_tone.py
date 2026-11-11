import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_commands import process
from test_receipt_photos import fields, photo, send

from jarvis.adapters.persistence.models import Owner
from jarvis.modules.personal.models import LedgerEntry
from jarvis.modules.receipts.reading import ReceiptReading


def test_purchase_advice_uses_the_month_and_records_only_an_actual_expense(engine, clock, signed):
    def ask(text):
        return process(engine, clock, msg_id=text, text=text)

    ask("Meu orçamento mensal é R$ 100")
    ask("Minha reserva mensal é R$ 25,50")
    ask("Gastei R$ 10 no almoço")
    boundary = ask("Quero comprar um tênis por R$ 64,50")
    assert "Cabe no seu orçamento" in boundary and "R$ 0,00 de margem" in boundary
    reserve = ask("Estou pensando em gastar R$ 64,51")
    assert reserve.startswith("Ei pô, te acalma.") and "faltariam R$ 0,01" in reserve
    over = ask("Se eu comprar o tênis por R$ 95,50, cabe?")
    assert over.startswith("Tá ficando doido?") and "R$ 5,50 acima do limite" in over
    assert "faltariam R$ 31,00" in over and "Reserva protegida: R$ 25,50" in over
    planned = ask("Sobraria dinheiro se eu comprasse algo de 95,50 reais?")
    assert planned.startswith("Tá ficando doido?") and "R$ 5,50 acima do limite" in planned
    impulse = ask("Quero gastar R$ 20 por impulso")
    assert impulse.startswith("Ei pô, te acalma.") and "R$ 44,50 de margem" in impulse
    assert "preço da compra" in ask("Quero comprar 2 blusas por impulso")
    ask("Meu orçamento de fevereiro é R$ 200")
    later = ask("Quero gastar R$ 100 em fevereiro")
    assert "deixaria R$ 74,50" in later and "Orçamento de 02/2031" in later
    ask("Já gastei R$ 80 se eu confirmar amanhã")
    with Session(engine) as db:
        assert db.scalars(
            select(LedgerEntry.amount).where(LedgerEntry.kind == "expense")
        ).all() == [1000]

    done = ask("Já gastei R$ 95,50 no tênis")
    assert done.startswith("Ei pô, te controla.") and "faltam R$ 31,00" in done
    assert "reserva de R$ 25,50" in done and "Despesa registrada" in done and "R$ 95,50" in done
    with Session(engine) as db:
        assert sorted(
            db.scalars(select(LedgerEntry.amount).where(LedgerEntry.kind == "expense"))
        ) == [1000, 9550]


def test_does_not_tease_on_unknown_budget_essential_costs_or_neutral_tone(engine, clock, signed):
    def ask(text):
        return process(engine, clock, msg_id=text, text=text)

    assert "não tenho um limite" in ask("Quero gastar R$ 800")
    impulse = ask("Quero gastar R$ 800 por impulso")
    assert impulse.startswith("Ei pô, te acalma.") and "não tenho um limite" in impulse
    unnecessary = ask("Já gastei R$ 1 no que não precisava")
    assert unnecessary.startswith("Ei pô, te controla.") and "não tenho um limite" in unnecessary
    assert ask("Paguei R$ 10 numa corrida").startswith("Despesa registrada")
    ask("Meu orçamento mensal é R$ 50")
    for text in ("Preciso gastar R$ 60", "Posso comprar remédios por R$ 60?"):
        answer = ask(text)
        assert "R$ 21,00 acima" in answer and "adiaria" not in answer
        assert "Tá ficando doido" not in answer and "Ei pô" not in answer
    assert "te controla" not in ask("Já gastei R$ 60 na farmácia")
    with Session(engine) as db, db.begin():
        db.get_one(Owner, 1).gender = "Não binário"
    another_profile = ask("Posso gastar R$ 100?")
    assert another_profile.startswith("Ei pô, te acalma.") and "doido" not in another_profile
    with Session(engine) as db, db.begin():
        db.get_one(Owner, 1).tone = "neutro"
    assert "Tá ficando doido" not in ask("Posso gastar R$ 80 no cinema?")
    assert "te controla" not in ask("Já gastei R$ 80 no cinema")


@pytest.mark.parametrize(("merchant", "tease"), [("Loja Lua", True), ("Farmácia Lua", False)])
def test_receipt_confirms_exact_difference_once_and_respects_essential_costs(
    engine, clock, signed, monkeypatch, merchant, tease
):
    process(engine, clock, msg_id="budget", text="Meu orçamento mensal é R$ 20")
    monkeypatch.setattr(
        "jarvis.entrypoints.worker.analyze",
        lambda *a, **kw: ReceiptReading.model_validate(fields(merchant=merchant)),
    )
    image_id, draft = send(engine, clock, "receipt", "[Imagem]", binary=photo())
    assert "te controla" not in draft
    confirm = f"confirmar cupom C{image_id}"
    _, posted = send(engine, clock, "yes", confirm)
    assert posted.startswith("Ei pô, te controla.") is tease
    assert "R$ 16,49 acima" in posted
    assert f"Despesa registrada (F{image_id})" in posted
    _, repeat = send(engine, clock, "again", confirm)
    assert "já foi registrado" in repeat and "te controla" not in repeat
