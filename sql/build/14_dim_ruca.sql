-- RUCA (Rural-Urban Commuting Area) code and its description. Measured: 22 codes.
--
-- Part B only; Part D carries no RUCA at all. The code is text, not a number: the values
-- include '1', '1.1' and '10.6', and read as a float they collapse and stop joining.
-- 7,600 Part B rows have no RUCA, and those are excluded here rather than given a row.

CREATE OR REPLACE VIEW dim_ruca AS
SELECT
    Rndrng_Prvdr_RUCA                       AS ruca,
    any_value(Rndrng_Prvdr_RUCA_Desc)       AS ruca_desc
FROM raw_part_b
WHERE Rndrng_Prvdr_RUCA IS NOT NULL
GROUP BY ruca;
