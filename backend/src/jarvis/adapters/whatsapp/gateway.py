"""Fronteira Neonize. Somente mensagens privadas do dono entram na fila."""

import json
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from neonize.client import NewClient
from neonize.events import ConnectedEv, DisconnectedEv, LoggedOutEv, MessageEv
from neonize.exc import SendMessageError
from neonize.utils.jid import build_jid
from sqlalchemy import Engine

from jarvis.adapters.ai.speech import MAX_BYTES, MAX_SECONDS
from jarvis.adapters.persistence.db import utcnow
from jarvis.adapters.whatsapp.contacts import in_directory, parse_card, replace_directory
from jarvis.adapters.whatsapp.profile import Profile, binding_code
from jarvis.adapters.whatsapp.recipient import UnregisteredNumber, phone_jid, resolve_recipient
from jarvis.modules.messaging import queue
from jarvis.modules.scheduling import dispatch
from jarvis.modules.stickers.catalog import sticker_bytes

log = logging.getLogger(__name__)


def private_sender(event: Any, resolve_lid: Callable) -> str | None:
    src = event.Info.MessageSource
    if src.IsFromMe or src.IsGroup or event.IsEdit or event.Info.Multicast:
        return None
    if src.BroadcastListOwner.User or src.Chat.Server not in {"s.whatsapp.net", "lid"}:
        return None
    if src.Sender.Server not in {"s.whatsapp.net", "lid"}:
        return None

    # SenderAlt é fornecido pelo protocolo. A prova só vale se o chat e o autor apontarem
    # para a mesma pessoa. Nome de exibição, texto citado e o número digitado não identificam o autor.
    def number(jid):
        if jid.Server == "s.whatsapp.net":
            return phone_jid(jid)
        if (
            src.Sender.Server == "s.whatsapp.net"
            and src.SenderAlt.Server == "lid"
            and src.SenderAlt.User
            and (jid.User, jid.Server) == (src.SenderAlt.User, src.SenderAlt.Server)
        ):
            return phone_jid(src.Sender)
        if jid.User == src.Sender.User and jid.Server == src.Sender.Server:
            alt = phone_jid(src.SenderAlt)
            if alt:
                return alt
        try:
            return phone_jid(resolve_lid(jid))
        except Exception:
            return None

    sender = number(src.Sender)
    return sender if sender and number(src.Chat) == sender else None


class Gateway:
    def __init__(self, engine: Engine, profile: Profile, client: Any):
        self.engine, self.profile, self.client = engine, profile, client

    def register(self) -> None:
        self.client.event.qr(lambda _, raw: self.profile.on_qr(raw))
        self.client.event(ConnectedEv)(self.connected)
        self.client.event(DisconnectedEv)(lambda *_: self.profile.disconnected())
        self.client.event(LoggedOutEv)(lambda *_: self.profile.disconnected(logout=True))
        self.client.event(MessageEv)(self.receive)

    def connected(self, client: NewClient, _event: Any) -> None:
        try:
            number = phone_jid(getattr(client.me, "JID", None)) or phone_jid(client.get_me().JID)
            if number:
                self.profile.connected(number)
                return
        except Exception:
            pass
        self.profile.disconnected()
        log.warning("Conta WhatsApp sem identidade verificável; conversa suspensa.")

    def receive(self, _client: Any, event: Any) -> None:
        try:
            if not event.Info.ID:
                return
            message = event.Message
            text = message.conversation or message.extendedTextMessage.text
            sender = private_sender(event, self.client.get_pn_from_lid)
            if text and binding_code(text) is not None:
                self.profile.verify(sender, text)
                return
            route = self.profile.route()
            if not route or sender != route[1]:
                return
            received_at = utcnow()
            kind: queue.InboundKind = "text"
            media = None
            if message.HasField("audioMessage"):
                audio = message.audioMessage
                if audio.contextInfo.isForwarded:
                    kind, text = "notice", "Envie o comando em sua própria voz ou por texto."
                elif not 0 < audio.fileLength <= MAX_BYTES or audio.seconds > MAX_SECONDS:
                    kind, text = "notice", "Envie um áudio de até dois minutos e 8 MB."
                else:
                    try:
                        media = self.client.download_any(message)
                    except Exception:
                        media = None
                    if isinstance(media, bytes) and 0 < len(media) <= MAX_BYTES:
                        kind, text = "audio", "[Áudio]"
                    else:
                        kind, text, media = (
                            "notice",
                            "Não consegui baixar o áudio. Reenvie ou escreva.",
                            None,
                        )
            elif message.HasField("imageMessage") or message.HasField("stickerMessage"):
                if queue.already_received(self.engine, route[0], event.Info.ID):
                    return
                kind, text, media = self._read_visual(event)
            elif message.HasField("contactMessage"):
                card = message.contactMessage
                kind = "card"
                text = json.dumps(
                    {"name": card.displayName[:120], "phones": parse_card(card.vcard)}
                )
            elif message.extendedTextMessage.contextInfo.isForwarded:
                kind, text = "notice", "Escreva o que deseja fazer com a mensagem encaminhada."
            if text.strip() and self.profile.route() == route:
                queue.accept_inbound(
                    self.engine,
                    chat_id=route[0],
                    message_id=event.Info.ID,
                    text=text,
                    kind=kind,
                    media=media,
                    now=received_at,
                )
        except Exception as e:
            log.error("Entrada WhatsApp não registrada (%s).", type(e).__name__)

    def _read_visual(self, event: Any) -> tuple[queue.InboundKind, str, bytes | None]:
        message = event.Message
        kind: Literal["image", "sticker"] = (
            "image" if message.HasField("imageMessage") else "sticker"
        )
        part = message.imageMessage if kind == "image" else message.stickerMessage
        if (
            event.IsViewOnce
            or event.IsViewOnceV2
            or event.IsViewOnceV2Extension
            or (kind == "image" and part.viewOnce)
        ):
            return "notice", "Para eu ler a imagem, envie uma cópia comum pelo WhatsApp.", None
        if kind == "sticker" and (event.IsLottieSticker or part.isLottie):
            if 0 < len(part.pngThumbnail) <= queue.MAX_MEDIA_BYTES:
                return "sticker", queue.LOTTIE_PREVIEW, part.pngThumbnail
            return (
                "notice",
                "Essa figurinha usa Lottie. Envie uma captura ou uma figurinha WebP.",
                None,
            )
        if not 0 < part.fileLength <= queue.MAX_MEDIA_BYTES:
            return "notice", "Envie uma imagem ou figurinha de até 8 MiB.", None
        try:
            binary = self.client.download_any(message)
        except Exception:
            binary = None
        if not isinstance(binary, bytes) or not 0 < len(binary) <= queue.MAX_MEDIA_BYTES:
            return "notice", "Não consegui baixar a imagem ou figurinha. Pode reenviar?", None
        # A legenda encaminhada veio de outra pessoa. O material visual continua disponível para leitura.
        caption = (
            part.caption.strip() if kind == "image" and not part.contextInfo.isForwarded else ""
        )
        return kind, caption or ("[Imagem]" if kind == "image" else "[Figurinha]"), binary

    def sync_contacts(self, route: tuple[str, str]) -> None:
        entries = {}
        for contact in self.client.contact.get_all_contacts():
            jid = contact.JID
            if jid.Server == "lid":
                try:
                    jid = self.client.get_pn_from_lid(jid)
                except Exception:
                    continue
            phone = phone_jid(jid)
            name = (contact.Info.FullName or contact.Info.FirstName).strip()
            if phone and name and phone != self.profile.account and "\x00" not in name:
                entries[phone] = name[:120]
        if self.profile.route() == route:
            replace_directory(self.engine, route[0], entries)

    def send_scheduled(self, clock: Callable[[], datetime] = utcnow) -> bool:
        route = self.profile.route()
        if not route:
            return False
        job = dispatch.claim(self.engine, route[0], clock())
        if not job:
            return False
        try:
            if job.phone == route[1]:
                # A prova de vínculo já verificou este chat, como nas respostas do Jarvis.
                destination = build_jid(route[1][1:])
            else:
                destination = resolve_recipient(
                    self.client,
                    job.phone,
                    device_contact=in_directory(self.engine, route[0], job.phone),
                )
        except UnregisteredNumber:
            dispatch.finish(self.engine, job, "invalid_number", clock())
            return True
        except Exception:
            # A transmissão ainda não foi iniciada. Uma consulta incompleta é repetível.
            dispatch.finish(self.engine, job, "lookup_incomplete", clock())
            return True
        if self.profile.route() != route:
            dispatch.finish(self.engine, job, "not_sent", clock())
            return True
        send_at = clock()
        if send_at > job.due + dispatch.GRACE:
            dispatch.finish(self.engine, job, "late", clock())
            return True
        try:
            response = self.client.send_message(destination, job.text_at(send_at))
            receipt = getattr(response, "ID", "")
            outcome = "accepted" if receipt else "uncertain"
        except SendMessageError as exc:
            receipt = None
            outcome = (
                "not_sent"
                if str(exc).strip().lower() in {"client is nil", "not connected"}
                else "uncertain"
            )
        except Exception:
            receipt, outcome = None, "uncertain"
        dispatch.finish(self.engine, job, outcome, clock(), receipt)
        return True

    def send_next(self) -> bool:
        route = self.profile.route()
        if not route:
            return False
        delivery = queue.claim_delivery(self.engine, chat_id=route[0])
        if delivery is None:
            return False
        if self.profile.route() != route:
            queue.finish_delivery(self.engine, delivery, outcome="not_sent")
            return True
        if delivery.reaction_to is not None and not queue.renew_delivery(self.engine, delivery):
            queue.finish_delivery(self.engine, delivery, outcome="not_sent", retry=False)
            return True
        payload = delivery.text
        if delivery.sticker_key is not None:
            try:
                data = sticker_bytes(delivery.sticker_key)
            except (OSError, ValueError):
                if delivery.reaction_to is not None:
                    queue.finish_delivery(self.engine, delivery, outcome="not_sent", retry=False)
                    return True
                payload = "O arquivo dessa figurinha está indisponível no catálogo local."
                if not queue.replace_missing_sticker(self.engine, delivery, payload):
                    return True
            else:
                try:
                    # O SDK só faz upload aqui. O envio para o chat ocorre abaixo.
                    payload = self.client.build_sticker_message(data, passthrough=True)
                except Exception as exc:
                    log.warning("Preparo de figurinha indisponível (%s).", type(exc).__name__)
                    queue.finish_delivery(self.engine, delivery, outcome="not_sent")
                    return True
                if self.profile.route() != route or not queue.renew_delivery(self.engine, delivery):
                    queue.finish_delivery(
                        self.engine,
                        delivery,
                        outcome="not_sent",
                        retry=delivery.reaction_to is None,
                    )
                    return True
        try:
            result = self.client.send_message(build_jid(route[1][1:]), payload)
            provider_id = getattr(result, "ID", "")
        except SendMessageError as e:
            if str(e).strip().lower() in {"client is nil", "not connected"}:
                queue.finish_delivery(self.engine, delivery, outcome="not_sent")
                return True
            provider_id = ""
        except Exception as e:
            # A chamada pode ter produzido efeito mesmo sem resposta.
            log.warning("Transmissão sem confirmação (%s).", type(e).__name__)
            provider_id = ""
        if queue.valid_text(provider_id, 192):
            queue.finish_delivery(
                self.engine, delivery, outcome="accepted", external_id=provider_id
            )
        else:
            queue.finish_delivery(self.engine, delivery, outcome="uncertain")
        return True
