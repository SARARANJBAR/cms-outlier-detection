-- The serving layer: one row per peer group per measure, holding the precomputed
-- distribution. This is the table the app queries instead of computing a percentile over
-- 36M rows while someone waits, and the only artifact that ships inside the repo.
--
-- Built from the Parquet fact tables, not the CSVs — by the time this runs the facts have
-- already been written, so this reads 751 MB of sorted columnar data instead of 6.5 GB of
-- text, and reads only the columns it needs.
--
-- Long form, one row per (group, measure), rather than a column per measure. Adding a
-- measure is then a new row and not a schema change, and the app has one query path for
-- both datasets even though they share no measure names.
--
-- Measure names match the *_pct column suffixes on the fact tables: measure 'tot_srvcs'
-- here is fact_part_b_service.tot_srvcs_pct there. That is the app's join between a
-- distribution and a provider's position in it, so it is a contract, not a coincidence.
--
-- Part B measures are volume, patients, standardized price, and total payment — the last
-- one because the fact rows carry per-service averages, so a provider who is ordinary on
-- price and extreme on volume is only visible in the product. Part D measures are claims
-- and cost only: tot_benes is suppressed on 55.08% of rows, so any per-beneficiary
-- percentile would describe the surviving half of the distribution and not say so.
--
-- Groups below $min_peers rows are dropped. Measured cost of that rule: it excludes 77.9%
-- of Part B peer groups but only 2.40% of rows, and 73.2% / 0.71% for Part D.
--
-- n_providers and n_rows differ only for Part D, where the grain includes generic_name but
-- the peer key does not, so one prescriber can contribute several rows to a group. The
-- threshold and the percentiles are on rows, which is what the coverage figures above were
-- measured on; n_providers is carried so the difference is visible rather than implied.
--
-- UNPIVOT drops NULL values, which is what percentile computation wants anyway. No measure
-- below is ever NULL in the 2023 data, and sql/checks/14_peer_stats.sql asserts that the
-- fact tables and this table agree on exactly which groups are scoreable — which is the
-- observable consequence if that ever stops being true.

CREATE OR REPLACE VIEW peer_stats AS
WITH part_b_long AS (
    SELECT * FROM (
        UNPIVOT (
            SELECT
                specialty,
                hcpcs_code                                  AS code,
                place_of_service,
                npi,
                tot_srvcs::DOUBLE                           AS tot_srvcs,
                tot_benes::DOUBLE                           AS tot_benes,
                avg_medicare_standardized::DOUBLE           AS avg_medicare_standardized,
                (tot_srvcs * avg_medicare_payment)::DOUBLE  AS total_medicare_payment
            FROM fact_part_b_service
        )
        ON tot_srvcs, tot_benes, avg_medicare_standardized, total_medicare_payment
        INTO NAME measure VALUE value
    )
),
part_d_long AS (
    SELECT * FROM (
        UNPIVOT (
            SELECT
                specialty,
                brand_name                                  AS code,
                CAST(NULL AS VARCHAR)                       AS place_of_service,
                npi,
                tot_claims::DOUBLE                          AS tot_claims,
                tot_drug_cost::DOUBLE                       AS tot_drug_cost,
                (tot_drug_cost / tot_claims)::DOUBLE        AS cost_per_claim
            FROM fact_part_d_drug
        )
        ON tot_claims, tot_drug_cost, cost_per_claim
        INTO NAME measure VALUE value
    )
),
combined AS (
    SELECT 'part_b' AS dataset, * FROM part_b_long
    UNION ALL
    SELECT 'part_d' AS dataset, * FROM part_d_long
)
SELECT
    dataset,
    specialty,
    code,
    place_of_service,
    measure,
    count(DISTINCT npi)                     AS n_providers,
    count(*)                                AS n_rows,
    quantile_cont(value, 0.10)              AS p10,
    quantile_cont(value, 0.25)              AS p25,
    quantile_cont(value, 0.50)              AS p50,
    quantile_cont(value, 0.75)              AS p75,
    quantile_cont(value, 0.90)              AS p90,
    quantile_cont(value, 0.95)              AS p95,
    quantile_cont(value, 0.99)              AS p99
FROM combined
GROUP BY dataset, specialty, code, place_of_service, measure
HAVING count(*) >= $min_peers;
