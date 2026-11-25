"""The MCP server over HTTP: who is let through to it, and under which names.

No tool is called here, so the tools behind the server are a stand-in. What the tools answer
is in ``tests/integration/test_mcp_server.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from skylink_care.config import Settings
from skylink_care.mcp_server.main import allowed_hosts
from skylink_care.mcp_server.server import build_server, http_app

KEY = "a-key-for-the-mcp-endpoint-0123456789"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def guarded(key: str | None, **options: Any) -> Any:
    return http_app(build_server(SimpleNamespace()), key=key, **options)  # type: ignore[arg-type]


async def ask(
    app: Any,
    *,
    bearer: str | None = None,
    host: str = "127.0.0.1:8082",
    authorization: list[str] | None = None,
    path: str = "/mcp",
    root_path: str = "",
) -> Any:
    """One request to the MCP endpoint, with the application started and stopped around it.

    ``authorization`` gives the header's values as they are sent, one header each; ``bearer``
    is the usual one. ``root_path`` is the prefix a server mounted under one would report."""
    values = (
        authorization if authorization is not None else ([f"Bearer {bearer}"] if bearer else [])
    )
    headers = [*MCP_HEADERS.items(), *(("Authorization", value) for value in values)]
    async with (
        app.app.router.lifespan_context(app.app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, root_path=root_path),
            base_url=f"http://{host}",
        ) as client,
    ):
        return await client.post(path, json=INITIALIZE, headers=headers)


async def test_health_is_open_and_says_nothing_else() -> None:
    app = guarded(KEY)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8082"
    ) as client:
        answer = await client.get("/health")

    assert answer.status_code == 200
    assert answer.json() == {"status": "ok"}


@pytest.mark.parametrize("bearer", [None, "not-the-key", KEY[:-1], KEY + "x", ""])
async def test_the_endpoint_answers_nobody_without_the_key(bearer: str | None) -> None:
    answer = await ask(guarded(KEY), bearer=bearer)

    assert answer.status_code == 401
    assert answer.headers["WWW-Authenticate"] == "Bearer"
    assert "closed" in answer.json()["message"]


@pytest.mark.parametrize("bearer", [None, "anything", "Bearer", "None"])
async def test_with_no_key_configured_the_endpoint_is_closed_not_open(bearer: str | None) -> None:
    answer = await ask(guarded(None), bearer=bearer)

    assert answer.status_code == 401
    assert "no key is configured" in answer.json()["message"]


async def test_the_key_lets_a_client_begin() -> None:
    answer = await ask(guarded(KEY), bearer=KEY)

    assert answer.status_code == 200
    assert answer.json()["result"]["serverInfo"]["name"] == "skylink-care"


async def test_the_scheme_of_the_header_may_be_written_in_any_case() -> None:
    answer = await ask(guarded(KEY), authorization=[f"bearer {KEY}"])

    assert answer.status_code == 200


@pytest.mark.parametrize(
    "authorization",
    [
        [f"Bearer {KEY}", "Bearer not-the-key"],
        ["Bearer not-the-key", f"Bearer {KEY}"],
        [KEY],
        [f"Basic {KEY}"],
        [f"Bearer  {KEY}"],
        [f"Bearer {KEY} "],
    ],
)
async def test_a_header_that_is_not_one_bearer_key_is_no_key(authorization: list[str]) -> None:
    """Two headers are refused whichever comes first: the guard does not guess which one a
    proxy added."""
    answer = await ask(guarded(KEY), authorization=authorization)

    assert answer.status_code == 401


@pytest.mark.parametrize("path", ["/", "/other", "/mcp/", "/mcpx", "/health/x", "/health/../mcp"])
async def test_everything_but_the_health_check_asks_for_the_key(path: str) -> None:
    """The guard names what is open. A path it was never told about is closed with the rest."""
    answer = await ask(guarded(KEY), path=path)

    assert answer.status_code == 401


async def test_a_prefix_in_front_of_the_endpoint_does_not_open_it() -> None:
    """Mounted under a prefix, the server routes ``/api/mcp`` to the MCP endpoint. A guard
    that looked for paths beginning with ``/mcp`` would have let it through unasked."""
    without = await ask(guarded(KEY), path="/api/mcp", root_path="/api")
    with_the_key = await ask(guarded(KEY), path="/api/mcp", root_path="/api", bearer=KEY)

    assert without.status_code == 401
    assert with_the_key.status_code == 200


async def test_the_key_is_asked_for_before_the_name_is_looked_at() -> None:
    """Whoever has no key learns nothing else, not even whether the name would have done."""
    answer = await ask(guarded(KEY), host="evil.example")

    assert answer.status_code == 401


async def test_a_request_under_another_host_name_is_turned_away_even_with_the_key() -> None:
    """A page in a browser can point a name of its own at 127.0.0.1. The name it used is in
    the request, and only the loopback names are served."""
    answer = await ask(guarded(KEY), bearer=KEY, host="evil.example")

    assert answer.status_code == 421


async def test_a_host_name_that_was_allowed_is_served() -> None:
    answer = await ask(
        guarded(KEY, allowed_hosts=["mcp.internal:*"]), bearer=KEY, host="mcp.internal:8082"
    )

    assert answer.status_code == 200


def test_a_key_too_short_to_be_one_is_no_key() -> None:
    def settings(**given: Any) -> Settings:
        return Settings(env="test", db_password="unused", **given)

    assert settings().mcp_key_for_http() is None
    assert settings(mcp_key="short").mcp_key_for_http() is None
    assert settings(mcp_key=KEY).mcp_key_for_http() == KEY
    assert KEY not in repr(settings(mcp_key=KEY))


def test_extra_host_names_are_read_from_one_setting() -> None:
    settings = Settings(
        env="test", db_password="unused", mcp_allowed_hosts=" mcp.internal:* , care.example ,"
    )

    assert allowed_hosts(settings) == ["mcp.internal:*", "care.example"]
