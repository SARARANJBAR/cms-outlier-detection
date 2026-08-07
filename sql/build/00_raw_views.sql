-- Typed views over the raw CMS CSVs. Everything downstream reads these views, never
-- the files.
--
-- Types are declared, not sniffed. DuckDB's own auto-detection over all 36M rows gets
-- two columns wrong for this project's purposes:
--
--   NPI  -> BIGINT. An NPI is an identifier and is never arithmetic. As an integer it
--           invites AVG(npi), sorts numerically, and would drop a leading zero if CMS
--           ever issued one.
--   RUCA -> DOUBLE. It is a categorical code with 22 values ('1', '1.1', '10.6'). As a
--           float, '1' and '1.0' stop being the same key and the join to dim_ruca
--           silently misses.
--
-- Zip5 and State_FIPS auto-detect as VARCHAR only because leading zeros make them
-- non-numeric, so declaring them is not redundant, it is the same decision made on
-- purpose.
--
-- Everything else here matches what auto-detection found over every row. Declaring it
-- anyway turns the type into an assertion: if a future year puts a decimal in
-- Tot_Day_Suply, the build fails with a conversion error instead of quietly widening.

CREATE OR REPLACE VIEW raw_part_b AS
SELECT * FROM read_csv(
    '$part_b_csv',
    header = true,
    types = {
        'Rndrng_NPI': 'VARCHAR',
        'Rndrng_Prvdr_State_FIPS': 'VARCHAR',
        'Rndrng_Prvdr_Zip5': 'VARCHAR',
        'Rndrng_Prvdr_RUCA': 'VARCHAR',
        'Tot_Benes': 'BIGINT',
        'Tot_Srvcs': 'DOUBLE',  -- not an integer: measured minimum 5.5
        'Tot_Bene_Day_Srvcs': 'BIGINT',
        'Avg_Sbmtd_Chrg': 'DOUBLE',
        'Avg_Mdcr_Alowd_Amt': 'DOUBLE',
        'Avg_Mdcr_Pymt_Amt': 'DOUBLE',
        'Avg_Mdcr_Stdzd_Amt': 'DOUBLE'
    }
);

CREATE OR REPLACE VIEW raw_part_d AS
SELECT * FROM read_csv(
    '$part_d_csv',
    header = true,
    types = {
        'Prscrbr_NPI': 'VARCHAR',
        'Prscrbr_State_FIPS': 'VARCHAR',
        'Tot_Clms': 'BIGINT',
        'Tot_30day_Fills': 'DOUBLE',
        'Tot_Day_Suply': 'BIGINT',
        'Tot_Drug_Cst': 'DOUBLE',
        'Tot_Benes': 'BIGINT',  -- null on 55% of rows: CMS blanks small counts here
        'GE65_Tot_Clms': 'BIGINT',
        'GE65_Tot_30day_Fills': 'DOUBLE',
        'GE65_Tot_Day_Suply': 'BIGINT',
        'GE65_Tot_Drug_Cst': 'DOUBLE',
        'GE65_Tot_Benes': 'BIGINT'
    }
);
