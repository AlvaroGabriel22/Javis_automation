from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from jarvis.adapters.persistence.models import OwnerTone
from jarvis.modules.identity.preferences import Preferences


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(Input):
    code: str = Field(min_length=1, max_length=128)


class OwnerView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str


class SessionView(BaseModel):
    profile: Preferences
    owner: OwnerView
    csrf_token: str


class PreferencesInput(Input):
    preferred_name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
    ]
    gender: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    address_as: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)
    ]
    tone: OwnerTone = "proximo"

    @field_validator("preferred_name", "gender", "address_as")
    @classmethod
    def single_line(cls, value: str) -> str:
        if not value.isprintable():
            raise ValueError("Use um texto de uma linha.")
        return value


class ContactInput(Input):
    aliases: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    ] = Field(default_factory=list, max_length=16)
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    phone: str = Field(pattern=r"^\+[1-9][0-9]{6,14}$", examples=["+5592999990001"])

    @field_validator("name")
    @classmethod
    def text_field(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Nome inválido.")
        return value


class ContactView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    phone: str


class RescheduleInput(Input):
    scheduled_at: AwareDatetime


class ScheduleInput(Input):
    recurrence: Literal["none", "daily", "weekly"] = "none"
    contact_id: UUID
    scheduled_at: AwareDatetime = Field(examples=["2030-03-05T09:37:00-04:00"])
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Informe o texto da mensagem.")
        return value

    @field_validator("scheduled_at", mode="before")
    @classmethod
    def iso_only(cls, value: object) -> object:
        if not isinstance(value, str) or "T" not in value.upper():
            raise ValueError("Informe data, hora e fuso em ISO 8601.")
        return value


class ScheduleView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    contact_id: UUID
    recipient: ContactView
    scheduled_at: datetime
    text: str
    status: Literal[
        "scheduled", "cancelled", "accepted", "failed", "sending", "uncertain", "needs_review"
    ]
    recurrence: Literal["none", "daily", "weekly"]
    armed: bool
    result_detail: str | None
    created_at: datetime


class Page[T](BaseModel):
    items: list[T]
    next_cursor: UUID | None


class StatusView(BaseModel):
    status: Literal["ok", "unavailable"]
    whatsapp: Literal["connected", "not_connected"] = "not_connected"
    delivery_enabled: bool = False


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorView(BaseModel):
    detail: ErrorDetail
