"""Representação imutável compartilhada pelo Excel e pelo Google Sheets."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class SheetRow:
    key: str
    day: date
    kind: str
    description: str
    amount_cents: int | None = None
    quantity: str = ""
    deleted: bool = False
