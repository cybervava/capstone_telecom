"""The demo console at ``/console/``: a page that plays the network, the field and the failures
for whoever presents the platform, through the simulator's admin API.

The page holds no key. The presenter types the admin key into it, and every call it makes
carries that key, so the page alone can change nothing. The platform never talks to it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

CONSOLE_DIR = Path(__file__).resolve().parent / "console"
PAGE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; object-src 'none'; frame-ancestors 'none';"
        " base-uri 'self'; form-action 'self'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # A release brings a new page: the browser asks again rather than keep an old one.
    "Cache-Control": "no-cache",
}


class _ConsoleFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers.update(PAGE_HEADERS)
        return response


def mount_console(app: FastAPI) -> None:
    app.mount("/console", _ConsoleFiles(directory=CONSOLE_DIR, html=True), name="console")
