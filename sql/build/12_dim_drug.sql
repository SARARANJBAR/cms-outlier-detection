-- Brand-to-generic mapping, as observed in Part D. Measured for 2023: 3,027 brands and
-- 1,779 generics.
--
-- The grain is the (brand, generic) pair, not the brand, because the relationship is
-- many-to-many in both directions: 63.8% of generics have exactly one brand and the rest
-- average 1.77, running as high as 50. Keying this table on brand alone would force a
-- choice of one generic per brand and quietly lose the rest.

CREATE OR REPLACE VIEW dim_drug AS
SELECT
    Brnd_Name   AS brand_name,
    Gnrc_Name   AS generic_name
FROM raw_part_d
GROUP BY brand_name, generic_name;
