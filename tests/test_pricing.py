"""The Python fee calculation against hand-calculated cases."""

from __future__ import annotations

from decimal import Decimal

import pytest
from cases import CLIENT_CASES, OVERRIDES, STANDARD_CASES, as_decimal

from feerecon.generate import published_schedule
from feerecon.pricing import Override, client_fee, money, standard_fee

SCHEDULE = published_schedule()
CLIENT = "TEST"
TERMS = [Override(CLIENT, service, kind, Decimal(value), start, end) for service, kind, value, start, end in OVERRIDES]


@pytest.mark.parametrize("service, month, measure, kwargs, expected", STANDARD_CASES)
def test_standard_fee(service, month, measure, kwargs, expected):
    fee = standard_fee(SCHEDULE, service, month, Decimal(measure), **kwargs)

    assert (None if fee is None else money(fee)) == as_decimal(expected)


@pytest.mark.parametrize("service, month, measure, mode, expected", CLIENT_CASES)
def test_client_fee(service, month, measure, mode, expected):
    fee = client_fee(SCHEDULE, TERMS, CLIENT, service, month, Decimal(measure), override_mode=mode)

    assert fee == as_decimal(expected)


def test_terms_of_another_client_do_not_apply():
    assert client_fee(SCHEDULE, TERMS, "SOMEONE_ELSE", "SETTLE", STANDARD_CASES[0][1], Decimal(2500)) == Decimal("3400.00")
