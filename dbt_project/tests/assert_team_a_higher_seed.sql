-- Assert team_a_seed <= team_b_seed in tournament_matchups (our convention)
select *
from {{ ref('tournament_matchups') }}
where team_a_seed > team_b_seed
