"""Processo local de conversa: uma inferência por vez, com lease renovável."""

import argparse
import signal
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from threading import Event, Thread

from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from jarvis.adapters.ai.provider import ChatConfigurationError, UnavailableChat, chat_from_env
from jarvis.adapters.ai.vision import VisionError, analyze
from jarvis.adapters.persistence.db import new_engine, ready, utcnow
from jarvis.bootstrap.settings import ConfigurationError
from jarvis.modules.assistant.agent import ChatModel, respond
from jarvis.modules.messaging import queue
from jarvis.modules.receipts.reading import ReceiptReading
from jarvis.modules.receipts.service import propose_receipt


def process_one(
    engine: Engine, model: ChatModel, *, clock: Callable[[], datetime] = utcnow
) -> bool:
    turn = queue.claim_turn(engine, now=clock())
    if not turn:
        return False
    claimed = turn
    done = Event()

    def heartbeat():
        while not done.wait(queue.TURN_LEASE.total_seconds() / 3):
            try:
                if not queue.renew_turn(engine, claimed, now=clock()):
                    return
            except SQLAlchemyError:
                # O commit de conclusão ainda exige o token e o prazo vigentes.
                return

    keeper = Thread(target=heartbeat, daemon=True)
    keeper.start()
    try:
        if turn.kind == "notice":
            queue.complete_turn(engine, turn, turn.text, now=clock())
            return True
        if turn.kind == "audio" and turn.media is not None:
            from jarvis.adapters.ai.speech import SpeechError, transcribe

            try:
                transcript = transcribe(turn.media)
            except SpeechError as exc:
                queue.complete_turn(engine, turn, str(exc), now=clock())
                return True
            if not queue.valid_text(transcript, 16000):
                queue.complete_turn(
                    engine,
                    turn,
                    "Não consegui obter uma transcrição válida. Envie um áudio mais curto ou escreva a mensagem.",
                    now=clock(),
                )
                return True
            if not queue.store_transcription(engine, turn, transcript, now=clock()):
                return True
            turn = replace(turn, text=transcript, media=None)
        if turn.kind in ("image", "sticker"):
            try:
                reading = analyze(turn.media, kind=turn.kind, caption=turn.text)
            except VisionError as exc:
                queue.complete_turn(engine, turn, str(exc), now=clock())
                return True
            if isinstance(reading, ReceiptReading):
                current = clock()
                queue.apply_command(
                    engine,
                    turn,
                    lambda db: propose_receipt(db, turn, reading, current),
                    now=current,
                )
            else:
                queue.complete_turn(engine, turn, reading, now=clock())
            return True
        respond(engine, turn, model, clock)
    finally:
        done.set()
        keeper.join()
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Worker de conversa do Jarvis")
    parser.add_argument("--once", action="store_true", help="processa até uma entrada e encerra")
    args = parser.parse_args()
    stop = Event()

    def shutdown(signum, frame):
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    try:
        model = chat_from_env()
    except ChatConfigurationError as exc:
        print(exc.issue.message, file=sys.stderr)
        model = UnavailableChat(exc.issue)
    except (ConfigurationError, ValidationError, ValueError):
        print(
            "Confira o provedor de conversa no .env. As ferramentas locais seguem ativas.",
            file=sys.stderr,
        )
        model = UnavailableChat()
    try:
        engine = new_engine()
    except (ConfigurationError, ValidationError, ValueError):
        print("Configuração de banco inválida.", file=sys.stderr)
        return 1
    try:
        if not ready(engine):
            print("Migração pendente. Execute alembic upgrade head.", file=sys.stderr)
            return 1
        while not stop.is_set():
            try:
                worked = process_one(engine, model)
            except SQLAlchemyError:
                print("O worker perdeu acesso ao banco local.", file=sys.stderr)
                if args.once:
                    return 1
                stop.wait(5)
                continue
            if args.once:
                return 0
            if not worked:
                stop.wait(1)
    except SQLAlchemyError:
        print("Banco ou migração indisponível.", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
