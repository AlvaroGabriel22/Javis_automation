"""Contatos informados pelo usuário e catálogo autenticado do aparelho."""

import re

from sqlalchemy import Engine, delete
from sqlalchemy.orm import Session

from jarvis.adapters.persistence.models import DeviceContact
from jarvis.adapters.whatsapp.profile import PHONE


def parse_card(value: str) -> list[str]:
    if len(value) > 16384:
        return []
    numbers = []
    unfolded = re.sub(r"\r?\n[ \t]", "", value)
    for line in unfolded.splitlines():
        key, sep, val = line.partition(":")
        if not sep or key.split(";")[0].upper().split(".")[-1] != "TEL":
            continue
        val = re.sub(r"^tel:", "", val, flags=re.I)
        phone = re.sub(r"[ ().-]", "", val)
        if not PHONE.fullmatch(phone):
            match = re.search(r"(?:^|;)waid=([1-9]\d{6,14})(?:;|$)", key, re.I)
            if not match:
                continue
            phone = "+" + match.group(1)
        if phone not in numbers:
            numbers.append(phone)
    return numbers


def replace_directory(engine: Engine, route: str, entries: dict[str, str]) -> None:
    with Session(engine) as db, db.begin():
        db.execute(delete(DeviceContact).where(DeviceContact.route_key == route))
        db.add_all(
            DeviceContact(route_key=route, phone=phone, name=name)
            for phone, name in entries.items()
        )


def in_directory(engine: Engine, route: str, phone: str) -> bool:
    with Session(engine) as db:
        return db.get(DeviceContact, (route, phone)) is not None
