with source as (
    select * from {{ source('raw', 'kenpom_four_factors') }}
)

, crosswalk as (
    select * from {{ ref('team_crosswalk') }}
)

select
    s.team_name
    , s.conference
    , cast(s.off_efg_pct as double) as off_efg_pct
    , cast(s.off_to_pct as double) as off_to_pct
    , cast(s.off_or_pct as double) as off_or_pct
    , cast(s.off_ft_rate as double) as off_ft_rate
    , cast(s.def_efg_pct as double) as def_efg_pct
    , cast(s.def_to_pct as double) as def_to_pct
    , cast(s.def_or_pct as double) as def_or_pct
    , cast(s.def_ft_rate as double) as def_ft_rate
    , cast(s.season as integer) as season
    , cw.canonical_name
    , cw.kaggle_team_id
from source s
left join crosswalk cw
    on s.team_name = cw.kenpom_team_name
