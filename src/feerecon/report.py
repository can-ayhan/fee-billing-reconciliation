"""Console summary of what the control found."""

from __future__ import annotations

import psycopg


def _rows(conn: psycopg.Connection, sql: str) -> list[tuple]:
    return conn.execute(sql).fetchall()


def print_report(conn: psycopg.Connection) -> None:
    print("Fee schedule")
    issues = _rows(conn, "select severity, issue_code, service_code, effective_from, detail from dq.schedule_issues order by 1, 3, 4")
    for severity, code, service, effective_from, detail in issues:
        print(f"  {severity:<7} {code:<20} {service:<8} {effective_from}  {detail}")
    if not issues:
        print("  no findings")

    print("\nClient master")
    for code, count in _rows(conn, "select issue_code, count(*) from dq.client_issues group by 1 order by 1"):
        print(f"  {code:<18} {count:>4}")
    records, clients = _rows(conn, "select count(*), count(distinct client_key) from core.client_xref")[0]
    print(f"  {records} records -> {clients} clients")

    o = _rows(conn, "select * from recon.v_overview")[0]
    print("\nReconciliation (client x month x service)")
    print(f"  lines {o[0]:,}: match {o[1]:,}, within tolerance {o[2]:,}, mismatch {o[3]:,}, "
          f"not billed {o[4]:,}, no activity {o[5]:,}, not priceable {o[6]:,}")
    print(f"  billed {o[7]:,.2f}   over-billed {o[9]:,.2f}   under-billed {o[10]:,.2f}")

    print(f"\n  {'cause':<28}{'lines':>6}{'over-billed':>15}{'under-billed':>15}{'not verified':>15}")
    for cause, lines, over, under, _net, unverified in _rows(
            conn, "select * from recon.v_summary_by_cause order by over_billed + under_billed + billed_not_verified desc"):
        print(f"  {cause:<28}{lines:>6}{over:>15,.2f}{under:>15,.2f}{unverified:>15,.2f}")
