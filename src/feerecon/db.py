"""Thin helpers around psycopg."""

from __future__ import annotations

import psycopg

from feerecon.config import SQL_DIR, get_settings


def connect() -> psycopg.Connection:
    return psycopg.connect(get_settings().conninfo)


def run_sql_file(conn: psycopg.Connection, name: str) -> None:
    """Run one of the numbered scripts in sql/ as a single batch."""
    conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))
