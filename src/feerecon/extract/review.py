"""Review queue between extraction and the pricing tables.

Extracted rows land in pricing.tariff_review. A row is approved automatically
only when the model was confident, the row quotes the document and its
schedule version passed the checks. Everything else waits for a person.
Publishing a document is refused while any of its rows is still pending, and
the approved set is checked once more before it is added to the schedule.

Functions here do not commit; the caller decides.
"""

from __future__ import annotations

import psycopg

from feerecon.extract.agent import ExtractionResult
from feerecon.extract.checks import check_rows
from feerecon.extract.schema import TariffRow

AUTO_APPROVE_CONFIDENCE = 0.85

COLUMNS = ["service_code", "basis", "band_method", "unit", "tier_from", "tier_to", "rate",
           "effective_from", "effective_to", "confidence", "source_quote"]


class ReviewError(RuntimeError):
    """The requested step is not allowed in the current state of the queue."""


def known_services(conn: psycopg.Connection) -> list[dict]:
    rows = conn.execute("select service_code, service_name from pricing.v_service order by service_code").fetchall()
    return [{"service_code": code, "service_name": name} for code, name in rows]


def queue(conn: psycopg.Connection, document: str, result: ExtractionResult) -> dict[str, int]:
    """Replace the document's open rows with a new extraction. Returns the number of rows per status."""
    conn.execute("delete from pricing.tariff_review where document = %s and status <> 'published'", [document])
    counts = {"approved": 0, "pending": 0}
    for row in result.rows:
        reasons = [p.message for p in result.problems if p.blocking and p.concerns(row)]
        if row.confidence < AUTO_APPROVE_CONFIDENCE:
            reasons.append(f"confidence {row.confidence:.2f} is below {AUTO_APPROVE_CONFIDENCE:.2f}")
        status = "pending" if reasons else "approved"
        counts[status] += 1
        values = [getattr(row, column) for column in COLUMNS]
        conn.execute(
            f"""insert into pricing.tariff_review (document, {', '.join(COLUMNS)}, status, reason, decided_by, decided_at)
                values (%s, {', '.join(['%s'] * len(COLUMNS))}, %s, %s, %s, case when %s then now() end)""",
            [document, *values, status, "; ".join(reasons) or None, None if reasons else "auto", not reasons],
        )
    return counts


def listing(conn: psycopg.Connection, document: str | None = None, only_pending: bool = True) -> list[tuple]:
    return conn.execute(
        """select review_id, document, status, service_code, tier_from, tier_to, rate, unit, effective_from,
                  confidence, reason, source_quote
           from pricing.tariff_review
           where (%(document)s::text is null or document = %(document)s)
             and (not %(only_pending)s or status = 'pending')
           order by document, service_code, effective_from, tier_from""",
        {"document": document, "only_pending": only_pending},
    ).fetchall()


def decide(conn: psycopg.Connection, review_ids: list[int], approve: bool, decided_by: str) -> int:
    """Approve or reject pending rows. Returns how many rows changed."""
    cursor = conn.execute(
        """update pricing.tariff_review
           set status = %s, decided_by = %s, decided_at = now()
           where review_id = any(%s) and status = 'pending'""",
        ["approved" if approve else "rejected", decided_by, review_ids],
    )
    return cursor.rowcount


def publish(conn: psycopg.Connection, document: str) -> int:
    """Add the document's approved rows to pricing.fee_schedule. Returns the number of rows added."""
    statuses = dict(conn.execute(
        "select status, count(*) from pricing.tariff_review where document = %s group by status", [document]
    ).fetchall())
    if statuses.get("pending"):
        raise ReviewError(f"{statuses['pending']} rows of {document} are still waiting for review")
    if not statuses.get("approved"):
        raise ReviewError(f"{document} has no approved rows to publish")

    records = conn.execute(
        f"select {', '.join(COLUMNS)} from pricing.tariff_review where document = %s and status = 'approved'", [document]
    ).fetchall()
    rows = [TariffRow(**dict(zip(COLUMNS, record))) for record in records]

    # rejecting single bands can leave a version with a hole: check the set that would go live
    blocking = [p for p in check_rows(rows, {s["service_code"] for s in known_services(conn)}) if p.blocking]
    if blocking:
        raise ReviewError("approved rows do not form a complete schedule: " + "; ".join(p.message for p in blocking))

    clash = conn.execute(
        """select distinct r.service_code, r.effective_from
           from pricing.tariff_review r
           join pricing.fee_schedule f using (service_code, effective_from)
           where r.document = %s and r.status = 'approved'""",
        [document],
    ).fetchall()
    if clash:
        versions = ", ".join(f"{service} from {start}" for service, start in clash)
        raise ReviewError(f"the schedule already has a version for {versions}")

    conn.execute("update pricing.tariff_review set status = 'published' where document = %s and status = 'approved'",
                 [document])
    return conn.execute("select pricing.apply_published_tariffs()").fetchone()[0]
