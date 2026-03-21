with source as (
    select * from {{ source('raw', 'kenpom_four_factors') }}
)

, crosswalk as (
    select * from {{ ref('team_crosswalk') }}
)

select
    s."TeamName" as team_name
    , s."Season" as season
    , cast(s."eFG_Pct" as double) as off_efg_pct
    , cast(s."TO_Pct" as double) as off_to_pct
    , cast(s."OR_Pct" as double) as off_or_pct
    , cast(s."FT_Rate" as double) as off_ft_rate
    , cast(s."DeFG_Pct" as double) as def_efg_pct
    , cast(s."DTO_Pct" as double) as def_to_pct
    , cast(s."DOR_Pct" as double) as def_or_pct
    , cast(s."DFT_Rate" as double) as def_ft_rate
    , cw.canonical_name
    , cw.kaggle_team_id
from source s
left join crosswalk cw
    on s."TeamName" = cw.kenpom_team_name
