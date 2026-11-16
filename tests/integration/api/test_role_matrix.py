"""Who may call what: every route of the API against every role, as one table.

The tests beside this one check particular refusals where they matter. This one is the whole
grid, so that a pair nobody thought of is still tried, and a route added without a row here
fails a test before it reaches a reviewer.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from fastapi.routing import APIRoute

from skylink_care.api.app import ApiDeps, create_app
from skylink_care.api.auth import Role
from skylink_care.db.database import Database
from tests.integration.api.test_api import SECRET, token

CUSTOMER_SIDE = frozenset({Role.GUEST, Role.CUSTOMER, Role.CHANNEL})
STAFF = frozenset({Role.AGENT, Role.SUPERVISOR, Role.OPS})
EVERYONE = CUSTOMER_SIDE | STAFF
# Method, path as the application declares it, and who may call it.
ROUTES: tuple[tuple[str, str, frozenset[Role]], ...] = (
    ("POST", "/v1/contacts", CUSTOMER_SIDE),
    ("GET", "/v1/contacts/{contact_id}", EVERYONE),
    ("POST", "/v1/contacts/{contact_id}/messages", CUSTOMER_SIDE),
    ("GET", "/v1/contacts/{contact_id}/audit", STAFF),
    ("POST", "/v1/contacts/{contact_id}/takeover", frozenset({Role.AGENT, Role.SUPERVISOR})),
    ("GET", "/v1/handoffs", STAFF),
    ("POST", "/v1/handoffs/{handoff_id}/done", frozenset({Role.AGENT, Role.SUPERVISOR})),
    ("GET", "/v1/ops/load", STAFF),
    ("GET", "/v1/ops", STAFF),
    ("POST", "/v1/ops/{item_id}/done", frozenset({Role.OPS, Role.SUPERVISOR})),
    ("GET", "/v1/approvals", frozenset({Role.AGENT, Role.SUPERVISOR})),
    ("POST", "/v1/approvals/{approval_id}/decision", frozenset({Role.AGENT, Role.SUPERVISOR})),
)
# Served without a token, by design: the health check, and in a development environment only
# the endpoint that hands out tokens (it asks for the staff key itself where one is needed).
OPEN = {("GET", "/health"), ("POST", "/v1/dev/token")}
PAIRS = [
    pytest.param(method, path, role, role in allowed, id=f"{method} {path} as {role.value}")
    for method, path, allowed in ROUTES
    for role in Role
]


def headers_for(role: Role) -> dict[str, str]:
    if role is Role.CUSTOMER:
        return token(role, customer_id="C0000001")
    if role is Role.GUEST:
        return token(role, session_id=f"s-{uuid4().hex}")
    return token(role)


def concrete(path: str) -> str:
    """The path with an identifier nobody owns in place of each parameter."""
    return re.sub(r"\{[^}]+\}", str(uuid4()), path)


def shape(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


@pytest.fixture
async def client(db: Database) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(ApiDeps(database=db, jwt_secret=SECRET, env="test"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://care.test"
    ) as http:
        yield http


@pytest.mark.parametrize(("method", "path", "role", "allowed"), PAIRS)
async def test_a_role_reaches_a_route_only_if_the_table_says_so(
    client: httpx.AsyncClient, method: str, path: str, role: Role, allowed: bool
) -> None:
    response = await client.request(method, concrete(path), headers=headers_for(role), json={})

    if allowed:
        # Past the door: what follows is about the request (nothing by that id, or a body that
        # says nothing), never about who is asking.
        assert response.status_code not in {401, 403}, response.text
    else:
        assert response.status_code == 403, response.text
        assert response.json()["error"]["code"] == "HTTP_403"


@pytest.mark.parametrize(("method", "path"), [(method, path) for method, path, _ in ROUTES])
async def test_no_route_of_the_table_answers_without_a_token(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, concrete(path), json={})

    assert response.status_code == 401


@pytest.mark.parametrize("env", ["test", "dev"])
def test_every_route_the_application_serves_is_in_the_table(db: Database, env: str) -> None:
    """A new route must be given its row, or be named as open on purpose."""
    app = create_app(
        ApiDeps(database=db, jwt_secret=SECRET, env=env, dev_staff_key="k" * 40)
        if env == "dev"
        else ApiDeps(database=db, jwt_secret=SECRET, env=env)
    )
    served = {
        (method, shape(route.path))
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods - {"HEAD", "OPTIONS"}
    }
    tabled = {(method, shape(path)) for method, path, _ in ROUTES}

    assert served - tabled <= OPEN
    assert tabled <= served
    # The endpoint that hands out tokens exists in a development environment and nowhere else.
    assert (("POST", "/v1/dev/token") in served) is (env == "dev")
    # Whatever else answers is the page of static files, which holds no data. A sub-application
    # or a socket mounted beside the routes would be served without ever passing this table.
    others = [route for route in app.routes if not isinstance(route, APIRoute)]
    assert [(type(route).__name__, route.name) for route in others] == [("Mount", "web")]
