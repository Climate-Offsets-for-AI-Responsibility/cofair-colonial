{{ config(materialized='view') }}

{#
  Parses pricing.json from scrape_pricing.py.
  Structure: payload.pricing[] with provider_id, model_id, input_price, output_price, type (chat/cache)
  Unpivots to one row per sku_type (base_input_tokens, output_tokens, cache_hit_refresh)
#}
with pricing_rows as (
    select
        r.content_sha256,
        r.ingested_at,
        r.payload->'meta'->>'last_run_datetime' as pricing_version,
        x.provider_id as provider,
        x.model_id as model,
        x.input_price,
        x.output_price,
        x.type as row_type
    from {{ source('raw', 'pricing_json') }} as r
    cross join lateral jsonb_to_recordset(r.payload->'pricing') as x(
        provider_id text,
        model_id text,
        input_price numeric,
        output_price numeric,
        type text
    )
),
unpivoted as (
    select content_sha256, ingested_at, pricing_version, provider, model,
           'base_input_tokens' as sku_type, input_price as price_per_1m_tokens_usd
    from pricing_rows
    where row_type = 'chat' and input_price is not null

    union all

    select content_sha256, ingested_at, pricing_version, provider, model,
           'output_tokens' as sku_type, output_price as price_per_1m_tokens_usd
    from pricing_rows
    where row_type = 'chat' and output_price is not null

    union all

    select content_sha256, ingested_at, pricing_version, provider, model,
           'cache_hit_refresh' as sku_type, input_price as price_per_1m_tokens_usd
    from pricing_rows
    where row_type = 'cache' and input_price is not null
)
select
    content_sha256,
    ingested_at,
    pricing_version,
    'USD' as currency,
    provider,
    model,
    sku_type,
    price_per_1m_tokens_usd,
    round(price_per_1m_tokens_usd / 1000.0, 6) as price_per_1k_tokens_usd
from unpivoted
