-- Fee calculation.
--
--   pricing.standard_fee   what the published schedule charges for a monthly volume
--   pricing.client_fee     the same after the client's negotiated terms, rounded to cents
--
-- Both return NULL when the schedule cannot price the volume (no version or
-- more than one version in force, a gap or an overlap in the bands). NULL is a
-- finding, not zero: the caller must not treat it as "nothing to charge".
--
-- The optional arguments reprice under a different assumption. The
-- reconciliation uses them to test what the billing system might have done:
-- the previous rate card, slab instead of graduated bands, an override that
-- has expired or was not applied.

-- Negotiated terms keyed to the surviving client record.
drop table if exists pricing.client_override cascade;
create table pricing.client_override as
select
    x.client_key,
    o.service_code,
    o.override_type,
    o.value,
    o.effective_from,
    o.effective_to
from staging.client_overrides o
join core.client_xref x using (client_id);

create index on pricing.client_override (client_key, service_code);


create or replace function pricing.unit_factor(p_unit text) returns numeric
language sql immutable as $$
    select case p_unit
        when 'per_item'   then 1
        when 'bps'        then 0.0001
        when 'bps_annual' then 0.0001 / 12.0   -- annual basis points, charged monthly
    end::numeric
$$;


create or replace function pricing.standard_fee(
    p_service        text,
    p_month          date,                  -- first day of the billing month
    p_measure        numeric,               -- item count or value, by the service's basis
    p_version_offset integer default 0,     -- -1: price with the version before the one in force
    p_force_slab     boolean default false  -- price every unit at the rate of the band reached
) returns numeric
language plpgsql stable as $$
declare
    v_month_end date := (p_month + interval '1 month' - interval '1 day')::date;
    v_versions  date[];
    v_from      date;
    v_method    text;
    v_factor    numeric;
    v_fee       numeric;
    v_covered   numeric;
    v_bands     integer;
begin
    if p_measure is null then
        return null;
    end if;

    -- a version prices a month only if it is in force for the whole month
    select array_agg(distinct effective_from order by effective_from)
    into v_versions
    from pricing.fee_schedule
    where service_code = p_service
      and effective_from <= p_month
      and (effective_to is null or effective_to >= v_month_end);

    if v_versions is null or cardinality(v_versions) <> 1 then
        return null;                       -- nothing in force, or two versions disagree
    end if;
    v_from := v_versions[1];

    if p_version_offset = -1 then
        select max(effective_from) into v_from
        from pricing.fee_schedule
        where service_code = p_service and effective_from < v_versions[1];
        if v_from is null then
            return null;
        end if;
    end if;

    select band_method, pricing.unit_factor(unit) into v_method, v_factor
    from pricing.fee_schedule
    where service_code = p_service and effective_from = v_from
    limit 1;

    if p_measure = 0 then
        return 0;
    end if;

    if p_force_slab or v_method = 'slab' then
        select count(*), max(rate) into v_bands, v_fee
        from pricing.fee_schedule
        where service_code = p_service and effective_from = v_from
          and tier_from < p_measure and (tier_to is null or p_measure <= tier_to);
        if v_bands <> 1 then
            return null;                   -- the volume falls into a gap or into two bands
        end if;
        return v_fee * p_measure * v_factor;
    end if;

    -- graduated: each band charges its own rate for the part of the volume inside it
    select
        sum(rate * (least(p_measure, coalesce(tier_to, p_measure)) - tier_from)),
        sum(least(p_measure, coalesce(tier_to, p_measure)) - tier_from)
    into v_fee, v_covered
    from pricing.fee_schedule
    where service_code = p_service and effective_from = v_from
      and tier_from < p_measure;

    if v_covered is distinct from p_measure then
        return null;                       -- bands below the volume leave a gap or overlap
    end if;
    return v_fee * v_factor;
end $$;


create or replace function pricing.client_fee(
    p_client         text,
    p_service        text,
    p_month          date,
    p_measure        numeric,
    p_override_mode  text default 'active', -- active | ignore_end (expired terms still applied) | none
    p_version_offset integer default 0,
    p_force_slab     boolean default false
) returns numeric
language plpgsql stable as $$
declare
    v_month_end date := (p_month + interval '1 month' - interval '1 day')::date;
    v_type      text;
    v_value     numeric;
    v_fee       numeric;
begin
    if p_measure is null then
        return null;
    end if;

    if p_override_mode <> 'none' then
        select override_type, value into v_type, v_value
        from pricing.client_override
        where client_key = p_client and service_code = p_service
          and effective_from <= p_month
          and (p_override_mode = 'ignore_end' or effective_to is null or effective_to >= v_month_end)
        order by effective_from desc
        limit 1;
    end if;

    if v_type = 'flat_rate' then           -- replaces the bands altogether
        select round(p_measure * v_value * pricing.unit_factor(unit), 2) into v_fee
        from pricing.fee_schedule
        where service_code = p_service
        limit 1;
        return v_fee;
    end if;

    v_fee := pricing.standard_fee(p_service, p_month, p_measure, p_version_offset, p_force_slab);
    if v_type = 'discount_pct' then
        v_fee := v_fee * (1 - v_value / 100);
    end if;
    return round(v_fee, 2);
end $$;
