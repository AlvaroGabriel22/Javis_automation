"""Resolve o endereço privado de envio na sessão WhatsApp que fará a transmissão."""

import re
from typing import Any

from neonize.client import NewClient
from neonize.proto.Neonize_pb2 import JID
from neonize.utils.jid import build_jid

from jarvis.adapters.whatsapp.profile import PHONE


class UnregisteredNumber(Exception):
    """O serviço respondeu explicitamente que o número consultado está fora do WhatsApp."""


class LookupIncomplete(Exception):
    """Não foi possível relacionar uma única identidade privada ao telefone escolhido."""


def phone_jid(jid: Any) -> str | None:
    digits = getattr(jid, "User", "")
    if getattr(jid, "Server", "") != "s.whatsapp.net" or not isinstance(digits, str):
        return None
    phone = "+" + digits
    return phone if PHONE.fullmatch(phone) and not jid.IsEmpty else None


def _private_lid(jid: JID) -> bool:
    return (
        jid.Server == "lid"
        and not jid.IsEmpty
        and bool(re.fullmatch(r"[1-9][0-9]{0,19}", jid.User))
    )


def _stored_address(client: NewClient, phone: str) -> JID | None:
    try:
        lid = client.get_lid_from_pn(build_jid(phone[1:]))
        if _private_lid(lid) and phone_jid(client.get_pn_from_lid(lid)) == phone:
            return build_jid(lid.User, "lid")
    except Exception:
        # O protocolo ainda pode resolver o número diretamente se a agenda estiver incompleta.
        pass
    return None


def resolve_recipient(client: NewClient, phone: str, *, device_contact: bool) -> JID:
    if device_contact:
        known = _stored_address(client, phone)
        if known is not None:
            return known

    rows = client.is_on_whatsapp(phone)
    if len(rows) != 1:
        raise LookupIncomplete
    row = rows[0]
    query = row.Query.strip().removesuffix("@c.us").removesuffix("@s.whatsapp.net")
    if query and query.removeprefix("+") != phone[1:]:
        raise LookupIncomplete
    jid = row.JID
    canonical = phone_jid(jid)
    if not row.IsIn:
        if query or canonical == phone:
            raise UnregisteredNumber
        raise LookupIncomplete
    if canonical and (query or canonical == phone):
        return build_jid(canonical[1:])
    if _private_lid(jid) and (query or phone_jid(client.get_pn_from_lid(jid)) == phone):
        return build_jid(jid.User, "lid")
    raise LookupIncomplete
