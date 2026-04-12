# Fee Billing Reconciliation

A billing control for tiered fee schedules. It reprices what each client should have been charged from the tariff and their activity, compares that with what the billing system invoiced, and explains every difference.

The setting is a securities-services provider billing institutional clients monthly for settlement, custody, FX, corporate actions and messaging. Fees depend on volume bands, the tariff changes during the year, and some clients have negotiated terms. The data is synthetic: a generator writes the inputs and a simulated billing system that makes specific, known mistakes.

On the generated year (3,247 client-month-service lines, EUR 10.5 million billed) the control finds EUR 109,769 over-billed and EUR 27,434 under-billed, and EUR 1.08 million that cannot be verified because the fee schedule itself is defective.

## How it works

```mermaid
flowchart LR
    F[fee_schedule.csv] --> V[validate schedule]
    C[clients.csv] --> M[clean client master]
    O[client_overrides.csv] --> P
    A[activity.csv] --> P
    V --> P[reprice in SQL]
    M --> P
    P --> R[reconcile and classify]
    I[invoice_lines.csv] --> R
```

| Step | Where | What happens |
|---|---|---|
| Land and type | `sql/01`, `sql/02` | Files are loaded as text and cast without failing on bad values |
| Validate the schedule | `sql/03` | Overlapping dates, date gaps, missing tiers, overlapping tiers |
| Clean the client master | `sql/04` | LEI check digits, duplicate records mapped to one surviving client |
| Reprice | `sql/05`, `sql/06` | Fee functions for tiers, volume bands and client overrides |
| Reconcile | `sql/07` | Billed against expected, with a cause for every difference |

## Inputs

| File | Rows | Content |
|---|---:|---|
| `clients.csv` | 66 | Client master with LEI, country, segment |
| `fee_schedule.csv` | 24 | Published tariff: one row per band of a schedule version |
| `client_overrides.csv` | 15 | Negotiated discounts and flat rates with validity dates |
| `activity.csv` | 19,901 | Billable activity per client, service and day for 2025 |
| `invoice_lines.csv` | 3,527 | What the billing system charged per client, month and service |

`data/raw/planted_issues.json` lists every problem the generator put in. The tests compare the control's findings against that list as sets, so a missed error and a false alarm both fail.

## Fee schedule validation

A schedule version is a service plus the date it comes into force. The checks look between versions (validity dates) and inside a version (bands).

| Finding | Service | Consequence |
|---|---|---|
| Date gap | Corporate actions | No rate in force in April 2025 |
| Date overlap | Messaging | Old rate never end-dated, two rates in force from October 2025 |
| Missing tier | FX | No band between EUR 10m and EUR 50m from July 2025 |
| Overlapping tiers | Settlement | 2026 card: third band starts inside the second |

Each finding carries the billing months it affects. Lines that fall into one are reported as "cannot be verified" instead of being priced with a guess: 355 lines, EUR 1.08 million billed.

## Client master

- **LEI check digits** are validated in SQL with ISO 7064 MOD 97-10: 3 records have check digits that do not match (typing errors), 1 has the wrong length, 3 have no LEI. An LEI written in lower case or with spaces is normalised, not flagged.
- **Duplicates**: records sharing a valid LEI are the same legal entity and are merged into the one onboarded first (5 records). Records that only share a name and country are listed as candidates and merged only when a decision is on file (1 record).

66 records become 60 clients. Activity and invoices of a merged record are attributed to the surviving client.

## Repricing

Two SQL functions do the pricing:

- `pricing.standard_fee(service, month, measure)` prices a monthly volume from the schedule version in force. **Graduated** bands charge each band at its own rate for the part of the volume inside it; **slab** bands charge the whole volume at the rate of the band reached. Rates are per item, in basis points, or in annual basis points charged monthly.
- `pricing.client_fee(client, service, month, measure)` applies the client's negotiated terms on top (a percentage discount, or a flat rate that replaces the bands) and rounds to cents.

A rate applies to a billing month only if it is in force for the whole month. When the schedule cannot price a volume (no version, two versions, a gap or overlap below the volume) the functions return NULL, which the reconciliation treats as a finding and never as zero.

The same arithmetic exists a second time in Python (`src/feerecon/pricing.py`). The tests run hand-calculated cases against both and compare the two on all 3,200 repriced lines.

## Reconciliation

Differences up to EUR 0.05 are rounding. Anything larger gets a cause, found by repricing under the mistakes a billing system typically makes and checking which variant reproduces the invoiced amount:

| Cause | Lines | Over-billed | Under-billed |
|---|---:|---:|---:|
| Duplicate client records, each banded on its own | 104 | 104,942.28 | 0.00 |
| Negotiated terms not applied | 8 | 3,050.45 | 8,626.38 |
| Slab instead of graduated bands | 6 | 0.00 | 8,650.00 |
| Activity not billed | 5 | 0.00 | 6,087.35 |
| Expired terms still applied | 8 | 0.00 | 2,058.69 |
| Invoice quantity differs from activity | 12 | 633.45 | 1,198.85 |
| Unexplained | 4 | 1,005.61 | 89.94 |
| Previous rate card | 10 | 0.00 | 723.10 |
| Billed without activity | 3 | 137.50 | 0.00 |

What no variant reproduces stays "unexplained" instead of being forced into a category.

The largest finding comes from the master data, not from pricing: a client that exists twice has its volume split over two records, so less of it reaches the cheaper bands.

## Run it

Requirements: Python 3.11+ and Docker.

```bash
docker compose up -d                      # PostgreSQL on port 5434
python -m venv .venv
.venv/Scripts/activate                    # source .venv/bin/activate on Linux and macOS
pip install -e ".[dev]"
cp .env.example .env

python -m feerecon run                    # load, validate, reprice, reconcile, print the findings
pytest                                    # needs the database
```

`python -m feerecon generate` rewrites the inputs. With the default seed it reproduces the committed files byte for byte.

## Layout

```
data/raw/          generated inputs and the list of planted issues
sql/               numbered scripts: staging, validation, client master, pricing, reconciliation
src/feerecon/      generator, loader, pipeline, console report
tests/             pytest suite, runs against the docker-compose database
```

## Next

- Excel workbook for the billing team: every discrepancy with its cause and amount
