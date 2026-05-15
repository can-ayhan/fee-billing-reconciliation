"""Shared fixtures.

Most of the logic is SQL, so most tests need the PostgreSQL server from
docker-compose. They are skipped (not failed) when it is not running.

The tests run in their own database on that server. The working database
holds review decisions and published tariff rows, which are not rebuilt from
the input files; the tests must neither depend on them nor change them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from feerecon.config import RAW_DIR, get_settings  # noqa: E402
from feerecon.db import connect  # noqa: E402
from feerecon.load import load_inputs  # noqa: E402
from feerecon.pipeline import build  # noqa: E402

TEST_DATABASE = "feerecon_test"


@pytest.fixture(scope="session")
def conn():
    try:
        with psycopg.connect(get_settings().conninfo, autocommit=True) as server:
            exists = server.execute("select 1 from pg_database where datname = %s", [TEST_DATABASE]).fetchone()
            if not exists:
                server.execute(f'create database "{TEST_DATABASE}"')
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL is not reachable: {exc}")

    os.environ["PGDATABASE"] = TEST_DATABASE      # every connection made by the tests goes to the test database
    connection = connect()
    yield connection
    connection.close()


@pytest.fixture(scope="session")
def built(conn):
    """Load the committed inputs and run the whole control once per test run."""
    load_inputs(conn)
    build(conn)
    return conn


@pytest.fixture(scope="session")
def planted() -> dict:
    return json.loads((RAW_DIR / "planted_issues.json").read_text(encoding="utf-8"))["planted"]
