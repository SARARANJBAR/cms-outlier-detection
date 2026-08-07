-- Sizes and integrity of the reference dimensions.
--
-- Two of these are assertions, not reports:
--
--   geography_rows_labelled_state must be 50. dim_geography's CASE has an ELSE branch
--   that labels anything unrecognised as a US state, so a code CMS adds later would be
--   silently miscategorised. This is the tripwire on that.
--
--   hcpcs_codes_multi_desc must be 0. dim_hcpcs takes any_value() of the description;
--   if a code carries two, the dimension would show whichever one the scan happened to
--   reach first.
--
-- The rest are row counts, recorded so the build report carries measured numbers rather
-- than the ones written into docs/schema.md by hand.

SELECT
    (SELECT count(*) FROM dim_hcpcs)                                AS dim_hcpcs_rows,
    (SELECT count(*) FROM (
        SELECT HCPCS_Cd FROM raw_part_b
        GROUP BY HCPCS_Cd HAVING count(DISTINCT HCPCS_Desc) > 1
    ))                                                              AS hcpcs_codes_multi_desc,
    (SELECT count(*) FROM dim_drug)                                 AS dim_drug_rows,
    (SELECT count(DISTINCT brand_name) FROM dim_drug)               AS distinct_brands,
    (SELECT count(DISTINCT generic_name) FROM dim_drug)             AS distinct_generics,
    (SELECT count(*) FROM dim_geography)                            AS dim_geography_rows,
    (SELECT count(*) FROM dim_geography WHERE category = 'state')   AS geography_rows_labelled_state,
    (SELECT count(*) FROM dim_ruca)                                 AS dim_ruca_rows;
