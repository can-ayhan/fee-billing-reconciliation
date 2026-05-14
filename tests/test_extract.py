"""Extraction loop and review queue, driven by a scripted stand-in for the model.

No API key is needed. The fake client replays canned tool calls, which is
enough to test what this code is responsible for: validating rows, sending
failures back, and keeping unreviewed rows out of the pricing tables.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

pytest.importorskip("pydantic")

from feerecon.config import ROOT, ConfigError  # noqa: E402
from feerecon.db import run_sql_file  # noqa: E402
from feerecon.extract import review  # noqa: E402
from feerecon.extract.agent import extract_tariff  # noqa: E402
from feerecon.extract.schema import TOOL, TOOL_NAME  # noqa: E402

DOCUMENT = (ROOT / "docs" / "tariffs" / "schedule_of_fees_2026-07.txt").read_text(encoding="utf-8")
SERVICES = [{"service_code": code, "service_name": code.title()}
            for code in ("SETTLE", "CUSTODY", "FXSPOT", "CORPACT", "SWIFT")]
START = "2026-07-01"


def row(service, basis, method, unit, tier_from, tier_to, rate, quote, confidence=0.97):
    return {"service_code": service, "basis": basis, "band_method": method, "unit": unit, "tier_from": tier_from,
            "tier_to": tier_to, "rate": rate, "effective_from": START, "effective_to": None,
            "confidence": confidence, "source_quote": quote}


def good_rows() -> list[dict]:
    return [
        row("SETTLE", "count", "graduated", "per_item", 0, 500, 1.95, "first 500 instructions EUR 1.95 each"),
        row("SETTLE", "count", "graduated", "per_item", 500, 2000, 1.45, "next 1,500 instructions (501 to 2,000) EUR 1.45 each"),
        row("SETTLE", "count", "graduated", "per_item", 2000, 10000, 1.00, "next 8,000 instructions (2,001 to 10,000) EUR 1.00 each"),
        row("SETTLE", "count", "graduated", "per_item", 10000, None, 0.60, "above 10,000 instructions EUR 0.60 each"),
        row("CUSTODY", "amount", "slab", "bps_annual", 0, 250_000_000, 1.50, "up to EUR 250 million 1.50 basis points per annum"),
        row("CUSTODY", "amount", "slab", "bps_annual", 250_000_000, 1_000_000_000, 1.00,
            "above EUR 250 million and up to EUR 1 billion 1.00 basis point per annum"),
        row("CUSTODY", "amount", "slab", "bps_annual", 1_000_000_000, None, 0.60, "above EUR 1 billion 0.60 basis points per annum"),
        row("FXSPOT", "amount", "graduated", "bps", 0, 10_000_000, 1.80, "first EUR 10 million 1.80 basis points"),
        row("FXSPOT", "amount", "graduated", "bps", 10_000_000, 50_000_000, 1.20,
            "next EUR 40 million (up to EUR 50 million) 1.20 basis points"),
        row("FXSPOT", "amount", "graduated", "bps", 50_000_000, None, 0.70, "above EUR 50 million 0.70 basis points"),
        row("CORPACT", "count", "graduated", "per_item", 0, None, 27.50, "EUR 27.50 per corporate action event processed."),
        # the document leaves a reduced rate open for large senders, so the model is unsure about this one
        row("SWIFT", "count", "graduated", "per_item", 0, None, 0.40, "EUR 0.40 per message.", confidence=0.6),
    ]


def completion(content=None, calls=(), finish_reason="stop"):
    message = SimpleNamespace(content=content, tool_calls=list(calls) or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


def tool_call(rows: list[dict], call_id: str = "call_1", notes: str = ""):
    arguments = json.dumps({"rows": rows, "notes": notes})
    call = SimpleNamespace(id=call_id, type="function", function=SimpleNamespace(name=TOOL_NAME, arguments=arguments))
    return completion(calls=[call], finish_reason="tool_calls")


def text_answer(text: str):
    return completion(content=text)


class FakeClient:
    """Returns scripted responses in order and keeps a copy of every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(copy.deepcopy(request))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def model_setting(monkeypatch):
    monkeypatch.setenv("FEERECON_MODEL", "scripted-model")


def last_feedback(client: FakeClient) -> dict:
    return client.requests[-1]["messages"][-1]


# extraction loop ---------------------------------------------------------------

def test_clean_answer_is_accepted_on_the_first_attempt():
    client = FakeClient(tool_call(good_rows(), notes="A reduced messaging rate may be agreed for large senders."))

    result = extract_tariff(DOCUMENT, SERVICES, client=client)

    assert result.accepted and result.attempts == 1
    assert len(result.rows) == 12
    assert result.rows[1].tier_from == Decimal(500) and result.rows[1].effective_from == dt.date(2026, 7, 1)
    request = client.requests[0]
    assert request["model"] == "scripted-model"
    assert request["tools"] == [TOOL]
    assert request["tool_choice"] == {"type": "function", "function": {"name": TOOL_NAME}}   # no other way to answer
    system, user = request["messages"]
    assert system["role"] == "system" and "SETTLE" in system["content"]
    assert DOCUMENT in user["content"]


def test_tool_schema_leaves_no_room_for_other_shapes():
    schema = TOOL["function"]["parameters"]
    item = schema["properties"]["rows"]["items"]

    assert TOOL["type"] == "function" and TOOL["function"]["name"] == TOOL_NAME
    assert schema["additionalProperties"] is False and item["additionalProperties"] is False
    assert set(item["required"]) == set(item["properties"])


def test_missing_band_is_sent_back_with_the_reason():
    incomplete = [r for r in good_rows() if not (r["service_code"] == "FXSPOT" and r["tier_from"] == 10_000_000)]
    client = FakeClient(tool_call(incomplete, "call_1"), tool_call(good_rows(), "call_2"))

    result = extract_tariff(DOCUMENT, SERVICES, client=client)

    assert result.accepted and result.attempts == 2
    feedback = last_feedback(client)
    assert feedback["role"] == "tool" and feedback["tool_call_id"] == "call_1"
    assert "FXSPOT from 2026-07-01: no band between 10000000 and 50000000" in feedback["content"]
    # the model's own call is replayed before the reply to it
    assert client.requests[-1]["messages"][-2]["tool_calls"][0]["id"] == "call_1"


def test_overlapping_bands_are_sent_back():
    rows = good_rows()
    rows[2]["tier_from"] = 1500           # third settlement band starts inside the second
    client = FakeClient(tool_call(rows), tool_call(good_rows(), "call_2"))

    result = extract_tariff(DOCUMENT, SERVICES, client=client)

    assert result.accepted
    assert "overlaps the band starting at 1500" in last_feedback(client)["content"]


def test_values_the_schema_cannot_catch_are_sent_back():
    rows = good_rows()
    rows[0]["rate"] = -1.95
    rows[4]["effective_from"] = "1 July 2026"
    client = FakeClient(tool_call(rows), tool_call(good_rows(), "call_2"))

    result = extract_tariff(DOCUMENT, SERVICES, client=client)

    feedback = last_feedback(client)["content"]
    assert result.accepted and result.attempts == 2
    assert "rows.0.rate" in feedback and "rows.4.effective_from" in feedback


def test_a_rate_that_is_not_in_the_document_is_never_accepted():
    rows = good_rows()
    rows[10]["rate"] = 22.00
    rows[10]["source_quote"] = "EUR 22.00 per corporate action event processed."
    client = FakeClient(*[tool_call(copy.deepcopy(rows), f"call_{n}") for n in range(3)])

    result = extract_tariff(DOCUMENT, SERVICES, client=client)

    assert not result.accepted and result.attempts == 3
    assert [p.code for p in result.problems] == ["QUOTE_NOT_FOUND"]
    assert "the quote is not in the document" in last_feedback(client)["content"]


def test_text_answer_is_asked_to_use_the_tool():
    client = FakeClient(text_answer("Here are the fees: ..."), tool_call(good_rows()))

    result = extract_tariff(DOCUMENT, SERVICES, client=client)

    assert result.accepted and result.attempts == 2
    assert TOOL_NAME in last_feedback(client)["content"]


def test_call_rejected_by_the_api_counts_as_a_failed_attempt():
    class ToolUseFailed(Exception):
        status_code = 400
        body = {"error": {"code": "tool_use_failed", "message": "tool call validation failed: rows is required"}}

    client = FakeClient(ToolUseFailed("400"), tool_call(good_rows()))

    result = extract_tariff(DOCUMENT, SERVICES, client=client)

    assert result.accepted and result.attempts == 2
    assert "rows is required" in last_feedback(client)["content"]


def test_other_api_errors_are_not_swallowed_and_the_model_setting_is_required(monkeypatch):
    class RateLimited(Exception):
        status_code = 429
        body = {"error": {"code": "rate_limit_exceeded"}}

    with pytest.raises(RateLimited):
        extract_tariff(DOCUMENT, SERVICES, client=FakeClient(RateLimited("429")))

    monkeypatch.setenv("FEERECON_MODEL", "")
    with pytest.raises(ConfigError, match="FEERECON_MODEL"):
        extract_tariff(DOCUMENT, SERVICES, client=FakeClient())


# review queue -------------------------------------------------------------------

DOC = "test-schedule.txt"


@pytest.fixture
def queue(built):
    """An extraction of the sample document in the queue, inside a transaction that is rolled back."""
    built.rollback()
    result = extract_tariff(DOCUMENT, review.known_services(built), client=FakeClient(tool_call(good_rows())))
    counts = review.queue(built, DOC, result)
    yield built, counts
    built.rollback()


def pending_ids(conn) -> list[int]:
    return [r[0] for r in review.listing(conn, DOC)]


def test_only_confident_rows_are_approved_automatically(queue):
    conn, counts = queue
    pending = review.listing(conn, DOC)

    assert counts == {"approved": 11, "pending": 1}
    assert [(r[3], r[2]) for r in pending] == [("SWIFT", "pending")]
    assert "confidence 0.60 is below 0.85" in pending[0][10]


def test_nothing_reaches_the_schedule_before_it_is_published(queue):
    conn, _ = queue
    in_schedule = conn.execute("select count(*) from pricing.fee_schedule where effective_from = %s", [START]).fetchone()[0]

    assert in_schedule == 0
    with pytest.raises(review.ReviewError, match="1 rows of test-schedule.txt are still waiting"):
        review.publish(conn, DOC)


def test_publishing_after_approval_extends_the_schedule(queue):
    conn, _ = queue
    assert review.decide(conn, pending_ids(conn), approve=True, decided_by="reviewer") == 1

    assert review.publish(conn, DOC) == 12

    fee = conn.execute("select pricing.client_fee('X', 'SETTLE', '2026-07-01', 2500)").fetchone()[0]
    assert fee == Decimal("3650.00")                     # 500*1.95 + 1500*1.45 + 500*1.00
    fx = conn.execute("select pricing.client_fee('X', 'FXSPOT', '2026-08-01', 30000000)").fetchone()[0]
    assert fx == Decimal("4200.00")                      # the new card has the band the old one lost
    still_open = conn.execute(
        "select count(*) from pricing.fee_schedule where effective_to is null and effective_from < %s", [START]
    ).fetchone()[0]
    assert still_open == 0                               # earlier versions were closed the day before


def test_published_rows_survive_a_rebuild_of_the_schedule(queue):
    conn, _ = queue
    review.decide(conn, pending_ids(conn), approve=True, decided_by="reviewer")
    review.publish(conn, DOC)

    run_sql_file(conn, "02_typed.sql")                   # schedule reloaded from the file
    run_sql_file(conn, "02b_tariff_review.sql")
    run_sql_file(conn, "03_schedule_validation.sql")

    versions = conn.execute("select count(distinct service_code) from pricing.fee_schedule where effective_from = %s",
                            [START]).fetchone()[0]
    new_findings = conn.execute("select count(*) from dq.schedule_issues where effective_from = %s", [START]).fetchone()[0]
    assert versions == 5 and new_findings == 0


def test_rejecting_one_band_blocks_the_whole_version(queue):
    conn, _ = queue
    review.decide(conn, pending_ids(conn), approve=True, decided_by="reviewer")
    conn.execute("""update pricing.tariff_review set status = 'rejected'
                    where document = %s and service_code = 'SETTLE' and tier_from = 500""", [DOC])

    with pytest.raises(review.ReviewError, match="SETTLE from 2026-07-01: no band between 500 and 2000"):
        review.publish(conn, DOC)


def test_a_version_that_already_exists_is_not_overwritten(queue):
    conn, _ = queue
    review.decide(conn, pending_ids(conn), approve=True, decided_by="reviewer")
    conn.execute("update pricing.tariff_review set effective_from = '2025-07-01' where document = %s", [DOC])

    with pytest.raises(review.ReviewError, match="already has a version for"):
        review.publish(conn, DOC)


def test_rows_that_failed_the_checks_wait_for_review(built):
    rows = good_rows()
    rows[10]["source_quote"] = "EUR 27.50 per event, as agreed"
    built.rollback()
    try:
        result = extract_tariff(DOCUMENT, review.known_services(built),
                                client=FakeClient(*[tool_call(copy.deepcopy(rows), f"c{n}") for n in range(3)]))
        counts = review.queue(built, DOC, result)
        waiting = {r[3]: r[10] for r in review.listing(built, DOC)}
    finally:
        built.rollback()

    assert counts == {"approved": 10, "pending": 2}
    assert "the quote is not in the document" in waiting["CORPACT"]
