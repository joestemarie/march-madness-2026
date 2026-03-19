-- One row per tournament matchup (Rounds 1 & 2) with KenPom differentials.
-- team_a is always the higher seed (lower number).

with matchups as (
    select * from {{ ref('tournament_matchups') }}
    where round in (1, 2)
)

, kenpom as (
    select * from {{ ref('stg_kenpom_ratings') }}
)

, four_factors as (
    select * from {{ ref('stg_kenpom_four_factors') }}
)

select
    m.season
    , m.round
    , m.team_a_id
    , m.team_b_id
    , m.team_a_seed
    , m.team_b_seed
    , m.team_a_canonical_name
    , m.team_b_canonical_name
    , m.winner_id
    , m.team_a_won

    -- Seed differential
    , m.team_b_seed - m.team_a_seed as seed_diff

    -- Team A raw KenPom values
    , ka.adj_em as team_a_adj_em
    , ka.adj_o as team_a_adj_o
    , ka.adj_d as team_a_adj_d
    , ka.adj_t as team_a_adj_t
    , ka.sos_adj_em as team_a_sos_adj_em
    , ka.ncsos_adj_em as team_a_ncsos_adj_em
    , ka.luck as team_a_luck
    , ka.kenpom_rank as team_a_kenpom_rank

    -- Team B raw KenPom values
    , kb.adj_em as team_b_adj_em
    , kb.adj_o as team_b_adj_o
    , kb.adj_d as team_b_adj_d
    , kb.adj_t as team_b_adj_t
    , kb.sos_adj_em as team_b_sos_adj_em
    , kb.ncsos_adj_em as team_b_ncsos_adj_em
    , kb.luck as team_b_luck
    , kb.kenpom_rank as team_b_kenpom_rank

    -- Core efficiency differentials (team_a - team_b)
    , ka.adj_em - kb.adj_em as adj_em_diff
    , ka.adj_o - kb.adj_o as adj_o_diff
    , ka.adj_d - kb.adj_d as adj_d_diff
    , ka.adj_t - kb.adj_t as adj_t_diff

    -- Strength of schedule differentials
    , ka.sos_adj_em - kb.sos_adj_em as sos_adj_em_diff
    , ka.ncsos_adj_em - kb.ncsos_adj_em as ncsos_adj_em_diff

    -- Luck differential
    , ka.luck - kb.luck as luck_diff

    -- Four factors: Team A
    , ffa.off_efg_pct as team_a_off_efg_pct
    , ffa.off_to_pct as team_a_off_to_pct
    , ffa.off_or_pct as team_a_off_or_pct
    , ffa.off_ft_rate as team_a_off_ft_rate
    , ffa.def_efg_pct as team_a_def_efg_pct
    , ffa.def_to_pct as team_a_def_to_pct
    , ffa.def_or_pct as team_a_def_or_pct
    , ffa.def_ft_rate as team_a_def_ft_rate

    -- Four factors: Team B
    , ffb.off_efg_pct as team_b_off_efg_pct
    , ffb.off_to_pct as team_b_off_to_pct
    , ffb.off_or_pct as team_b_off_or_pct
    , ffb.off_ft_rate as team_b_off_ft_rate
    , ffb.def_efg_pct as team_b_def_efg_pct
    , ffb.def_to_pct as team_b_def_to_pct
    , ffb.def_or_pct as team_b_def_or_pct
    , ffb.def_ft_rate as team_b_def_ft_rate

    -- Four factors differentials
    , ffa.off_efg_pct - ffb.off_efg_pct as off_efg_pct_diff
    , ffa.def_efg_pct - ffb.def_efg_pct as def_efg_pct_diff
    , ffa.off_to_pct - ffb.off_to_pct as off_to_pct_diff
    , ffa.def_to_pct - ffb.def_to_pct as def_to_pct_diff
    , ffa.off_or_pct - ffb.off_or_pct as off_or_pct_diff
    , ffa.def_or_pct - ffb.def_or_pct as def_or_pct_diff
    , ffa.off_ft_rate - ffb.off_ft_rate as off_ft_rate_diff
    , ffa.def_ft_rate - ffb.def_ft_rate as def_ft_rate_diff

from matchups m
left join kenpom ka
    on m.team_a_id = ka.kaggle_team_id and m.season = ka.season
left join kenpom kb
    on m.team_b_id = kb.kaggle_team_id and m.season = kb.season
left join four_factors ffa
    on m.team_a_id = ffa.kaggle_team_id and m.season = ffa.season
left join four_factors ffb
    on m.team_b_id = ffb.kaggle_team_id and m.season = ffb.season
