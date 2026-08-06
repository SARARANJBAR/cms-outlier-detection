# 1. DuckDB over Parquet as the analytical engine and storage format

Date: 2026-08-04

Status: Accepted — amended 2026-08-06, see [Amendments](#amendments)

## Context

The project needs a query engine and a storage format for provider-level Medicare
data. One year (2023) is on disk: Part B at 9,660,647 rows / 2.9 GB CSV, Part D at
26,794,878 rows / 3.6 GB CSV — ~36M rows and 6.5 GB total. CMS publishes each calendar
year as a separate file, so extending to five years would put the project in the
110–180M row range.

Two things run on top of that data:

1. A SQL layer — joins across provider, procedure/drug, and geography attributes, plus
   window functions computing where a provider sits relative to its peer group
   (same specialty, same geography, same code).
2. A Streamlit dashboard, which implies interactive latency on filter changes.

The workload is read-only after a batch load, single-writer, and analytical: large
`GROUP BY` aggregations and percentile computations over tens of millions of rows.
There are no transactions and no concurrent writers.

The candidates considered were PostgreSQL and DuckDB.

## Decision

**DuckDB as the engine of record, Hive-partitioned Parquet as the storage format, and a
precomputed peer-statistics table as the serving layer for the app.**

Reasoning:

1. **Workload shape.** This is OLAP. Column pruning and vectorized execution are the
   properties the workload needs; a row-store OLTP engine does not provide them.

2. **The optimizations that matter here are warehouse optimizations.** File layout,
   sort order and zone-map pruning, partition pruning, projection pushdown, and
   spill-to-disk behavior map onto Snowflake clustering keys, BigQuery clustering, and
   Databricks liquid clustering. B-tree index tuning does not. For healthcare analytics
   work, where the platform is typically a columnar warehouse, the DuckDB material is
   the more transferable of the two.

3. **Indexing addresses the wrong half of the problem.** A B-tree index helps the
   single-provider point lookup (`WHERE npi = ? AND hcpcs_code = ?`). It does nothing
   for the peer-group percentile computation, which is the actual cost driver. The
   correct fix for that is a pre-aggregated peer-statistics table — which is not an
   index story, and which is built the same way in either engine.

4. **Hosting economics.** Managed Postgres free tiers are reported to be well under
   1 GB per project (Neon 0.5 GB, Supabase 500 MB — to be verified before either
   figure is cited in user-facing docs). A single year of this data in Postgres, with
   indexes, is far beyond that, so hosting would mean a recurring bill. Parquet plus a
   small precomputed `.duckdb` serving file is a plausible fit for free Streamlit
   Community Cloud hosting, subject to the open question below.

5. **Reproducibility.** The dataset cannot live in the repo under either option, so a
   download-and-build script is required regardless. Postgres adds a container and a
   multi-minute `COPY` on top of that, and each additional setup step costs reviewers.

6. **Testability.** DuckDB runs in-process, so the project's SQL is unit-testable
   against a sampled fixture in CI with no service container. The queries actually get
   executed on every push rather than being tested only by hand.

## Alternatives rejected

**PostgreSQL as the database of record.** Its strongest argument was recognition value
— it is the more common keyword on data-role job listings — plus a conventional query
optimization case study (index scan vs. sequential scan, declarative partitioning,
`EXPLAIN (ANALYZE, BUFFERS)`). That argument did not survive point 3 above: the
optimization it showcases is not the one this workload needs. The recognition-value
concern is real but small, and is addressed by naming the underlying warehouse concepts
(partition pruning, clustering, projection pushdown) in the writeup rather than leaning
on the product name.

**Parquet + DuckDB for analytics, with Postgres kept solely to stage the optimization
case study.** Rejected as transparently ornamental — a case study on an engine the
project does not otherwise use is not evidence of anything.

## Consequences

- Storage becomes a build artifact: raw CSV is converted to typed, Hive-partitioned
  Parquet by a script in the repo. `data/raw/` stays gitignored; the Parquet build is
  reproducible from it.
- The optimization writeup is framed as *"how I got interactive latency on 36M rows,"*
  with each step measured — not as a manufactured slow-query-then-fix narrative. Levers
  to measure: CSV → typed Parquet + ZSTD; sort order and zone-map pruning; Hive
  partitioning by year; projection pushdown; larger-than-memory behavior; live
  percentile computation vs. the precomputed peer-stat table.
- Partition and sort keys are a schema-design decision that depends on measured
  cardinality per specialty and per procedure/drug code. Deferred until the
  distribution exploration is done.
- Per project rule 7, no figure above or in any downstream doc is written as fact until
  it has been measured. The row counts and CSV sizes in the Context section were
  measured; the hosting figures were not, and are marked accordingly.

## Amendments

### 2026-08-06 — after the full-data exploration

The decision itself stands unchanged. Three things it asserted or deferred were
measured in [`notebooks/02_distributions.ipynb`](../../notebooks/02_distributions.ipynb),
and two of them came out differently than this ADR assumed. Recorded here rather than
edited into the text above, so the record of what we believed when we decided survives.

- **Sort key: confirmed, and the reasoning in the original open question was wrong.**
  That question worried that peer groups being *small* relative to a Parquet row group
  would weaken the pruning win. That has it backwards: what determines pruning is
  whether a filtered group's rows are contiguous on disk, so small groups make the sort
  pay off *more*, not less. Measured: peer groups have a median of 4 rows (Part B) and 5
  (Part D), and Part B's largest group (96,640 rows) fits inside a single 122,880-row
  row group. Sorted by `(specialty, code)`, a single-peer-group filter should touch ~1
  row group out of ~79 (Part B) / ~218 (Part D). That is now a prediction lever 2 must
  verify, not an open question.

- **Lever 1 is probably not "the largest single win."** This ADR's consequences list
  led with CSV → typed Parquet. Measured, a full scan of the 2.9 GB Part B CSV takes
  ~1.2–1.5 s (warm cache, DuckDB 1.5.5, 15 threads), which is already close to
  interactive for a single pass. The honest headline is more likely sort order (lever 2)
  and the pre-aggregated peer-stat table (lever 6). The writeup should follow the
  measurements rather than this ordering.

- **Partition and sort keys are no longer deferred.** Partition by year only; nothing in
  the measured cardinality argues for a second level. Peer key measured at
  `(specialty, code, place_of_service)` for Part B and `(specialty, generic)` for Part D:
  adding state would make 19.35% of Part B rows unscoreable at a minimum peer-group size
  of 30, against 1.95% for the base key, while RUCA costs 4.84% and captures the
  urban/rural density that plausibly drives utilization. State is a filter dimension,
  not a grouping one.

- **One provider dimension, not two.** Not an open question in the original, but it
  affects the schema this ADR implies: 706,614 NPIs appear in both datasets, no provider
  carries more than one specialty within a dataset, and specialty strings agree across
  datasets for 100% of the overlap. No crosswalk is required.

## Open questions

- **Streamlit Community Cloud resource limit.** The serving artifact must fit within
  the platform's app resource limit (reported around 1 GB). Both the true limit and the
  size of the peer-stats artifact need to be measured before hosting is assumed viable.
- **Larger-than-memory behavior.** Only 2023 is downloaded, so the multi-year spill
  scenario cannot be exercised yet. Either pull additional years or drop that lever from
  the writeup; do not describe behavior that has not been observed.
- **Cold-cache timings.** Every timing measured so far is warm — the files had been read
  earlier in the same session. The CSV-vs-Parquet comparison needs both formats measured
  the same way, cold and warm, or the speedup figure is meaningless.

## Environment

DuckDB 1.5.5, added to `pyproject.toml` as a runtime dependency.
