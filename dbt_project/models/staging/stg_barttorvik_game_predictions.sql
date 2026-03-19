with source as (
    select * from {{ source('raw', 'barttorvik_game_predictions') }}
)

, crosswalk_team as (
    select * from {{ ref('team_crosswalk') }}
)

, crosswalk_opp as (
    select * from {{ ref('team_crosswalk') }}
)

select
    s.*
    , cast(s.season as integer) as season_int
    , ct.canonical_name as team_canonical_name
    , ct.kaggle_team_id as team_kaggle_id
    , co.canonical_name as opponent_canonical_name
    , co.kaggle_team_id as opponent_kaggle_id
from source s
left join crosswalk_team ct
    on s.team_name = ct.barttorvik_team_name
left join crosswalk_opp co
    on s.opponent_name = co.barttorvik_team_name
