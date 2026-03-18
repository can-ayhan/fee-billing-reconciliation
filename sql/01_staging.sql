-- Landing tables: every file as text, so a bad value never fails the load.

drop table if exists staging.raw_clients cascade;
create table staging.raw_clients (
    row_num integer not null,
    client_id text, client_name text, lei text, country text, segment text, onboarded_on text, status text
);

drop table if exists staging.raw_fee_schedule cascade;
create table staging.raw_fee_schedule (
    row_num integer not null,
    service_code text, service_name text, basis text, band_method text, unit text,
    tier_from text, tier_to text, rate text, effective_from text, effective_to text
);

drop table if exists staging.raw_client_overrides cascade;
create table staging.raw_client_overrides (
    row_num integer not null,
    client_id text, service_code text, override_type text, value text, effective_from text, effective_to text
);

drop table if exists staging.raw_activity cascade;
create table staging.raw_activity (
    row_num integer not null,
    activity_id text, activity_date text, client_id text, service_code text, quantity text, notional_eur text
);

drop table if exists staging.raw_invoice_lines cascade;
create table staging.raw_invoice_lines (
    row_num integer not null,
    invoice_no text, client_id text, billing_month text, service_code text,
    billed_quantity text, billed_amount text, currency text
);

-- Duplicate client records that somebody has confirmed by hand.
drop table if exists staging.raw_client_merge_decisions cascade;
create table staging.raw_client_merge_decisions (
    row_num integer not null,
    duplicate_client_id text, surviving_client_id text, decided_on text, note text
);
