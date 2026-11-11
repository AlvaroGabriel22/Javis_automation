"""Números por extenso compartilhados por datas, horários e ferramentas aritméticas."""

import re

UNITS = {
    "zero": 0,
    "um": 1,
    "uma": 1,
    "dois": 2,
    "duas": 2,
    "tres": 3,
    "quatro": 4,
    "cinco": 5,
    "seis": 6,
    "sete": 7,
    "oito": 8,
    "nove": 9,
    "dez": 10,
    "onze": 11,
    "doze": 12,
    "treze": 13,
    "quatorze": 14,
    "catorze": 14,
    "quinze": 15,
    "dezesseis": 16,
    "dezessete": 17,
    "dezoito": 18,
    "dezenove": 19,
}
TENS = {
    "vinte": 20,
    "trinta": 30,
    "quarenta": 40,
    "cinquenta": 50,
    "sessenta": 60,
    "setenta": 70,
    "oitenta": 80,
    "noventa": 90,
}
SPOKEN_NUMBER = rf"(?:{'|'.join(TENS)})(?: e (?:{'|'.join(UNITS)}))?|{'|'.join(UNITS)}"
NUMBER = rf"(?:[0-9]{{1,6}}|{SPOKEN_NUMBER})"


def read_number(value: str) -> int:
    if re.fullmatch(r"[0-9]{1,6}", value):
        return int(value)
    if value in UNITS:
        return UNITS[value]
    tens, _, units = value.partition(" e ")
    if tens in TENS and (not units or units in UNITS and 0 < UNITS[units] < 10):
        return TENS[tens] + UNITS.get(units, 0)
    raise ValueError


HUNDREDS = dict(
    zip(
        "cem duzentos trezentos quatrocentos quinhentos seiscentos setecentos oitocentos novecentos".split(),
        range(100, 1000, 100),
        strict=True,
    )
)
HUNDREDS["cento"] = 100
WORD = "(?:" + "|".join([*UNITS, *TENS, *HUNDREDS, "mil"]) + ")"
SPOKEN = rf"{WORD}(?: (?:e )?{WORD}){{0,12}}"


def under_thousand(part: str):
    head, separator, rest = part.partition(" e ")
    if head in HUNDREDS:
        if head == "cem" and separator or head == "cento" and not separator:
            raise ValueError("Número por extenso inválido.")
        value = HUNDREDS[head]
        part = rest
    else:
        value = 0
    try:
        return value + (read_number(part) if part else 0)
    except ValueError as exc:
        raise ValueError("Número por extenso inválido.") from exc


def integer(part: str):
    if part.isdecimal():
        return int(part)
    thousands = re.fullmatch(r"(?:(.+) )?mil(?: (?:e )?(.+))?", part)
    if thousands:
        high = under_thousand(thousands[1]) if thousands[1] else 1
        return high * 1000 + (under_thousand(thousands[2]) if thousands[2] else 0)
    return under_thousand(part)
