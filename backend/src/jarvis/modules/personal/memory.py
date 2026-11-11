"""Memória declarativa e busca auditável nas falas antigas do próprio usuário."""

import json
import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from jarvis.modules.assistant.intent import fold
from jarvis.modules.calendar.service import ZONE
from jarvis.modules.messaging.models import Inbound
from jarvis.modules.messaging.queue import Turn
from jarvis.modules.personal.models import Fact, PersonalAction

STOP = set(
    """a agora ai algo algum alguma algumas alguem antes ao aos aqui aquele aquela as assim ate com como
conversa conversas conversamos conversado conversando conversou contar contei contamos da das de dela dele depois disse
dissemos disso do dos e ela ele eles em entao era eram essa essas esse esses esta estava este eu falou falei falar falamos
fale fala foi foram gente ha isso ja la lembra lembranca lembrancas lembrar lembro mesmo meu meus minha minhas muito
na nada nao nas nem no nos nossas nosso nossos nossa num numa o os outra outro ou para pelo pela por pq pra quando
qual quais que quem quero sabe saber se sem ser seu seus sobre sua suas tambem te tem tempo tenho tinha tipo tu um uma
umas uns vez vc voces voce ontem me meses atras antes mes ano anos gostaria acha registro registros antes antigamente
algum antigo antiga nosso nossa ficou ficou""".split()
)
SAVE = re.compile(
    r"^(?:(?:guarde|guarda|salve|salva|grave|memorize|registre|registre isso|anote)(?: (?:na|em sua|em|na sua) memoria)?(?: (?:o fato de|a informacao de|isso))? que|(?:lembre-se|se lembre) (?:de )?que|(?:guarde|anote|salve) (?:isso )?na memoria\s*[:,-]?)\s+"
)
PREFERENCE = re.compile(
    r"^(?:(?:eu )?prefiro |(?:eu )?gosto (?:quando voce |de respostas )|(?:comigo[, ]+)?(?:converse|fale|responda) (?:comigo |de forma |sempre |mais |menos |sem )|(?:pode|voce pode) (?:falar|responder|conversar) (?:comigo |de forma |mais |menos |sem ))"
)
FORGET = re.compile(
    r"^(?:esqueca|esquece|apague da memoria|remova da memoria)\s+(?:(?:a lembranca|o fato)\s+)?(?:de )?(?:que )?"
)
RECALL = re.compile(
    r"^(?:(?:o )?que (?:voce |vc )?(?:lembra|sabe) (?:sobre|de)|(?:o )?que (?:eu |a gente |nos )?(?:ja )?(?:te )?(?:falei|contei|conversamos|falamos|conversou)(?: (?:mesmo |antes )?(?:sobre|de|a respeito de))?|(?:(?:voce|vc) )?(?:se )?lembra(?:-se)? (?:d[aeo]s? |sobre )|(?:busque|procure|pesquise) (?:nas? (?:nossas? )?conversas|no (?:nosso )?historico)(?: (?:sobre|por))?)\s*"
)
LIST = re.compile(
    r"^(?:(?:o )?que (?:voce |vc )?(?:sabe|lembra) (?:sobre|de) mim|(?:mostre|liste|quais sao) (?:as |minhas )?(?:memorias|lembrancas|preferencias)(?: salvas)?|minhas memorias)$"
)
SENSITIVE = re.compile(
    r"(?:senha|password|credencia(?:l|is)|chave privada|chave (?:da |de )?api|api key|access token|token de acesso|cvv)\b|\b(?:sk-|gsk_)[a-z0-9]{12,}",
    re.I,
)


def excluded_history_ids(route: str):
    return select(PersonalAction.inbound_id).where(PersonalAction.route == route)


def tokens(query: str):
    words = re.findall(r"[a-z0-9]+", fold(query))
    return list(dict.fromkeys(w for w in words if len(w) > 2 and w not in STOP))[:8]


def historic(db: Session, turn: Turn, words: list[str], limit: int = 5):
    """Termos saneados, busca indexada, vínculo e limite aplicados pelo banco."""
    if not words:
        return []
    if db.get_bind().dialect.name == "sqlite":
        relation = "personal_history_fts f JOIN wa_inbox i ON i.id=f.rowid"
        match = "personal_history_fts MATCH :query"
        order = "bm25(personal_history_fts), i.id DESC"
        query = " AND ".join('"' + w + '"' for w in words)
    else:
        relation = "wa_inbox i"
        vector = "to_tsvector('simple', translate(lower(i.text),'áàâãéêíóôõúçü','aaaaeeioooucu'))"
        match = vector + " @@ plainto_tsquery('simple', :query)"
        order = "i.id DESC"
        query = " ".join(words)
    rows = db.execute(
        text(f"""SELECT i.id, i.text, i.received_at FROM {relation}
        WHERE {match} AND i.chat_id=:route AND i.id<:before AND i.kind IN ('text','audio')
        AND i.state='completed' AND NOT EXISTS (SELECT 1 FROM personal_actions a WHERE a.inbound_id=i.id)
        ORDER BY {order} LIMIT :limit"""),
        dict(route=turn.chat_id, before=turn.id, query=query, limit=limit * 2),
    )
    # Carimbos passam pelo type decorator do projeto em SQLite e PostgreSQL.
    ids = [r.id for r in rows if not SENSITIVE.search(fold(r.text))]
    if not ids:
        return []
    records = {r.id: r for r in db.scalars(select(Inbound).where(Inbound.id.in_(ids[:limit])))}
    return [records[i] for i in ids[:limit]]


def facts(db: Session, route: str, words: list[str], limit: int = 6, *, preferences=False):
    stmt = (
        select(Fact, Inbound.received_at)
        .join(Inbound, Inbound.id == Fact.inbound_id)
        .where(Fact.route == route)
    )
    if preferences:
        stmt = stmt.where(Fact.kind == "preference")
    for word in words:
        stmt = stmt.where(Fact.searchable.contains(word, autoescape=True))
    return db.execute(stmt.order_by(Fact.inbound_id.desc()).limit(limit)).all()


def snippet(content: str, words: list[str], size: int):
    compact = " ".join(content.split())
    if len(compact) <= size:
        return compact
    position = min((fold(compact).find(w) for w in words if w in fold(compact)), default=0)
    start = max(0, min(position - 35, len(compact) - size))
    return (
        ("…" if start else "")
        + compact[start : start + size]
        + ("…" if len(compact) > start + size else "")
    )


def memory_context(db: Session, turn: Turn) -> str:
    """Dados da conversa em JSON. Nenhum dado recuperado concede autorização para agir."""
    words = tokens(turn.text)
    if SENSITIVE.search(fold(turn.text)):
        return ""
    preferences = facts(db, turn.chat_id, [], 4, preferences=True)
    relevant = facts(db, turn.chat_id, words, 4) if words else []
    seen = set()
    records = []
    for fact, stamp in [*preferences, *relevant]:
        if fact.inbound_id in seen:
            continue
        seen.add(fact.inbound_id)
        records.append(
            {
                "data": stamp.astimezone(ZONE).date().isoformat(),
                "tipo": fact.kind,
                "texto": fact.content[:260],
            }
        )
    history = historic(db, turn, words, 2) if words else []
    messages = db.scalars(
        select(Inbound.text)
        .where(
            Inbound.chat_id == turn.chat_id,
            Inbound.id < turn.id,
            Inbound.kind.in_(("audio", "text")),
            Inbound.state == "completed",
            Inbound.id.not_in(excluded_history_ids(turn.chat_id)),
        )
        .order_by(Inbound.id.desc())
        .limit(32)
    ).all()
    # Descreve somente sinais recorrentes. Um episódio isolado não vira preferência.
    signals = []
    if len(messages) >= 4:
        casual = sum(
            bool(re.search(r"\b(?:vc|tbm|blz|mano|vcs|pq|bora)\b", fold(v))) for v in messages
        )
        laughter = sum(
            bool(re.search(r"\b(?:k{3,}|(?:ha){2,}|(?:rs){2,})\b", fold(v))) for v in messages
        )
        if casual >= max(3, len(messages) // 3):
            signals.append("O dono costuma usar abreviações e linguagem informal.")
        if laughter >= max(2, len(messages) // 4):
            signals.append("O dono costuma usar risadas na conversa.")
    if not records and not history and not signals:
        return ""
    return json.dumps(
        {
            "memorias_do_usuario": records,
            "trechos_anteriores": [
                {
                    "data": row.received_at.astimezone(ZONE).date().isoformat(),
                    "texto": snippet(row.text, words, 200),
                }
                for row in history
            ],
            "marcas_de_estilo": signals,
        },
        ensure_ascii=False,
    )


def memory(db, turn, raw, normal, *, question):
    if LIST.fullmatch(normal):
        rows = facts(db, turn.chat_id, [], 11)
        if not rows:
            return "Ainda não tenho fatos ou preferências salvos neste vínculo."
        items = [
            f"M{fact.inbound_id} · {stamp.astimezone(ZONE):%d/%m/%Y} · {fact.content[:240]}"
            for fact, stamp in rows[:10]
        ]
        return "Memórias recentes:\n" + "\n".join(items)
    if forget := FORGET.match(normal):
        topic = normal[forget.end() :].strip()
        code = re.fullmatch(r"#?m(\d+)", topic)
        if code:
            fact = db.get(Fact, int(code[1])) if len(code[1]) < 16 else None
            matches = [fact] if fact and fact.route == turn.chat_id else []
        else:
            words = tokens(topic)
            if not words:
                return "Qual memória quer remover? Pode mandar «esqueça M123», usando o código que aparece em «minhas memórias»."
            matches = [f for f, _ in facts(db, turn.chat_id, words, 5)]
        if len(matches) > 1:
            return "Qual destas memórias? Envie «esqueça M…».\n" + "\n".join(
                f"M{f.inbound_id} · {f.content[:120]}" for f in matches
            )
        if not matches:
            return "Não encontrei essa memória salva neste vínculo."
        db.delete(matches[0])
        return "Removi essa memória das preferências e fatos que uso para conversar com você."
    if recall := RECALL.match(normal):
        words = tokens(normal[recall.end() :])
        if not words:
            return "Que assunto devo procurar nas nossas conversas?"
        rows = facts(db, turn.chat_id, words, 5)
        older = historic(db, turn, words, 5)
        items = [
            f"{stamp.astimezone(ZONE):%d/%m/%Y} · memória M{fact.inbound_id}: {snippet(fact.content, words, 210)}"
            for fact, stamp in rows
        ]
        items += [
            f"{row.received_at.astimezone(ZONE):%d/%m/%Y} · sua mensagem #{row.id}: «{snippet(row.text, words, 210)}»"
            for row in older
        ]
        return (
            "Encontrei o seguinte:\n" + "\n".join(items)
            if items
            else "Não encontrei esse assunto nas memórias ou nas mensagens deste vínculo."
        )
    save = SAVE.match(normal)
    explicit_style = PREFERENCE.match(normal)
    if question or not (save or explicit_style):
        return None
    content = raw[save.end() :].strip() if save else raw
    if not content or len(content) > 600:
        return "Pode resumir essa memória em até 600 caracteres?"
    if SENSITIVE.search(fold(content)):
        return "Guarde credenciais em um gerenciador de senhas. Reservo as memórias para fatos e preferências."
    key = fold(content)
    duplicate = db.scalar(
        select(Fact).where(Fact.route == turn.chat_id, Fact.searchable == key).limit(1)
    )
    if duplicate:
        return f"Já tenho essa memória salva como M{duplicate.inbound_id}."
    kind = "preference" if PREFERENCE.match(key) else "fact"
    db.add(Fact(inbound_id=turn.id, route=turn.chat_id, kind=kind, content=content, searchable=key))
    return f"Guardei {'essa preferência' if kind == 'preference' else 'essa memória'} (M{turn.id}): {content}"
