"""Shared fixtures.

Most of the logic is SQL, so most tests need the database from docker-compose.
They are skipped (not failed) when it is not running.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from feerecon.config import RAW_DIR  # noqa: E402
from feerecon.db import connect  # noqa: E402
from feerecon.load import load_inputs  # noqa: E402
from feerecon.pipeline import build  # noqa: E402


@pytest.fixture(scope="session")
def conn():
    try:
        connection = connect()
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL is not reachable: {exc}")
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
