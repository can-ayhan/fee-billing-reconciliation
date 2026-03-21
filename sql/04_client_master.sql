-- Client master remediation: validate LEIs, find duplicate records, map every
-- client id that appears in activity or invoices to one surviving client.

-- ISO 17442: 18 alphanumeric characters plus two check digits. Replace each
-- letter by its number (A = 10 ... Z = 35) and the whole thing must be 1 mod 97.
create or replace function core.lei_is_valid(lei text) returns boolean
language sql immutable as $$
    select case
        when lei is null or lei !~ '^[A-Z0-9]{18}[0-9]{2}$' then false
        else (
            select mod(string_agg(
                           case when c between '0' and '9' then c else (ascii(c) - 55)::text end,
                           '' order by position)::numeric, 97) = 1
            from unnest(string_to_array(lei, null)) with ordinality as chars(c, position)
        )
    end
$$;

create or replace function core.lei_status(lei text) returns text
language sql immutable as $$
    select case
        when lei is null                          then 'missing'
        when lei !~ '^[A-Z0-9]{18}[0-9]{2}$'      then 'bad format'
        when not core.lei_is_valid(lei)           then 'bad check digits'
        else 'valid'
    end
$$;


-- Which record survives. Records that share a valid LEI are the same legal
-- entity: keep the one onboarded first. Records without a usable LEI are only
-- merged when somebody confirmed it (client_merge_decisions.csv).
drop table if exists core.client_xref cascade;
create table core.client_xref as
with by_lei as (
    select
        client_id,
        first_value(client_id) over (partition by lei order by onboarded_on, client_id) as lei_survivor,
        count(*) over (partition by lei)                                                as records_with_lei
    from staging.clients
    where core.lei_is_valid(lei)
),
decided as (
    select btrim(duplicate_client_id) as client_id, btrim(surviving_client_id) as decided_survivor
    from staging.raw_client_merge_decisions
),
first_pass as (
    select
        c.client_id,
        coalesce(d.decided_survivor, l.lei_survivor, c.client_id) as survivor,
        case
            when d.decided_survivor is not null                                 then 'manual decision'
            when l.lei_survivor is not null and l.lei_survivor <> c.client_id   then 'same LEI'
            else 'self'
        end as merge_reason
    from staging.clients c
    left join by_lei l using (client_id)
    left join decided d using (client_id)
)
-- a manual decision can point at a record that is itself merged by LEI
select
    f.client_id,
    coalesce(nullif(s.survivor, f.survivor), f.survivor) as client_key,
    f.merge_reason
from first_pass f
left join first_pass s on s.client_id = f.survivor;

create unique index on core.client_xref (client_id);

drop table if exists core.clients cascade;
create table core.clients as
select
    c.client_id                                 as client_key,
    c.client_name,
    case when core.lei_is_valid(c.lei) then c.lei end as lei,
    core.lei_status(c.lei)                      as lei_status,
    c.country,
    c.segment,
    c.onboarded_on,
    m.merged_ids,
    m.record_count
from staging.clients c
join (
    select client_key, count(*) as record_count,
           string_agg(client_id, ', ' order by client_id) filter (where client_id <> client_key) as merged_ids
    from core.client_xref
    group by client_key
) m on m.client_key = c.client_id;

create unique index on core.clients (client_key);


drop table if exists dq.client_issues cascade;
create table dq.client_issues as
select
    case core.lei_status(lei)
        when 'missing'          then 'LEI_MISSING'
        when 'bad format'       then 'LEI_FORMAT'
        when 'bad check digits' then 'LEI_CHECK_DIGIT'
    end                                         as issue_code,
    client_id,
    client_name,
    lei_as_entered                              as lei,
    case core.lei_status(lei)
        when 'missing'          then 'no LEI on the record'
        when 'bad format'       then format('%s characters, an LEI has 20 (18 alphanumeric + 2 digits)', length(lei))
        when 'bad check digits' then 'check digits do not match (ISO 7064 MOD 97-10), probably a typing error'
    end                                         as detail,
    null::text                                  as related_client_id
from staging.clients
where core.lei_status(lei) <> 'valid'

union all
select
    'DUPLICATE_LEI',
    x.client_id,
    c.client_name,
    c.lei_as_entered,
    format('same LEI as %s (%s), merged into it', x.client_key, s.client_name),
    x.client_key
from core.client_xref x
join staging.clients c using (client_id)
join staging.clients s on s.client_id = x.client_key
where x.merge_reason = 'same LEI'

union all
-- same name and country but no LEI to prove it: a candidate until somebody decides
select
    'DUPLICATE_NAME',
    a.client_id,
    a.client_name,
    a.lei_as_entered,
    format('same name and country as %s (%s): %s', b.client_id, b.client_name,
           case when x.merge_reason = 'manual decision' then 'merged by decision' else 'not decided, kept separate' end),
    b.client_id
from staging.clients a
join staging.clients b
  on b.name_key = a.name_key and b.country = a.country and b.client_id <> a.client_id
join core.client_xref x on x.client_id = a.client_id
join core.client_xref xb on xb.client_id = b.client_id
where not (core.lei_is_valid(a.lei) and core.lei_is_valid(b.lei))   -- LEI duplicates are reported above
  and (a.onboarded_on, a.client_id) > (b.onboarded_on, b.client_id);
