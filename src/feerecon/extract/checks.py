"""Deterministic checks on extracted rows.

Same rules as sql/03_schedule_validation.sql, applied before anything reaches
the database: a version has to start at zero and its bands have to follow each
other without gaps or overlaps. On top of that every row must quote the
document, so a rate the model made up has nothing to point at.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

from feerecon.extract.schema import TariffRow


@dataclass(frozen=True)
class Problem:
    code: str
    service_code: str | None
    effective_from: dt.date | None
    message: str
    blocking: bool = True      # False: worth a look, but not a reason to send the rows back

    def concerns(self, row: TariffRow) -> bool:
        return self.service_code == row.service_code and self.effective_from in (None, row.effective_from)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def check_rows(rows: list[TariffRow], known_services: set[str], document: str | None = None) -> list[Problem]:
    """Return what is wrong with the rows. `document` enables the quote check."""
    problems: list[Problem] = []
    if not rows:
        return [Problem("NO_ROWS", None, None, "no rows were recorded")]

    text = _squash(document) if document is not None else None
    for row in rows:
        if row.service_code not in known_services:
            problems.append(Problem("UNKNOWN_SERVICE", row.service_code, row.effective_from,
                                    f"{row.service_code} is not a known service code"))
        if text is not None and _squash(row.source_quote) not in text:
            problems.append(Problem("QUOTE_NOT_FOUND", row.service_code, row.effective_from,
                                    f"{row.service_code} band from {row.tier_from}: the quote is not in the document"))

    versions: dict[tuple[str, dt.date], list[TariffRow]] = {}
    for row in rows:
        versions.setdefault(row.version, []).append(row)

    for (service, start), tiers in sorted(versions.items()):
        tiers.sort(key=lambda t: t.tier_from)
        label = f"{service} from {start}"

        def add(code: str, message: str, blocking: bool = True) -> None:
            problems.append(Problem(code, service, start, f"{label}: {message}", blocking))

        if len({(t.basis, t.band_method, t.unit, t.effective_to) for t in tiers}) > 1:
            add("INCONSISTENT_VERSION", "bands disagree on basis, band method, unit or end date")
        if tiers[0].tier_from != 0:
            add("FIRST_TIER_NOT_ZERO", f"first band starts at {tiers[0].tier_from} instead of 0")
        for lower, upper in zip(tiers, tiers[1:]):
            if lower.tier_to is None or lower.tier_to > upper.tier_from:
                end = "unlimited" if lower.tier_to is None else lower.tier_to
                add("TIER_OVERLAP", f"band from {lower.tier_from} to {end} overlaps the band starting at {upper.tier_from}")
            elif lower.tier_to < upper.tier_from:
                add("TIER_GAP", f"no band between {lower.tier_to} and {upper.tier_from}")
        if tiers[-1].tier_to is not None:
            add("LAST_TIER_CAPPED", f"last band ends at {tiers[-1].tier_to}, larger volumes have no price", blocking=False)

    return problems
