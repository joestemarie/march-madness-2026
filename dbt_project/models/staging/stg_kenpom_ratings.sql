with source as (
    select * from {{ source('raw', 'kenpom_ratings') }}
)

, crosswalk as (
    select * from {{ ref('team_crosswalk') }}
)

select
    s.team_name
    , s.conference
    , cast(s.adj_em as double) as adj_em
    , cast(s.adj_o as double) as adj_o
    , cast(s.adj_d as double) as adj_d
    , cast(s.adj_t as double) as adj_t
    , cast(s.sos_adj_em as double) as sos_adj_em
    , cast(s.sos_adj_o as double) as sos_adj_o
    , cast(s.sos_adj_d as double) as sos_adj_d
    , cast(s.luck as double) as luck
    , cast(s.ncsos_adj_em as double) as ncsos_adj_em
    , cast(s.kenpom_rank as integer) as kenpom_rank
    , cast(s.season as integer) as season
    , 'kenpom' as source_name
    , cw.canonical_name
    , cw.kaggle_team_id
from source s
left join crosswalk cw
    on s.team_name = cw.kenpom_team_name
