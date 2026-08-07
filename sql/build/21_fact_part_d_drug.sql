-- Part D facts. Grain: one row per (npi, brand_name, generic_name).
-- Measured: 26,794,878 rows for 2023.
--
-- brand_name is the peer key: against the generic key it leaves coverage essentially
-- unchanged while halving the within-group cost spread (p90 of p99/p50, 9.74 -> 5.86).
-- generic_name is kept for rollup and browsing, and is part of the grain.
--
-- tot_benes is null on 55.08% of rows, and not at random: CMS blanks it for the small
-- prescriber-drug pairs. Any per-beneficiary metric built on it silently deletes the
-- bottom half of the distribution, which is why the measures the app scores are claims
-- and cost. The column is carried anyway so the app can say what is missing.
--
-- No year column: year is the Hive partition key and lives in the directory path.

CREATE OR REPLACE VIEW fact_part_d_drug AS
SELECT
    Prscrbr_NPI                 AS npi,
    Brnd_Name                   AS brand_name,
    Gnrc_Name                   AS generic_name,
    Prscrbr_Type                AS specialty,
    Prscrbr_State_Abrvtn        AS state,
    Prscrbr_Type_Src            AS specialty_source,
    Tot_Clms                    AS tot_claims,
    Tot_30day_Fills             AS tot_30day_fills,
    Tot_Day_Suply               AS tot_day_supply,
    Tot_Drug_Cst                AS tot_drug_cost,
    Tot_Benes                   AS tot_benes,
    GE65_Sprsn_Flag             AS ge65_suppression_flag,
    GE65_Tot_Clms               AS ge65_tot_claims,
    GE65_Tot_30day_Fills        AS ge65_tot_30day_fills,
    GE65_Tot_Day_Suply          AS ge65_tot_day_supply,
    GE65_Tot_Drug_Cst           AS ge65_tot_drug_cost,
    GE65_Bene_Sprsn_Flag        AS ge65_bene_suppression_flag,
    GE65_Tot_Benes              AS ge65_tot_benes
FROM raw_part_d;
