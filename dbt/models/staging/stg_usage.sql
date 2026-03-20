{{ config(materialized='view') }}

{#
  Parses raw.usage (dataclaw from HuggingFace) into structured columns.
#}
SELECT
    dataset_id,
    payload->>'session_id' AS session_id,
    payload->>'model' AS model,
    payload->>'git_branch' AS git_branch,
    (payload->>'start_time')::timestamp AS start_time,
    (payload->>'end_time')::timestamp AS end_time,
    payload->>'project' AS project,
    payload->'messages' AS messages,
    (payload->'stats'->>'input_tokens')::bigint AS input_tokens,
    (payload->'stats'->>'output_tokens')::bigint AS output_tokens
FROM {{ source('raw', 'usage') }}
WHERE dataset_id IS NOT NULL
