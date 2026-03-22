"""Fee schedule checks and client master remediation against the planted issues."""

from __future__ import annotations

import datetime as dt

import pytest

from feerecon.generate import lei_check_digits


def test_schedule_defects_are_found_and_nothing_else(built, planted):
    found = {
        (code, service, effective_from.isoformat())
        for code, service, effective_from in built.execute(
            "select issue_code, service_code, effective_from from dq.schedule_issues"
        )
    }
    assert found == {tuple(issue) for issue in planted["schedule"]}


def test_affected_period_of_each_defect(built):
    periods = {
        code: (start, end)
        for code, start, end in built.execute("select issue_code, affected_from, affected_to from dq.schedule_issues")
    }
    assert periods["DATE_GAP"] == (dt.date(2025, 4, 1), dt.date(2025, 4, 30))
    assert periods["DATE_OVERLAP"] == (dt.date(2025, 10, 1), None)        # the old rate was never end-dated
    assert periods["TIER_GAP"] == (dt.date(2025, 7, 1), None)


def test_schedule_checks_on_a_hand_built_tariff(built):
    """One service per defect, inserted for the test and rolled back."""
    rows = [
        # service, tier_from, tier_to, rate, effective_from, effective_to
        ("T_OK", 0, 100, 2, "2025-01-01", None), ("T_OK", 100, None, 1, "2025-01-01", None),
        ("T_FIRST", 10, None, 1, "2025-01-01", None),                                             # does not start at 0
        ("T_CAPPED", 0, 100, 1, "2025-01-01", None),                                              # nothing above 100
        ("T_RATE", 0, None, 0, "2025-01-01", None),                                               # zero rate
        ("T_GAP", 0, 100, 2, "2025-01-01", None), ("T_GAP", 150, None, 1, "2025-01-01", None),
        ("T_OVER", 0, 100, 2, "2025-01-01", None), ("T_OVER", 80, None, 1, "2025-01-01", None),
        ("T_DATES", 0, None, 1, "2025-01-01", "2025-03-31"), ("T_DATES", 0, None, 1, "2025-03-01", None),
        ("T_HOLE", 0, None, 1, "2025-01-01", "2025-01-31"), ("T_HOLE", 0, None, 1, "2025-03-01", None),
    ]
    built.rollback()
    try:
        built.execute("delete from pricing.fee_schedule")
        for n, (service, tier_from, tier_to, rate, start, end) in enumerate(rows):
            built.execute(
                """insert into pricing.fee_schedule
                   values (%s, %s, %s, 'count', 'graduated', 'per_item', %s, %s, %s, %s, %s)""",
                [n, service, service, tier_from, tier_to, rate, start, end],
            )
        built.execute(open("sql/03_schedule_validation.sql", encoding="utf-8").read())
        found = set(built.execute("select service_code, issue_code from dq.schedule_issues").fetchall())
    finally:
        built.rollback()

    assert found == {
        ("T_FIRST", "FIRST_TIER_NOT_ZERO"), ("T_CAPPED", "LAST_TIER_CAPPED"), ("T_RATE", "RATE_INVALID"),
        ("T_GAP", "TIER_GAP"), ("T_OVER", "TIER_OVERLAP"), ("T_DATES", "DATE_OVERLAP"), ("T_HOLE", "DATE_GAP"),
    }


# client master ---------------------------------------------------------------

VALID = "ZZ9900ABCDEF123456" + lei_check_digits("ZZ9900ABCDEF123456")


@pytest.mark.parametrize(
    "lei, expected",
    [
        (VALID, "valid"),
        (VALID[:6] + VALID[7] + VALID[6] + VALID[8:], "bad check digits"),   # transposition
        (VALID[:19], "bad format"),
        (VALID + "0", "bad format"),
        (VALID[:10] + "?" + VALID[11:], "bad format"),
        (None, "missing"),
    ],
)
def test_lei_status(built, lei, expected):
    assert built.execute("select core.lei_status(%s)", [lei]).fetchone()[0] == expected


def test_sql_lei_check_agrees_with_python_for_every_client(built):
    from feerecon.generate import lei_is_valid

    rows = built.execute("select lei, core.lei_is_valid(lei) from staging.clients where lei is not null").fetchall()

    assert len(rows) > 60
    assert all(lei_is_valid(lei) == valid for lei, valid in rows)


def test_lei_findings(built, planted):
    def found(code: str) -> set:
        return {r[0] for r in built.execute("select client_id from dq.client_issues where issue_code = %s", [code])}

    assert found("LEI_CHECK_DIGIT") == set(planted["lei_check_digit"])
    assert found("LEI_FORMAT") == set(planted["lei_format"])
    assert found("LEI_MISSING") == set(planted["lei_missing"])


def test_duplicate_records_map_to_one_client(built, planted):
    merged = {
        (client_id, client_key): reason
        for client_id, client_key, reason in built.execute(
            "select client_id, client_key, merge_reason from core.client_xref where client_id <> client_key"
        )
    }
    by_lei = {tuple(pair) for pair in planted["duplicate_clients_by_lei"]}
    by_name = {tuple(pair) for pair in planted["duplicate_clients_by_name"]}

    assert {pair for pair, reason in merged.items() if reason == "same LEI"} == by_lei
    assert {pair for pair, reason in merged.items() if reason == "manual decision"} == by_name
    assert built.execute("select count(*) from core.clients").fetchone()[0] == 60


def test_lei_written_with_spaces_or_lower_case_is_not_an_error(built, planted):
    """Duplicates were typed in differently; that must not hide them or flag them."""
    duplicates = [pair[0] for pair in planted["duplicate_clients_by_lei"]]
    flagged = built.execute(
        "select count(*) from dq.client_issues where issue_code like 'LEI%%' and client_id = any(%s)", [duplicates]
    ).fetchone()[0]
    assert flagged == 0


@pytest.mark.parametrize(
    "name, key",
    [
        ("Hanseatic Kapital AG", "hanseatic kapital"),
        ("HANSEATIC KAPITAL AG", "hanseatic kapital"),
        ("Hanseatic Kapital AG (Custody)", "hanseatic kapital"),
        ("Hanseatic Kapital - Securities Desk", "hanseatic kapital"),
        ("Vistula Securities S.A.", "vistula securities"),
        ("Pomerania Capital Sp. z o.o.", "pomerania capital"),
        ("Øresund Invest A/S", "øresund invest"),
    ],
)
def test_name_key(built, name, key):
    assert built.execute("select staging.name_key(%s)", [name]).fetchone()[0] == key
