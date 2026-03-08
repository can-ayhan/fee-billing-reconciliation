"""Generate the inputs a fee billing control works from.

Nothing here is real client data. The setting is a securities-services
provider that bills institutional clients monthly for settlement, custody, FX,
corporate actions and messaging, from a tiered fee schedule with negotiated
client overrides. The generator writes:

* clients.csv            client master, with duplicates and broken LEIs
* fee_schedule.csv       the published tariff, with the defects such tables collect
* client_overrides.csv   negotiated discounts and flat rates, with validity dates
* activity.csv           billable activity per client, service and day
* invoice_lines.csv      what the billing system charged per client, month and service

The invoices come from a simulated billing system that prices correctly most
of the time and then makes the mistakes a billing control is there to catch.
Every planted problem is recorded in planted_issues.json for the tests.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import random
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from feerecon.pricing import Override, Tier, active_override, client_fee, money, standard_fee

SEED = 2718
MONTHS = [dt.date(2025, m, 1) for m in range(1, 13)]

D = dt.date
SERVICES = {
    "SETTLE": "Settlement instruction",
    "CUSTODY": "Safekeeping (assets under custody)",
    "FXSPOT": "FX spot execution",
    "CORPACT": "Corporate action processing",
    "SWIFT": "Messaging and reporting",
}


def _tiers(service, basis, method, unit, start, end, bands) -> list[Tier]:
    return [Tier(service, basis, method, unit, Decimal(str(lo)), None if hi is None else Decimal(str(hi)),
                 Decimal(rate), start, end) for lo, hi, rate in bands]


def published_schedule() -> list[Tier]:
    """The tariff as it sits in the pricing table, defects included."""
    return [
        *_tiers("SETTLE", "count", "graduated", "per_item", D(2025, 1, 1), D(2025, 6, 30),
                [(0, 500, "1.80"), (500, 2000, "1.35"), (2000, 10000, "0.95"), (10000, None, "0.60")]),
        *_tiers("SETTLE", "count", "graduated", "per_item", D(2025, 7, 1), D(2025, 12, 31),
                [(0, 500, "1.90"), (500, 2000, "1.40"), (2000, 10000, "0.95"), (10000, None, "0.60")]),
        # next year's card was keyed in with the third band starting inside the second
        *_tiers("SETTLE", "count", "graduated", "per_item", D(2026, 1, 1), None,
                [(0, 500, "1.95"), (500, 2000, "1.45"), (1500, 10000, "1.00"), (10000, None, "0.60")]),
        *_tiers("CUSTODY", "amount", "slab", "bps_annual", D(2025, 1, 1), None,
                [(0, 250_000_000, "1.50"), (250_000_000, 1_000_000_000, "1.00"), (1_000_000_000, None, "0.60")]),
        *_tiers("FXSPOT", "amount", "graduated", "bps", D(2025, 1, 1), D(2025, 6, 30),
                [(0, 10_000_000, "2.00"), (10_000_000, 50_000_000, "1.20"), (50_000_000, None, "0.70")]),
        # the July repricing lost its middle band
        *_tiers("FXSPOT", "amount", "graduated", "bps", D(2025, 7, 1), None,
                [(0, 10_000_000, "1.80"), (50_000_000, None, "0.70")]),
        # the old rate ends in March, the new one starts in May
        *_tiers("CORPACT", "count", "graduated", "per_item", D(2025, 1, 1), D(2025, 3, 31), [(0, None, "25.00")]),
        *_tiers("CORPACT", "count", "graduated", "per_item", D(2025, 5, 1), None, [(0, None, "27.50")]),
        # the old rate was never end-dated when the October rate went in
        *_tiers("SWIFT", "count", "graduated", "per_item", D(2025, 1, 1), None, [(0, None, "0.35")]),
        *_tiers("SWIFT", "count", "graduated", "per_item", D(2025, 10, 1), None, [(0, None, "0.40")]),
    ]


def working_schedule() -> list[Tier]:
    """What the billing system actually applied where the published table is ambiguous."""
    fixed = []
    for t in published_schedule():
        if t.service_code == "CORPACT" and t.effective_to == D(2025, 3, 31):
            t = Tier(**{**t.__dict__, "effective_to": D(2025, 4, 30)})
        if t.service_code == "SWIFT" and t.effective_from == D(2025, 1, 1):
            t = Tier(**{**t.__dict__, "effective_to": D(2025, 9, 30)})
        fixed.append(t)
    fixed += _tiers("FXSPOT", "amount", "graduated", "bps", D(2025, 7, 1), None, [(10_000_000, 50_000_000, "1.20")])
    return fixed


# (name, country, segment)
CLIENTS = [
    ("Hanseatic Kapital AG", "DE", "BANK"), ("Rheingold Wertpapierhandel GmbH", "DE", "BROKER"),
    ("Elbufer Vermögensverwaltung GmbH", "DE", "ASSET_MANAGER"), ("Schwarzwald Pensionskasse VVaG", "DE", "PENSION"),
    ("Mainkai Privatbank AG", "DE", "BANK"), ("Isarwerk Asset Management GmbH", "DE", "ASSET_MANAGER"),
    ("Ostseehafen Broker GmbH", "DE", "BROKER"), ("Bodensee Kapitalanlage GmbH", "DE", "ASSET_MANAGER"),
    ("Vistula Securities S.A.", "PL", "BROKER"), ("Dom Maklerski Bursztyn S.A.", "PL", "BROKER"),
    ("Tatra Fundusze TFI S.A.", "PL", "ASSET_MANAGER"), ("Bank Odra S.A.", "PL", "BANK"),
    ("Mazovia Asset Management S.A.", "PL", "ASSET_MANAGER"), ("Pomerania Capital Sp. z o.o.", "PL", "FAMILY_OFFICE"),
    ("Wawel Emerytalny PTE S.A.", "PL", "PENSION"), ("Silesia Dom Inwestycyjny S.A.", "PL", "BROKER"),
    ("Zuiderzee Vermogensbeheer B.V.", "NL", "ASSET_MANAGER"), ("Waddenkust Effecten N.V.", "NL", "BROKER"),
    ("Polderland Pensioenfonds", "NL", "PENSION"), ("Grachtenhuis Capital B.V.", "NL", "FAMILY_OFFICE"),
    ("Brabant Effectenbank N.V.", "NL", "BANK"), ("Loire Gestion SAS", "FR", "ASSET_MANAGER"),
    ("Garonne Courtage SA", "FR", "BROKER"), ("Banque du Vercors SA", "FR", "BANK"),
    ("Camargue Patrimoine SAS", "FR", "FAMILY_OFFICE"), ("Saône Finance SAS", "FR", "ASSET_MANAGER"),
    ("Kirchberg Fund Services S.A.", "LU", "ASSET_MANAGER"), ("Meuse Capital S.A.", "LU", "FAMILY_OFFICE"),
    ("Liffey Fund Administration Ltd", "IE", "ASSET_MANAGER"), ("Shannon Asset Managers Ltd", "IE", "ASSET_MANAGER"),
    ("Burren Capital DAC", "IE", "FAMILY_OFFICE"), ("Alpenrhein Privatbank AG", "AT", "BANK"),
    ("Donauufer Wertpapier GmbH", "AT", "BROKER"), ("Carinthia Invest GmbH", "AT", "ASSET_MANAGER"),
    ("Aaretal Vermögen AG", "CH", "ASSET_MANAGER"), ("Lac Léman Gestion SA", "CH", "FAMILY_OFFICE"),
    ("Ticino Fiduciaria SA", "CH", "FAMILY_OFFICE"), ("Mälaren Kapitalförvaltning AB", "SE", "ASSET_MANAGER"),
    ("Skärgård Securities AB", "SE", "BROKER"), ("Øresund Invest A/S", "DK", "ASSET_MANAGER"),
    ("Limfjord Pension A/S", "DK", "PENSION"), ("Vltava Investiční a.s.", "CZ", "ASSET_MANAGER"),
    ("Moravia Makléř a.s.", "CZ", "BROKER"), ("Lagoon Wealth SGR S.p.A.", "IT", "ASSET_MANAGER"),
    ("Appennino Titoli SIM S.p.A.", "IT", "BROKER"), ("Banca del Tirreno S.p.A.", "IT", "BANK"),
    ("Meseta Gestión SGIIC S.A.", "ES", "ASSET_MANAGER"), ("Cantábrico Valores S.V. S.A.", "ES", "BROKER"),
    ("Scheldt Asset Management NV", "BE", "ASSET_MANAGER"), ("Ardennes Courtage SA", "BE", "BROKER"),
    ("Saimaa Varainhoito Oy", "FI", "ASSET_MANAGER"), ("Hardanger Forvaltning AS", "NO", "ASSET_MANAGER"),
    ("Tejo Gestão de Activos S.A.", "PT", "ASSET_MANAGER"), ("Balaton Befektetési Zrt.", "HU", "ASSET_MANAGER"),
    ("Nemunas Kapitalas UAB", "LT", "FAMILY_OFFICE"), ("Peipsi Varahaldus AS", "EE", "FAMILY_OFFICE"),
    ("Daugava Brokeri AS", "LV", "BROKER"), ("Dunaj Asset Management a.s.", "SK", "ASSET_MANAGER"),
    ("Thames Reach Securities Ltd", "GB", "BROKER"), ("Pennine Pension Trustees Ltd", "GB", "PENSION"),
]

# segment -> service -> (monthly level low, high, share of clients that use it)
PROFILES = {
    "BROKER": {"SETTLE": (2500, 16000, 1.0), "FXSPOT": (4e6, 60e6, 0.8), "CUSTODY": (40e6, 600e6, 1.0), "CORPACT": (1, 10, 0.7)},
    "BANK": {"SETTLE": (600, 6000, 1.0), "FXSPOT": (15e6, 220e6, 1.0), "CUSTODY": (100e6, 2.5e9, 1.0), "CORPACT": (2, 15, 0.8)},
    "ASSET_MANAGER": {"SETTLE": (150, 2500, 1.0), "FXSPOT": (1e6, 35e6, 0.8), "CUSTODY": (300e6, 6e9, 1.0), "CORPACT": (5, 45, 1.0)},
    "PENSION": {"SETTLE": (20, 250, 1.0), "FXSPOT": (0.5e6, 8e6, 0.5), "CUSTODY": (500e6, 9e9, 1.0), "CORPACT": (4, 40, 1.0)},
    "FAMILY_OFFICE": {"SETTLE": (10, 150, 1.0), "FXSPOT": (0.2e6, 6e6, 0.6), "CUSTODY": (20e6, 350e6, 1.0), "CORPACT": (1, 6, 0.6)},
}
BASIS = {"SETTLE": "count", "CUSTODY": "amount", "FXSPOT": "amount", "CORPACT": "count", "SWIFT": "count"}


@dataclass
class Entity:
    client_id: str
    name: str
    country: str
    segment: str
    lei: str
    onboarded: dt.date
    levels: dict = field(default_factory=dict)      # service -> typical monthly level
    duplicate_id: str | None = None                 # second master record for the same legal entity
    duplicate_share: float = 0.0                    # part of the activity booked under the duplicate


@dataclass
class Planted:
    duplicate_clients_by_lei: list = field(default_factory=list)     # [duplicate id, surviving id]
    duplicate_clients_by_name: list = field(default_factory=list)
    lei_check_digit: list = field(default_factory=list)
    lei_format: list = field(default_factory=list)
    lei_missing: list = field(default_factory=list)
    schedule: list = field(default_factory=list)                     # [issue, service, effective_from]
    expired_discount: list = field(default_factory=list)             # [client, month, service]
    override_not_applied: list = field(default_factory=list)
    old_rate_card: list = field(default_factory=list)
    slab_instead_of_graduated: list = field(default_factory=list)
    volume_mismatch: list = field(default_factory=list)
    not_billed: list = field(default_factory=list)
    no_activity: list = field(default_factory=list)
    unexplained: list = field(default_factory=list)
    rounding: list = field(default_factory=list)


# --------------------------------------------------------------------------
# LEI
# --------------------------------------------------------------------------

LEI_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def lei_check_digits(first18: str) -> str:
    """ISO 17442: two check digits so that the whole code is 1 modulo 97 (ISO 7064 MOD 97-10)."""
    number = int("".join(str(LEI_ALPHABET.index(c)) for c in first18 + "00"))
    return f"{98 - number % 97:02d}"


def lei_is_valid(lei: str) -> bool:
    if len(lei) != 20 or any(c not in LEI_ALPHABET for c in lei) or not lei[18:].isdigit():
        return False
    return int("".join(str(LEI_ALPHABET.index(c)) for c in lei)) % 97 == 1


def make_lei(rng: random.Random) -> str:
    # "ZZ99" is not an issuer prefix in use, so these cannot collide with a real LEI
    body = "ZZ9900" + "".join(rng.choice(LEI_ALPHABET) for _ in range(12))
    return body + lei_check_digits(body)


# --------------------------------------------------------------------------
# master data
# --------------------------------------------------------------------------

def _entities(rng: random.Random) -> list[Entity]:
    entities = []
    for n, (name, country, segment) in enumerate(CLIENTS):
        entity = Entity(f"C{10001 + n * 7 + rng.randint(0, 5)}", name, country, segment, make_lei(rng),
                        D(rng.randint(2015, 2023), rng.randint(1, 12), rng.randint(1, 28)))
        for service, (low, high, share) in PROFILES[segment].items():
            if rng.random() < share:
                entity.levels[service] = low * (high / low) ** rng.random()   # log-uniform
        entity.levels["SWIFT"] = entity.levels["SETTLE"] * rng.uniform(1.5, 3.0)
        entities.append(entity)
    return entities


def _name_variant(rng: random.Random, name: str) -> str:
    variants = [name.upper(), name.replace(" S.A.", " SA").replace(" GmbH", " Gmbh").replace(" Ltd", " Limited"),
                name + " (Custody)", name.replace(" AG", "").replace(" N.V.", "") + " - Securities Desk"]
    options = [v for v in variants if v != name]
    return rng.choice(options)


def _client_rows(rng: random.Random, entities: list[Entity], planted: Planted) -> tuple[list[dict], list[dict]]:
    rows = {e.client_id: {"client_id": e.client_id, "client_name": e.name, "lei": e.lei, "country": e.country,
                          "segment": e.segment, "onboarded_on": e.onboarded.isoformat(), "status": "ACTIVE"}
            for e in entities}
    decisions = []
    next_id = 10900

    # busy clients are the ones that end up onboarded twice (second desk, second mandate)
    busy = sorted(entities, key=lambda e: -e.levels["SETTLE"])
    for entity in rng.sample(busy[:25], 6):
        next_id += rng.randint(3, 40)
        entity.duplicate_id = f"C{next_id}"
        entity.duplicate_share = rng.uniform(0.25, 0.45)
        duplicate = dict(rows[entity.client_id], client_id=entity.duplicate_id,
                         client_name=_name_variant(rng, entity.name),
                         onboarded_on=(entity.onboarded + dt.timedelta(days=rng.randint(200, 1500))).isoformat())
        rows[entity.duplicate_id] = duplicate

    with_duplicate = [e for e in entities if e.duplicate_id]
    for entity in with_duplicate[:5]:
        style = rng.choice(["lower", "spaced", "same"])
        lei = entity.lei
        rows[entity.duplicate_id]["lei"] = {"lower": lei.lower(), "same": lei,
                                             "spaced": " ".join(lei[i:i + 4] for i in range(0, 20, 4))}[style]
        planted.duplicate_clients_by_lei.append([entity.duplicate_id, entity.client_id])
    by_name = with_duplicate[5]
    rows[by_name.duplicate_id]["lei"] = ""            # no LEI on the second record: only the name gives it away
    rows[by_name.duplicate_id]["client_name"] = by_name.name.upper()
    planted.duplicate_clients_by_name.append([by_name.duplicate_id, by_name.client_id])
    planted.lei_missing.append(by_name.duplicate_id)
    decisions.append({"duplicate_client_id": by_name.duplicate_id, "surviving_client_id": by_name.client_id,
                      "decided_on": "2026-01-14", "note": "Same legal entity, confirmed with relationship manager"})

    clean = [e for e in entities if not e.duplicate_id]
    picks = rng.sample(clean, 6)
    for entity in picks[:3]:                           # two characters swapped when the LEI was typed in
        lei = entity.lei
        i = rng.randint(6, 16)
        while lei[i] == lei[i + 1]:
            i = rng.randint(6, 16)
        rows[entity.client_id]["lei"] = lei[:i] + lei[i + 1] + lei[i] + lei[i + 2:]
        planted.lei_check_digit.append(entity.client_id)
    rows[picks[3].client_id]["lei"] = picks[3].lei[:19]
    planted.lei_format.append(picks[3].client_id)
    for entity in picks[4:]:
        rows[entity.client_id]["lei"] = ""
        planted.lei_missing.append(entity.client_id)

    ordered = sorted(rows.values(), key=lambda r: r["client_id"])
    return ordered, decisions


def _overrides(rng: random.Random, entities: list[Entity]) -> list[Override]:
    clean = [e for e in entities if not e.duplicate_id]
    picks = rng.sample(clean, 15)
    overrides = []
    for entity in picks[:3]:      # volume discount that ran out mid-year
        overrides.append(Override(entity.client_id, "SETTLE", "discount_pct", Decimal(rng.choice(["10", "12.5", "15"])),
                                  D(2024, 1, 1), D(2025, 6, 30)))
    for entity in picks[3:5]:
        overrides.append(Override(entity.client_id, "SETTLE", "discount_pct", Decimal(rng.choice(["7.5", "10"])),
                                  D(2024, 4, 1), D(2025, 8, 31)))
    for entity in picks[5:8]:     # open-ended discounts
        overrides.append(Override(entity.client_id, "SETTLE", "discount_pct", Decimal(rng.choice(["5", "10", "20"])),
                                  D(2024, 7, 1), None))
    for entity in picks[8:11]:    # negotiated flat custody rate
        overrides.append(Override(entity.client_id, "CUSTODY", "flat_rate", Decimal(rng.choice(["0.70", "0.80", "0.90"])),
                                  D(2024, 7, 1), None))
    for entity in picks[11:13]:
        overrides.append(Override(entity.client_id, "SETTLE", "flat_rate", Decimal(rng.choice(["0.85", "0.90"])),
                                  D(2025, 3, 1), None))
    for entity in picks[13:]:
        if "FXSPOT" in entity.levels:
            overrides.append(Override(entity.client_id, "FXSPOT", "discount_pct", Decimal("10"), D(2025, 1, 1), None))
        else:
            overrides.append(Override(entity.client_id, "SWIFT", "discount_pct", Decimal("25"), D(2025, 1, 1), None))
    return overrides


# --------------------------------------------------------------------------
# activity
# --------------------------------------------------------------------------

def _business_days(month: dt.date) -> list[dt.date]:
    days, day = [], month
    while day.month == month.month:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


def _split(rng: random.Random, total: int, parts: int) -> list[int]:
    weights = [rng.uniform(0.4, 1.6) for _ in range(parts)]
    scale = total / sum(weights)
    amounts = [int(w * scale) for w in weights]
    amounts[-1] += total - sum(amounts)
    return amounts


def _activity(rng: random.Random, entities: list[Entity]) -> list[dict]:
    """One row per client, service and day. `quantity` counts items, `notional_eur` is the value."""
    rows = []

    def add(entity: Entity, day: dt.date, service: str, quantity: int, notional: float) -> None:
        if quantity <= 0 and notional <= 0:
            return
        client = entity.client_id
        if entity.duplicate_id and rng.random() < entity.duplicate_share:
            client = entity.duplicate_id
        rows.append({"activity_date": day, "client_id": client, "service_code": service,
                     "quantity": quantity, "notional_eur": round(notional, 2)})

    for entity in entities:
        auc = entity.levels["CUSTODY"]
        for month in MONTHS:
            days = _business_days(month)
            drift = 1 + 0.015 * (month.month - 6.5) / 6          # a little growth over the year

            settle = int(entity.levels["SETTLE"] * drift * rng.lognormvariate(0, 0.18))
            settle_days = sorted(rng.sample(days, rng.randint(8, 16)))
            for day, count in zip(settle_days, _split(rng, settle, len(settle_days))):
                add(entity, day, "SETTLE", count, count * rng.uniform(40_000, 400_000))

            if "FXSPOT" in entity.levels:
                notional = entity.levels["FXSPOT"] * rng.lognormvariate(0, 0.35)
                deal_days = sorted(rng.sample(days, rng.randint(3, 9)))
                for day, part in zip(deal_days, _split(rng, int(notional * 100), len(deal_days))):
                    add(entity, day, "FXSPOT", rng.randint(1, 6), part / 100)

            messages = int(entity.levels["SWIFT"] * drift * rng.lognormvariate(0, 0.15))
            fridays = [d for d in days if d.weekday() == 4]
            for day, count in zip(fridays, _split(rng, messages, len(fridays))):
                add(entity, day, "SWIFT", count, 0)

            if "CORPACT" in entity.levels:
                events = max(0, int(rng.gauss(entity.levels["CORPACT"], entity.levels["CORPACT"] ** 0.5)))
                while events > 0:
                    batch = min(events, rng.randint(1, 3))
                    add(entity, rng.choice(days), "CORPACT", batch, 0)
                    events -= batch

            auc *= rng.lognormvariate(0.003, 0.03)                # month-end assets under custody
            if entity.duplicate_id:                               # two safekeeping accounts, one per record
                first = auc * (1 - entity.duplicate_share)
                rows.append({"activity_date": days[-1], "client_id": entity.client_id, "service_code": "CUSTODY",
                             "quantity": 1, "notional_eur": round(first, 2)})
                rows.append({"activity_date": days[-1], "client_id": entity.duplicate_id, "service_code": "CUSTODY",
                             "quantity": 1, "notional_eur": round(auc - first, 2)})
            else:
                rows.append({"activity_date": days[-1], "client_id": entity.client_id, "service_code": "CUSTODY",
                             "quantity": 1, "notional_eur": round(auc, 2)})

    rows.sort(key=lambda r: (r["activity_date"], r["client_id"], r["service_code"]))
    for n, row in enumerate(rows, start=1):
        row["activity_id"] = f"A{n:07d}"
        row["activity_date"] = row["activity_date"].isoformat()
    return rows


def monthly_measures(activity: list[dict]) -> dict[tuple[str, dt.date, str], Decimal]:
    """Billing measure per client, month and service: item count or summed value, by the service's basis."""
    totals: dict[tuple[str, dt.date, str], Decimal] = {}
    for row in activity:
        month = dt.date.fromisoformat(row["activity_date"]).replace(day=1)
        key = (row["client_id"], month, row["service_code"])
        value = Decimal(str(row["notional_eur"])) if BASIS[row["service_code"]] == "amount" else Decimal(row["quantity"])
        totals[key] = totals.get(key, Decimal(0)) + value
    return totals


# --------------------------------------------------------------------------
# invoices
# --------------------------------------------------------------------------

def _invoice_lines(rng: random.Random, entities: list[Entity], overrides: list[Override], activity: list[dict],
                   planted: Planted) -> list[dict]:
    published, working = published_schedule(), working_schedule()
    measures = monthly_measures(activity)
    merged_ids = {e.client_id for e in entities if e.duplicate_id} | {e.duplicate_id for e in entities if e.duplicate_id}

    lines = {key: {"measure": measure, "amount": client_fee(working, overrides, key[0], key[2], key[1], measure)}
             for key, measure in measures.items()}

    def priceable(key) -> bool:
        return standard_fee(published, key[2], key[1], lines[key]["measure"]) is not None

    def has_override(key, mode="active") -> bool:
        return active_override(overrides, key[0], key[2], key[1], mode) is not None

    free = [k for k in sorted(lines) if k[0] not in merged_ids and priceable(k)]
    used: set = set()

    def take(candidates: list, count: int) -> list:
        chosen = rng.sample([c for c in candidates if c not in used], count)
        used.update(chosen)
        return chosen

    def label(key) -> list:
        return [key[0], key[1].strftime("%Y-%m"), key[2]]

    # discount kept running after it expired
    expired = [k for k in free if not has_override(k) and has_override(k, "ignore_end")]
    for key in take(expired, min(8, len(expired))):
        lines[key]["amount"] = client_fee(working, overrides, key[0], key[2], key[1], lines[key]["measure"], "ignore_end")
        planted.expired_discount.append(label(key))

    # negotiated terms missing from the billing setup for a month
    for key in take([k for k in free if has_override(k)], 8):
        lines[key]["amount"] = client_fee(working, overrides, key[0], key[2], key[1], lines[key]["measure"], "none")
        planted.override_not_applied.append(label(key))

    # July rate change not picked up straight away
    plain = [k for k in free if not has_override(k, "ignore_end")]
    for key in take([k for k in plain if k[2] == "SETTLE" and k[1].month in (7, 8, 9)], 10):
        lines[key]["amount"] = client_fee(working, overrides, key[0], key[2], key[1], lines[key]["measure"],
                                          version_offset=-1)
        planted.old_rate_card.append(label(key))

    # whole volume charged at the rate of the band reached
    banded = [k for k in plain if (k[2] == "SETTLE" and lines[k]["measure"] > 500)
              or (k[2] == "FXSPOT" and k[1].month <= 6 and lines[k]["measure"] > 10_000_000)]
    for key in take(banded, 6):
        lines[key]["amount"] = client_fee(working, overrides, key[0], key[2], key[1], lines[key]["measure"],
                                          force_slab=True)
        planted.slab_instead_of_graduated.append(label(key))

    # activity booked after the billing cut-off, or counted twice
    for key in take([k for k in plain if k[2] in ("SETTLE", "SWIFT", "CORPACT") and lines[k]["measure"] >= 20], 12):
        factor = rng.choice([rng.uniform(0.85, 0.97), rng.uniform(1.04, 1.10)])
        billed = Decimal(max(1, round(float(lines[key]["measure"]) * factor)))
        if billed == lines[key]["measure"]:
            billed += 1
        lines[key]["billed_measure"] = billed
        lines[key]["amount"] = client_fee(working, overrides, key[0], key[2], key[1], billed)
        planted.volume_mismatch.append(label(key))

    for key in take(plain, 4):    # manual adjustments nobody documented
        lines[key]["amount"] = money(lines[key]["amount"] * Decimal(str(rng.choice([0.93, 0.95, 1.04, 1.08]))))
        planted.unexplained.append(label(key))

    for key in take(plain, 30):   # cent differences from rounding per day instead of per month
        lines[key]["amount"] += Decimal(rng.choice(["-0.03", "-0.02", "-0.01", "0.01", "0.02", "0.03"]))
        planted.rounding.append(label(key))

    for key in take([k for k in plain if lines[k]["amount"] > 50], 5):
        del lines[key]
        planted.not_billed.append(label(key))

    quiet = [(e.client_id, m, "CORPACT") for e in entities if "CORPACT" not in e.levels and not e.duplicate_id
             for m in MONTHS if m.month not in (4,)]
    for key in rng.sample(quiet, 3):
        events = Decimal(rng.randint(1, 3))
        lines[key] = {"measure": events, "amount": client_fee(working, overrides, key[0], key[2], key[1], events)}
        planted.no_activity.append(label(key))

    rows = []
    for (client, month, service), line in sorted(lines.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2])):
        rows.append({
            "invoice_no": f"INV-{month:%Y%m}-{client[1:]}",
            "client_id": client,
            "billing_month": month.strftime("%Y-%m"),
            "service_code": service,
            "billed_quantity": f"{line.get('billed_measure', line['measure']):f}",
            "billed_amount": f"{line['amount']:.2f}",
            "currency": "EUR",
        })
    return rows


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------

def _write(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _number(value: Decimal | None) -> str:
    return "" if value is None else f"{value.normalize():f}"


def generate(out_dir: Path, seed: int = SEED) -> dict[str, int]:
    rng = random.Random(seed)
    planted = Planted()

    entities = _entities(rng)
    client_rows, decisions = _client_rows(rng, entities, planted)
    overrides = _overrides(rng, entities)
    activity = _activity(rng, entities)
    invoice_rows = _invoice_lines(rng, entities, overrides, activity, planted)

    planted.schedule = [
        ["TIER_OVERLAP", "SETTLE", "2026-01-01"],
        ["TIER_GAP", "FXSPOT", "2025-07-01"],
        ["DATE_GAP", "CORPACT", "2025-05-01"],
        ["DATE_OVERLAP", "SWIFT", "2025-10-01"],
    ]
    schedule_rows = [{
        "service_code": t.service_code, "service_name": SERVICES[t.service_code], "basis": t.basis,
        "band_method": t.band_method, "unit": t.unit, "tier_from": _number(t.tier_from),
        "tier_to": _number(t.tier_to), "rate": f"{t.rate:.2f}", "effective_from": t.effective_from.isoformat(),
        "effective_to": t.effective_to.isoformat() if t.effective_to else "",
    } for t in published_schedule()]
    override_rows = [{
        "client_id": o.client_id, "service_code": o.service_code, "override_type": o.override_type,
        "value": f"{o.value:f}", "effective_from": o.effective_from.isoformat(),
        "effective_to": o.effective_to.isoformat() if o.effective_to else "",
    } for o in sorted(overrides, key=lambda o: (o.client_id, o.service_code))]

    out_dir.mkdir(parents=True, exist_ok=True)
    _write(out_dir / "clients.csv", client_rows, list(client_rows[0]))
    _write(out_dir / "fee_schedule.csv", schedule_rows, list(schedule_rows[0]))
    _write(out_dir / "client_overrides.csv", override_rows, list(override_rows[0]))
    _write(out_dir / "activity.csv", activity,
           ["activity_id", "activity_date", "client_id", "service_code", "quantity", "notional_eur"])
    _write(out_dir / "invoice_lines.csv", invoice_rows, list(invoice_rows[0]))
    _write(out_dir / "client_merge_decisions.csv", decisions, list(decisions[0]))

    counts = {"clients.csv": len(client_rows), "fee_schedule.csv": len(schedule_rows),
              "client_overrides.csv": len(override_rows), "activity.csv": len(activity),
              "invoice_lines.csv": len(invoice_rows)}
    manifest = {"seed": seed, "rows": counts,
                "planted": {name: sorted(keys) for name, keys in vars(planted).items()}}
    (out_dir / "planted_issues.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    return counts
