
  create view "neondb"."staging"."stg_donors__dbt_tmp"
    
    
  as (
    

with exploded as (
    select
        r.content_sha256,
        r.ingested_at,

        x.donor_id,
        x.org_type,
        x.platform,
        x.monthly_budget::numeric              as monthly_budget,
        x.volatility::numeric                  as volatility,
        x.weekend_factor::numeric              as weekend_factor,
        x.subscription_start_date::date        as subscription_start_date,
        x.billing_cycle,
        x.billing_cycle_days::int              as billing_cycle_days,
        x.current_cycle_start::date            as current_cycle_start,
        x.current_cycle_end::date              as current_cycle_end,
        x.days_remaining_in_cycle::int         as days_remaining_in_cycle

    from "neondb"."raw"."donor_csv" as r
    cross join lateral jsonb_to_recordset(r.payload) as x(    -- This look complicated but its just parsing the CSV
        donor_id text,
        org_type text,
        platform text,
        monthly_budget text,
        volatility text,
        weekend_factor text,
        subscription_start_date text,
        billing_cycle text,
        billing_cycle_days text,
        current_cycle_start text,
        current_cycle_end text,
        days_remaining_in_cycle text
    )
)

select *
from exploded
  );