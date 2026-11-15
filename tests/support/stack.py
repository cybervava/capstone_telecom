"""Fixtures that stand up the systems around the care platform, in process.

``datapack`` generates a small synthetic data pack once per session. ``mock_oss_app`` is the mock
OSS/BSS gateway seeded from it; adapters reach it through an ASGI transport, so contract and
end-to-end tests exercise the real HTTP contract without opening a port. ``stack_clock`` is one
clock shared by the gateway and the platform, so tests can move time instead of sleeping.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from mock_oss.clock import ManualClock
from mock_oss.config import CLIENT_ENV_VARS
from mock_oss.config import Settings as MockSettings
from mock_oss.main import create_app
from mock_oss.seed import seed_database
from skylink_care.adapters.gateway import OssGateway
from skylink_care.config import OssKeys
from skylink_datagen.config import DefectRates, GenerateConfig
from skylink_datagen.pipeline import run_generate

MOCK_URL = "http://mock-oss.test"
SMALL_PACK_CUSTOMERS = 1000


@pytest.fixture(scope="session")
def datapack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small, seeded data pack that conforms to docs/contracts/data.md."""
    out = tmp_path_factory.mktemp("datapack")
    run_generate(
        GenerateConfig(out=out, customers=SMALL_PACK_CUSTOMERS, seed=7, rates=DefectRates())
    )
    return out


@pytest.fixture(scope="session")
def seeded_mock_db(datapack: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("mock-template") / "mock.sqlite"
    seed_database(datapack, path, seed=7)
    return path


@pytest.fixture(scope="session")
def oss_key_values() -> dict[str, str]:
    """A fresh random key per client for this test session."""
    return {client: f"test-{client}-{uuid4().hex}" for client in CLIENT_ENV_VARS}


@pytest.fixture
def stack_clock() -> ManualClock:
    """Starts at the real time, then moves only when a test advances it."""
    return ManualClock(datetime.now(UTC))


@pytest.fixture
def mock_oss_app(
    seeded_mock_db: Path, tmp_path: Path, oss_key_values: dict[str, str], stack_clock: ManualClock
) -> FastAPI:
    db_path = tmp_path / "mock.sqlite"
    shutil.copy(seeded_mock_db, db_path)
    settings = MockSettings(db_path=db_path, line_test_delay_ms=(0, 0), api_keys=oss_key_values)
    return create_app(settings, clock=stack_clock)


@pytest.fixture
def oss_transport(mock_oss_app: FastAPI) -> httpx.ASGITransport:
    return httpx.ASGITransport(app=mock_oss_app)


@pytest.fixture
def oss_keys(oss_key_values: dict[str, str]) -> OssKeys:
    return OssKeys(**{name: value for name, value in oss_key_values.items() if name != "admin"})


@pytest.fixture
async def gateway(
    oss_transport: httpx.ASGITransport, oss_keys: OssKeys
) -> AsyncIterator[OssGateway]:
    client = OssGateway(MOCK_URL, oss_keys, transport=oss_transport)
    yield client
    await client.aclose()


class Simulator:
    """Thin helper over the gateway's ``/admin`` controls: scenarios, faults, fixtures, counters."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.post(path, json=body or {})
        response.raise_for_status()
        return dict(response.json())

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return dict(response.json())

    async def find_customer(self, **filters: Any) -> dict[str, Any]:
        """One fixture customer matching the filters (ground truth included)."""
        found = await self.get("/admin/v1/customers/search", limit=1, **filters)
        if not found["customers"]:
            raise LookupError(f"no customer in the data pack matches {filters}")
        return dict(found["customers"][0])

    async def find_customers(self, limit: int, **filters: Any) -> list[dict[str, Any]]:
        found = await self.get("/admin/v1/customers/search", limit=limit, **filters)
        return [dict(item) for item in found["customers"]]

    async def start_outage(self, node_id: str, **options: Any) -> dict[str, Any]:
        body = {"node_id": node_id, "cause": "FIBRE_CUT", "eta_minutes": 180, **options}
        return await self.post("/admin/v1/outages", body)

    async def resolve_outage(self, outage_id: str) -> dict[str, Any]:
        return await self.post(f"/admin/v1/outages/{outage_id}/resolve")

    async def fault(self, target: str, mode: str, **options: Any) -> dict[str, Any]:
        return await self.post("/admin/v1/chaos", {"target": target, "mode": mode, **options})

    async def state(self) -> dict[str, Any]:
        return await self.get("/admin/v1/state")


@pytest.fixture
async def simulator(
    oss_transport: httpx.ASGITransport, oss_key_values: dict[str, str]
) -> AsyncIterator[Simulator]:
    async with httpx.AsyncClient(
        transport=oss_transport,
        base_url=MOCK_URL,
        headers={"X-API-Key": oss_key_values["admin"]},
        timeout=10.0,
    ) as client:
        yield Simulator(client)
