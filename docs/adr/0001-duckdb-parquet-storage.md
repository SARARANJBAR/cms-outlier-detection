# 1. DuckDB over Parquet as the analytical engine and storage format

Date: 2026-08-04

Status: Accepted — amended 2026-08-06 and twice on 2026-08-07, see [Amendments](#amendments)

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

- **The precomputed ranks are the biggest storage lever in the build, and rounding is how
  it is pulled.** Each fact row carries its percentile rank within its peer group, so the
  drill-down path is a lookup too. At full DOUBLE precision those columns were 30% of
  `fact_part_b_service` and 36% of `fact_part_d_drug`, because `cume_dist()` over a large
  group gives nearly every row a distinct value — 19.6M distinct in one Part D column,
  128 MB for it alone. Rounded to basis points, one digit finer than the app displays, it is
  10,001 distinct values and 43 MB. The core layer landed at **941 MB** rather than 1,146 MB,
  and the rank columns at 19.3% / 19.6%.

  Two measurements from that exercise belong in the writeup rather than only here. Narrowing
  the type to `FLOAT` instead of rounding makes the file *larger* (45.9 MB against 43.3), so
  the dictionary is what pays and the obvious move is the wrong one. And before rounding,
  `tot_claims_pct` cost 43 MB against `cost_per_claim_pct`'s 128 MB off identical rows,
  because claims are integers with many ties and cost is continuous — the compressibility of
  a derived column is a property of the tie structure of what it derives from.

- **A number in the 2026-08-06 amendment above was wrong.** It said the brand peer key
  "leaves coverage essentially unchanged" against the generic key. Measured on the built
  facts: the generic key costs 0.71% of Part D rows and the brand key 0.91% — 53,018 more
  rows unscoreable, a 28% relative increase on a small base. The decision does not change,
  because the spread argument that motivated it (p90 of `p99/p50`, 9.74 → 5.86) is the far
  larger effect, but "essentially unchanged" was not what the data said.
  `sql/checks/14_peer_stats.sql` now recomputes both figures on every build so the trade
  stays a measurement.

### 2026-08-07 — the optimization measurements

[`notebooks/03_optimization.ipynb`](../../notebooks/03_optimization.ipynb) measures the
levers, reading the fact table over a local HTTP server that counts every byte it serves —
so the drill-down numbers are real range requests, not a model of them.

| Lever | Measured |
|---|---|
| 1. CSV → typed Parquet | 128x faster warm, 9.6x smaller on disk |
| 2. Sort order → zone-map pruning | 26x fewer bytes over HTTP than the same data unsorted (0.56% of the file vs 14.62%) |
| 4. Projection pushdown | 3.6x fewer bytes than `SELECT *` |
| 6. Precomputed `peer_stats` | 23.8x faster than the same percentile over the remote facts |
| 3. Partition pruning | not measured — one partition loaded |

Four things worth recording beyond the table.

- **On the remote path, latency is dominated by round trips, not bytes.** The live
  percentile over the remote facts reads only 555 KB — but it takes 68.8 ms, while the same
  query over the *local* 319 MB file takes 4.5 ms. Roughly 200 range requests, each with its
  own round trip, cost far more than the bytes inside them. This does not contradict the
  second amendment's "bytes read is the right metric" — bytes are what a Release asset and a
  free tier are billed in — but it adds that **request count is the right metric for
  latency**, and the two do not always move together. Any future tuning of the drill-down
  path should be about merging requests, not shrinking them.

- **`peer_stats` earns its place against the right baseline.** Against the live percentile
  over *local* Parquet it is only ~1.6x faster, because lever 2 already made that query fast.
  Against the deployment baseline — the same percentile over the remote facts, which is what
  an app without a serving layer would do — it is 23.8x. The comparison that flatters the
  live path is the one that does not describe the deployment, so the honest framing is the
  remote one.

- **The "~1 row group out of ~79" prediction was too optimistic, for an instructive reason.**
  Measured, 8 of 79 row groups can contain `Nurse Practitioner`. The prediction reasoned from
  peer-group size (median 4 rows), but the zone map is per column: the `specialty` statistics
  prune to the specialty's *whole* span, and the largest specialty spans 8 row groups. Sorting
  by `(specialty, code)` still delivers the win — 0.56% of the file — just through the
  combination of both columns' statistics rather than one row group.

- **Sorting also made the file 21% smaller** — 319.5 MB against 407.3 MB for identical rows in
  random order. Sort order was chosen for pruning; better compression came free, because runs
  of repeated values are what both mechanisms exploit.

## Open questions

- **Cold latency over HTTP.** Remote Parquet reads have not been exercised at all. If
  first-interaction latency turns out to be unacceptable, the fallback is shipping
  `dim_provider` alongside `peer_stats` — see the third amendment for why that is known to
  fit — but it is not chosen until the number exists.
- **Cold-cache timings.** Every local timing is still warm; dropping the macOS page cache
  needs `sudo purge`, which the notebook cannot do unattended, so it reports the gap instead
  of filling it with a mislabelled number. The lever-1 speedup (128x) should be read as
  warm-only. This matters less than it did when it was written: the drill-down path is now
  measured in bytes and requests over HTTP, which is not a cache-warmth question.

## Environment

DuckDB 1.5.5, added to `pyproject.toml` as a runtime dependency.
