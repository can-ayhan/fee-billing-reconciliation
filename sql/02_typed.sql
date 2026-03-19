-- Typed copies of the inputs. Casts that fail give NULL instead of an error.

create or replace function staging.to_date(value text) returns date
language plpgsql immutable as $$
begin
    return nullif(btrim(value), '')::date;
exception when others then
    return null;
end $$;

create or replace function staging.to_numeric(value text) returns numeric
language plpgsql immutable as $$
begin
    return nullif(btrim(value), '')::numeric;
exception when others then
    return null;
end $$;

-- Name without case, punctuation, brackets and legal form: "HANSEATIC KAPITAL AG"
-- and "Hanseatic Kapital AG (Custody)" both become "hanseatic kapital".
create or replace function staging.name_key(value text) returns text
language sql immutable as $$
    select btrim(regexp_replace(
        regexp_replace(
            regexp_replace(
                replace(regexp_replace(lower(coalesce(value, '')), '\(.*?\)|\s-\s.*$', ' ', 'g'), '.', ''),
                '[^[:alnum:] ]', ' ', 'g'),
            '\m(ag|gmbh|sa|nv|bv|ltd|limited|plc|sas|spa|ab|as|oy|uab|zrt|dac|llp|vvag|sp z oo|a s)\M', ' ', 'g'),
        '\s+', ' ', 'g'))
$$;


drop table if exists staging.clients cascade;
create table staging.clients as
select
    row_num,
    btrim(client_id)                                                    as client_id,
    btrim(client_name)                                                  as client_name,
    nullif(btrim(lei), '')                                              as lei_as_entered,
    nullif(upper(regexp_replace(coalesce(lei, ''), '[^A-Za-z0-9]', '', 'g')), '') as lei,   -- spaces and case are not errors
    upper(btrim(country))                                               as country,
    btrim(segment)                                                      as segment,
    staging.to_date(onboarded_on)                                       as onboarded_on,
    btrim(status)                                                       as status,
    staging.name_key(client_name)                                       as name_key
from staging.raw_clients;

drop table if exists staging.activity cascade;
create table staging.activity as
select
    btrim(activity_id)                          as activity_id,
    staging.to_date(activity_date)              as activity_date,
    date_trunc('month', staging.to_date(activity_date))::date as billing_month,
    btrim(client_id)                            as client_id,
    btrim(service_code)                         as service_code,
    staging.to_numeric(quantity)                as quantity,
    staging.to_numeric(notional_eur)            as notional_eur
from staging.raw_activity;

create index on staging.activity (client_id, billing_month, service_code);

drop table if exists staging.invoice_lines cascade;
create table staging.invoice_lines as
select
    btrim(invoice_no)                               as invoice_no,
    btrim(client_id)                                as client_id,
    staging.to_date(btrim(billing_month) || '-01')  as billing_month,
    btrim(service_code)                             as service_code,
    staging.to_numeric(billed_quantity)             as billed_quantity,
    staging.to_numeric(billed_amount)               as billed_amount,
    btrim(currency)                                 as currency
from staging.raw_invoice_lines;

drop table if exists staging.client_overrides cascade;
create table staging.client_overrides as
select
    btrim(client_id)                    as client_id,
    btrim(service_code)                 as service_code,
    btrim(override_type)                as override_type,   -- discount_pct | flat_rate
    staging.to_numeric(value)           as value,
    staging.to_date(effective_from)     as effective_from,
    staging.to_date(effective_to)       as effective_to
from staging.raw_client_overrides;

-- The tariff. One row per band of a schedule version; a version is a service
-- plus the date it comes into force.
drop table if exists pricing.fee_schedule cascade;
create table pricing.fee_schedule as
select
    row_num,
    btrim(service_code)                 as service_code,
    btrim(service_name)                 as service_name,
    btrim(basis)                        as basis,          -- count | amount: what the bands measure
    btrim(band_method)                  as band_method,    -- graduated | slab
    btrim(unit)                         as unit,           -- per_item | bps | bps_annual
    staging.to_numeric(tier_from)       as tier_from,
    staging.to_numeric(tier_to)         as tier_to,        -- NULL = no upper limit
    staging.to_numeric(rate)            as rate,
    staging.to_date(effective_from)     as effective_from,
    staging.to_date(effective_to)       as effective_to    -- NULL = until further notice
from staging.raw_fee_schedule;

create index on pricing.fee_schedule (service_code, effective_from);
