"""Valores reais por texto e ditado, sem ponto flutuante."""

import re
from decimal import Decimal, InvalidOperation

from jarvis.modules.assistant.numbers_pt import SPOKEN
from jarvis.modules.assistant.numbers_pt import integer as spoken_integer


class FinanceInput(ValueError):
    pass


def money(cents: int):
    prefix = "-" if cents < 0 else ""
    whole, fractional = divmod(abs(cents), 100)
    integer = f"{whole:,}".replace(",", ".")
    return f"{prefix}R$ {integer},{fractional:02d}"


def cents(part: str):
    if re.fullmatch(r"(?:[1-9]\d{0,2}(?:\.\d{3})+|\d+)(?:,\d{1,2})?", part):
        normalized = part.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d+\.\d{1,2}", part):
        normalized = part
    else:
        raise FinanceInput("Confira o valor em reais. Por exemplo, R$ 1.234,56.")
    try:
        result = int(Decimal(normalized) * 100)
    except (InvalidOperation, ValueError, OverflowError) as err:
        raise FinanceInput("Confira o valor em reais.") from err
    if not 0 < result <= 99_999_999_999:
        raise FinanceInput("Informe um valor positivo de até R$ 999.999.999,99.")
    return result


NUMERAL = r"\d+(?:[.,]\d+)*"
VALUE = re.compile(
    rf"(?<![\w.,])(?:r\$\s*)?(?P<major>{SPOKEN}|{NUMERAL})\s+(?P<unit>reais|real|centavos?)\b"
    rf"(?: e (?P<minor>{SPOKEN}|\d+) centavos?\b)?|"
    rf"(?<![\w.,])(?:r\$\s*)?(?P<digits>{NUMERAL})(?![\w]|[.,]\d)"
)
UNSUPPORTED = re.compile(
    r"\b(?:dolares?|euros?|usd|mil|milhao|milhoes|vezes|parcelas?|aproximadamente|talvez|estimativa|desconto|estorno|reembolso|reembolsad[oa])\b|[€£$%]|\d\s*x|\bx\s*\d"
)
ASK = "Preciso de um único valor exato em reais por lançamento ou consulta. Por exemplo, R$ 42,50."


def integer(part: str):
    try:
        return spoken_integer(part)
    except ValueError as exc:
        raise FinanceInput(ASK) from exc


def amount(text: str):
    matches = list(VALUE.finditer(text))
    if len(matches) != 1:
        raise FinanceInput(ASK)
    hit = matches[0]
    before, after = text[: hit.start()], text[hit.end() :]
    if UNSUPPORTED.search(before + " " + after) or before.rstrip().endswith(("-", "menos")):
        raise FinanceInput(ASK)
    if hit["digits"]:
        value = cents(hit["digits"])
    elif hit["unit"].startswith("centavo"):
        if hit["minor"]:
            raise FinanceInput(ASK)
        value = integer(hit["major"])
    else:
        major = hit["major"]
        if re.search(r"[.,]", major) and hit["minor"]:
            raise FinanceInput(ASK)
        value = cents(major) if re.search(r"[.,]", major) else integer(major) * 100
        if hit["minor"]:
            minor = integer(hit["minor"])
            if not 0 <= minor <= 99:
                raise FinanceInput(ASK)
            value += minor
    if not 0 < value <= 99_999_999_999:
        raise FinanceInput(ASK)
    return value, hit
