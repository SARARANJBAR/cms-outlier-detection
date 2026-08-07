-- One row per state code appearing in either dataset. Measured: 62 values, not 50.
--
-- The 12 that are not US states, with their CMS FIPS codes, enumerated here rather than
-- inferred, because they are the reason a naive join to a 50-row state table drops rows
-- without saying so:
--
--   district           DC (11)                                    1
--   territory          AS (60) GU (66) MP (69) PR (72) VI (78)     5
--   freely_associated  FM (no FIPS) -- Federated States of Micronesia
--   military           AA (9A) AE (9B) AP (9C)                     3
--   unknown            XX (9D) ZZ (9E)                             2
--
-- The ELSE branch below is the one that could rot: a state code CMS adds in a later year
-- would be labelled 'state' by default. sql/checks/13_reference_dims.sql asserts that
-- exactly 50 rows land in that branch, so the rot fails the build.

CREATE OR REPLACE VIEW dim_geography AS
WITH observed AS (
    SELECT
        Rndrng_Prvdr_State_Abrvtn               AS state,
        any_value(Rndrng_Prvdr_State_FIPS)      AS state_fips
    FROM raw_part_b
    GROUP BY state
    UNION ALL
    SELECT
        Prscrbr_State_Abrvtn                    AS state,
        any_value(Prscrbr_State_FIPS)           AS state_fips
    FROM raw_part_d
    GROUP BY state
),
labelled AS (
    SELECT
        state,
        max(state_fips) AS state_fips,
        CASE
            WHEN state = 'DC'                           THEN 'district'
            WHEN state IN ('AS', 'GU', 'MP', 'PR', 'VI') THEN 'territory'
            WHEN state = 'FM'                           THEN 'freely_associated'
            WHEN state IN ('AA', 'AE', 'AP')            THEN 'military'
            WHEN state IN ('XX', 'ZZ')                  THEN 'unknown'
            ELSE 'state'
        END AS category
    FROM observed
    GROUP BY state
)
SELECT
    state,
    state_fips,
    category,
    category = 'state' AS is_us_state
FROM labelled;
