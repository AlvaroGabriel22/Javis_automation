"""Extrai somente campos de um cupom. O valor que entra no caixa é validado em centavos."""

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from jarvis.modules.personal.money import FinanceInput, cents

AmountText = Annotated[str, Field(min_length=1, max_length=25)]


class ReceiptReading(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    currency: Literal["BRL", "other", "unknown"]
    merchant: Annotated[str, Field(min_length=1, max_length=180)] | None
    day: Annotated[str, Field(min_length=1, max_length=10)] | None
    subtotal: AmountText | None
    discount: AmountText | None
    surcharge: AmountText | None
    total_candidates: Annotated[list[AmountText], Field(max_length=5)]
    tendered: AmountText | None
    change: AmountText | None


class PhotoReading(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    description: Annotated[str, Field(min_length=1, max_length=3500)]
    receipt: ReceiptReading | None


def read_date(value: str | None, today: date) -> date | None:
    if value:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                candidate = datetime.strptime(value, fmt).date()
            except ValueError:
                continue
            if 1900 <= candidate.year and candidate <= today:
                return candidate
    return None


def currency_value(value: str) -> int:
    digits = re.sub(r"^R\$\s*", "", value.strip(), flags=re.I)
    if re.fullmatch(r"0(?:[.,]0{1,2})?", digits):
        return 0
    return cents(digits)


def paid_amount(receipt: ReceiptReading) -> int | None:
    """Use um total visível. Cálculos verificam a leitura sem preencher campos ilegíveis."""
    if receipt.currency != "BRL":
        return None
    try:
        values = {currency_value(s) for s in receipt.total_candidates}
        if len(values) != 1 or not (total := values.pop()):
            return None
        subtotal, discount, surcharge, tendered, change = [
            currency_value(s) if s is not None else None
            for s in (
                receipt.subtotal,
                receipt.discount,
                receipt.surcharge,
                receipt.tendered,
                receipt.change,
            )
        ]
        if subtotal is not None and subtotal - (discount or 0) + (surcharge or 0) != total:
            return None
        if tendered is not None and change is not None and tendered - change != total:
            return None
        return total
    except FinanceInput:
        return None
