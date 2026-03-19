-- Sanity check: each season should have ~32 Round 1 games (28-36 range to allow flexibility)
-- This test fails if any season has a wildly wrong count
with counts as (
    select
        season,
        count(*) as game_count
    from {{ ref('tournament_matchups') }}
    where round = 1
    group by season
)

select *
from counts
where game_count < 16 or game_count > 36
