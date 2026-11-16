"""The care API.

``POST /v1/contacts`` is the reference-first intake: it validates the request, commits the
contact with its complaint reference, and answers. It calls no model and no enterprise system,
so it keeps answering when all of them are down. This process holds no OSS/BSS key at all.
"""

from __future__ import annotations

import hmac
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Lifespan

from skylink_care.api.approvals import register_approval_routes
from skylink_care.api.auth import Principal, Role, check_secret, issue_token, require
from skylink_care.api.handoffs import register_handoff_routes
from skylink_care.api.ops import register_ops_routes
from skylink_care.api.ratelimit import RateLimiter
from skylink_care.api.schemas import (
    ContactAccepted,
    ContactIn,
    ContactView,
    MessageIn,
    MessageView,
    TokenIn,
    failed,
    ok,
)
from skylink_care.clock import Clock, utc_now
from skylink_care.db.audit import AuditRepository
from skylink_care.db.contacts import Contact, ContactRepository, NewContact, RequestIdReusedError
from skylink_care.db.database import Database
from skylink_care.db.inbox import InboxRepository
from skylink_care.db.messages import MessageRepository
from skylink_care.domain.models import Channel

log = structlog.get_logger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
CLOSED_STATUSES = frozenset({"CLOSED", "RESOLVED"})
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; object-src 'none'; frame-ancestors 'none';"
        " base-uri 'self'; form-action 'self'"
    ),
}
# Responses under /v1 carry customer data and must not be stored by browsers or proxies.
API_PREFIX = "/v1/"
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

CUSTOMER_SIDE = (Role.GUEST, Role.CUSTOMER, Role.CHANNEL)
STAFF_SIDE = (Role.AGENT, Role.SUPERVISOR, Role.OPS)
# The personas the dev token endpoint hands to anyone: the two a customer can be.
KEYLESS_IN_DEV = (Role.GUEST, Role.CUSTOMER)


@dataclass(frozen=True)
class ApiDeps:
    database: Database
    jwt_secret: str
    env: str = "dev"
    # Follow-up messages per contact, and further contacts per caller after the first.
    message_limit: RateLimiter | None = None
    contact_limit: RateLimiter | None = None
    # The clock approvals are offered and decided by.
    now: Clock = utc_now
    # What a person must know to get a staff token from the dev token endpoint. None: nobody.
    dev_staff_key: str | None = None


def create_app(deps: ApiDeps, *, lifespan: Lifespan[FastAPI] | None = None) -> FastAPI:
    check_secret(deps.jwt_secret)
    app = FastAPI(
        title="SkyLink care API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.jwt_secret = deps.jwt_secret
    contacts = ContactRepository(deps.database)
    messages = MessageRepository(deps.database)
    inbox = InboxRepository(deps.database)
    audit = AuditRepository(deps.database)
    message_limit = deps.message_limit or RateLimiter(capacity=20, refill_per_second=20 / 60)
    contact_limit = deps.contact_limit or RateLimiter(capacity=5, refill_per_second=5 / 60)

    _install_error_handling(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Response:
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if SAFE_REQUEST_ID.fullmatch(supplied) else uuid4().hex
        request.state.request_id = request_id
        request.state.started = time.monotonic()
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if request.url.path.startswith(API_PREFIX):
            response.headers["Cache-Control"] = "no-store"
        else:
            # The console's pages and scripts: kept by the browser, checked before every use.
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.get("/health")
    async def health() -> dict[str, Any]:
        async with deps.database.connection() as conn:
            await conn.execute("SELECT 1")
        return ok({"status": "ok"})

    @app.post("/v1/contacts", status_code=status.HTTP_201_CREATED)
    async def open_contact(
        body: ContactIn,
        request: Request,
        response: Response,
        principal: Annotated[Principal, Depends(require(*CUSTOMER_SIDE))],
    ) -> dict[str, Any]:
        if body.caller_phone and principal.role is not Role.CHANNEL:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "only a channel may state the caller line"
            )
        caller = _caller_key(principal)
        # The first contact of a caller always gets a reference. Only repeats are limited.
        if contact_limit.has_history(caller) and not contact_limit.allow(caller):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many new contacts")
        if not contact_limit.has_history(caller):
            contact_limit.allow(caller)
        try:
            result = await contacts.accept(
                NewContact(
                    channel=Channel(body.channel),
                    client_request_id=f"{caller}:{body.client_request_id}",
                    text=body.text,
                    correlation_id=uuid4().hex,
                    received_at=datetime.now(UTC),
                    # A guest's session comes from the token. Only a channel integration may
                    # label a contact with a session of its own.
                    session_id=(
                        body.session_id if principal.role is Role.CHANNEL else principal.session_id
                    ),
                    authenticated_customer_id=principal.customer_id,
                    caller_phone=body.caller_phone,
                    created_by=principal.subject,
                ),
                started=request.state.started,
            )
        except RequestIdReusedError as exc:
            return _conflict(response, exc)
        if result.replayed:
            response.status_code = status.HTTP_200_OK
        return ok(ContactAccepted(**result.__dict__).model_dump(mode="json"))

    @app.get("/v1/contacts/{contact_id}")
    async def read_contact(
        contact_id: UUID,
        principal: Annotated[Principal, Depends(require(*CUSTOMER_SIDE, *STAFF_SIDE))],
    ) -> dict[str, Any]:
        contact = await _owned_contact(contacts, contact_id, principal)
        stored = await messages.list_for_contact(contact_id)
        view = ContactView(
            contact_id=contact.contact_id,
            complaint_ref=contact.complaint_ref,
            status=contact.status,
            channel=contact.channel.value,
            received_at=contact.received_at,
            reference_issued_at=contact.reference_issued_at,
            network_state=contact.network_state,
            outage_id=contact.outage_id,
            messages=[
                MessageView(
                    message_id=m.message_id,
                    direction=m.direction,
                    author=m.author,
                    body=m.body,
                    created_at=m.created_at,
                    template_id=m.meta.get("template_id"),
                )
                for m in stored
            ],
        )
        return ok(view.model_dump(mode="json"))

    @app.post("/v1/contacts/{contact_id}/messages", status_code=status.HTTP_202_ACCEPTED)
    async def add_message(
        contact_id: UUID,
        body: MessageIn,
        principal: Annotated[Principal, Depends(require(*CUSTOMER_SIDE))],
    ) -> dict[str, Any]:
        contact = await _owned_contact(contacts, contact_id, principal)
        if contact.status in CLOSED_STATUSES:
            raise HTTPException(status.HTTP_409_CONFLICT, "this contact is closed; open a new one")
        if not message_limit.allow(str(contact_id)):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many messages")
        async with deps.database.transaction() as conn:
            stored = await messages.add(
                contact_id,
                direction="inbound",
                author="customer",
                body=body.text,
                dedupe_key=f"inbound:{body.client_message_id}",
                conn=conn,
            )
            if stored:
                await inbox.add(
                    contact_id,
                    "customer.message",
                    dedupe_key=f"message:{body.client_message_id}",
                    conn=conn,
                )
        return ok({"accepted": True, "replayed": not stored})

    @app.get("/v1/contacts/{contact_id}/audit")
    async def read_audit(
        contact_id: UUID, _: Annotated[Principal, Depends(require(*STAFF_SIDE))]
    ) -> dict[str, Any]:
        if await contacts.get(contact_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
        trail = await audit.for_contact(contact_id)
        return ok(jsonable_encoder([record.__dict__ for record in trail]))

    register_handoff_routes(app, deps.database, deps.now)
    register_ops_routes(app, deps.database, deps.now)
    register_approval_routes(app, deps.database, deps.now)

    if deps.env == "dev":

        @app.post("/v1/dev/token")
        async def dev_token(body: TokenIn) -> dict[str, Any]:
            """Local development only: mint a token for a demo persona. Absent outside dev."""
            role = Role(body.role)
            if role is Role.CUSTOMER and not body.customer_id:
                raise HTTPException(422, "customer_id is required")
            subject = body.customer_id or f"demo-{role.value}"
            if role not in KEYLESS_IN_DEV:
                # Not for whoever can reach the port: a staff token can approve a credit, and a
                # channel token is believed about who is calling.
                if not _knows_the_staff_key(body.staff_key, deps.dev_staff_key):
                    raise HTTPException(status.HTTP_403_FORBIDDEN, "staff sign-in was refused")
                subject = f"{role.value}:{body.name}" if body.name else subject
            token = issue_token(
                deps.jwt_secret,
                subject=subject,
                role=role,
                customer_id=body.customer_id if role is Role.CUSTOMER else None,
                session_id=uuid4().hex if role is Role.GUEST else None,
            )
            return ok({"token": token, "role": role.value})

    if WEB_DIR.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def _knows_the_staff_key(offered: str | None, expected: str | None) -> bool:
    """Compared in constant time. With no key configured nobody knows it."""
    if not expected or not offered:
        return False
    return hmac.compare_digest(offered.encode(), expected.encode())


def _caller_key(principal: Principal) -> str:
    """Who is asking, for idempotency and rate limiting.

    Built only from signed token claims, so a caller cannot choose a key that collides with
    someone else's or that resets its own limit.
    """
    if principal.role is Role.CUSTOMER:
        return f"customer:{principal.customer_id}"
    if principal.role is Role.GUEST:
        return f"guest:{principal.session_id or principal.subject}"
    return f"channel:{principal.subject}"


async def _owned_contact(
    contacts: ContactRepository, contact_id: UUID, principal: Principal
) -> Contact:
    """Load a contact the caller may see. Someone else's contact looks the same as none."""
    contact = await contacts.get(contact_id)
    allowed = contact is not None and (
        principal.is_staff
        # A channel integration sees the contacts it opened, not everyone's.
        or (principal.role is Role.CHANNEL and contact.created_by == principal.subject)
        or (
            principal.role is Role.CUSTOMER
            and contact.authenticated_customer_id == principal.customer_id
        )
        or (
            principal.role is Role.GUEST
            and principal.session_id is not None
            and contact.session_id == principal.session_id
        )
    )
    if contact is None or not allowed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "contact not found")
    return contact


def _conflict(response: Response, exc: RequestIdReusedError) -> dict[str, Any]:
    response.status_code = status.HTTP_409_CONFLICT
    return failed(
        "REQUEST_ID_REUSED",
        "this client_request_id was already used for a different request",
        complaint_ref=exc.complaint_ref,
    )


def _install_error_handling(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            failed(f"HTTP_{exc.status_code}", str(exc.detail)), status_code=exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {"field": ".".join(str(part) for part in error["loc"][1:]), "problem": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            failed("INVALID_REQUEST", "the request is not valid", fields=problems),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=request.url.path)
        return JSONResponse(
            failed("INTERNAL_ERROR", "something went wrong on our side"),
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
