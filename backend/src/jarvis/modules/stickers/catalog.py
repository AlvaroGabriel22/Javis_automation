"""Identidades estáveis do catálogo e leitura dos WebP distribuídos com o Jarvis."""

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from io import BytesIO

from PIL import Image

MAX_BYTES = 100 * 1024


@dataclass(frozen=True)
class Sticker:
    key: str
    name: str
    aliases: tuple[str, ...]


CATALOG = (
    Sticker("combinado", "Combinado", ("combinado", "ok", "confirmado", "beleza")),
    Sticker("bom-dia", "Bom dia", ("bom dia", "sol")),
    Sticker("boa-noite", "Boa noite", ("boa noite", "lua")),
    Sticker("valeu", "Valeu", ("valeu", "obrigado", "obrigada", "agradecimento")),
    Sticker("risada", "Risada", ("risada", "rindo", "gargalhada", "kkkk")),
    Sticker("boa", "Boa!", ("boa", "comemoracao", "conquista", "parabens")),
)
BY_KEY = {item.key: item for item in CATALOG}
BY_NAME = {name: item for item in CATALOG for name in (*item.aliases, item.key)}


@lru_cache(maxsize=len(CATALOG))
def sticker_bytes(key: str) -> bytes:
    # Um ID externo nunca se torna caminho. Os arquivos são recursos do pacote instalado.
    item = BY_KEY.get(key)
    if item is None:
        raise ValueError("Figurinha desconhecida.")
    asset = files(__package__).joinpath("assets", item.key + ".webp")
    with asset.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if not 0 < len(data) <= MAX_BYTES:
        raise ValueError("Tamanho de figurinha inválido.")
    with Image.open(BytesIO(data), formats=("WEBP",)) as picture:
        if picture.size != (512, 512) or getattr(picture, "n_frames", 1) != 1:
            raise ValueError("Dimensão ou animação inesperada.")
        picture.load()
    return data
