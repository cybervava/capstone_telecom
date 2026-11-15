"""Batch load of the network inventory and the address → node mapping into the care platform.

Reads three files of the data pack and nothing else. In particular it never opens
``customers.csv`` (the platform keeps no copy of names, phones or emails; it asks the CRM when it
needs them) and never opens the simulator's ground-truth file. From ``services.csv`` it keeps
identifiers only: which customer has which service at which address.

The load replaces the three reference tables in one transaction, so readers see either the old
version or the new one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from psycopg import sql

from skylink_care.db.database import Connection, Database

NODE_COLUMNS = (
    "node_id",
    "node_type",
    "service_type",
    "parent_node_id",
    "city",
    "area",
    "pincode",
    "status",
)
MAP_COLUMNS = (
    "address_id",
    "service_type",
    "node_id",
    "source",
    "valid_from",
    "valid_to",
    "map_version",
)
SERVICE_COLUMNS = (
    "service_id",
    "customer_id",
    "service_type",
    "plan_name",
    "monthly_fee_inr",
    "status",
    "address_id",
    "router_model",
    "router_serial",
    "activated_on",
    "billing_cycle_day",
)
_FILES = {
    "nodes.csv": NODE_COLUMNS,
    "address_node_map.csv": MAP_COLUMNS,
    "services.csv": SERVICE_COLUMNS,
}
_CHUNK_BYTES = 1 << 20


class DataPackError(Exception):
    """The data pack is missing a file or a file does not have the agreed columns."""


@dataclass(frozen=True)
class LoadStats:
    nodes: int
    mappings: int
    services: int
    seconds: float


def check_data_pack(data_dir: Path) -> None:
    """Fail fast, before touching the database, if the pack does not match the data contract."""
    for name, expected in _FILES.items():
        path = data_dir / name
        if not path.is_file():
            raise DataPackError(f"missing file: {path}")
        with path.open(encoding="utf-8") as handle:
            header = tuple(handle.readline().strip().split(","))
        if header != expected:
            raise DataPackError(
                f"{name}: expected columns {', '.join(expected)}; found {', '.join(header)}"
            )


async def load_reference(database: Database, data_dir: Path) -> LoadStats:
    check_data_pack(data_dir)
    started = time.monotonic()
    async with database.transaction() as conn:
        await conn.execute("TRUNCATE nodes, address_node_map, service_index")
        await conn.execute(
            sql.SQL("CREATE TEMP TABLE staging_services ({}) ON COMMIT DROP").format(
                sql.SQL(", ").join(
                    sql.SQL("{} text").format(sql.Identifier(column)) for column in SERVICE_COLUMNS
                )
            )
        )
        nodes = await _copy(conn, "nodes", NODE_COLUMNS, data_dir / "nodes.csv")
        mappings = await _copy(
            conn, "address_node_map", MAP_COLUMNS, data_dir / "address_node_map.csv"
        )
        await _copy(conn, "staging_services", SERVICE_COLUMNS, data_dir / "services.csv")
        cursor = await conn.execute(
            "INSERT INTO service_index (service_id, customer_id, service_type, address_id, status)"
            " SELECT service_id, customer_id, service_type, address_id, status"
            " FROM staging_services"
        )
        services = cursor.rowcount
        await conn.execute("ANALYZE nodes, address_node_map, service_index")
    return LoadStats(nodes, mappings, services, round(time.monotonic() - started, 2))


async def _copy(conn: Connection, table: str, columns: tuple[str, ...], path: Path) -> int:
    statement = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    async with conn.cursor() as cursor:
        async with cursor.copy(statement) as copy:
            with path.open("rb") as handle:
                while chunk := handle.read(_CHUNK_BYTES):
                    await copy.write(chunk)
        return cursor.rowcount
