-- Checks on the fee schedule before anything is priced from it.
--
-- A schedule version is (service, effective_from). Two kinds of defects:
--   between versions  overlapping validity dates, or a period no version covers
--   inside a version  bands that overlap, leave a gap, do not start at zero
--
-- affected_from / affected_to say which billing months a defect touches, so the
-- reconciliation can tell "invoice wrong" apart from "tariff cannot say".

drop table if exists dq.schedule_issues cascade;
create table dq.schedule_issues as
with versions as (
    select
        service_code,
        effective_from,
        max(effective_to) filter (where effective_to is not null)   as effective_to,
        bool_or(effective_to is null)                               as is_open_ended
    from pricing.fee_schedule
    group by service_code, effective_from
),
ordered_versions as (
    select
        v.*,
        case when is_open_ended then null else effective_to end as valid_to,
        lead(effective_from) over (partition by service_code order by effective_from) as next_from
    from versions v
),
tiers as (
    select
        f.*,
        lead(tier_from) over (partition by service_code, effective_from order by tier_from) as next_tier_from,
        row_number()    over (partition by service_code, effective_from order by tier_from) as tier_no
    from pricing.fee_schedule f
)

-- validity of an earlier version runs into the next one
select
    'DATE_OVERLAP'                      as issue_code,
    'error'                             as severity,
    service_code,
    next_from                           as effective_from,
    next_from                           as affected_from,
    valid_to                            as affected_to,        -- NULL: the overlap never ends
    format('version from %s is still valid (%s) when the version from %s starts',
           effective_from, coalesce('until ' || valid_to, 'no end date'), next_from) as detail
from ordered_versions
where next_from is not null and (valid_to is null or valid_to >= next_from)

union all
-- no version in force between two versions
select
    'DATE_GAP', 'error', service_code, next_from,
    valid_to + 1, next_from - 1,
    format('no rate in force from %s to %s', valid_to + 1, next_from - 1)
from ordered_versions
where next_from is not null and valid_to < next_from - 1

union all
-- a band ends before the next one starts: volumes in between have no rate
select
    'TIER_GAP', 'error', t.service_code, t.effective_from,
    t.effective_from, v.valid_to,
    format('no band between %s and %s', t.tier_to, t.next_tier_from)
from tiers t
join ordered_versions v using (service_code, effective_from)
where t.next_tier_from is not null and t.tier_to < t.next_tier_from

union all
-- a band reaches into the next one (or has no upper limit although another band follows)
select
    'TIER_OVERLAP', 'error', t.service_code, t.effective_from,
    t.effective_from, v.valid_to,
    format('band from %s to %s overlaps the band starting at %s',
           t.tier_from, coalesce(t.tier_to::text, 'unlimited'), t.next_tier_from)
from tiers t
join ordered_versions v using (service_code, effective_from)
where t.next_tier_from is not null and (t.tier_to is null or t.tier_to > t.next_tier_from)

union all
select
    'FIRST_TIER_NOT_ZERO', 'error', t.service_code, t.effective_from,
    t.effective_from, v.valid_to,
    format('first band starts at %s instead of 0', t.tier_from)
from tiers t
join ordered_versions v using (service_code, effective_from)
where t.tier_no = 1 and t.tier_from is distinct from 0

union all
select
    'RATE_INVALID', 'error', t.service_code, t.effective_from,
    t.effective_from, v.valid_to,
    format('band from %s has rate %s', t.tier_from, coalesce(t.rate::text, 'missing'))
from tiers t
join ordered_versions v using (service_code, effective_from)
where t.rate is null or t.rate <= 0

union all
-- not wrong, but worth knowing: above the last band there is no price
select
    'LAST_TIER_CAPPED', 'warning', t.service_code, t.effective_from,
    t.effective_from, v.valid_to,
    format('last band ends at %s, larger volumes cannot be priced', t.tier_to)
from tiers t
join ordered_versions v using (service_code, effective_from)
where t.next_tier_from is null and t.tier_to is not null;
