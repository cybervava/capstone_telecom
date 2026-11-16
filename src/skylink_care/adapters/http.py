"""One HTTP path to the OSS/BSS gateway, shared by every adapter.

Responsibilities: authenticate as one agent identity, propagate correlation ids, apply timeouts,
validate the response against a typed model, and translate every failure into an ``Err``.

A dependency that keeps failing is rested by its circuit (``adapters.breaker``): the call is then
not made and the caller gets ``CIRCUIT_OPEN``. A read that fails fast (a refused connection, a
server error) is tried again, as often as this client was told to and no more. A timeout is not:
the caller has waited long enough. A write is never repeated here: only the executor replays
one, under its idempotency key.

Every call also has a limit on its whole duration. The client's own timeouts apply to each
step (connecting, each read), so an answer that arrives a byte at a time would never end.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from skylink_care.adapters.breaker import Circuit, Circuits, Ticket
from skylink_care.adapters.result import Err, ErrorKind, Ok, Result

log = structlog.get_logger(__name__)

# The dependency gave no usable answer. Anything else, a refusal included, is an answer.
_NO_ANSWER = frozenset({ErrorKind.TIMEOUT, ErrorKind.UNAVAILABLE})
# On top of a call's own timeout, for connecting and for sending the request.
WHOLE_CALL_MARGIN_SECONDS = 4.0

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

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        identity: str,
        circuits: Circuits | None = None,
        read_retries: int = 0,
        retry_pause_seconds: float = 0.1,
        whole_call_seconds: float | None = None,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._identity = identity
        self._circuits = circuits
        self._read_retries = read_retries
        self._retry_pause_seconds = retry_pause_seconds
        # The longest a call may take from start to finish when it names no timeout of its
        # own: by default, each of the client's step timeouts once.
        self._whole_call_seconds = whole_call_seconds or _steps_together(client.timeout)

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
        retry: bool = True,
    ) -> Result[T]:
        """One call to the gateway. ``retry=False`` is for a read that has a loop of its own."""
        circuit = self._circuits.for_path(path) if self._circuits is not None else None
        ticket = circuit.admit() if circuit is not None else None
        if circuit is not None and ticket is None:
            return _not_attempted(f"{circuit.name} is being rested")

        async def send() -> Result[T]:
            return await self._send(
                method,
                path,
                model,
                correlation_id=correlation_id,
                params=params,
                json=json,
                idempotency_key=idempotency_key,
                timeout_seconds=timeout_seconds,
            )

        result = await _attempt(circuit, ticket, send)
        tries_left = self._read_retries if method == "GET" and retry else 0
        while tries_left and isinstance(result, Err) and result.kind is ErrorKind.UNAVAILABLE:
            tries_left -= 1
            await asyncio.sleep(self._retry_pause_seconds * (0.5 + random.random()))  # noqa: S311
            ticket = circuit.admit() if circuit is not None else None
            if circuit is not None and ticket is None:
                break  # rested in the meantime: the caller keeps what the attempt found
            result = await _attempt(circuit, ticket, send)
        return result

    async def _send[T: BaseModel](
        self,
        method: str,
        path: str,
        model: type[T],
        *,
        correlation_id: str,
        params: dict[str, Any] | None,
        json: dict[str, Any] | None,
        idempotency_key: str | None,
        timeout_seconds: float | None,
    ) -> Result[T]:
        request_id = uuid4().hex
        headers = self._headers(request_id, correlation_id, idempotency_key)
        whole = (
            timeout_seconds + WHOLE_CALL_MARGIN_SECONDS
            if timeout_seconds is not None
            else self._whole_call_seconds
        )
        try:
            async with asyncio.timeout(whole):
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
        except TimeoutError:
            # Sent, perhaps answered in part, and never finished within the call's whole time.
            return self._failed(
                ErrorKind.TIMEOUT, method, path, request_id, f"no full answer in {whole:g} s"
            )
        except httpx.PoolTimeout:
            # Every connection of this process was busy. Nothing was sent, and nothing was
            # learnt about the dependency this call was meant for.
            return _not_attempted("no connection to the gateway was free")
        except httpx.TimeoutException as exc:
            return self._failed(ErrorKind.TIMEOUT, method, path, request_id, repr(exc))
        except httpx.DecodingError as exc:
            return self._failed(ErrorKind.PROTOCOL, method, path, request_id, repr(exc))
        except httpx.HTTPError as exc:
            # Connection failures, too many redirects, and anything else the client can raise:
            # all mean "no usable answer", never an exception for the caller to forget about.
            return self._failed(ErrorKind.UNAVAILABLE, method, path, request_id, repr(exc))
        return self._interpret(response, model, method, path, request_id)

    def _headers(
        self, request_id: str, correlation_id: str, idempotency_key: str | None
    ) -> dict[str, str]:
        headers = {
            "X-API-Key": self._api_key,
            "X-Request-ID": request_id,
            "X-Correlation-ID": correlation_id,
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return headers

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


async def _attempt[T: BaseModel](
    circuit: Circuit | None, ticket: Ticket | None, send: Callable[[], Awaitable[Result[T]]]
) -> Result[T]:
    """Make one request and tell the dependency's circuit how it went."""
    try:
        result = await send()
    except BaseException:
        # Cancelled, or an error no adapter expects. Either way the circuit must not go on
        # waiting for this call.
        if circuit is not None and ticket is not None:
            circuit.abandoned(ticket)
        raise
    if circuit is not None and ticket is not None:
        if not isinstance(result, Err):
            circuit.answered(ticket)
        elif result.kind in _NO_ANSWER:
            circuit.failed(ticket)
        elif result.kind is ErrorKind.CIRCUIT_OPEN:
            circuit.abandoned(ticket)  # never sent: no word about the dependency
        else:
            circuit.answered(ticket)  # a refusal is an answer
    return result


def _not_attempted(why: str) -> Err:
    """The call was not made. Not logged for every call: a circuit logs when it opens and
    closes, and a full connection pool shows in the calls that hold it."""
    return Err(kind=ErrorKind.CIRCUIT_OPEN, message=why)


def _steps_together(timeout: httpx.Timeout) -> float:
    """Each step of a call at its own limit, once: the longest an honest call can take."""
    steps = (timeout.connect, timeout.read, timeout.write, timeout.pool)
    return sum(step for step in steps if step is not None) or 10.0


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
