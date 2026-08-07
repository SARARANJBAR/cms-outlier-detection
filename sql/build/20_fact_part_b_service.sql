-- Part B facts. Grain: one row per (npi, hcpcs_code, place_of_service).
-- Measured: 9,660,647 rows for 2023.
--
-- specialty, state and ruca are copied onto the fact rows on purpose. Sort order and
-- zone-map pruning are properties of the physical Parquet file and only work on columns
-- stored in it, so the denormalization is what buys the pruning that justifies the
-- storage format (docs/schema.md).
--
-- The avg_* columns are per-service averages, not totals. Total Medicare payment for a
-- row is tot_srvcs * avg_medicare_payment; the source has no total column. That is what
-- lets a provider be ordinary on price and extreme on volume.
--
-- No year column: year is the Hive partition key and lives in the directory path, which
-- is the convention and avoids the same column existing twice on read.
--
-- The *_pct columns are each row's percentile rank within its peer group
-- (specialty, hcpcs_code, place_of_service), precomputed here so the app can mark a
-- provider on a distribution without scanning the group. cume_dist() is the empirical
-- CDF — "this fraction of peers bills at or below you" — which is the number a user can
-- read, and the exact inverse of the interpolated breakpoints in peer_stats.
--
-- They are NULL when the peer group has fewer than $min_peers rows. Refusing to rank a
-- provider against a handful of peers is the documented policy (docs/schema.md), and
-- putting the NULL in the column is what makes the policy hold everywhere the data goes
-- rather than only in whichever query remembers to filter.
--
-- total_medicare_payment has no column of its own: it is tot_srvcs * avg_medicare_payment
-- and the app can multiply. Only its rank needs precomputing, because only the rank needs
-- the rest of the peer group to compute.

CREATE OR REPLACE VIEW fact_part_b_service AS
SELECT
    Rndrng_NPI                  AS npi,
    HCPCS_Cd                    AS hcpcs_code,
    Place_Of_Srvc               AS place_of_service,
    Rndrng_Prvdr_Type           AS specialty,
    Rndrng_Prvdr_State_Abrvtn   AS state,
    Rndrng_Prvdr_RUCA           AS ruca,
    Tot_Benes                   AS tot_benes,
    Tot_Srvcs                   AS tot_srvcs,
    Tot_Bene_Day_Srvcs          AS tot_bene_day_srvcs,
    Avg_Sbmtd_Chrg              AS avg_submitted_charge,
    Avg_Mdcr_Alowd_Amt          AS avg_medicare_allowed,
    Avg_Mdcr_Pymt_Amt           AS avg_medicare_payment,
    Avg_Mdcr_Stdzd_Amt          AS avg_medicare_standardized,
    CASE WHEN count(*) OVER peer >= $min_peers
         THEN cume_dist() OVER (peer ORDER BY Tot_Srvcs)
    END                         AS tot_srvcs_pct,
    CASE WHEN count(*) OVER peer >= $min_peers
         THEN cume_dist() OVER (peer ORDER BY Tot_Benes)
    END                         AS tot_benes_pct,
    CASE WHEN count(*) OVER peer >= $min_peers
         THEN cume_dist() OVER (peer ORDER BY Avg_Mdcr_Stdzd_Amt)
    END                         AS avg_medicare_standardized_pct,
    CASE WHEN count(*) OVER peer >= $min_peers
         THEN cume_dist() OVER (peer ORDER BY Tot_Srvcs * Avg_Mdcr_Pymt_Amt)
    END                         AS total_medicare_payment_pct
FROM raw_part_b
WINDOW peer AS (PARTITION BY Rndrng_Prvdr_Type, HCPCS_Cd, Place_Of_Srvc);
