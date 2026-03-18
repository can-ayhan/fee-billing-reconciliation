"""Land the input files in the staging schema, every column as text."""

from __future__ import annotations

import csv
from pathlib import Path

import psycopg

from feerecon.config import RAW_DIR
from feerecon.db import run_sql_file

FILES = {
    "clients.csv": "staging.raw_clients",
    "fee_schedule.csv": "staging.raw_fee_schedule",
    "client_overrides.csv": "staging.raw_client_overrides",
    "activity.csv": "staging.raw_activity",
    "invoice_lines.csv": "staging.raw_invoice_lines",
    "client_merge_decisions.csv": "staging.raw_client_merge_decisions",
}


def load_file(conn: psycopg.Connection, path: Path, table: str) -> int:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = [column.strip().lower() for column in next(reader)]
        count = 0
        with conn.cursor() as cur:
            with cur.copy(f"copy {table} (row_num, {', '.join(header)}) from stdin") as copy:
                for row_num, row in enumerate(reader, start=2):
                    if not any(cell.strip() for cell in row):
                        continue
                    copy.write_row([row_num, *(row + [""] * len(header))[:len(header)]])
                    count += 1
    return count


def load_inputs(conn: psycopg.Connection, raw_dir: Path = RAW_DIR) -> dict[str, int]:
    run_sql_file(conn, "00_schemas.sql")
    run_sql_file(conn, "01_staging.sql")
    counts = {table: load_file(conn, raw_dir / filename, table) for filename, table in FILES.items()}
    conn.commit()
    return counts
