with source as (
    select * from {{ source('raw', 'barttorvik_ratings') }}
)

, crosswalk as (
    select * from {{ ref('team_crosswalk') }}
)

select
    s.*
    , cast(s.season as integer) as season_int
    , cw.canonical_name
    , cw.kaggle_team_id
from source s
left join crosswalk cw
    on s.team = cw.barttorvik_team_name
