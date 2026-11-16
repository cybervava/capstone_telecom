"""API-key authentication: key -> client -> scopes (the table is fixed in code)."""

from __future__ import annotations

import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from fastapi import Request

from mock_oss.errors import ApiError

_BASE_SCOPES: dict[str, frozenset[str]] = {
    # Triage finds the customer and checks what they know. Nothing else may read a profile.
    "triage": frozenset({"crm:profile", "crm:verify"}),
    "diagnostics": frozenset({"network:read", "network:test"}),
    "resolution": frozenset({"billing:read"}),
    "dispatch": frozenset({"field:read"}),
    "ingest": frozenset({"network:feed"}),
    "executor": frozenset(
        {
            "billing:read",
            "billing:write",
            "field:read",
            "field:write",
            "incident:read",
            "incident:write",
            "notify:write",
            "network:read",
            # Whether the customer may be sent a notice, asked at the moment of sending.
            # Not the profile: the executor has no use for a name or a number.
            "crm:consent",
        }
    ),
}

CLIENT_SCOPES: Mapping[str, frozenset[str]] = {
    **_BASE_SCOPES,
    "admin": frozenset({"admin"}).union(*_BASE_SCOPES.values()),
}


@dataclass(frozen=True)
class Principal:
    client: str
    scopes: frozenset[str]


class KeyRing:
    """Resolves an API key to a client using constant-time comparison."""

    def __init__(self, api_keys: Mapping[str, str]) -> None:
        unknown = set(api_keys) - set(CLIENT_SCOPES)
        if unknown:
            raise ValueError(f"unknown clients in api_keys: {sorted(unknown)}")
        self._entries = [(key.encode(), client) for client, key in api_keys.items() if key]

    def resolve(self, presented: str | None) -> Principal | None:
        if not presented:
            return None
        candidate = presented.encode()
        match: str | None = None
        for key, client in self._entries:
            if hmac.compare_digest(key, candidate):
                match = client
        return Principal(match, CLIENT_SCOPES[match]) if match is not None else None


def require(scope: str) -> Callable[[Request], Principal]:
    """FastAPI dependency factory: 401 for a missing/unknown key, 403 for a missing scope."""

    def dependency(request: Request) -> Principal:
        keyring: KeyRing = request.app.state.keyring
        principal = keyring.resolve(request.headers.get("X-API-Key"))
        if principal is None:
            raise ApiError(401, "UNAUTHENTICATED", "missing or unknown X-API-Key")
        if scope not in principal.scopes:
            raise ApiError(403, "FORBIDDEN", f"client {principal.client!r} lacks scope {scope}")
        return principal

    return dependency
