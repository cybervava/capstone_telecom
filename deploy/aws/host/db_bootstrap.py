"""Prepare the RDS database for the platform, as RDS's master user.

The database is deploy/aws/terraform/database.tf. deploy/aws/host/deploy.sh runs this from the
release's image before every start; every step can be run again. The platform's role logs in
with the password of the host's settings, may create in the public schema and so owns its own
tables, and has no other right. The vector extension, which only the master user may create, is
put in first: the migration that asks for it then finds it there.

Reads the master user's secret, as RDS keeps it ({"username": ..., "password": ...}), on stdin;
CARE_DB_HOST, CARE_DB_PORT, CARE_DB_NAME, CARE_DB_USER and CARE_DB_PASSWORD from the
environment, with PGSSLMODE and PGSSLROOTCERT for the connection.
"""

from __future__ import annotations

import json
import os
import sys

import psycopg
from psycopg import sql


def main() -> int:
    master = json.load(sys.stdin)
    env = os.environ
    role = sql.Identifier(env["CARE_DB_USER"])
    database = sql.Identifier(env["CARE_DB_NAME"])
    with psycopg.connect(
        host=env["CARE_DB_HOST"],
        port=env["CARE_DB_PORT"],
        dbname=env["CARE_DB_NAME"],
        user=master["username"],
        password=master["password"],
        application_name="skylink-care-db-bootstrap",
        autocommit=True,
    ) as conn:
        known = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (env["CARE_DB_USER"],))
        if known.fetchone() is None:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(role))
        # A utility statement takes no bound parameters: the password goes in as a quoted literal.
        password = sql.Literal(env["CARE_DB_PASSWORD"])
        conn.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(role, password))
        grant = sql.SQL("GRANT CONNECT, TEMPORARY ON DATABASE {} TO {}").format(database, role)
        conn.execute(grant)
        conn.execute(sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(role))
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        vector = conn.execute("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        version = vector.fetchone()
        tls = conn.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone()
    sys.stdout.write(
        f"database ready: role {env['CARE_DB_USER']},"
        f" pgvector {version[0] if version else 'missing'}, TLS {bool(tls and tls[0])}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
