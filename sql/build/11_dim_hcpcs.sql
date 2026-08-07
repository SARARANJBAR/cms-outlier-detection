-- One row per HCPCS code seen in Part B. Measured: 6,405 codes for 2023.
--
-- The description travels on every fact row in the source CSV; this is where it stops
-- doing that. sql/checks/13_reference_dims.sql counts codes carrying more than one
-- distinct description, which is the only thing any_value() could hide here.

CREATE OR REPLACE VIEW dim_hcpcs AS
SELECT
    HCPCS_Cd                            AS hcpcs_code,
    any_value(HCPCS_Desc)               AS hcpcs_desc,
    any_value(HCPCS_Drug_Ind) = 'Y'     AS is_drug
FROM raw_part_b
GROUP BY hcpcs_code;
