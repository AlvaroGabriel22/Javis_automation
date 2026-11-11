"""Gramática dos pedidos do dono. Texto de recado e comando ocupam campos distintos."""

import re
import unicodedata

from jarvis.modules.assistant.dates import CALENDAR_DATE, DAY_NUMBER


def fold(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(c)
    )


NUDGE = r"(?:da|de|dar) um toque (?:na|no|pra|pro|para|ao|a)"
VERBS = re.compile(
    rf"\b(?P<verb>{NUDGE}|avisa|avise|avisar|manda|mande|mandar|enviar|envia|envie|informe|informa|informar|diga|diz|dizer|fale|fala|falar|agende|agenda|agendar|"
    r"programe|programa|remarque|remarca|reagende|reagenda|muda|mude|altere|altera|"
    r"cancele|cancela|cancelar|desmarque|desmarca)\b"
)
WHEN = re.compile(
    r"\b(?:agora\b|depois de amanha|amanha|hoje|daqui\b|em (?=\w+(?: e \w+)? (?:segundos?|minutos?|horas?|dias?))|"
    rf"as (?=\w)|(?:para as|pras) (?=\w)|(?:no )?dia (?={DAY_NUMBER}\b)|todo(?:s)?\b|toda(?:s)?\b|diariamente|semanalmente|"
    r"(?:(?:na |no |nesta |neste )?(?:proxim[ao] )?)(?:segunda|terca|quarta|quinta|sexta|sabado|domingo)\b|"
    rf"{CALENDAR_DATE}\b|"
    r"\d{1,2}(?:h\d{0,2}\b|:\d\d\b)|(?:ao )?meio[- ]dia|(?:a )?meia[- ]noite|a (?=uma\b|1h?\b))"
)
LEAD = re.compile(
    r"^(?:(?:oi|ola|bom dia|boa tarde|boa noite)\b[,.!\s]+|jarvis\b[,.! ]*|"
    r"(?:por favor|por gentileza)[, ]*|(?:(?:voce|vc|ce) )?(?:pode|poderia) (?:por favor )?|"
    r"(?:eu )?(?:quero|preciso|gostaria) (?:que (?:voce )?)?)+"
)
LIST = re.compile(
    r"^(?:listar?|liste|mostr(?:a|e)|consult(?:a|e)|quais(?: sao)?|(?:o )?que (?:tem|esta))"
    r"\b.{0,55}\b(?:agenda|agendamentos|agendadas|agendado|agendados|marcados|envios|recados)\b"
)
BODY = re.compile(
    r"\s+(?:exatamente assim|literalmente|dizendo(?: que)?|com o (?:texto|recado)|"
    r"(?:a |esta )?mensagem(?=\s*[:“‘\"'])|que\b)\s*:?\s*|:(?!\d)|(?=[“‘\"])"
)
ALIAS = re.compile(r"^(?:chame|chama|apelide)\s+(.+?)\s+de\s+(.+?)[.!]?$", re.I)


def tidy(value: str) -> str:
    return value.strip(" \t\n,;:.!?")


def endpoint(value: str) -> str:
    value = tidy(value)
    prefix = re.match(
        r"^(?:(?:uma? |o |a )?(?:aviso|recado|mensagem|agendamento|envio)\s+)?"
        r"(?:(?:para (?:o |a )?|p/|pra |pro |ao |à |a |o |da |do |de )+)?",
        value,
        re.I,
    )
    return tidy(value[prefix.end() :]) if prefix else value


def unquote(text: str) -> str:
    text = text.strip()
    quotes = {'"': '"', "'": "'", "“": "”", "‘": "’", "«": "»"}
    if text and (end := quotes.get(text[0])):
        pos = text.rfind(end)
        if pos > 0 and not text[pos + 1 :].strip(" .!?"):
            return text[1:pos]
    return text


def split_fields(tail: str) -> tuple[str, str, str]:
    """Extrai o conteúdo antes de procurar o horário, que também pode aparecer no recado."""
    normal = fold(tail)
    marker = BODY.search(normal)
    # A pausa do ditado também separa o destinatário do recado. Uma vírgula antes
    # de "daqui a..." ou "que..." ainda pertence à instrução, inclusive em transcrições.
    for pause in re.finditer(r",(?!\d)\s*|[.!]\s+|\n+", normal):
        if marker and marker.start() <= pause.start():
            break
        rest = normal[pause.end() :]
        if (
            not rest
            or WHEN.match(rest)
            or re.match(r"(?:que|dizendo|exatamente|literalmente|para|pra|pro)\b", rest)
        ):
            continue
        marker = pause
        break
    greeting = re.search(r"\s+(?=(?:oi|ola|bom dia|boa noite|boa tarde)\b)", normal)
    if greeting and (marker is None or greeting.start() < marker.start()):
        marker = greeting
    head, body = tail, ""
    if marker is not None:
        body = unquote(tail[marker.end() :])
        head = tail[: marker.start()]
    duration = re.search(
        r"\b(?:daqui(?: a| ha)?|em) [\w ]{1,40}? (?:minutos?|segundos?|horas?|dias?)\b", fold(head)
    )
    if duration:
        remainder = tail[duration.end() :]
        gap = re.match(r"\s+(?=\w)", remainder)
        if (
            gap
            and not re.match(
                r"(?:que|dizendo|com|literalmente|exatamente|e|ou|se|quando|a mensagem|para|pra|pro|por volta|aproximadamente)\b",
                fold(remainder).lstrip(),
            )
            and not WHEN.match(fold(remainder).lstrip())
        ):
            head = tail[: duration.end()]
            body = tail[duration.end() + gap.end() :]
    tm = WHEN.search(fold(head))
    if tm is None:
        return endpoint(head), "", body
    before, after = head[: tm.start()], head[tm.start() :]
    # "agenda amanhã às 10h para Ana" também explicita o destinatário depois da hora.
    dest = re.search(r"\s+(?:para (?!as?\b)(?:o |a )?|pra (?!as?\b)|pro )(.+)$", after, re.I)
    if dest:
        return endpoint(dest.group(1)), tidy(after[: dest.start()]), body
    return endpoint(before), tidy(after), body


def unclear_timed_audio(text: str) -> bool:
    normal = fold(text.strip())
    normal = re.sub(r"^(?:(?:jarvis|javis|jarves|por favor)[,.! ]+)+", "", normal)
    return bool(
        re.match(r"^(?:daqui|aqui (?:ha|a)) .{0,30}?(?:segundos?|minutos?|horas?|dias?)\b", normal)
        or (WHEN.match(normal) and re.search(r"\b(?:para|avise|envie|voce|que)\b", normal))
        or re.match(r"^(?:envi|e vi|da vis|a vis) (?:para|pra|a|o) ", normal)
    )


def mentions_message_request(text: str) -> bool:
    # Comando indireto que a gramática não reconheceu: o chat livre não confirma ações.
    return bool(
        re.search(
            r"\b(?:envi[ae]r?|mand[ae]r?|avis[ae]r?|inform[ae]r?|diga|dizer|fal[ae]r?)\b"
            r"[^.!?\n]{0,55}\b(?:pra|pro|para)\s+\w",
            fold(text),
        )
    )


def parse_command(text: str, *, audio: bool = False) -> dict | None:
    raw = text.strip()
    if audio:
        raw = re.sub(r"^(?:javis|jarves|jarviz)\b[,.! ]*", "", raw, flags=re.I)
    normal = fold(raw)
    lead = LEAD.match(normal)
    if lead:
        raw = raw[lead.end() :].strip()
        normal = fold(raw)
    if audio:
        # Corrige apenas o verbo dentro do cabeçalho, jamais palavras do recado.
        misheard = re.match(
            r"^((?:daqui (?:a |ha )?[\w ]{1,30}? (?:segundos?|minutos?|horas?|dias?)[, ]+)?)"
            r"(envi|e vi)(?= (?:para|pra|pro) )",
            normal,
        )
        if misheard and (misheard[1] or misheard[2] == "envi"):
            raw = raw[: misheard.start(2)] + "envie" + raw[misheard.end(2) :]
            normal = fold(raw)
    if LIST.search(normal) or normal.rstrip(" ?!.") in {
        "minha agenda",
        "meus agendamentos",
        "minhas mensagens agendadas",
    }:
        return {"action": "list"}
    if alias := ALIAS.fullmatch(raw):
        return {
            "action": "alias",
            "recipient": endpoint(alias.group(1)),
            "alias": tidy(alias.group(2)),
        }
    verb = VERBS.search(normal)
    if verb is None:
        return None
    prefix = tidy(raw[: verb.start()])
    prefix = tidy(
        re.sub(
            r"(?:^|[\s,])(?:(?:você|voce|vc|cê|ce)(?:\s+pode)?|pode|por favor)$",
            "",
            prefix,
            flags=re.I,
        )
    )
    self_target = bool(re.search(r"(?:^|\s)me$", prefix, re.I))
    if self_target:
        prefix = tidy(prefix[:-2])
    if prefix and not WHEN.match(fold(prefix)):
        return None
    word, tail = verb["verb"], raw[verb.end() :].strip()
    if word.startswith(("cancel", "desmar", "remarc", "reag", "mud", "alter")):
        action = "cancel" if word.startswith(("cancel", "desmar")) else "move"
        if action == "cancel" and prefix:
            return None
        if not tail and action == "move":
            return None
        # "Muda o assunto", por exemplo, continua no chat.
        if word.startswith(("mud", "alter")) and not re.search(
            r"\b(?:aviso|agenda\w*|recado|envio|mensagem|pra|pro|pras|para|hora\w*)\b", fold(tail)
        ):
            return None
        name, when, body = split_fields(tail)
        if action == "cancel" and (when or body):
            return {"action": "clarify_cancel"}
        return {"action": action, "recipient": name, "when": when or prefix}
    if word.startswith(("mand", "envi")):
        # Só trate “zap” como o canal quando também houver um destinatário expresso.
        channel = re.match(r"(?:um )?(?:zap|whatsapp)\s+(?=(?:pra|pro|para|ao|a)\s)", fold(tail))
        if channel:
            tail = tail[channel.end() :]
    name, when, body = split_fields(tail)
    if "um toque" in word and not (name and body):
        return None
    if word in {
        "diga",
        "diz",
        "dizer",
        "fale",
        "fala",
        "falar",
        "informe",
        "informa",
        "informar",
    } and not (
        when
        or prefix
        or (body and name)
        or re.match(r"(?:pra|pro|para|ao|à)\s", tail, re.I)
        or self_target
    ):
        return None
    if not body and re.search(r"\b(?:audio original|proprio audio)\b", fold(tail)):
        return {"action": "original_audio"}
    if word.startswith(("mand", "envi")) and not (
        self_target
        or prefix
        or re.search(r"\b(?:pra|pro|para|mensagem|recado|aviso)\b", fold(tail))
    ):
        return None
    if self_target and (
        not name or fold(name) in {"mensagem", "uma mensagem", "um recado", "um aviso"}
    ):
        name = "mim"
    # Dois horários concorrentes requerem uma escolha explícita.
    return {
        "action": "create",
        "recipient": name,
        "when": " ".join(filter(None, (prefix, when))),
        "body": body,
    }
