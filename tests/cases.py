"""Hand-calculated fees, run against both the Python and the SQL implementation.

All cases use the published schedule in data/raw/fee_schedule.csv:

  SETTLE   per item, graduated: 0-500 / 500-2,000 / 2,000-10,000 / above
           1.80 / 1.35 / 0.95 / 0.60 until June, 1.90 / 1.40 / 0.95 / 0.60 from July
  CUSTODY  annual bps on month-end assets, slab: 1.50 up to 250m, 1.00 up to 1bn, 0.60 above
  FXSPOT   bps on notional, graduated: 2.00 up to 10m, 1.20 up to 50m, 0.70 above (until June)
  CORPACT  25.00 per event until March, 27.50 from May
  SWIFT    0.35 per message, 0.40 from October (old rate never end-dated)
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

D = dt.date

# (service, month, measure, kwargs, expected fee or None when the schedule cannot price it)
STANDARD_CASES = [
    # graduated bands
    ("SETTLE", D(2025, 1, 1), 2500, {}, "3400.00"),          # 500*1.80 + 1500*1.35 + 500*0.95
    ("SETTLE", D(2025, 1, 1), 500, {}, "900.00"),            # exactly the first band
    ("SETTLE", D(2025, 1, 1), 12000, {}, "11725.00"),        # 900 + 2025 + 7600 + 2000*0.60
    ("SETTLE", D(2025, 1, 1), 0, {}, "0.00"),
    ("SETTLE", D(2025, 7, 1), 2500, {}, "3525.00"),          # July card: 500*1.90 + 1500*1.40 + 500*0.95
    # variants the reconciliation tests against
    ("SETTLE", D(2025, 7, 1), 2500, {"version_offset": -1}, "3400.00"),   # previous card
    ("SETTLE", D(2025, 1, 1), 2500, {"force_slab": True}, "2375.00"),     # all 2500 at 0.95
    ("SETTLE", D(2025, 1, 1), 2500, {"version_offset": -1}, None),        # there is no earlier card
    # slab, annual basis points charged monthly
    ("CUSTODY", D(2025, 3, 1), 300_000_000, {}, "2500.00"),  # 300m * 1.00 bps / 12
    ("CUSTODY", D(2025, 3, 1), 250_000_000, {}, "3125.00"),  # the boundary belongs to the lower band
    ("CUSTODY", D(2025, 3, 1), 2_000_000_000, {}, "10000.00"),
    # basis points, graduated
    ("FXSPOT", D(2025, 2, 1), 30_000_000, {}, "4400.00"),    # 10m * 2.00 bps + 20m * 1.20 bps
    # schedule defects
    ("FXSPOT", D(2025, 8, 1), 8_000_000, {}, "1440.00"),     # below the missing band: still priceable
    ("FXSPOT", D(2025, 8, 1), 30_000_000, {}, None),         # reaches the missing band
    ("FXSPOT", D(2025, 8, 1), 60_000_000, {}, None),
    ("CORPACT", D(2025, 3, 1), 4, {}, "100.00"),
    ("CORPACT", D(2025, 4, 1), 4, {}, None),                 # no rate in force in April
    ("CORPACT", D(2025, 5, 1), 4, {}, "110.00"),
    ("SWIFT", D(2025, 9, 1), 1000, {}, "350.00"),
    ("SWIFT", D(2025, 10, 1), 1000, {}, None),               # two rates in force
    ("SETTLE", D(2026, 1, 1), 1000, {}, "1700.00"),          # below the overlapping bands
    ("SETTLE", D(2026, 1, 1), 1800, {}, None),               # inside the overlap
]

# Negotiated terms for an imaginary client.
#   SETTLE   10 % discount until 30 June 2025
#   CUSTODY  flat 0.80 bps, open-ended
#   SWIFT    flat 0.30 per message from March 2025
OVERRIDES = [
    ("SETTLE", "discount_pct", "10", D(2024, 1, 1), D(2025, 6, 30)),
    ("CUSTODY", "flat_rate", "0.80", D(2024, 7, 1), None),
    ("SWIFT", "flat_rate", "0.30", D(2025, 3, 1), None),
]

# (service, month, measure, override mode, expected)
CLIENT_CASES = [
    ("SETTLE", D(2025, 6, 1), 2500, "active", "3060.00"),        # 3400 less 10 %
    ("SETTLE", D(2025, 7, 1), 2500, "active", "3525.00"),        # discount has ended
    ("SETTLE", D(2025, 7, 1), 2500, "ignore_end", "3172.50"),    # 3525 less the expired 10 %
    ("SETTLE", D(2025, 6, 1), 2500, "none", "3400.00"),
    ("CUSTODY", D(2025, 3, 1), 300_000_000, "active", "2000.00"),  # 300m * 0.80 bps / 12, bands ignored
    ("CUSTODY", D(2025, 3, 1), 300_000_000, "none", "2500.00"),
    ("SWIFT", D(2025, 2, 1), 1000, "active", "350.00"),          # flat rate not yet in force
    ("SWIFT", D(2025, 11, 1), 1000, "active", "300.00"),         # flat rate prices it although the schedule cannot
    ("SWIFT", D(2025, 11, 1), 1000, "none", None),
    ("FXSPOT", D(2025, 2, 1), 30_000_000, "active", "4400.00"),  # no terms for this service
]


def as_decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)
