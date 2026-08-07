-- Is (npi, hcpcs_code, place_of_service) actually unique?
--
-- CMS documents this grain; nothing in the file enforces it. If it is not unique, every
-- peer-group percentile is computed over a set that double-counts some providers, and
-- the whole scoring layer is wrong in a way no downstream query would reveal. So the
-- build asserts it: duplicate_keys must be 0.

SELECT
    count(*)                AS duplicate_keys,
    coalesce(sum(n), 0)     AS rows_in_duplicate_keys
FROM (
    SELECT count(*) AS n
    FROM fact_part_b_service
    GROUP BY npi, hcpcs_code, place_of_service
    HAVING count(*) > 1
);
