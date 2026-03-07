# Fee Billing Reconciliation

A billing control for tiered fee schedules: reprice what each client should have been charged from the tariff and their activity, then compare it with what the billing system invoiced.

The setting is a securities-services provider billing institutional clients monthly for settlement, custody, FX, corporate actions and messaging. Fees depend on volume bands, the tariff changes during the year, and some clients have negotiated terms. That is enough moving parts for invoices to drift from the contract without anyone noticing.

## Plan

1. Synthetic inputs: client master, fee schedule, client overrides, daily activity, invoice lines
2. Validate the fee schedule: overlapping dates, missing tiers, gaps
3. Clean the client master: LEI check digits, duplicate records
4. Reprice in SQL from tiers, volume bands and overrides
5. Reconcile against the invoices and explain each difference
6. Exception workbook for the billing team

## Status

Work in progress.
