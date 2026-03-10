from __future__ import annotations

from feerecon.config import RAW_DIR
from feerecon.generate import generate, lei_check_digits, lei_is_valid

FILES = ["clients.csv", "fee_schedule.csv", "client_overrides.csv", "activity.csv", "invoice_lines.csv",
         "client_merge_decisions.csv", "planted_issues.json"]


def test_committed_inputs_are_reproducible(tmp_path):
    """data/raw must be exactly what the generator writes for the default seed."""
    generate(tmp_path)
    for name in FILES:
        assert (tmp_path / name).read_bytes() == (RAW_DIR / name).read_bytes(), name


def test_lei_check_digits():
    body = "ZZ9900ABCDEF123456"
    lei = body + lei_check_digits(body)

    assert lei_is_valid(lei)
    assert not lei_is_valid(lei[:6] + lei[7] + lei[6] + lei[8:])     # two characters swapped
    assert not lei_is_valid(lei[:-1] + str((int(lei[-1]) + 1) % 10))  # last digit off by one
    assert not lei_is_valid(lei[:19])
    assert not lei_is_valid(lei.lower())
