"""API local. Não contém cliente de WhatsApp nem mecanismo de envio."""

import ipaddress
import secrets
from collections import deque
from collections.abc import Callable
from datetime import datetime
from functools import lru_cache
from threading import Lock
from time import monotonic
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from jarvis.adapters.persistence.db import new_engine, ready, utcnow
from jarvis.adapters.persistence.models import DeviceContact, Owner
from jarvis.adapters.whatsapp import ipc
from jarvis.bootstrap.settings import ConfigurationError
from jarvis.http.calendar import routes as calendar_routes
from jarvis.http.database import Db
from jarvis.http.finance import routes as finance_routes
from jarvis.http.schemas import (
    ContactInput,
    ContactView,
    ErrorView,
    LoginInput,
    OwnerView,
    Page,
    PreferencesInput,
    RescheduleInput,
    ScheduleInput,
    ScheduleView,
    SessionView,
    StatusView,
)
from jarvis.http.whatsapp import routes as whatsapp_routes
from jarvis.modules.errors import Problem
from jarvis.modules.identity import contacts
from jarvis.modules.identity import service as identity
from jarvis.modules.identity.preferences import Preferences
from jarvis.modules.messaging.models import Inbound
from jarvis.modules.scheduling import service as agenda

PREFIX = "/api/v1"
COOKIE = "jarvis_session"
session_cookie = APIKeyCookie(name=COOKIE, scheme_name="OwnerSession", auto_error=False)
CookieToken = Annotated[str | None, Depends(session_cookie)]
Key = Annotated[
    str,
    Header(alias="Idempotency-Key", min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9._:-]+$"),
]
Limit = Annotated[int, Query(ge=1, le=100)]


def local_peer(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def problem_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": message}}, status_code=status)


def owner_session(request: Request, db: Db, raw: CookieToken = None) -> Owner:
    return identity.session_owner(db, raw or "", request.app.state.clock())


Actor = Annotated[Owner, Depends(owner_session)]


def protected(
    owner: Actor,
    token: Annotated[
        str, Header(alias="X-CSRF-Token", description="Token retornado pela sessão do painel.")
    ],
    raw: CookieToken = None,
) -> Owner:
    if not secrets.compare_digest((token or "").encode(), identity.csrf_token(raw or "").encode()):
        raise Problem(403, "csrf_invalid", "Atualize a sessão antes de tentar novamente.")
    return owner


Writer = Annotated[Owner, Depends(protected)]


def create_app(engine: Engine | None = None, clock: Callable[[], datetime] = utcnow) -> FastAPI:
    app = FastAPI(
        title="Jarvis local",
        version="0.6.3",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
        description="Painel local do proprietário, agendamentos e conversa privada pelo WhatsApp.",
        responses={
            403: {"model": ErrorView, "description": "Origem ou CSRF inválido"},
            422: {"model": ErrorView, "description": "Entrada, cursor ou horário inválido"},
            503: {"model": ErrorView, "description": "Persistência indisponível"},
        },
    )
    attempts: deque[float] = deque()
    lock = Lock()

    @lru_cache(maxsize=1)
    def database() -> Engine:
        return engine if engine is not None else new_engine()

    app.state.database = database
    app.state.clock = clock

    @app.middleware("http")
    async def browser_boundary(request: Request, call_next):
        # Bloqueia DNS rebinding e requisições mutáveis vindas de outra origem.
        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("//" + host).hostname or ""
        except ValueError:
            hostname = ""
        if hostname != "localhost" and not local_peer(hostname):
            response = problem_response(403, "local_only", "Use o endereço local do Jarvis.")
        elif (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and (origin := request.headers.get("origin")) is not None
            and origin != f"{request.url.scheme}://{host}"
        ):
            response = problem_response(403, "foreign_origin", "Origem não autorizada.")
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(Problem)
    async def handle_problem(request: Request, exc: Problem):
        return problem_response(exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError):
        if any(
            tuple(err["loc"]) == ("header", "X-CSRF-Token") and err["type"] == "missing"
            for err in exc.errors()
        ):
            return problem_response(
                403, "csrf_invalid", "Atualize a sessão antes de tentar novamente."
            )
        # Não devolver valores rejeitados: uma entrada pode conter códigos ou conteúdo privado.
        return problem_response(422, "invalid_input", "Confira os dados e cabeçalhos do pedido.")

    @app.exception_handler(ConfigurationError)
    @app.exception_handler(SQLAlchemyError)
    async def handle_storage(request: Request, exc: Exception):
        return problem_response(503, "storage_unavailable", "Banco local indisponível.")

    @app.get(
        PREFIX + "/status",
        response_model=StatusView,
        responses={503: {"model": StatusView}},
        tags=["Saúde"],
    )
    def status(response: Response) -> StatusView:
        try:
            healthy = ready(database())
        except (SQLAlchemyError, ConfigurationError):
            healthy = False
        if not healthy:
            response.status_code = 503
        health = ipc.call("health")
        return StatusView(
            status="ok" if healthy else "unavailable",
            delivery_enabled=healthy and bool(health.get("delivery_enabled")),
            whatsapp="connected" if health.get("connected") else "not_connected",
        )

    @app.post(
        PREFIX + "/auth/login",
        response_model=SessionView,
        responses={401: {"model": ErrorView}, 429: {"model": ErrorView}},
        tags=["Sessão"],
    )
    def login(data: LoginInput, request: Request, response: Response, db: Db) -> SessionView:
        if not request.client or not local_peer(request.client.host):
            raise Problem(403, "local_only", "Ative a sessão neste computador.")
        with lock:
            stamp = monotonic()
            while attempts and attempts[0] <= stamp - 60:
                attempts.popleft()
            if len(attempts) >= 20:
                raise Problem(429, "rate_limited", "Aguarde um minuto antes de tentar novamente.")
            attempts.append(stamp)
        raw, owner = identity.login(db, data.code, clock())
        response.set_cookie(
            COOKIE,
            raw,
            max_age=identity.SESSION_SECONDS,
            path="/api/v1",
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )
        return SessionView(
            profile=Preferences.for_owner(owner),
            owner=OwnerView.model_validate(owner),
            csrf_token=identity.csrf_token(raw),
        )

    @app.get(
        PREFIX + "/auth/session",
        response_model=SessionView,
        responses={401: {"model": ErrorView}},
        tags=["Sessão"],
    )
    def current(owner: Actor, raw: CookieToken = None) -> SessionView:
        return SessionView(
            profile=Preferences.for_owner(owner),
            owner=OwnerView.model_validate(owner),
            csrf_token=identity.csrf_token(raw or ""),
        )

    @app.get(PREFIX + "/profile", response_model=Preferences, tags=["Perfil"])
    def get_profile(owner: Actor):
        return Preferences.for_owner(owner)

    @app.put(PREFIX + "/profile", response_model=Preferences, tags=["Perfil"])
    def update_profile(data: PreferencesInput, owner: Writer, db: Db):
        owner.preferred_name = data.preferred_name
        owner.gender = data.gender
        owner.address_as = data.address_as
        if "tone" in data.model_fields_set:
            owner.tone = data.tone
        db.commit()
        return Preferences.for_owner(owner)

    @app.post(
        PREFIX + "/auth/logout",
        status_code=204,
        responses={401: {"model": ErrorView}},
        tags=["Sessão"],
    )
    def logout(owner: Writer, db: Db, raw: CookieToken = None) -> Response:
        identity.logout(db, raw or "")
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, path="/api/v1", httponly=True, samesite="strict")
        return response

    @app.get(
        PREFIX + "/contacts",
        response_model=Page[ContactView],
        responses={401: {"model": ErrorView}},
        tags=["Contatos"],
    )
    def list_contacts(owner: Actor, db: Db, cursor: UUID | None = None, limit: Limit = 50):
        rows, after = contacts.list_contacts(db, owner.id, cursor, limit)
        return Page[ContactView](
            items=[ContactView.model_validate(r) for r in rows], next_cursor=after
        )

    @app.post(
        PREFIX + "/contacts",
        response_model=ContactView,
        status_code=201,
        responses={
            401: {"model": ErrorView},
            409: {"model": ErrorView},
            200: {"model": ContactView, "description": "Repetição do pedido"},
        },
        tags=["Contatos"],
    )
    def add_contact(data: ContactInput, response: Response, db: Db, owner: Writer, key: Key):
        row, created = contacts.create_contact(
            db, owner.id, key, data.name, data.phone, data.aliases
        )
        response.status_code = 201 if created else 200
        return ContactView.model_validate(row)

    @app.delete(PREFIX + "/contacts/{cid}", status_code=204, tags=["Contatos"])
    def delete_contact(cid: UUID, db: Db, owner: Writer) -> Response:
        contacts.delete_contact(db, owner.id, cid)
        return Response(status_code=204)

    @app.get(
        PREFIX + "/schedules",
        response_model=Page[ScheduleView],
        responses={401: {"model": ErrorView}},
        tags=["Agendamentos"],
    )
    def list_schedules(owner: Actor, db: Db, cursor: UUID | None = None, limit: Limit = 50):
        rows, after = agenda.list_schedules(db, owner.id, cursor, limit)
        return Page[ScheduleView](
            items=[ScheduleView.model_validate(r) for r in rows], next_cursor=after
        )

    @app.post(
        PREFIX + "/schedules",
        response_model=ScheduleView,
        status_code=201,
        responses={
            401: {"model": ErrorView},
            404: {"model": ErrorView},
            409: {"model": ErrorView},
            200: {"model": ScheduleView, "description": "Repetição do pedido"},
        },
        tags=["Agendamentos"],
    )
    def schedule(data: ScheduleInput, response: Response, db: Db, owner: Writer, key: Key):
        row, created = agenda.create_schedule(
            db,
            owner.id,
            key,
            data.contact_id,
            data.scheduled_at,
            data.text,
            clock(),
            ipc.call("route").get("route_key"),
            data.recurrence,
        )
        response.status_code = 201 if created else 200
        return ScheduleView.model_validate(row)

    @app.delete(
        PREFIX + "/schedules/{sid}",
        response_model=ScheduleView,
        responses={401: {"model": ErrorView}, 404: {"model": ErrorView}},
        tags=["Agendamentos"],
    )
    def cancel(sid: UUID, db: Db, owner: Writer):
        return ScheduleView.model_validate(agenda.cancel_schedule(db, owner.id, sid))

    @app.patch(PREFIX + "/schedules/{sid}", response_model=ScheduleView, tags=["Agendamentos"])
    def reschedule(sid: UUID, data: RescheduleInput, db: Db, owner: Writer):
        route = ipc.call("route").get("route_key")
        if not route:
            raise Problem(
                409, "no_channel", "Conecte o aparelho e vincule seu número para ativar o envio."
            )
        row = agenda.stage_reschedule(db, owner.id, sid, data.scheduled_at, clock(), route)
        db.commit()
        return ScheduleView.model_validate(row)

    @app.get(PREFIX + "/whatsapp/directory", tags=["Contatos"])
    def directory(db: Db, owner: Actor, q: Annotated[str, Query(max_length=120)] = ""):
        route = ipc.call("route").get("route_key")
        if not route:
            return []
        stmt = select(DeviceContact).where(DeviceContact.route_key == route)
        if q:
            stmt = stmt.where(
                DeviceContact.name.icontains(q, autoescape=True)
                | DeviceContact.phone.contains(q, autoescape=True)
            )
        return [
            {"name": c.name, "phone": c.phone}
            for c in db.scalars(stmt.order_by(DeviceContact.name, DeviceContact.phone).limit(50))
        ]

    @app.get(PREFIX + "/whatsapp/latest-transcript", tags=["WhatsApp"])
    def latest_transcript(db: Db, owner: Actor):
        route = ipc.call("route").get("route_key")
        if route:
            row = db.scalar(
                select(Inbound)
                .where(
                    Inbound.chat_id == route,
                    Inbound.kind == "audio",
                    Inbound.media.is_(None),
                    Inbound.text != "[Áudio]",
                )
                .order_by(Inbound.id.desc())
                .limit(1)
            )
            if row:
                return {"text": row.text, "received_at": row.received_at}
        raise Problem(404, "transcript_absent", "Nenhum áudio transcrito neste vínculo.")

    app.include_router(calendar_routes(owner_session, protected))
    app.include_router(finance_routes(owner_session, protected))
    app.include_router(whatsapp_routes(owner_session, protected))
    return app


app = create_app()
