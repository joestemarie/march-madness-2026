with source as (
    select * from {{ source('raw', 'kenpom_ratings') }}
)

, crosswalk as (
    select * from {{ ref('team_crosswalk') }}
)

select
    s."TeamName" as team_name
    , s."ConfShort" as conference
    , cast(s."AdjEM" as double) as adj_em
    , cast(s."AdjOE" as double) as adj_o
    , cast(s."AdjDE" as double) as adj_d
    , cast(s."AdjTempo" as double) as adj_t
    , cast(s."SOS" as double) as sos_adj_em
    , cast(s."SOSO" as double) as sos_adj_o
    , cast(s."SOSD" as double) as sos_adj_d
    , cast(s."Luck" as double) as luck
    , cast(s."NCSOS" as double) as ncsos_adj_em
    , cast(s."RankAdjEM" as integer) as kenpom_rank
    , cast(s."Season" as integer) as season
    , 'kenpom' as source_name
    , cw.canonical_name
    , cw.kaggle_team_id
from source s
left join crosswalk cw
    on s."TeamName" = cw.kenpom_team_name
