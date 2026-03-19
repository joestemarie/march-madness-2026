-- Tournament seeds from Kaggle (historical) unioned with manual seeds (current year).
-- Manual seeds use kenpom_team_name directly — no crosswalk join needed since
-- predict.py resolves names via KenPom ratings.

with source as (
    select * from {{ source('raw', 'kaggle_seeds') }}
)

, crosswalk as (
    select * from {{ ref('team_crosswalk') }}
)

, manual as (
    select * from {{ ref('manual_seeds') }}
)

, kaggle_seeds as (
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
        , cw.kenpom_team_name
    from source s
    left join crosswalk cw
        on cast(s."TeamID" as integer) = cw.kaggle_team_id
)

, manual_seeds as (
    select
        m.season
        , null::integer as team_id
        , m.region || lpad(cast(m.seed_number as varchar), 2, '0') as seed_raw
        , m.region
        , m.seed_number
        , m.kenpom_team_name as canonical_name
        , m.kenpom_team_name
    from manual m
)

select
    season
    , team_id
    , seed_raw
    , region
    , seed_number
    , canonical_name
    , kenpom_team_name
from kaggle_seeds

union all

select
    season
    , team_id
    , seed_raw
    , region
    , seed_number
    , canonical_name
    , kenpom_team_name
from manual_seeds
