with source as (
    select * from {{ source('raw', 'barttorvik_game_predictions') }}
)

, crosswalk as (
    select * from {{ ref('team_crosswalk') }}
)

select
    s.team as team_name
    , s.season
    , cast(s.season as integer) as season_int
    , s.game_day_num
    , s.location
    , s.tempo
    , s.ppp
    , s.pts
    , s.win_per
    , s.did_win
    , cw.canonical_name
    , cw.kaggle_team_id
from source s
left join crosswalk cw
    on s.team = cw.barttorvik_team_name
