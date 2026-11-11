from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import Owner
from jarvis.modules.calendar.service import ZONE
from jarvis.modules.messaging import queue
from jarvis.modules.messaging.models import Inbound, Outbound
from jarvis.modules.personal.models import LedgerEntry
from jarvis.modules.personal.service import excluded_history_ids, handle_personal, memory_context

ROUTE = "bot-1|owner-a"
OTHER = "bot-2|owner-b"
TODAY = datetime(2030, 4, 15, 9, tzinfo=ZONE)


@pytest.fixture
def run(engine):
    with Session(engine) as db, db.begin():
        db.add(Owner(id=1, name="Álvaro"))

    def submit(
        text, *, at=TODAY, route=ROUTE, msg_id=None, kind: Literal["text", "audio"] = "text"
    ):
        assert queue.accept_inbound(
            engine,
            chat_id=route,
            message_id=msg_id or uuid4().hex,
            kind=kind,
            media=b"voice" if kind == "audio" else None,
            text=text,
            now=at,
        )
        turn = queue.claim_turn(engine, now=at)
        assert turn
        result = queue.apply_command(engine, turn, lambda db: handle_personal(db, turn, at), now=at)
        if not result:
            queue.complete_turn(engine, turn, "Conversa normal", now=at)
            return None, turn
        with Session(engine) as db:
            return db.get_one(Outbound, turn.id).text, turn

    return submit


def test_money_exact_periods_receipt_budget_removal_and_isolation(engine, run):
    assert "R$ 1.800,50" in run("Meu orçamento mensal é R$ 1.800,50")[0]
    for i in range(3):
        assert "R$ 0,10" in run("Gastei R$ 0,10 no café", msg_id=f"cafe-{i}")[0]
    bill, turn = run("Registre uma despesa de R$ 1.500,20 com aluguel ontem", kind="audio")
    assert "14/04/2030" in bill and "1.500,20" in bill
    assert not queue.accept_inbound(
        engine, chat_id=ROUTE, message_id="cafe-1", text="Gastei R$ 0,10 no café", now=TODAY
    )
    assert "R$ 1.500,50" in run("Quanto gastei esse mês?")[0]
    assert "R$ 0,30" in run("Quanto gastei no café este mês?")[0]
    assert "R$ 0,00" in run("Quanto gastei mês passado?")[0]
    assert "R$ 1.500,20" in run("Mostre minhas despesas de ontem")[0]
    assert "R$ 2.500,10" in run("Recebi 2.500,10 de salário")[0]
    assert "Saldo dos lançamentos: R$ 999,60" in run("Resumo financeiro deste mês")[0]
    assert "deixaria R$ 299,99" in run("Posso gastar R$ 0,01 com chocolate?")[0]
    assert "R$ 10,00 acima" in run("Posso comprar sapato por R$ 310,00?")[0]
    assert "não tenho um limite" in run("Posso gastar R$ 0,01?", route=OTHER)[0]
    assert "Não encontrei" in run(f"apague o gasto F{turn.id}", route=OTHER)[0]
    assert "Excluí" in run(f"apague o gasto F{turn.id}")[0]
    assert "R$ 0,30" in run("Quanto gastei em abril de 2030?")[0]
    with Session(engine) as db:
        assert (
            db.scalar(
                select(func.count()).select_from(LedgerEntry).where(LedgerEntry.kind == "expense")
            )
            == 3
        )


def test_monthly_and_single_month_budget_temporal_validity(engine, run):
    run("Meu orçamento mensal é R$ 100")
    assert "de 05/2030: R$ 200,00" in run("Meu orçamento de maio é de R$ 200")[0]
    assert "R$ 200,00" in run("Qual meu orçamento em maio de 2030?")[0]
    assert "R$ 100,00" in run("Qual meu orçamento em junho de 2030?")[0]
    assert "não tenho um limite" in run("Qual meu orçamento em março de 2030?")[0]


@pytest.mark.parametrize(
    "text",
    [
        "Se eu gastar 99 reais num almoço, cabe?",
        "Gastei R$ 12 ou 13 no café",
        "Paguei -R$ 80",
        "Gastei R$ 40 em 31/02/2030",
        "Ganhei 7 mil de comissão",
        "Comprei um item por 25 dólares",
        "Gastei R$ 45 amanhã",
        "Avise a Ana que gastei R$ 12 no café",
        "Gastei 22,123 em transporte",
        "Gastei 20,00 no almoço?",
        "Gastei R$ 30 ontem e R$ 15 hoje",
        "Paguei 12,50 no almoço e 28,20 no mercado",
        "Ganhei 80 seguidores",
        "Comprei 2 livros",
        "Gastei 30 minutos tentando corrigir a agenda",
        "Recebi uma mensagem para pagar 300",
        "Gastei sem querer USD 10",
    ],
)
def test_never_post_uncertain_or_quoted_amounts(engine, run, text):
    run(text)
    with Session(engine) as db:
        assert not db.scalar(select(LedgerEntry.inbound_id))


def test_old_history_prefers_original_message_and_scope(engine, run):
    old = TODAY - timedelta(days=750)
    _, source = run("Visitei o museu de arqueologia na viagem a Belém.", at=old)
    run("Visitei o museu de arqueologia de outra pessoa.", at=old, route=OTHER)
    for i in range(14):
        run(f"Conversa de outro assunto {i}", at=old + timedelta(days=i))
    answer, _ = run("O que te contei sobre arqueologia?")
    assert "outra pessoa" not in answer and f"sua mensagem #{source.id}" in answer
    assert "Belém" in answer and old.strftime("%d/%m/%Y") in answer
    # Transcrições posteriores à criação do inbox também são indexadas.
    with Session(engine) as db, db.begin():
        db.execute(
            update(Inbound)
            .where(Inbound.id == source.id)
            .values(text="Visitei a pinacoteca em Belém.")
        )
    assert "Não encontrei" in run("O que te contei sobre arqueologia?")[0]
    assert "pinacoteca" in run("Procure nas conversas sobre pinacoteca")[0]


def test_explicit_preference_forgetting_and_context_boundaries(engine, run):
    saved, source = run("Guarde que eu prefiro respostas curtas")
    assert f"M{source.id}" in saved and "preferência" in saved
    assert "Já tenho" in run("Eu prefiro respostas curtas")[0]
    assert "respostas curtas" in run("Minhas memórias")[0]
    _, turn = run("Vamos conversar sobre um assunto diferente")
    with Session(engine) as db:
        assert "respostas curtas" in memory_context(db, turn)
    assert "Não encontrei" in run(f"Esqueça M{source.id}", route=OTHER)[0]
    assert "Removi" in run("Esqueça respostas curtas")[0]
    assert "Não encontrei" in run("O que te contei sobre respostas curtas?")[0]
    _, turn = run("Vamos conversar mais")
    with Session(engine) as db:
        assert "respostas curtas" not in memory_context(db, turn)
        assert source.id in db.scalars(excluded_history_ids(ROUTE)).all()


def test_sql_literal_search_and_natural_style(engine, run):
    for v in [
        "vc viu o filme kkk",
        "vc viu a série kkk",
        "vc já ouviu essa música?",
        "bora começar!",
    ]:
        run(v)
    _, turn = run("conte outra história")
    with Session(engine) as db:
        assert "abreviações" in memory_context(db, turn) and "risadas" in memory_context(db, turn)
    assert "Não encontrei" in run("Busque no histórico sobre inexistente OR 1=1--")[0]


def test_spoken_brl_and_currency_guard(run):
    assert (
        "R$ 12,45" in run("Gastei doze reais e quarenta e cinco centavos no café", kind="audio")[0]
    )
    assert "R$ 1.230,00" in run("Meu orçamento mensal é mil duzentos e trinta reais")[0]
    assert "R$ 12,45" in run("Quanto gastei hoje?")[0]
    assert "R$ 0,45" in run("Paguei quarenta e cinco centavos pelo envelope")[0]
    assert "único valor" in run("Paguei quinze reais no táxi e vinte reais no cinema")[0]
    assert "R$ 12,90" in run("Quanto gastei no dia 15/04/2030?")[0]


def test_older_schema_backfills_preexisting_messages(tmp_path):
    from alembic import command
    from alembic.config import Config
    from conftest import ROOT
    from sqlalchemy import text

    from jarvis.adapters.persistence.db import new_engine
    from jarvis.modules.personal.memory import historic

    db_engine = new_engine("sqlite:///" + str(tmp_path / "old.sqlite"))
    config = Config(str(ROOT / "alembic.ini"))
    with db_engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0009_calendar_pairs")
        conn.execute(text("INSERT INTO wa_chats (chat_id) VALUES (:route)"), dict(route=ROUTE))
        conn.execute(
            text("""INSERT INTO wa_inbox (chat_id,message_id,kind,text,received_at,available_at,state,attempts)
            VALUES (:route,'old','audio','Visitei o aquário antes da atualização','2027-01-10 09:00','2027-01-10 09:00','completed',1)"""),
            dict(route=ROUTE),
        )
        command.upgrade(config, "head")
    with Session(db_engine) as db:
        turn = queue.Turn(2, "token", ROUTE, "aquário", 1, TODAY)
        result = historic(db, turn, ["aquario"])
        assert len(result) == 1 and result[0].message_id == "old"
    db_engine.dispose()
