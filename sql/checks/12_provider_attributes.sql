-- Is dim_provider's "Part B wins, Part D fills the gaps" rule losing anything?
--
-- Two separate questions.
--
-- Within a dataset: dim_provider collapses every fact row for an NPI down to one row
-- with any_value(). That is only lossless if the attribute really is constant per NPI.
-- Specialty is asserted (the single-provider-table decision rests on it, docs/schema.md);
-- name and city are reported, because a provider with two billing cities is a real thing
-- and we would rather see the number than assume it away.
--
-- Across datasets: for the NPIs in both, does Part B agree with Part D? Specialty was
-- measured at 100% agreement and is asserted here. Name and city were never measured,
-- which is exactly what this check exists to fix. Reported both exactly and normalized
-- (trimmed, upper-cased), since a pure casing difference is not a real disagreement.

WITH b AS (
    SELECT
        Rndrng_NPI                                      AS npi,
        count(DISTINCT Rndrng_Prvdr_Type)               AS n_specialty,
        count(DISTINCT Rndrng_Prvdr_Last_Org_Name)      AS n_name,
        count(DISTINCT Rndrng_Prvdr_City)               AS n_city,
        any_value(Rndrng_Prvdr_Type)                    AS specialty,
        any_value(Rndrng_Prvdr_Last_Org_Name)           AS name,
        any_value(Rndrng_Prvdr_City)                    AS city
    FROM raw_part_b
    GROUP BY npi
),
d AS (
    SELECT
        Prscrbr_NPI                                     AS npi,
        count(DISTINCT Prscrbr_Type)                    AS n_specialty,
        count(DISTINCT Prscrbr_Last_Org_Name)           AS n_name,
        count(DISTINCT Prscrbr_City)                    AS n_city,
        any_value(Prscrbr_Type)                         AS specialty,
        any_value(Prscrbr_Last_Org_Name)                AS name,
        any_value(Prscrbr_City)                         AS city
    FROM raw_part_d
    GROUP BY npi
),
overlap AS (
    SELECT
        b.specialty AS b_specialty, d.specialty AS d_specialty,
        b.name      AS b_name,      d.name      AS d_name,
        b.city      AS b_city,      d.city      AS d_city
    FROM b JOIN d ON b.npi = d.npi
)
SELECT
    (SELECT count(*) FROM b)                                    AS part_b_providers,
    (SELECT count(*) FROM d)                                    AS part_d_providers,
    (SELECT count(*) FROM overlap)                              AS overlapping_npis,

    (SELECT count(*) FROM b WHERE n_specialty > 1)              AS part_b_npis_multi_specialty,
    (SELECT count(*) FROM d WHERE n_specialty > 1)              AS part_d_npis_multi_specialty,
    (SELECT count(*) FROM b WHERE n_name > 1)                   AS part_b_npis_multi_name,
    (SELECT count(*) FROM d WHERE n_name > 1)                   AS part_d_npis_multi_name,
    (SELECT count(*) FROM b WHERE n_city > 1)                   AS part_b_npis_multi_city,
    (SELECT count(*) FROM d WHERE n_city > 1)                   AS part_d_npis_multi_city,

    (SELECT count(*) FROM overlap
      WHERE b_specialty IS DISTINCT FROM d_specialty)           AS specialty_disagreements,
    (SELECT count(*) FROM overlap
      WHERE b_name IS DISTINCT FROM d_name)                     AS name_disagreements,
    (SELECT count(*) FROM overlap
      WHERE upper(trim(b_name)) IS DISTINCT FROM upper(trim(d_name)))
                                                                AS name_disagreements_normalized,
    (SELECT count(*) FROM overlap
      WHERE b_city IS DISTINCT FROM d_city)                     AS city_disagreements,
    (SELECT count(*) FROM overlap
      WHERE upper(trim(b_city)) IS DISTINCT FROM upper(trim(d_city)))
                                                                AS city_disagreements_normalized;
