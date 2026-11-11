"""Limites para reações opcionais. A escolha de tom e da figurinha cabe ao modelo."""

import re
from collections.abc import Sequence

from jarvis.modules.assistant.intent import fold, mentions_message_request
from jarvis.modules.calendar.periods import command_text
from jarvis.modules.personal.memory import SENSITIVE

# Reconhece sinais que merecem sobriedade mesmo quando há "kkk" ou emoji na mensagem.
DISTRESS = re.compile(
    r"\b(?:luto|falec\w*|faleceu|faleceram|morreu|morreram|velorio|enterro|funeral|"
    r"perdi (?:meu|minha|o meu|a minha) (?:mae|pai|irma[oa]|avo|filh[ao]|amig[oa]|cachorr[ao]|gat[oa]|emprego)|"
    r"estou mal|to mal|estou pessim[oa]|to pessim[oa]|tudo (?:deu|dando) errado|"
    r"tristes?|tristeza|desesperad[oa]|arrasad[oa]|demitid[oa]|sem emprego|desempregad[oa]|"
    r"depress\w*|ansiedade|crise de panico|suicid\w*|me matar|quero morrer|"
    r"violencia|ameac\w*|abus[oa]|assedi\w*|assaltad[oa]|acidente|socorro|emergencia|"
    r"hospital\w*|internad[oa]s?|cirurgia|diagnostico|cancer|tratamento medico|"
    r"remedio|medicamento|doente|doenca|passando mal|muita dor|dores fortes|dor no peito|"
    r"assunto serio|falando serio|sem (?:piadas?|gracinhas?|brincadeiras?|humor|figurinhas?)|"
    r"apenas (?:texto|por escrito)|so em texto)\b"
)
PRIVATE_OR_LEDGER = re.compile(
    r"\b(?:cpf|cnpj|rg|dados bancarios|conta bancaria|cartao de credito|pix|endereco residencial|"
    r"boleto|fatura|extrato|saldo|orcamento|financiamento|dividas?|endividad[oa]|"
    r"sem dinheiro|gastei|quanto (?:custa|posso gastar)|cupom fiscal|"
    r"(?:meu|minha|seu|sua) (?:telefone|celular|endereco|e-?mail))\b|"
    r"\br\$\s*\d|\b\d+(?:[.,]\d+)? reais\b|\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|"
    r"\+?\d[\d ()-]{7,}\d|\S+@\S+\.\S+|https?://|\x60\x60\x60"
)
ACTION = re.compile(
    r"^(?:(?:agend|marqu|remarqu|mand|envi|avis|registr|anot|salv|guard|lembr|apagu|exclu|"
    r"pesquis|procur|consult|busqu|calcul|corrij|cancel|reagend|confirm|preench|cri|ger|"
    r"program|transcrev|resum|traduz|escrev|organiz)(?:ar|er|ir|a|e|i)?|"
    r"lembre-me|me lembr[ae]|faca|fazer|preciso que|quero que|me ajude a|me ajuda a)\b"
)
RESOURCE = r"(?:relatorios?|planilhas?|google sheets|google agenda|icloud|agendamentos?|agendas?)"
STATUS = r"(?:pront[oa]|salv[oa]|confirmad[oa]|sincronizad[oa]|atualizad[oa]|gerad[oa]|enviad[oa]|pendente|cancelad[oa])s?"
OPERATIONS = re.compile(
    r"\b(?:codigo de (?:acesso|vinculo|confirmacao)|"
    rf"{RESOURCE} (?:(?:ja |ainda )?(?:esta|estao|foi|foram) )?{STATUS}|"
    rf"(?:gerei|enviei|salvei|sincronizei|consultei|atualizei) (?:[a-z]+ ){{0,3}}{RESOURCE}|"
    r"aguardando confirmacao|"
    r"salv[oa] (?:no|na) (?:jarvis|agenda|planilha)|"
    r"(?:despesa|receita) registrada|confirme (?:o|a) (?:valor|envio|destinatario))\b"
)
RESOURCE_LOOKUP = re.compile(
    rf"^(?:como (?:esta|acessar|usar|configurar)|o que (?:tem|ha)|cad[eê]|qual|quais|mostr[ae]|verifique|liste|abra|baixe|exporte)"
    rf"\b[^.!?]{{0,90}}\b{RESOURCE}\b"
)
# Uma instrução continua operacional se estiver citada ou vier depois de "kkk.".
CLAUSES = re.compile(r"""[.!?;:"'“”‘’«»\n]+\s*""")
MESSAGE_TO_PERSON = re.compile(
    r"\b(?:avise|avisa|informe|notifique|encaminhe|mande|envie|repass[ae]|diga|fale) "
    r"(?:(?:isso|uma? (?:mensagem|recado)) )?(?:ao?|aos?|pra|pro|para)\b"
)
MEMORY_PREFIX = "Dados recuperados da minha conversa para referência factual e de tom: "


def reaction_safe_reply(text: str) -> bool:
    if not text or not text.strip():
        return False
    value = fold(" ".join(text.split()))
    return not any(
        pattern.search(value) for pattern in (DISTRESS, SENSITIVE, PRIVATE_OR_LEDGER, OPERATIONS)
    )


def social_candidate(text: str) -> bool:
    if not reaction_safe_reply(text):
        return False
    value = fold(text)
    if MESSAGE_TO_PERSON.search(value) or mentions_message_request(text):
        return False
    for clause in CLAUSES.split(value):
        phrase = command_text(clause)
        if ACTION.match(phrase) or RESOURCE_LOOKUP.match(phrase):
            return False
    return True


def recent_social_context(messages: Sequence[dict[str, str]]) -> bool:
    """Preserva a sobriedade em continuações como 'valeu' depois de um relato delicado."""
    previous = messages[:-1]
    for message in reversed(previous):
        role, text = message.get("role"), message.get("content", "")
        if role == "system" or text.startswith(MEMORY_PREFIX):
            continue
        if role == "user":
            return social_candidate(text)
        if role == "assistant" and not reaction_safe_reply(text):
            return False
    return True
