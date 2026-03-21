-- Assert every tournament team (from seeds) has a match in the crosswalk
select s.*
from {{ ref('stg_kaggle_seeds') }} s
where s.canonical_name is null
