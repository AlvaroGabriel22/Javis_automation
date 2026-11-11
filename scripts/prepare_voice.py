"""Instala os pesos verificados do modelo base ou aproveita uma cópia local."""

import os
import shutil
from pathlib import Path

MODEL = "Systran/faster-whisper-base"
REVISION = "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66"
FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")


def main():
    target = Path(__file__).resolve().parents[1] / "backend/.voice/model"
    if all((target / name).is_file() for name in FILES):
        return
    source = os.getenv("WHISPER_MODEL_SOURCE")
    if source:
        folder = Path(source)
    else:
        from huggingface_hub import snapshot_download

        folder = Path(
            snapshot_download(MODEL, revision=REVISION, allow_patterns=list(FILES))
        )
    if not all((folder / name).is_file() for name in FILES):
        raise SystemExit("A pasta de origem precisa conter o modelo base completo.")
    target.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        temp = target / (name + ".part")
        shutil.copyfile(folder / name, temp)
        temp.replace(target / name)


if __name__ == "__main__":
    main()
