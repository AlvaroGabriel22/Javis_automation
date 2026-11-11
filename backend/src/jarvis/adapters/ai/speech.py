"""Executa o reconhecimento local em ambiente isolado das dependências do gateway."""

import os
import subprocess
from pathlib import Path

from jarvis.bootstrap.settings import ROOT

MAX_BYTES = 8 * 1024 * 1024
MAX_SECONDS = 120


class SpeechError(Exception):
    pass


def transcribe(data: bytes) -> str:
    if not 0 < len(data) <= MAX_BYTES:
        raise SpeechError("Envie um áudio de até 8 MB e dois minutos.")
    voice = ROOT / "backend/.voice"
    executable = voice / "bin/python"
    model = Path(os.getenv("JARVIS_WHISPER_MODEL_DIR", str(voice / "model")))
    if not executable.is_file() or not (model / "model.bin").is_file():
        raise SpeechError(
            "A voz ainda não está instalada. Execute make voice na pasta do Jarvis ou mande por texto."
        )
    try:
        result = subprocess.run(
            [str(executable), str(ROOT / "scripts/recognize.py"), str(model), str(MAX_SECONDS)],
            input=data,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=240,
            check=False,
            env={**os.environ, "HF_HUB_OFFLINE": "1", "OMP_NUM_THREADS": "2"},
        )
        if result.returncode == 2:
            raise SpeechError(
                "Não consegui distinguir a fala. Envie um áudio mais claro, de até dois minutos, ou escreva."
            )
        text = result.stdout.decode().strip()
        if result.returncode or not text or len(text) > 4000:
            raise SpeechError(
                "Não consegui transcrever o áudio localmente. Confira o modelo de voz ou mande por texto."
            )
        return text
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise SpeechError(
            "A transcrição local ficou indisponível. Tente novamente ou envie por texto."
        ) from None
