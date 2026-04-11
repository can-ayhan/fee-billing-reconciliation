"""The reconciliation has to find every planted billing error, name its cause, and flag nothing else."""

from __future__ import annotations

import pytest

# cause reported by the control -> list in planted_issues.json
CAUSES = {
    "EXPIRED_TERMS": "expired_discount",
    "TERMS_NOT_APPLIED": "override_not_applied",
    "OLD_RATE_CARD": "old_rate_card",
    "SLAB_INSTEAD_OF_GRADUATED": "slab_instead_of_graduated",
    "VOLUME_MISMATCH": "volume_mismatch",
    "NOT_BILLED": "not_billed",
    "NO_ACTIVITY": "no_activity",
    "UNEXPLAINED": "unexplained",
}


def lines(conn, where: str) -> set:
    rows = conn.execute(
        f"select client_key, to_char(billing_month, 'YYYY-MM'), service_code from recon.lines where {where}"
    )
    return set(rows.fetchall())


@pytest.mark.parametrize("cause, planted_as", CAUSES.items())
def test_cause(built, planted, cause, planted_as):
    assert lines(built, f"cause = '{cause}'") == {tuple(key) for key in planted[planted_as]}


def test_cent_differences_are_not_findings(built, planted):
    rounding = {tuple(key) for key in planted["rounding"]}

    assert rounding <= lines(built, "status = 'WITHIN_TOLERANCE'")
    assert not rounding & lines(built, "cause is not null")


def test_duplicate_client_findings_belong_to_merged_clients(built, planted):
    merged = {pair[1] for pair in planted["duplicate_clients_by_lei"] + planted["duplicate_clients_by_name"]}
    flagged = {client for client, _, _ in lines(built, "cause = 'DUPLICATE_CLIENT'")}

    assert flagged == merged                     # every merged client has such lines, nobody else does

    # splitting a volume over two records can only push it into more expensive bands
    cheapest = built.execute("select min(difference) from recon.lines where cause = 'DUPLICATE_CLIENT'").fetchone()[0]
    assert cheapest > 0


def test_unverifiable_lines_point_at_a_schedule_defect(built):
    rows = built.execute(
        """select service_code, to_char(billing_month, 'YYYY-MM'), explanation
           from recon.lines where status = 'NOT_PRICEABLE'"""
    ).fetchall()

    assert rows
    for service, month, explanation in rows:
        assert "fee schedule has a defect" in explanation
        assert (service == "CORPACT" and month == "2025-04") \
            or (service == "SWIFT" and month >= "2025-10") \
            or (service == "FXSPOT" and month >= "2025-07"), (service, month)


def test_nothing_is_lost_between_invoices_and_reconciliation(built):
    invoiced = built.execute("select sum(billed_amount), count(*) from staging.invoice_lines").fetchone()
    reconciled = built.execute("select sum(billed_amount) from recon.lines").fetchone()[0]
    activity = built.execute("select count(*) from staging.activity").fetchone()[0]
    repriced = built.execute("select sum(activity_rows) from recon.repriced").fetchone()[0]

    assert reconciled == invoiced[0]
    assert repriced == activity


def test_every_exception_has_an_explanation_and_a_cause(built):
    missing = built.execute(
        "select count(*) from recon.v_exceptions where cause is null or explanation is null or explanation = ''"
    ).fetchone()[0]
    assert missing == 0


def test_overview_adds_up(built):
    o = built.execute("select * from recon.v_overview").fetchone()
    assert o[0] == sum(o[1:7])                   # every line has exactly one status
    by_cause = built.execute("select sum(over_billed), sum(under_billed) from recon.v_summary_by_cause").fetchone()
    assert (o[9], o[10]) == by_cause
