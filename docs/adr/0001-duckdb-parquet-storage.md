# 1. DuckDB over Parquet as the analytical engine and storage format

Date: 2026-08-04

Status: Accepted — amended 2026-08-06 and 2026-08-07, see [Amendments](#amendments)

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
  partitioning by year; projection pushdown; live percentile computation vs. the
  precomputed peer-stat table.
- Scope is **one year (2023)**. Partitioning by year is still the right layout — it is
  how CMS publishes the data and it costs nothing — but with a single partition loaded,
  nothing here measures multi-year behavior, and no claim is made about it.
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
  `(specialty, code, place_of_service)` for Part B and `(specialty, brand_name)` for
  Part D (notebook section 9b — brand rather than generic, because it leaves coverage
  essentially unchanged while halving the p90 within-group cost spread, 9.74 → 5.86):
  adding state would make 19.35% of Part B rows unscoreable at a minimum peer-group size
  of 30, against 1.95% for the base key, while RUCA costs 4.84% and captures the
  urban/rural density that plausibly drives utilization. State is a filter dimension,
  not a grouping one.

- **One provider dimension, not two.** Not an open question in the original, but it
  affects the schema this ADR implies: 706,614 NPIs appear in both datasets, no provider
  carries more than one specialty within a dataset, and specialty strings agree across
  datasets for 100% of the overlap. No crosswalk is required.

### 2026-08-06 — how the artifacts are delivered, and what the writeup claims

Two things this ADR left open turned out to be settled by arithmetic rather than by
measurement, and settling them changes what the optimization writeup is about.

- **The fact tables were never going to ship in the repo.** `peer_stats` is on the order
  of 10⁵ rows — roughly 25k qualifying peer groups times a handful of measures — and will
  be a few MB. The fact tables are 36M rows; even well compressed that is hundreds of MB,
  against GitHub's hard 100 MB per-file limit, and Streamlit Community Cloud deploys from
  a repo. So there is no "will it fit" question to answer later: **`peer_stats` ships in
  the repo, and the fact-table Parquet is published as a GitHub Release asset** (2 GB per
  file) and read remotely by DuckDB via `httpfs`. Both sizes above are estimates and are
  marked as such until the build reports real ones (rule 7).

- **That makes the layout levers load-bearing rather than illustrative.** Reading Parquet
  over HTTP, DuckDB issues range requests, so zone-map pruning and projection pushdown
  decide how many bytes the live app downloads per interaction — not merely how many
  seconds a local benchmark takes. The right metric for levers 2 and 4 is therefore
  **bytes read**, which is also the unit the sort-key prediction above is stated in
  (~1 row group out of ~79).

- **The claim the writeup defends** is *"interactive percentiles over 36M rows, served
  from free-tier hosting,"* along the two paths measured separately: the **serving** path,
  where precomputation replaces a live percentile scan (~1.3 s per CSV pass today, against
  a keyed lookup), and the **drill-down** path, where a provider's own rows are fetched
  from remote Parquet under the layout levers. The original framing — "how I got
  interactive latency on 36M rows" — is not false, but it is misleading about the serving
  path, which never touches 36M rows at query time.

### 2026-08-07 — the build ran, and the estimates became measurements

[`src/cms_outliers/sql/build.py`](../../src/cms_outliers/sql/build.py) now produces the
Parquet layer. Full numbers in [`docs/build_report.md`](../build_report.md), which the
build regenerates; the four that change what this ADR says:

- **The delivery split has real sizes behind it.** `fact_part_b_service` is 247.5 MB and
  `fact_part_d_drug` 503.3 MB, from the 6.5 GB of CSV. "Hundreds of MB" in the amendment above
  was right: both are several times GitHub's 100 MB file limit and both sit comfortably
  inside a Release asset's 2 GB. `peer_stats` is still an estimate, because it does not
  exist yet.

- **The row-group prediction held exactly.** 79 row groups in Part B against the ~79
  predicted, and 219 in Part D against ~218. Lever 2 now has an exact denominator to state
  its pruning against rather than an arithmetic guess.

- **Both fact grains are unique, and the cross-dataset attribute question is answered.**
  Zero duplicate keys on `(npi, hcpcs_code, place_of_service)` and on
  `(npi, brand_name, generic_name)`. Across the 706,614 overlapping NPIs there are zero
  name disagreements and zero city disagreements — exact string equality, no normalization
  needed — and no NPI carries more than one name or city *within* a dataset either, so
  collapsing the fact rows into one provider row loses nothing. Two open questions closed.

- **The HTTP fallback turns out not to need narrowing.** The open question below proposed
  a *narrow* provider-lookup table if remote reads are too slow. Measured, the entire
  `dim_provider` is 38.5 MB — under the 100 MB limit as it stands. That is a large thing to
  put in a git repo and not the first choice, but the fallback is known to be available
  without designing a reduced table for it.

- **`peer_stats` is 3.3 MB, and the serving path is real.** 94,727 rows — 27,529 qualifying
  peer groups across four Part B measures and three Part D ones — against the "on the order
  of 10⁵ rows and a few MB" estimate above. It ships in the repo at `data/serving/`, so the
  app answers its main question from 3.3 MB and never opens a fact table to draw a
  distribution. That is lever 6, and it is now an artifact rather than a plan.

- **The precomputed ranks cost a third of the fact files, and ties are why.** Each fact row
  carries its percentile rank within its peer group, so the drill-down path is a lookup too.
  Those columns are 30% of `fact_part_b_service` (which grew 247.5 → 352.2 MB) and 36% of
  `fact_part_d_drug` (503.3 → 794.0 MB), and the spread between them is the interesting
  part: `tot_claims_pct` is 43 MB while `cost_per_claim_pct` is 128 MB, off the same 26.8M
  rows. Claims are small integers with many ties, so `cume_dist()` returns few distinct
  values per group and the column encodes well; cost is continuous, so nearly every row gets
  its own value. The core layer is now 1.12 GB, still inside a Release asset's 2 GB but with
  a third of the headroom spent. Rounding the ranks to display precision would recover most
  of it and is worth measuring before publishing — noted, not done.

- **A number in the 2026-08-06 amendment above was wrong.** It said the brand peer key
  "leaves coverage essentially unchanged" against the generic key. Measured on the built
  facts: the generic key costs 0.71% of Part D rows and the brand key 0.91% — 53,018 more
  rows unscoreable, a 28% relative increase on a small base. The decision does not change,
  because the spread argument that motivated it (p90 of `p99/p50`, 9.74 → 5.86) is the far
  larger effect, but "essentially unchanged" was not what the data said.
  `sql/checks/14_peer_stats.sql` now recomputes both figures on every build so the trade
  stays a measurement.

## Open questions

- **Cold latency over HTTP.** Remote Parquet reads have not been exercised at all. If
  first-interaction latency turns out to be unacceptable, the fallback is shipping
  `dim_provider` alongside `peer_stats` — see the third amendment for why that is known to
  fit — but it is not chosen until the number exists.
- **Cold-cache timings.** Every timing measured so far is warm — the files had been read
  earlier in the same session. The CSV-vs-Parquet comparison needs both formats measured
  the same way, cold and warm, or the speedup figure is meaningless.

## Environment

DuckDB 1.5.5, added to `pyproject.toml` as a runtime dependency.
