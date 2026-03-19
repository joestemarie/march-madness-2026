-- One row per tournament game, with team_a always being the higher seed (lower number).
-- When seeds are equal, team_a is alphabetically first.

with results as (
    select * from {{ ref('stg_kaggle_tourney_results') }}
    where round is not null
)

, seeds as (
    select * from {{ ref('stg_kaggle_seeds') }}
)

, matchups_raw as (
    select
        r.season
        , r.round
        , r.team_id_winner
        , r.team_id_loser
        , r.score_winner
        , r.score_loser
        , r.winner_canonical_name
        , r.loser_canonical_name
        , sw.seed_number as winner_seed
        , sl.seed_number as loser_seed
        , sw.region as winner_region
        , sl.region as loser_region
    from results r
    left join seeds sw
        on r.team_id_winner = sw.team_id and r.season = sw.season
    left join seeds sl
        on r.team_id_loser = sl.team_id and r.season = sl.season
)

select
    season
    , round
    , case
        when winner_seed < loser_seed then team_id_winner
        when winner_seed > loser_seed then team_id_loser
        when winner_canonical_name <= loser_canonical_name then team_id_winner
        else team_id_loser
    end as team_a_id
    , case
        when winner_seed < loser_seed then team_id_loser
        when winner_seed > loser_seed then team_id_winner
        when winner_canonical_name <= loser_canonical_name then team_id_loser
        else team_id_winner
    end as team_b_id
    , case
        when winner_seed < loser_seed then winner_seed
        when winner_seed > loser_seed then loser_seed
        when winner_canonical_name <= loser_canonical_name then winner_seed
        else loser_seed
    end as team_a_seed
    , case
        when winner_seed < loser_seed then loser_seed
        when winner_seed > loser_seed then winner_seed
        when winner_canonical_name <= loser_canonical_name then loser_seed
        else winner_seed
    end as team_b_seed
    , case
        when winner_seed < loser_seed then winner_canonical_name
        when winner_seed > loser_seed then loser_canonical_name
        when winner_canonical_name <= loser_canonical_name then winner_canonical_name
        else loser_canonical_name
    end as team_a_canonical_name
    , case
        when winner_seed < loser_seed then loser_canonical_name
        when winner_seed > loser_seed then winner_canonical_name
        when winner_canonical_name <= loser_canonical_name then loser_canonical_name
        else winner_canonical_name
    end as team_b_canonical_name
    , team_id_winner as winner_id
    , case
        when winner_seed < loser_seed then 1
        when winner_seed > loser_seed then 0
        when winner_canonical_name <= loser_canonical_name then 1
        else 0
    end as team_a_won
from matchups_raw
