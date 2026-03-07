"""Fee calculation in plain Python.

The control itself reprices in SQL (sql/05_pricing_functions.sql). This module
is the same arithmetic written a second time: the generator uses it to produce
the invoices of the simulated billing system, and the tests use it to check
the SQL functions against an independent implementation.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

UNIT_FACTOR = {
    "per_item": Decimal(1),
    "bps": Decimal("0.0001"),
    "bps_annual": Decimal("0.0001") / 12,   # annual basis points, charged monthly
}


@dataclass(frozen=True)
class Tier:
    service_code: str
    basis: str            # count | amount: what the bands are measured in
    band_method: str      # graduated: each band at its own rate; slab: everything at the band reached
    unit: str             # per_item | bps | bps_annual
    tier_from: Decimal
    tier_to: Decimal | None
    rate: Decimal
    effective_from: dt.date
    effective_to: dt.date | None


@dataclass(frozen=True)
class Override:
    client_id: str
    service_code: str
    override_type: str    # discount_pct | flat_rate
    value: Decimal
    effective_from: dt.date
    effective_to: dt.date | None


def month_end(month: dt.date) -> dt.date:
    following = (month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return following - dt.timedelta(days=1)


def covers_month(start: dt.date, end: dt.date | None, month: dt.date) -> bool:
    """A rate applies to a billing month only if it is in force for the whole month."""
    return start <= month and (end is None or end >= month_end(month))


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def version_tiers(schedule: list[Tier], service: str, month: dt.date, version_offset: int = 0) -> list[Tier] | None:
    """Tiers of the schedule version in force for the month, or None if there is not exactly one."""
    versions = sorted({t.effective_from for t in schedule
                       if t.service_code == service and covers_month(t.effective_from, t.effective_to, month)})
    if len(versions) != 1:
        return None
    start = versions[0]
    if version_offset == -1:
        earlier = sorted({t.effective_from for t in schedule if t.service_code == service and t.effective_from < start})
        if not earlier:
            return None
        start = earlier[-1]
    return sorted((t for t in schedule if t.service_code == service and t.effective_from == start),
                  key=lambda t: t.tier_from)


def standard_fee(schedule: list[Tier], service: str, month: dt.date, measure: Decimal,
                 version_offset: int = 0, force_slab: bool = False) -> Decimal | None:
    """Fee from the standard schedule, unrounded. None when the schedule cannot price the measure."""
    tiers = version_tiers(schedule, service, month, version_offset)
    if not tiers:
        return None
    if measure == 0:
        return Decimal(0)
    factor = UNIT_FACTOR[tiers[0].unit]
    if force_slab or tiers[0].band_method == "slab":
        hit = [t for t in tiers if t.tier_from < measure and (t.tier_to is None or measure <= t.tier_to)]
        return hit[0].rate * measure * factor if len(hit) == 1 else None
    fee = covered = Decimal(0)
    for t in tiers:
        if t.tier_from < measure:
            portion = min(measure, t.tier_to if t.tier_to is not None else measure) - t.tier_from
            fee += t.rate * portion
            covered += portion
    return fee * factor if covered == measure else None   # a gap or an overlap below the measure


def active_override(overrides: list[Override], client: str, service: str, month: dt.date,
                    mode: str = "active") -> Override | None:
    if mode == "none":
        return None
    candidates = [o for o in overrides
                  if o.client_id == client and o.service_code == service and o.effective_from <= month
                  and (mode == "ignore_end" or o.effective_to is None or o.effective_to >= month_end(month))]
    return max(candidates, key=lambda o: o.effective_from) if candidates else None


def client_fee(schedule: list[Tier], overrides: list[Override], client: str, service: str, month: dt.date,
               measure: Decimal, override_mode: str = "active", version_offset: int = 0,
               force_slab: bool = False) -> Decimal | None:
    """What the client should be charged for the month, rounded to cents."""
    override = active_override(overrides, client, service, month, override_mode)
    if override is not None and override.override_type == "flat_rate":
        unit = next((t.unit for t in schedule if t.service_code == service), None)
        return money(measure * override.value * UNIT_FACTOR[unit]) if unit else None
    fee = standard_fee(schedule, service, month, measure, version_offset, force_slab)
    if fee is None:
        return None
    if override is not None and override.override_type == "discount_pct":
        fee *= 1 - override.value / 100
    return money(fee)
