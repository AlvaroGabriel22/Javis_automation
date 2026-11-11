"""A ordem da resposta no WhatsApp faz parte do retorno operacional."""

from test_assistant_commands import process

from jarvis.modules.assistant.intent import parse_command


def test_greeting_at_the_start_keeps_the_request_and_dictated_greeting_separate(
    engine, clock, signed
):
    reply = process(engine, clock, msg_id="cafe", text="Bom dia, Jarvis! Gastei R$ 12 no café.")
    assert "Despesa registrada" in reply and "R$ 12,00" in reply and "café" in reply
    command = parse_command("Olá! Avise a Gabi daqui a 2 minutos dizendo: Boa noite, xuxu!")
    assert command and command["action"] == "create"
    assert command["body"] == "Boa noite, xuxu!"


def test_answer_and_financial_breakdown_are_distinct(engine, clock, signed):
    process(engine, clock, msg_id="budget", text="Meu orçamento mensal é R$ 1.000")
    process(engine, clock, msg_id="reserve", text="Minha reserva mensal é R$ 200")
    process(engine, clock, msg_id="expense", text="Gastei R$ 350 no mercado")
    reply = process(engine, clock, msg_id="question", text="Posso gastar R$ 500?")
    advice, figures = reply.split("\n\n")
    assert "R$ 500,00" in advice and "faltariam R$ 50,00" in advice
    assert figures.splitlines() == [
        "Orçamento de 01/2031 · R$ 1.000,00",
        "Gastos registrados · R$ 350,00",
        "Reserva protegida: R$ 200,00",
        "Margem antes da compra · R$ 450,00",
    ]


def test_calendar_groups_schedule_details_before_confirmation(engine, clock, signed):
    reply = process(
        engine,
        clock,
        msg_id="create",
        text="Marque reunião amanhã das 9h às 11h e me lembre 15 minutos antes",
    )
    lines = reply.splitlines()
    assert "reunião" in lines[0]
    assert "12/01/2031 às 09:00 (Manaus)" in lines[1] and "#" in lines[1]
    assert lines[2:] == ["Término às 11:00.", "Aviso 15 minutos antes.", "Salvo na agenda local."]


def test_receipt_question_separates_reading_from_purchase_confirmation(
    engine, clock, signed, monkeypatch
):
    from test_receipt_photos import fields, photo, send

    from jarvis.modules.receipts.reading import ReceiptReading

    monkeypatch.setattr(
        "jarvis.entrypoints.worker.analyze",
        lambda *a, **kw: ReceiptReading.model_validate(fields()),
    )
    image_id, reply = send(engine, clock, "cupom", "[Imagem]", binary=photo())
    summary, actions = reply.split("\n\n")
    assert "R$ 36,49" in summary and "Papelaria Lua" in summary and "09/01/2031" in summary
    confirm, correct, discard = actions.splitlines()
    assert f"confirmar cupom C{image_id}" in confirm
    assert f"valor C{image_id}" in correct and f"data C{image_id}" in correct
    assert f"cancelar cupom C{image_id}" in discard
