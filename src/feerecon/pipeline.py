"""The steps that run inside PostgreSQL, in order."""

from __future__ import annotations

import psycopg

from feerecon.db import run_sql_file

BUILD_SCRIPTS = [
    "02_typed.sql",
    "03_schedule_validation.sql",
    "04_client_master.sql",
]


def build(conn: psycopg.Connection) -> None:
    for script in BUILD_SCRIPTS:
        run_sql_file(conn, script)
    conn.commit()
