-- Is (npi, brand_name, generic_name) actually unique?
--
-- Same reasoning as the Part B grain check. Note that (npi, brand_name) alone is not the
-- grain even though brand_name is the peer key, because one brand maps to several
-- generics; a duplicate here would mean something stronger than that.

SELECT
    count(*)                AS duplicate_keys,
    coalesce(sum(n), 0)     AS rows_in_duplicate_keys
FROM (
    SELECT count(*) AS n
    FROM fact_part_d_drug
    GROUP BY npi, brand_name, generic_name
    HAVING count(*) > 1
);
