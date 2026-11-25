"""Entry point for the MCP server process (``care-mcp``).

    care-mcp            Streamable HTTP on CARE_MCP_HOST:CARE_MCP_PORT, path /mcp. A client
                        presents CARE_MCP_KEY as a bearer token.
    care-mcp --stdio    Over standard input and output, for a desktop client that starts the
                        process itself. No key: whoever starts it is the client.

The process holds a database connection and one gateway key that can read a node's status
(``OSS_KEY_MCP``). It holds no key that can read a customer or write anything.
"""

from __future__ import annotations

import argparse
import asyncio

import structlog
import uvicorn

from skylink_care.adapters.gateway import open_gateway
from skylink_care.config import Settings, get_oss_keys, get_settings
from skylink_care.db.database import Database
from skylink_care.mcp_server.server import build_server, http_app
from skylink_care.mcp_server.tools import build_tools
from skylink_care.observability.logging import configure_logging

log = structlog.get_logger(__name__)
# A tool call holds a connection for one query at a time.
POOL_MAX = 4


def allowed_hosts(settings: Settings) -> list[str]:
    return [name.strip() for name in settings.mcp_allowed_hosts.split(",") if name.strip()]


async def serve(settings: Settings, *, stdio: bool) -> None:
    database = Database.create(settings.database_conninfo(), max_size=POOL_MAX)
    gateway = open_gateway(settings, get_oss_keys())
    tools = None
    try:
        await database.start()
        tools = build_tools(settings, database, gateway)
        server = build_server(tools)
        if stdio:
            await server.run_stdio_async()
            return
        key = settings.mcp_key_for_http()
        if key is None:
            # Said once, where whoever started it will look: it runs, and answers nobody.
            log.warning("mcp_endpoint_closed", reason="CARE_MCP_KEY is not set or too short")
        config = uvicorn.Config(
            http_app(server, key=key, allowed_hosts=allowed_hosts(settings)),
            host=settings.mcp_host,
            port=settings.mcp_port,
            log_level="info",
            # The server's own lines go through the one handler that redacts.
            log_config=None,
        )
        await uvicorn.Server(config).serve()
    finally:
        # Whatever was opened is closed, also when the start itself failed (a policy file that
        # cannot be read, a database that is not there).
        if tools is not None:
            await tools.aclose()
        await gateway.aclose()
        await database.close()


def run(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="care-mcp", description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--stdio", action="store_true", help="speak over stdin and stdout instead of HTTP"
    )
    args = parser.parse_args(argv)
    configure_logging()
    asyncio.run(serve(get_settings(), stdio=args.stdio))
