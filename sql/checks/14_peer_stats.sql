-- Does the serving layer agree with the fact tables?
--
-- The app reads a distribution from peer_stats and a provider's position from a fact
-- table's *_pct column. Those are computed by different SQL over different artifacts, so
-- nothing structural forces them to agree about which groups are scoreable. If they
-- disagree, the app shows a provider marked on a distribution that was never built, or
-- refuses to mark one that was — silently, in either direction. Hence the two mismatch
-- columns, both asserted at 0.
--
-- The breakpoint monotonicity check is a cheap guard on the percentile computation itself:
-- p10 <= p25 <= ... <= p99 must hold for every row by construction, so a nonzero count
-- means the aggregation is wrong, not that the data is surprising.
--
-- The unscoreable-row counts are reported, not asserted. They cross-check notebook 02,
-- which measured the minimum-peer rule at 2.40% of Part B rows, and they recompute the
-- brand-vs-generic peer key trade on every build: the generic-key count is the alternative
-- ADR 0001 rejected, so the cost of that choice stays a live measurement instead of a
-- sentence someone has to trust.

SELECT
    (SELECT count(*) FROM peer_stats)                                   AS peer_stats_rows,
    (SELECT count(*) FROM peer_stats WHERE dataset = 'part_b')          AS part_b_rows,
    (SELECT count(*) FROM peer_stats WHERE dataset = 'part_d')          AS part_d_rows,

    -- Scoreable groups, counted on each side and differenced.
    abs(
        (SELECT count(*) FROM (
            SELECT specialty, hcpcs_code, place_of_service
            FROM fact_part_b_service WHERE tot_srvcs_pct IS NOT NULL
            GROUP BY ALL
        ))
        - (SELECT count(*) FROM peer_stats
           WHERE dataset = 'part_b' AND measure = 'tot_srvcs')
    )                                                                   AS part_b_group_mismatch,
    abs(
        (SELECT count(*) FROM (
            SELECT specialty, brand_name
            FROM fact_part_d_drug WHERE tot_claims_pct IS NOT NULL
            GROUP BY ALL
        ))
        - (SELECT count(*) FROM peer_stats
           WHERE dataset = 'part_d' AND measure = 'tot_claims')
    )                                                                   AS part_d_group_mismatch,

    (SELECT count(*) FROM peer_stats WHERE n_rows < 30)                 AS groups_below_min_peers,
    (SELECT count(*) FROM peer_stats
      WHERE NOT (p10 <= p25 AND p25 <= p50 AND p50 <= p75
                 AND p75 <= p90 AND p90 <= p95 AND p95 <= p99))         AS non_monotonic_breakpoints,
    (SELECT count(*) FROM peer_stats WHERE n_providers <> n_rows)        AS groups_with_repeat_providers,

    (SELECT count(*) FROM fact_part_b_service
      WHERE tot_srvcs_pct IS NULL)                                      AS part_b_unscoreable_rows,
    (SELECT count(*) FROM fact_part_d_drug
      WHERE tot_claims_pct IS NULL)                                     AS part_d_unscoreable_rows,

    -- What the rejected generic key would have cost, for comparison.
    (SELECT coalesce(sum(n), 0) FROM (
        SELECT count(*) AS n FROM fact_part_d_drug
        GROUP BY specialty, generic_name HAVING count(*) < 30
    ))                                              AS part_d_unscoreable_rows_generic_key;
