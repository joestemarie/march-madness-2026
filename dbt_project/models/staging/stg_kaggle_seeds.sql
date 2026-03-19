with source as (
    select * from {{ source('raw', 'kaggle_seeds') }}
)

, crosswalk as (
    select * from {{ ref('team_crosswalk') }}
)

select
    cast(s."Season" as integer) as season
    , cast(s."TeamID" as integer) as team_id
    , s."Seed" as seed_raw
    , substring(s."Seed", 1, 1) as region
    , cast(
        regexp_replace(substring(s."Seed", 2), '[a-z]', '', 'g')
        as integer
    ) as seed_number
    , cw.canonical_name
from source s
left join crosswalk cw
    on cast(s."TeamID" as integer) = cw.kaggle_team_id
