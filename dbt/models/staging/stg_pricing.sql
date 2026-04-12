{{ config(materialized='view') }}

with exploded as (
    select
        r.content_sha256,
        r.ingested_at,
        r.payload->'meta'->>'schema_version' as schema_version,
        r.payload->'meta'->>'last_run_datetime' as last_run_datetime,

        x.pricing_id,
        x.provider_id,
        x.model_id,
        x.display_name,
        x.service_tier,
        x.context_window,
        x.modality,
        x.category,
        x.billing_variant,
        x.currency,
        x.input_price::numeric as input_price,
        x.input_unit,
        x.cached_input_price::numeric as cached_input_price,
        x.cached_input_unit,
        x.output_price::numeric as output_price,
        x.output_unit,
        x.cache_read_price::numeric as cache_read_price,
        x.cache_read_unit,
        x.cache_write_5m_price::numeric as cache_write_5m_price,
        x.cache_write_5m_unit,
        x.cache_write_1h_price::numeric as cache_write_1h_price,
        x.cache_write_1h_unit,
        x.training_price::numeric as training_price,
        x.training_unit,
        x.generation_price::numeric as generation_price,
        x.generation_unit,
        x.is_active
    from {{ source('raw', 'pricing_json') }} as r
    cross join lateral jsonb_to_recordset(r.payload->'pricing') as x(
        pricing_id text,
        provider_id text,
        model_id text,
        display_name text,
        service_tier text,
        context_window text,
        modality text,
        category text,
        billing_variant text,
        currency text,
        input_price text,
        input_unit text,
        cached_input_price text,
        cached_input_unit text,
        output_price text,
        output_unit text,
        cache_read_price text,
        cache_read_unit text,
        cache_write_5m_price text,
        cache_write_5m_unit text,
        cache_write_1h_price text,
        cache_write_1h_unit text,
        training_price text,
        training_unit text,
        generation_price text,
        generation_unit text,
        is_active boolean
    )
),
normalized as (
    select
        content_sha256,
        ingested_at,
        schema_version,
        last_run_datetime as pricing_version,
        pricing_id,
        provider_id as provider,
        model_id as model,
        display_name,
        service_tier,
        context_window,
        modality,
        category,
        billing_variant,
        currency,
        is_active,
        'input_tokens' as sku_type,
        input_price as price_usd,
        input_unit as unit
    from exploded
    where input_price is not null

    union all

    select
        content_sha256,
        ingested_at,
        schema_version,
        last_run_datetime as pricing_version,
        pricing_id,
        provider_id as provider,
        model_id as model,
        display_name,
        service_tier,
        context_window,
        modality,
        category,
        billing_variant,
        currency,
        is_active,
        'cached_input_tokens' as sku_type,
        cached_input_price as price_usd,
        cached_input_unit as unit
    from exploded
    where cached_input_price is not null

    union all

    select
        content_sha256,
        ingested_at,
        schema_version,
        last_run_datetime as pricing_version,
        pricing_id,
        provider_id as provider,
        model_id as model,
        display_name,
        service_tier,
        context_window,
        modality,
        category,
        billing_variant,
        currency,
        is_active,
        'output_tokens' as sku_type,
        output_price as price_usd,
        output_unit as unit
    from exploded
    where output_price is not null

    union all

    select
        content_sha256,
        ingested_at,
        schema_version,
        last_run_datetime as pricing_version,
        pricing_id,
        provider_id as provider,
        model_id as model,
        display_name,
        service_tier,
        context_window,
        modality,
        category,
        billing_variant,
        currency,
        is_active,
        'cache_read_tokens' as sku_type,
        cache_read_price as price_usd,
        cache_read_unit as unit
    from exploded
    where cache_read_price is not null

    union all

    select
        content_sha256,
        ingested_at,
        schema_version,
        last_run_datetime as pricing_version,
        pricing_id,
        provider_id as provider,
        model_id as model,
        display_name,
        service_tier,
        context_window,
        modality,
        category,
        billing_variant,
        currency,
        is_active,
        'cache_write_5m_tokens' as sku_type,
        cache_write_5m_price as price_usd,
        cache_write_5m_unit as unit
    from exploded
    where cache_write_5m_price is not null

    union all

    select
        content_sha256,
        ingested_at,
        schema_version,
        last_run_datetime as pricing_version,
        pricing_id,
        provider_id as provider,
        model_id as model,
        display_name,
        service_tier,
        context_window,
        modality,
        category,
        billing_variant,
        currency,
        is_active,
        'cache_write_1h_tokens' as sku_type,
        cache_write_1h_price as price_usd,
        cache_write_1h_unit as unit
    from exploded
    where cache_write_1h_price is not null

    union all

    select
        content_sha256,
        ingested_at,
        schema_version,
        last_run_datetime as pricing_version,
        pricing_id,
        provider_id as provider,
        model_id as model,
        display_name,
        service_tier,
        context_window,
        modality,
        category,
        billing_variant,
        currency,
        is_active,
        'training_hours' as sku_type,
        training_price as price_usd,
        training_unit as unit
    from exploded
    where training_price is not null

    union all

    select
        content_sha256,
        ingested_at,
        schema_version,
        last_run_datetime as pricing_version,
        pricing_id,
        provider_id as provider,
        model_id as model,
        display_name,
        service_tier,
        context_window,
        modality,
        category,
        billing_variant,
        currency,
        is_active,
        'generation' as sku_type,
        generation_price as price_usd,
        generation_unit as unit
    from exploded
    where generation_price is not null
)
select
    content_sha256,
    ingested_at,
    schema_version,
    pricing_version,
    pricing_id,
    provider,
    model,
    display_name,
    service_tier,
    context_window,
    modality,
    category,
    billing_variant,
    currency,
    is_active,
    sku_type,
    price_usd,
    unit,
    case
        when unit = 'per_1M_tokens' then price_usd
        when unit = 'per_1K_tokens' then price_usd * 1000.0
        else null
    end as price_per_1m_tokens_usd,
    case
        when unit = 'per_1M_tokens' then round(price_usd / 1000.0, 6)
        when unit = 'per_1K_tokens' then price_usd
        else null
    end as price_per_1k_tokens_usd
from normalized
