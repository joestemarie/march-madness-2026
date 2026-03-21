with source as (
    select * from {{ source('raw', 'kaggle_tourney_results') }}
)

, crosswalk as (
    select * from {{ ref('team_crosswalk') }}
)

select
    cast(s."Season" as integer) as season
    , cast(s."DayNum" as integer) as day_num
    , cast(s."WTeamID" as integer) as team_id_winner
    , cast(s."LTeamID" as integer) as team_id_loser
    , cast(s."WScore" as integer) as score_winner
    , cast(s."LScore" as integer) as score_loser
    , case
        when cast(s."DayNum" as integer) between 134 and 135 then 0  -- First Four (play-in)
        when cast(s."DayNum" as integer) between 136 and 137 then 1  -- Round of 64
        when cast(s."DayNum" as integer) between 138 and 139 then 2  -- Round of 32
        when cast(s."DayNum" as integer) between 143 and 144 then 3  -- Sweet 16
        when cast(s."DayNum" as integer) between 145 and 146 then 4  -- Elite 8
        when cast(s."DayNum" as integer) = 152 then 5                -- Final Four
        when cast(s."DayNum" as integer) = 154 then 6                -- Championship
        else null
    end as round
    , cw_w.canonical_name as winner_canonical_name
    , cw_l.canonical_name as loser_canonical_name
from source s
left join crosswalk cw_w
    on cast(s."WTeamID" as integer) = cw_w.kaggle_team_id
left join crosswalk cw_l
    on cast(s."LTeamID" as integer) = cw_l.kaggle_team_id
