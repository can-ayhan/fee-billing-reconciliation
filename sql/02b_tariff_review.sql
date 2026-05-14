-- Review queue for tariff rows extracted from fee schedule documents.
--
-- The table is the only thing in the pipeline that is not rebuilt from the
-- input files: it holds decisions made by people. Published rows are added to
-- pricing.fee_schedule on every build, after the schedule has been reloaded.

create table if not exists pricing.tariff_review (
    review_id      integer generated always as identity primary key,
    document       text    not null,
    service_code   text    not null,
    basis          text    not null,
    band_method    text    not null,
    unit           text    not null,
    tier_from      numeric not null,
    tier_to        numeric,
    rate           numeric not null,
    effective_from date    not null,
    effective_to   date,
    confidence     numeric not null,       -- the model's own estimate, 0 to 1
    source_quote   text    not null,       -- the words of the document the row was read from
    status         text    not null check (status in ('pending', 'approved', 'rejected', 'published')),
    reason         text,                   -- why the row was not approved automatically
    decided_by     text,                   -- 'auto' or the reviewer
    decided_at     timestamptz,
    queued_at      timestamptz not null default now()
);

create or replace function pricing.apply_published_tariffs() returns integer
language plpgsql as $$
declare
    v_rows integer;
begin
    insert into pricing.fee_schedule (
        row_num, service_code, service_name, basis, band_method, unit,
        tier_from, tier_to, rate, effective_from, effective_to
    )
    select
        -r.review_id,                      -- negative: did not come from the schedule file
        r.service_code,
        coalesce(s.service_name, r.service_code),
        r.basis, r.band_method, r.unit, r.tier_from, r.tier_to, r.rate, r.effective_from, r.effective_to
    from pricing.tariff_review r
    left join (
        select distinct on (service_code) service_code, service_name
        from pricing.fee_schedule
        order by service_code, effective_from desc
    ) s using (service_code)
    where r.status = 'published'
      and not exists (
          select 1 from pricing.fee_schedule f
          where f.service_code = r.service_code and f.effective_from = r.effective_from
      );
    get diagnostics v_rows = row_count;

    -- a published version replaces whatever was open-ended before it
    update pricing.fee_schedule f
    set effective_to = p.effective_from - 1
    from (
        select distinct service_code, effective_from
        from pricing.tariff_review
        where status = 'published'
    ) p
    where f.service_code = p.service_code
      and f.effective_from < p.effective_from
      and f.effective_to is null;

    return v_rows;
end $$;

select pricing.apply_published_tariffs();
