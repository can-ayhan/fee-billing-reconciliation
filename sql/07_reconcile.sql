-- Reconcile repriced fees against invoice lines and say why they differ.
--
-- Grain: surviving client, billing month, service.
-- difference = billed - expected, so positive means the client was over-billed.
--
-- A difference above the tolerance gets a cause. The cause is found by
-- repricing under the mistakes a billing system typically makes and checking
-- which variant reproduces the invoiced amount. What no variant reproduces is
-- left as UNEXPLAINED instead of being forced into a category.

drop table if exists recon.billed cascade;
create table recon.billed as
select
    coalesce(x.client_key, i.client_id)                         as client_key,
    i.billing_month,
    i.service_code,
    sum(i.billed_quantity)                                      as billed_quantity,
    sum(i.billed_amount)                                        as billed_amount,
    count(distinct i.client_id)                                 as billed_records,
    string_agg(distinct i.client_id, ', ' order by i.client_id) as billed_client_ids,
    string_agg(distinct i.invoice_no, ', ' order by i.invoice_no) as invoice_nos
from staging.invoice_lines i
left join core.client_xref x using (client_id)
group by coalesce(x.client_key, i.client_id), i.billing_month, i.service_code;


drop table if exists recon.lines cascade;
create table recon.lines as
with joined as (
    select
        coalesce(r.client_key, b.client_key)        as client_key,
        coalesce(r.billing_month, b.billing_month)  as billing_month,
        coalesce(r.service_code, b.service_code)    as service_code,
        r.measure,
        b.billed_quantity,
        b.billed_amount,
        r.expected_amount,
        r.split_amount,
        b.billed_records,
        b.billed_client_ids,
        b.invoice_nos,
        s.amount_tolerance                          as tolerance
    from recon.repriced r
    full join recon.billed b using (client_key, billing_month, service_code)
    cross join recon.settings s
),
flagged as (
    select
        j.*,
        (j.billing_month + interval '1 month' - interval '1 day')::date as month_end,
        j.billed_amount is not null and j.expected_amount is not null
            and abs(j.billed_amount - j.expected_amount) > j.tolerance  as to_explain
    from joined j
),
variants as (
    -- reprice only the lines that need explaining
    select
        f.*,
        case when to_explain then pricing.client_fee(client_key, service_code, billing_month, billed_quantity) end
            as fee_on_billed_quantity,
        case when to_explain then pricing.client_fee(client_key, service_code, billing_month, measure, 'ignore_end') end
            as fee_with_expired_terms,
        case when to_explain then pricing.client_fee(client_key, service_code, billing_month, measure, 'none') end
            as fee_without_terms,
        case when to_explain then pricing.client_fee(client_key, service_code, billing_month, measure, 'active', -1) end
            as fee_on_previous_card,
        case when to_explain then pricing.client_fee(client_key, service_code, billing_month, measure, 'active', 0, true) end
            as fee_as_slab,
        act.terms                                                   as active_terms,
        expd.terms                                                  as expired_terms,
        expd.effective_to                                           as terms_ended_on,
        ver.effective_from                                          as card_valid_from,
        dq.reason                                                   as schedule_defect
    from flagged f
    left join lateral (
        select format('%s %s', o.override_type, o.value) as terms
        from pricing.client_override o
        where o.client_key = f.client_key and o.service_code = f.service_code
          and o.effective_from <= f.billing_month and (o.effective_to is null or o.effective_to >= f.month_end)
        order by o.effective_from desc limit 1
    ) act on true
    left join lateral (
        select format('%s %s', o.override_type, o.value) as terms, o.effective_to
        from pricing.client_override o
        where o.client_key = f.client_key and o.service_code = f.service_code
          and o.effective_to < f.month_end
        order by o.effective_to desc limit 1
    ) expd on true
    left join lateral (
        select max(v.effective_from) as effective_from
        from pricing.fee_schedule v
        where v.service_code = f.service_code and v.effective_from <= f.billing_month
    ) ver on true
    left join lateral (
        select string_agg(distinct i.detail, '; ') as reason
        from dq.schedule_issues i
        where i.severity = 'error' and i.service_code = f.service_code
          and i.affected_from <= f.month_end and (i.affected_to is null or i.affected_to >= f.billing_month)
    ) dq on true
),
classified as (
    select
        v.*,
        case
            when billed_amount is null and expected_amount is null      then 'NOT_PRICEABLE'
            when billed_amount is null                                  then 'NOT_BILLED'
            when measure is null                                        then 'NO_ACTIVITY'
            when expected_amount is null                                then 'NOT_PRICEABLE'
            when billed_amount = expected_amount                        then 'MATCH'
            when abs(billed_amount - expected_amount) <= tolerance      then 'WITHIN_TOLERANCE'
            else 'MISMATCH'
        end as status
    from variants v
)
select
    client_key,
    billing_month,
    service_code,
    measure,
    billed_quantity,
    billed_amount,
    expected_amount,
    case status
        when 'NOT_BILLED'    then -expected_amount
        when 'NO_ACTIVITY'   then billed_amount
        when 'NOT_PRICEABLE' then null
        else billed_amount - expected_amount
    end                                                         as difference,
    status,
    case
        when status <> 'MISMATCH' then
            case when status in ('MATCH', 'WITHIN_TOLERANCE') then null else status end
        when billed_records > 1 and abs(billed_amount - split_amount) <= tolerance
            then 'DUPLICATE_CLIENT'
        when billed_quantity is distinct from measure and abs(billed_amount - fee_on_billed_quantity) <= tolerance
            then 'VOLUME_MISMATCH'
        when active_terms is null and expired_terms is not null and abs(billed_amount - fee_with_expired_terms) <= tolerance
            then 'EXPIRED_TERMS'
        when active_terms is not null and abs(billed_amount - fee_without_terms) <= tolerance
            then 'TERMS_NOT_APPLIED'
        when abs(billed_amount - fee_on_previous_card) <= tolerance
            then 'OLD_RATE_CARD'
        when abs(billed_amount - fee_as_slab) <= tolerance
            then 'SLAB_INSTEAD_OF_GRADUATED'
        else 'UNEXPLAINED'
    end                                                         as cause,
    case
        when status = 'NOT_PRICEABLE' then 'Cannot be verified, the fee schedule has a defect: ' || coalesce(schedule_defect, 'no rate found')
        when status = 'NOT_BILLED'    then format('Activity of %s with no invoice line', measure)
        when status = 'NO_ACTIVITY'   then 'Invoice line without any activity in the month'
        when status <> 'MISMATCH'     then null
        when billed_records > 1 and abs(billed_amount - split_amount) <= tolerance
            then format('Billed under %s client records (%s), each banded on its own. Priced as one client the fee is %s',
                        billed_records, billed_client_ids, expected_amount)
        when billed_quantity is distinct from measure and abs(billed_amount - fee_on_billed_quantity) <= tolerance
            then format('Invoice quantity %s, activity %s. The amount is right for the invoiced quantity', billed_quantity, measure)
        when active_terms is null and expired_terms is not null and abs(billed_amount - fee_with_expired_terms) <= tolerance
            then format('Charged with the client terms (%s) that ended on %s', expired_terms, terms_ended_on)
        when active_terms is not null and abs(billed_amount - fee_without_terms) <= tolerance
            then format('Client terms in force (%s) were not applied, the invoice is at standard rates', active_terms)
        when abs(billed_amount - fee_on_previous_card) <= tolerance
            then format('Amount matches the rate card that was replaced on %s', card_valid_from)
        when abs(billed_amount - fee_as_slab) <= tolerance
            then 'Whole volume charged at the rate of the band reached; the schedule is graduated'
        else 'Matches none of the known pricing variants'
    end                                                         as explanation,
    billed_records,
    billed_client_ids,
    invoice_nos
from classified;

create index on recon.lines (status);


-- What goes to the billing team: every line that is not a match.
create or replace view recon.v_exceptions as
select
    l.client_key,
    c.client_name,
    c.lei,
    to_char(l.billing_month, 'YYYY-MM')         as billing_month,
    l.service_code,
    s.service_name,
    l.measure                                   as activity_quantity,
    l.billed_quantity,
    l.billed_amount,
    l.expected_amount,
    l.difference,
    case
        when l.difference > 0 then 'over-billed'
        when l.difference < 0 then 'under-billed'
        else 'not verified'
    end                                         as direction,
    l.cause,
    l.explanation,
    l.invoice_nos
from recon.lines l
left join core.clients c using (client_key)
left join pricing.v_service s using (service_code)
where l.status not in ('MATCH', 'WITHIN_TOLERANCE');

create or replace view recon.v_summary_by_cause as
select
    cause,
    count(*)                                                    as lines,
    coalesce(sum(difference) filter (where difference > 0), 0)  as over_billed,
    coalesce(-sum(difference) filter (where difference < 0), 0) as under_billed,
    coalesce(sum(difference), 0)                                as net_difference,
    coalesce(sum(billed_amount) filter (where difference is null), 0) as billed_not_verified
from recon.v_exceptions
group by cause;

create or replace view recon.v_client_impact as
select
    client_key,
    client_name,
    count(*)                                                    as exception_lines,
    coalesce(sum(difference) filter (where difference > 0), 0)  as over_billed,
    coalesce(-sum(difference) filter (where difference < 0), 0) as under_billed,
    coalesce(sum(difference), 0)                                as net_difference
from recon.v_exceptions
group by client_key, client_name;

create or replace view recon.v_overview as
select
    count(*)                                                        as lines,
    count(*) filter (where status = 'MATCH')                        as matched,
    count(*) filter (where status = 'WITHIN_TOLERANCE')             as within_tolerance,
    count(*) filter (where status = 'MISMATCH')                     as mismatched,
    count(*) filter (where status = 'NOT_BILLED')                   as not_billed,
    count(*) filter (where status = 'NO_ACTIVITY')                  as no_activity,
    count(*) filter (where status = 'NOT_PRICEABLE')                as not_priceable,
    coalesce(sum(billed_amount), 0)                                 as billed_total,
    coalesce(sum(expected_amount) filter (where status <> 'NOT_PRICEABLE'), 0) as expected_total,
    -- cent differences inside the tolerance are not findings and are not counted here
    coalesce(sum(difference) filter (where difference > 0 and status not in ('MATCH', 'WITHIN_TOLERANCE')), 0)  as over_billed,
    coalesce(-sum(difference) filter (where difference < 0 and status not in ('MATCH', 'WITHIN_TOLERANCE')), 0) as under_billed
from recon.lines;
