"""API authentication and authorisation.

Every endpoint except ``/health`` needs a bearer token (HS256, signed with ``CARE_JWT_SECRET``;
in the target deployment the secret comes from the managed secret store and tokens are issued by
the channel's identity provider). The token says who is calling and in which role; what a role
may do is enforced here, in code.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

ISSUER = "skylink-care"
AUDIENCE = "care-api"
ALGORITHM = "HS256"
MIN_SECRET_LENGTH = 32


class Role(StrEnum):
    GUEST = "guest"  # a visitor who has not signed in; identified only by a session id
    CUSTOMER = "customer"  # signed in on the customer channel; carries a customer id
    CHANNEL = "channel"  # a trusted channel integration, e.g. the contact-centre platform
    AGENT = "agent"
    SUPERVISOR = "supervisor"
    OPS = "ops"


STAFF = frozenset({Role.AGENT, Role.SUPERVISOR, Role.OPS})


@dataclass(frozen=True)
class Principal:
    subject: str
    role: Role
    customer_id: str | None = None
    session_id: str | None = None

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF


class WeakSecretError(RuntimeError):
    """The signing secret is too short to be safe. Raised at start-up."""


def check_secret(secret: str) -> None:
    if len(secret) < MIN_SECRET_LENGTH:
        raise WeakSecretError(f"CARE_JWT_SECRET must be at least {MIN_SECRET_LENGTH} characters")


def issue_token(
    secret: str,
    *,
    subject: str,
    role: Role,
    customer_id: str | None = None,
    session_id: str | None = None,
    ttl: timedelta = timedelta(hours=1),
    now: datetime | None = None,
) -> str:
    issued = now or datetime.now(UTC)
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": subject,
        "role": role.value,
        "iat": issued,
        "exp": issued + ttl,
    }
    if customer_id:
        claims["customer_id"] = customer_id
    if session_id:
        claims["sid"] = session_id
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def verify_token(secret: str, token: str) -> Principal:
    """Return the caller, or raise ``jwt.InvalidTokenError`` for anything not fully valid."""
    claims = jwt.decode(
        token,
        secret,
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
        options={"require": ["exp", "iat", "sub", "role"]},
    )
    try:
        role = Role(claims["role"])
    except ValueError as exc:
        raise jwt.InvalidTokenError("unknown role") from exc
    customer_id = claims.get("customer_id")
    if role is Role.CUSTOMER and not customer_id:
        raise jwt.InvalidTokenError("a customer token must carry a customer id")
    return Principal(
        subject=str(claims["sub"]),
        role=role,
        customer_id=str(customer_id) if customer_id else None,
        session_id=str(claims["sid"]) if claims.get("sid") else None,
    )


_bearer = HTTPBearer(auto_error=False)


def current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Principal:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    secret: str = request.app.state.jwt_secret
    try:
        return verify_token(secret, credentials.credentials)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token") from exc


def require(*roles: Role) -> Callable[[Principal], Principal]:
    """Dependency factory: the caller must hold one of ``roles``."""
    allowed = frozenset(roles)

    def check(principal: Annotated[Principal, Depends(current_principal)]) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "this role may not do that")
        return principal

    return check
