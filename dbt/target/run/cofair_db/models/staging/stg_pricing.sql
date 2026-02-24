
  create view "cofair_db"."staging"."stg_pricing__dbt_tmp"
    
    
  as (
    

with exploded as (
    select
        r.content_sha256,
        r.ingested_at,
        r.payload->>'version'  as pricing_version,
        r.payload->>'currency' as currency,

        x.provider,
        x.model,
        x.sku_type,
        x.price_per_1m_tokens_usd,
        x.price_per_1k_tokens_usd,
        x.source
    from "cofair_db"."raw"."pricing_json" as r
    cross join lateral jsonb_to_recordset(r.payload->'rows') as x(  -- This look complicated but its just parsing the JSON
        provider text,
        model text,
        sku_type text,
        price_per_1m_tokens_usd numeric,
        price_per_1k_tokens_usd numeric,
        source text
    )
)

select *
from exploded
  );