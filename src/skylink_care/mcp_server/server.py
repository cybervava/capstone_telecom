"""The MCP server: the tools of ``tools.py`` declared to a Model Context Protocol client.

What the protocol needs is here and nothing else: the name and description a client's model
reads, the type and bounds of every argument, the note that every tool only reads, and the
key a client must present over HTTP. The limits are in the declarations, so an argument out of
bounds is refused by the server before a tool runs; they are not left to a prompt.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Awaitable, Sequence
from decimal import Decimal
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from skylink_care.mcp_server.tools import (
    CareTools,
    CreditQuote,
    GuideSearch,
    NetworkStatus,
    PlatformStatus,
    Refused,
)

NAME = "skylink-care"
MCP_PATH = "/mcp"
HEALTH_PATH = "/health"
INSTRUCTIONS = (
    "Read-only tools of the SkyLink care platform, for staff assistants and other agents."
    " Nothing here writes to an enterprise system, and nothing here reads or returns a"
    " customer's data: do not put names, phone numbers or customer ids into an argument."
    " How to use what comes back:"
    " (1) check_network_status gives one of three states. UNKNOWN is never an all-clear:"
    " do not say the network is fine on it, and suggest neither device steps nor a technician."
    " (2) search_router_guide returns steps from the guide of one router model. Give them word"
    " for word with their citation; do not rephrase, add or reorder steps, and never give the"
    " steps of another model's guide."
    " (3) quote_service_credit is the policy engine. Never work out a credit yourself, and never"
    " promise one: a quotation applies nothing, and a credit above the policy's limit needs a"
    " supervisor."
    " (4) platform_status says whether the platform's own evidence can be relied on just now."
)
# Shapes of the identifiers a tool accepts. A node id is what Network Monitoring issues; a
# router model is what a guide's front matter may carry (``rag.documents``).
NODE_ID = r"^[A-Za-z0-9._:-]{1,64}$"
ROUTER_MODEL = r"^[A-Z0-9][A-Z0-9-]{0,31}$"
MAX_PROBLEM_CHARS = 500
MAX_FEE_USD = Decimal(100_000)
# A quarter of a year: longer than any outage of the data pack, and a bound all the same.
MAX_OUTAGE_MINUTES = 60 * 24 * 92
# Names a client on the same machine reaches the endpoint by. A request under any other name is
# turned away before anything else is looked at (a page in a browser cannot reach a local
# server through a name it has pointed at 127.0.0.1).
LOOPBACK_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
LOOPBACK_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")
READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


async def _answered[Answer](asked: Awaitable[Answer]) -> Answer:
    """A refusal is an answer the client's model can read and act on, not a fault."""
    try:
        return await asked
    except Refused as refused:
        raise ToolError(str(refused)) from None


async def _read[Document](asked: Awaitable[Document]) -> Document:
    """The same for a document a client reads: a refusal in words, not a fault."""
    try:
        return await asked
    except Refused as refused:
        raise ResourceError(str(refused)) from None


def build_server(tools: CareTools) -> MCPServer:
    server = MCPServer(NAME, title="SkyLink care platform", instructions=INSTRUCTIONS)

    @server.tool(title="Network status of a node", annotations=READ_ONLY)
    async def check_network_status(
        node_id: Annotated[
            str,
            Field(pattern=NODE_ID, description="The network node, as Network Monitoring names it"),
        ],
    ) -> NetworkStatus:
        """What is known about one network node right now: ACTIVE (an outage is confirmed, with
        its cause and estimate), CONFIRMED_CLEAR (no outage reported, on a status read just
        now) or UNKNOWN (it could not be verified, which is never an all-clear). The answer
        says in words what may be said on it. It reads the live status and the NOC event feed;
        it tests no customer's line."""
        return await _answered(tools.check_network_status(node_id))

    @server.tool(title="Steps from a router's guide", annotations=READ_ONLY)
    async def search_router_guide(
        router_model: Annotated[
            str,
            Field(pattern=ROUTER_MODEL, description="The router's model code, such as AL-AX18"),
        ],
        problem: Annotated[
            str,
            Field(
                min_length=1,
                max_length=MAX_PROBLEM_CHARS,
                description="The fault as described, in plain words. Nothing personal.",
            ),
        ],
    ) -> GuideSearch:
        """The sections of one router model's troubleshooting guide that fit a described
        fault, best first, each with its numbered steps word for word and the citation to give
        with them. Only sections a customer may follow alone are returned: never a factory
        reset. Use it only once the network is confirmed clear for the customer's node."""
        return await _answered(tools.search_router_guide(router_model, problem))

    @server.tool(title="Service credit under the policy", annotations=READ_ONLY)
    async def quote_service_credit(
        service_type: Literal["broadband", "mobile"],
        monthly_fee_usd: Annotated[
            Decimal,
            Field(gt=0, le=MAX_FEE_USD, decimal_places=2, description="Monthly fee, US dollars"),
        ],
        outage_minutes: Annotated[
            int,
            Field(ge=0, le=MAX_OUTAGE_MINUTES, description="How long the resolved outage lasted"),
        ],
        planned: Annotated[
            bool, Field(description="Planned maintenance that was announced in advance")
        ] = False,
        credited_this_cycle_usd: Annotated[
            Decimal,
            Field(
                ge=0,
                le=MAX_FEE_USD,
                decimal_places=2,
                description="Outage credits already given in this billing cycle, US dollars",
            ),
        ] = Decimal(0),
    ) -> CreditQuote:
        """What the service credit policy gives for one resolved outage on these figures:
        whether a credit is due and why not if none is, the amount, who must approve it, and
        the working. It is the engine the platform itself uses. It quotes and applies nothing,
        and it knows no customer: the figures are the caller's, and it assumes an active
        service on an affected node with no earlier credit for the outage."""
        return await _answered(
            tools.quote_service_credit(
                service_type=service_type,
                monthly_fee_usd=monthly_fee_usd,
                outage_minutes=outage_minutes,
                planned=planned,
                credited_this_cycle_usd=credited_this_cycle_usd,
            )
        )

    @server.tool(title="How the platform is doing", annotations=READ_ONLY)
    async def platform_status() -> PlatformStatus:
        """Whether the platform's own evidence can be relied on just now: the NOC event feed
        (caught up, and intact or with a gap), how many contacts wait for the assistant and
        whether it is behind, the guide index, and the outages and alarms the feed shows as
        open. Nothing of a customer."""
        return await _answered(tools.platform_status())

    @server.resource(
        "skylink://credit-policy",
        name="credit-policy",
        title="Service credit policy",
        description="The versioned policy file the credit engine executes (a placeholder), as"
        " it was read when the server started.",
        mime_type="application/yaml",
    )
    async def credit_policy() -> str:
        return await _read(tools.credit_policy_text())

    @server.resource(
        "skylink://router-models",
        name="router-models",
        title="Router models with a guide",
        description="The router models whose troubleshooting guide is in the index.",
        mime_type="application/json",
    )
    async def router_models() -> str:
        return json.dumps(await _read(tools.router_models()), indent=2)

    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    # Open to whoever asks: it says that the process answers, and nothing else.
    server.custom_route(HEALTH_PATH, methods=["GET"])(health)
    return server


class BearerKey:
    """Lets a request through only with the key, compared in constant time. With no key
    configured the endpoint answers nobody: it is closed, not open.

    The guard names what is open, the health check, and asks the key of everything else. A
    path it was never told about (a prefix the server is mounted under, a route a later
    version of the SDK brings) is then closed with the rest.
    """

    def __init__(self, app: ASGIApp, key: str | None) -> None:
        self.app = app
        self._key = key.encode() if key else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan" or self._is_the_health_check(scope):
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # The protocol is served over HTTP requests. Nothing else is taken up.
            await send({"type": "websocket.close", "code": 1008})
            return
        if self._has_the_key(scope):
            await self.app(scope, receive, send)
            return
        why = "the key is missing or wrong" if self._key else "no key is configured"
        refusal = JSONResponse(
            {"error": "unauthorized", "message": f"this MCP endpoint is closed: {why}"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await refusal(scope, receive, send)

    @staticmethod
    def _is_the_health_check(scope: Scope) -> bool:
        if scope["type"] != "http" or scope["method"] not in ("GET", "HEAD"):
            return False
        # As the router reads it: without the prefix the server is mounted under.
        path, prefix = str(scope["path"]), str(scope.get("root_path", ""))
        return path.removeprefix(prefix) == HEALTH_PATH

    def _has_the_key(self, scope: Scope) -> bool:
        if self._key is None:
            return False
        offered = [value for name, value in scope["headers"] if name == b"authorization"]
        if len(offered) != 1:
            # None, or two: which of two a proxy added is not for the guard to guess.
            return False
        scheme, _, key = offered[0].partition(b" ")
        return scheme.lower() == b"bearer" and hmac.compare_digest(key, self._key)


def http_app(server: MCPServer, *, key: str | None, allowed_hosts: Sequence[str] = ()) -> BearerKey:
    """The server as an ASGI application: one request, one JSON answer, no session kept, the
    key asked for on every request but the one to ``/health``.

    The key is asked for first. A request that has it is then served only under a loopback
    name or one of ``allowed_hosts``: a page in a browser that has pointed a name of its own
    at this machine is turned away even if it has come by the key."""
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*LOOPBACK_HOSTS, *allowed_hosts],
        allowed_origins=[*LOOPBACK_ORIGINS, *(f"https://{host}" for host in allowed_hosts)],
    )
    inner: Any = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=security,
    )
    return BearerKey(inner, key)
