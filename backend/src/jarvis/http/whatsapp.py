from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from jarvis.adapters.whatsapp import ipc
from jarvis.adapters.whatsapp.profile import BindingStatus
from jarvis.modules.messaging.queue import queue_status


class BindingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str = Field(pattern=r"^\+[1-9][0-9]{6,14}$")


class BindingCode(BaseModel):
    phrase: str
    valid_for_seconds: int


class PairingView(BaseModel):
    state: Literal["offline", "idle", "connecting", "pairing", "connected", "disconnected"]
    account: str | None
    owner_phone: str | None
    pending_phone: str | None
    binding_status: BindingStatus | None = None
    observed_phone: str | None = None
    qr: str | None
    channel: Literal["conversation"]


class ChannelView(PairingView):
    queues: dict[str, dict[str, int]]


def routes(authenticated: Callable, writer: Callable) -> APIRouter:
    router = APIRouter(prefix="/api/v1/whatsapp", tags=["WhatsApp"])
    read = [Depends(authenticated)]
    write = [Depends(writer)]

    @router.get("", dependencies=read, response_model=ChannelView)
    def status(request: Request):
        return {**ipc.call("status"), "queues": queue_status(request.app.state.database())}

    @router.post("/pairing", dependencies=write, response_model=PairingView)
    def pair():
        return ipc.call("pair")

    @router.post("/binding", dependencies=write, response_model=BindingCode)
    def bind(info: BindingInput):
        return ipc.call("challenge", phone=info.phone)

    @router.delete("/binding", dependencies=write, response_model=PairingView)
    def revoke():
        return ipc.call("revoke")

    return router
