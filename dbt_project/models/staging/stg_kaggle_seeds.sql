with source as (
    select * from {{ source('raw', 'kaggle_seeds') }}
),

crosswalk as (
    select * from {{ ref('team_crosswalk') }}
),

cleaned as (
    select
        cast(s."Season" as integer) as season,
        cast(s."TeamID" as integer) as team_id,
        s."Seed" as seed_raw,
        -- Extract region (first character: W, X, Y, Z)
        substring(s."Seed", 1, 1) as region,
        -- Extract seed number (digits after region letter)
        -- Handle play-in seeds like "W16a", "W16b" by stripping trailing letters
        cast(
            regexp_replace(substring(s."Seed", 2), '[a-z]', '', 'g')
            as integer
        ) as seed_number,
        cw.canonical_name
    from source s
    left join crosswalk cw
        on cast(s."TeamID" as integer) = cw.kaggle_team_id
)

select * from cleaned
