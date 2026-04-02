"""The SQL fee functions: same hand-calculated cases, then every billed line against the Python reference."""

from __future__ import annotations

import csv
import datetime as dt
from decimal import Decimal

import pytest
from cases import CLIENT_CASES, OVERRIDES, STANDARD_CASES, as_decimal

from feerecon.config import RAW_DIR
from feerecon.generate import published_schedule
from feerecon.pricing import Override, client_fee

CLIENT = "TEST"


@pytest.mark.parametrize("service, month, measure, kwargs, expected", STANDARD_CASES)
def test_standard_fee(built, service, month, measure, kwargs, expected):
    fee = built.execute(
        "select round(pricing.standard_fee(%s, %s, %s, %s, %s), 2)",
        [service, month, measure, kwargs.get("version_offset", 0), kwargs.get("force_slab", False)],
    ).fetchone()[0]

    assert fee == as_decimal(expected)


@pytest.fixture
def test_client_terms(built):
    """Negotiated terms for a client that exists only inside this transaction."""
    built.rollback()
    for service, kind, value, start, end in OVERRIDES:
        built.execute("insert into pricing.client_override values (%s, %s, %s, %s, %s, %s)",
                      [CLIENT, service, kind, value, start, end])
    yield built
    built.rollback()


@pytest.mark.parametrize("service, month, measure, mode, expected", CLIENT_CASES)
def test_client_fee(test_client_terms, service, month, measure, mode, expected):
    fee = test_client_terms.execute(
        "select pricing.client_fee(%s, %s, %s, %s, %s)", [CLIENT, service, month, measure, mode]
    ).fetchone()[0]

    assert fee == as_decimal(expected)


def test_sql_and_python_agree_on_every_repriced_line(built):
    """Two implementations, written separately, have to give the same fee for all real volumes."""
    schedule = published_schedule()
    with (RAW_DIR / "client_overrides.csv").open(encoding="utf-8", newline="") as fh:
        terms = [Override(r["client_id"], r["service_code"], r["override_type"], Decimal(r["value"]),
                          dt.date.fromisoformat(r["effective_from"]),
                          dt.date.fromisoformat(r["effective_to"]) if r["effective_to"] else None)
                 for r in csv.DictReader(fh)]

    rows = built.execute(
        "select client_key, service_code, billing_month, measure, expected_amount from recon.repriced"
    ).fetchall()

    assert len(rows) > 3000
    differences = [
        (client, service, month, sql_fee, python_fee)
        for client, service, month, measure, sql_fee in rows
        if (python_fee := client_fee(schedule, terms, client, service, month, measure)) != sql_fee
    ]
    assert differences == []
