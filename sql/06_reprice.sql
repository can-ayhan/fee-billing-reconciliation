-- Reprice: monthly billing measure per client and service, and the fee it should cost.

create or replace view pricing.v_service as
select distinct on (service_code)
    service_code, service_name, basis, band_method, unit
from pricing.fee_schedule
order by service_code, effective_from desc;

-- Measure per client record as it appears in the activity feed. Kept by record
-- (not yet by surviving client) because duplicate records were billed separately.
drop table if exists recon.activity_by_record cascade;
create table recon.activity_by_record as
select
    coalesce(x.client_key, a.client_id)     as client_key,
    a.client_id,
    a.billing_month,
    a.service_code,
    case s.basis when 'amount' then sum(a.notional_eur) else sum(a.quantity) end as measure,
    count(*)                                as activity_rows
from staging.activity a
join pricing.v_service s using (service_code)
left join core.client_xref x using (client_id)
group by coalesce(x.client_key, a.client_id), a.client_id, a.billing_month, a.service_code, s.basis;

-- One row per surviving client, month and service.
--   expected_amount  fee on the client's whole volume: what the contract says
--   split_amount     fee if every client record is banded on its own: what a billing
--                    system that does not know about the duplicates would charge
drop table if exists recon.repriced cascade;
create table recon.repriced as
select
    client_key,
    billing_month,
    service_code,
    sum(measure)                as measure,
    count(*)                    as client_records,
    sum(activity_rows)          as activity_rows,
    pricing.client_fee(client_key, service_code, billing_month, sum(measure)) as expected_amount,
    case
        when count(pricing.client_fee(client_key, service_code, billing_month, measure)) = count(*)
        then sum(pricing.client_fee(client_key, service_code, billing_month, measure))
    end                         as split_amount
from recon.activity_by_record
group by client_key, billing_month, service_code;

create unique index on recon.repriced (client_key, billing_month, service_code);
