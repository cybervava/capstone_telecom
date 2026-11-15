"""One HTTP path to the OSS/BSS gateway, shared by every adapter.

Responsibilities: authenticate as one agent identity, propagate correlation ids, apply timeouts,
validate the response against a typed model, and translate every failure into an ``Err``.
It never retries: reads are retried by the caller that knows whether waiting is acceptable, and
writes are replayed only by the executor, under their idempotency key.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from skylink_care.adapters.result import Err, ErrorKind, Ok, Result

log = structlog.get_logger(__name__)

_STATUS_TO_KIND = {
    400: ErrorKind.INVALID,
    401: ErrorKind.UNAUTHORIZED,
    403: ErrorKind.FORBIDDEN,
    404: ErrorKind.NOT_FOUND,
    409: ErrorKind.CONFLICT,
    410: ErrorKind.GONE,
    422: ErrorKind.INVALID,
    429: ErrorKind.RATE_LIMITED,
    504: ErrorKind.TIMEOUT,
}


def segment(value: str) -> str:
    """Encode a value for use as exactly one URL path segment.

    Identifiers can come from customers. Slashes are escaped by ``quote``; dots are escaped as
    well because the HTTP client collapses a literal ``..`` segment, which would let a crafted
    identifier address a different resource.
    """
    return quote(value, safe="").replace(".", "%2E")


class OssHttp:
    """Calls the gateway as a single agent identity."""

    def __init__(self, client: httpx.AsyncClient, *, api_key: str, identity: str) -> None:
        self._client = client
        self._api_key = api_key
        self._identity = identity

    @property
    def identity(self) -> str:
        return self._identity

    async def call[T: BaseModel](
        self,
        method: str,
        path: str,
        model: type[T],
        *,
        correlation_id: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> Result[T]:
        request_id = uuid4().hex
        headers = {
            "X-API-Key": self._api_key,
            "X-Request-ID": request_id,
            "X-Correlation-ID": correlation_id,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        try:
            response = await self._client.request(
                method,
                path,
                params=params,
                json=json,
                headers=headers,
                timeout=(
                    timeout_seconds if timeout_seconds is not None else httpx.USE_CLIENT_DEFAULT
                ),
            )
        except httpx.TimeoutException as exc:
            return self._failed(ErrorKind.TIMEOUT, method, path, request_id, repr(exc))
        except httpx.DecodingError as exc:
            return self._failed(ErrorKind.PROTOCOL, method, path, request_id, repr(exc))
        except httpx.HTTPError as exc:
            # Connection failures, too many redirects, and anything else the client can raise:
            # all mean "no usable answer", never an exception for the caller to forget about.
            return self._failed(ErrorKind.UNAVAILABLE, method, path, request_id, repr(exc))
        return self._interpret(response, model, method, path, request_id)

    def _interpret[T: BaseModel](
        self, response: httpx.Response, model: type[T], method: str, path: str, request_id: str
    ) -> Result[T]:
        status = response.status_code
        if 200 <= status < 300:
            try:
                value = model.model_validate(response.json())
            except (ValueError, ValidationError) as exc:
                return self._failed(
                    ErrorKind.PROTOCOL, method, path, request_id, str(exc)[:500], status
                )
            return Ok(
                value,
                status_code=status,
                request_id=request_id,
                replayed=response.headers.get("Idempotent-Replay", "").lower() == "true",
            )
        kind = _STATUS_TO_KIND.get(status)
        if kind is None:
            kind = ErrorKind.UNAVAILABLE if status >= 500 else ErrorKind.PROTOCOL
        code, message = _error_details(response)
        return self._failed(kind, method, path, request_id, message, status, code)

    def _failed(
        self,
        kind: ErrorKind,
        method: str,
        path: str,
        request_id: str,
        message: str,
        status: int | None = None,
        code: str | None = None,
    ) -> Err:
        log.warning(
            "oss_call_failed",
            identity=self._identity,
            method=method,
            path=path,
            kind=kind.value,
            status=status,
            code=code,
            request_id=request_id,
        )
        return Err(kind=kind, message=message, status_code=status, code=code, request_id=request_id)


def _error_details(response: httpx.Response) -> tuple[str | None, str]:
    """Pull ``{"error": {"code", "message"}}`` out of a failure body, tolerating anything else."""
    try:
        body = response.json()
    except ValueError:
        return None, response.text[:200]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        return (str(code) if code else None), str(message or "")[:500]
    return None, str(body)[:200]
