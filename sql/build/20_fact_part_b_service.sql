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
    Avg_Mdcr_Stdzd_Amt          AS avg_medicare_standardized
FROM raw_part_b;
