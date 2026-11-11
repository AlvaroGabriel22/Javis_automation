import pytest
from sqlalchemy.orm import Session

from jarvis.modules.messaging import queue
from jarvis.modules.messaging.models import Outbound
from jarvis.modules.stickers.policy import (
    reaction_safe_reply,
    recent_social_context,
    social_candidate,
)
from jarvis.modules.stickers.preferences import (
    allows_reactions,
    handle_preference,
    requested_preference,
)


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("Jarvis, sem figurinhas por enquanto.", False),
        ("Por favor, prefiro sem figurinhas", False),
        ("Não me mande mais stickers", False),
        ("Para de me mandar figurinhas automáticas, por favor.", False),
        ("Só me mande figurinhas se eu pedir", False),
        ("Jarvis, pode voltar a mandar figurinhas!", True),
        ("Você pode me enviar figurinhas quando fizer sentido.", True),
        ("Manda a figurinha de boa noite", None),
        ("Por que você não envia figurinhas?", None),
        ("Ela disse: sem figurinhas por enquanto", None),
        ('Mande pra Gabi "sem figurinhas por enquanto"', None),
        ("Se eu pedir, pode voltar a mandar figurinhas.", None),
        ("Eu não disse para parar de mandar figurinhas.", None),
        ("Comprei o álbum sem figurinhas", None),
    ],
)
def test_preference_commands_match_only_requests_from_owner(request_text, expected):
    assert requested_preference(request_text) is expected


def test_preference_is_persistent_isolated_and_ignores_replay(engine, clock, signed):
    def change(route, key, text):
        assert queue.accept_inbound(engine, chat_id=route, message_id=key, text=text, now=clock())
        turn = queue.claim_turn(engine, now=clock())
        assert turn and queue.apply_command(
            engine, turn, lambda db: handle_preference(db, turn), now=clock()
        )
        return turn

    route_a, route_b = "vinculo-A", "vinculo-B"
    first = change(route_a, "opcao-1", "sem figurinhas por enquanto")
    with Session(engine) as db:
        assert not allows_reactions(db, route_a) and allows_reactions(db, route_b)
        assert db.get_one(Outbound, first.id).text == "Fechado. Só mando figurinha se você pedir."
    change(route_a, "opcao-2", "pode voltar a mandar figurinhas")
    with Session(engine) as db, db.begin():
        handle_preference(db, first)
    with Session(engine) as db:
        assert allows_reactions(db, route_a)
    change(route_b, "opcao-3", "desative as figurinhas")
    with Session(engine) as db:
        assert allows_reactions(db, route_a) and not allows_reactions(db, route_b)


@pytest.mark.parametrize(
    "text",
    [
        "Acabei passando em todas as provas, acredita?",
        "kkkk ainda estou rindo daquela piada",
        "Oi! Bom dia, meu consagrado!",
        "A Gabi contou a melhor piada da noite",
        "Trabalhei o dia todo, agora é sofá e série!",
        "Fechou!",
        "Consegui terminar aquele relatório haha",
        "Finalmente terminei a planilha, ficou linda!",
        "Fiz uma apresentação usando Google Sheets!",
    ],
)
def test_spontaneous_choice_is_available_in_free_conversation(text):
    assert social_candidate(text)


@pytest.mark.parametrize(
    "text",
    [
        "Fiquei triste mesmo. rsrs",
        "Minha vó faleceu hoje.",
        "Tô no hospital com o meu pai",
        "Qual a melhor opção para pagar minhas dívidas?",
        "Não conta pra ninguém. Meu CPF é 000.000.000-00.",
        "Beleza! Minha senha vai por aqui.",
        "Se eu não voltar, avisa a Gabi amanhã",
        "Jarvis, pode me ajudar? Mande um recado para o Dani",
        "Crie um texto sobre o jogo de ontem.",
        "Você poderia agendar aquela reunião?",
        "Jarvis, me lembre de comprar café",
        "Vou gastar R$ 420 nessa brincadeira",
        "Meu e-mail é dani@example.invalid",
        "Qual o status do relatório do mês?",
        "Mandei 'gere um relatório de outubro' por áudio.",
        "Kkk. Salve na planilha que comprei pipoca.",
        "A planilha já está sincronizada.",
        "Enviei o relatório de julho para seu e-mail.",
        "Como está minha agenda?",
    ],
)
def test_stickers_are_withheld_in_serious_private_and_operational_turns(text):
    assert not social_candidate(text)


def test_a_short_response_after_sensitive_story_keeps_immediate_context():
    now = {"role": "user", "content": "Valeu pelo apoio."}
    past = {"role": "user", "content": "Hoje perdi minha mãe."}
    support = {"role": "assistant", "content": "Estou aqui com você."}
    assert not recent_social_context([past, support, now])
    assert not reaction_safe_reply("Podemos falar do luto com calma.")
    # Uma preferência recuperada não transforma uma semana inteira de conversa em assunto delicado.
    recovered = {
        "role": "user",
        "content": "Dados recuperados da minha conversa para referência factual e de tom: tive um acidente anos atrás",
    }
    assert recent_social_context(
        [past, {"role": "user", "content": "kkkk adorei essa!"}, recovered, now]
    )
