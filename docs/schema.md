# Schema

How the CMS data is modelled once it leaves raw CSV. Decisions here follow from the
measurements in [`notebooks/02_distributions.ipynb`](../notebooks/02_distributions.ipynb)
and the storage decision in [ADR 0001](adr/0001-duckdb-parquet-storage.md).

## Shape: a star schema, with two fact tables

Two **fact** tables hold the measurements — one row per thing that happened, with
numbers on it. Several small **dimension** tables hold the descriptive attributes that
those rows point at (who the provider is, what the code means, where the state is).
This is the standard warehouse layout, and it is what makes the app's queries simple:
filter the dimensions, aggregate the facts.

```
                      dim_hcpcs        dim_drug
                          |                |
                          v                v
   dim_provider --> fact_part_b_service   fact_part_d_drug <-- dim_provider
                          |                |
                          +--> peer_stats <+
                                (serving layer)

   dim_geography, dim_ruca  — referenced by both fact tables
```

`peer_stats` is not a normal dimension or fact. It is a **precomputed serving layer**:
the percentile breakpoints for every peer group, calculated once at build time so the
app never computes a percentile over 36M rows while a user waits. It is lever 6 of the
optimization writeup and the main reason the dashboard can be fast.

## Keys: natural, not surrogate

We use the real identifiers — NPI, HCPCS code, brand name — as keys, rather than
inventing integer surrogate keys.

In a traditional row-store warehouse, surrogate keys save space and speed up joins.
Neither applies here: Parquet dictionary-encodes repeated values, so a repeated
10-digit NPI costs little more than a repeated integer, and the columnar engine is not
join-bound. Adding surrogate keys would mean maintaining a mapping table and making
every query indirect, for no measured benefit. If a measurement later shows otherwise,
that is worth revisiting.

## Denormalization: specialty, state and RUCA live on the fact rows

Textbook star schema would put specialty only on `dim_provider`. We copy it onto both
fact tables instead, along with state and RUCA. This is deliberate.

[ADR 0001](adr/0001-duckdb-parquet-storage.md) sorts the Parquet files by
`(specialty, code)` so that a query filtered to one peer group can skip almost every
row group in the file. Sort order and zone-map pruning are properties of the physical
file: they only work on columns actually stored in it. If specialty lived only in the
provider dimension, the engine would have to read the provider table and then all fact
rows to discover which ones matched — and the pruning that justifies the storage format
would never trigger.

The cost is duplication across ~36M rows, which in a row store would be expensive.
Measured in the built Parquet ([`build_report.md`](build_report.md)), it comes to **3.4% of
`fact_part_b_service` and 3.6% of `fact_part_d_drug`** — and the split inside that number
is the interesting part:

| Column | Part B bytes | Share |
|---|---:|---:|
| `specialty` | 9,211 | 0.004% |
| `state` | 6,842,796 | 2.7% |
| `ruca` | 1,920,553 | 0.7% |

`specialty` — 9 KB for 9.66M rows — is free because it *is* the leading sort key, so the
column arrives as a few long runs and encodes to nothing. `state` and `ruca` are not
sorted, so they cost what an ordinary low-cardinality column costs. The lesson is that the
sort key and the cheap column are the same decision, not two.

---

## `dim_provider`

One row per provider. **1,572,829 rows** — the union of 1,175,281 Part B providers and
1,104,162 Part D prescribers, overlapping on 706,614.

A single provider table is safe because of three measured facts: no NPI carries more
than one specialty within a dataset; for all 706,614 NPIs in both datasets the specialty
strings agree exactly (100%); and every NPI carries exactly one entity code. No
crosswalk is needed.

| Column | Type | Source | Notes |
|---|---|---|---|
| `npi` | VARCHAR | both | Natural key. Stored as text — it is an identifier, not a quantity |
| `entity_type` | VARCHAR | Part B | `I` individual, `O` organization. Null for Part-D-only providers |
| `last_or_org_name` | VARCHAR | both | |
| `first_name` | VARCHAR | both | |
| `middle_initial` | VARCHAR | Part B | Part D has no middle-initial column at all |
| `credentials` | VARCHAR | Part B | |
| `specialty` | VARCHAR | both | 104 values in Part B, 175 in Part D — one shared taxonomy, Part D exercises more of it |
| `street1`, `street2`, `zip5` | VARCHAR | Part B | Part B only. `zip5` **must** be text — leading zeros |
| `city`, `state` | VARCHAR | both | |
| `country` | VARCHAR | Part B | 395 Part B rows are non-US |
| `ruca`, `ruca_desc` | VARCHAR | Part B | Text, not float — a categorical code (22 values). 7,600 Part B rows null |
| `medicare_participating` | VARCHAR | Part B | Y/N |
| `in_part_b`, `in_part_d` | BOOLEAN | derived | Which datasets this provider appears in |

**Build rule.** Part B carries the richer attribute set (full address, RUCA, credentials,
participation); Part D has only city and state. Where a provider appears in both, prefer
the Part B values and fall back to Part D. Providers appearing only in Part D will have
null address detail, and that is expected, not an error.

**Build-time check, not an assumption:** whether name and city agree between the two
datasets for the overlapping NPIs is counted and reported by
[`sql/checks/12_provider_attributes.sql`](../sql/checks/12_provider_attributes.sql)
rather than assumed, along with whether any single NPI carries more than one name or city
*within* a dataset — which is what would make the `any_value()` collapse lossy. Results in
[`build_report.md`](build_report.md).

## `fact_part_b_service`

**9,660,647 rows** for 2023. Grain: one row per (provider, HCPCS code, place of service).

| Column | Type | Notes |
|---|---|---|
| `npi` | VARCHAR | → `dim_provider` |
| `hcpcs_code` | VARCHAR | → `dim_hcpcs` |
| `place_of_service` | VARCHAR | `F` facility, `O` office. Part of the grain *and* the peer key |
| `specialty` | VARCHAR | Denormalized — sort key, see above |
| `state`, `ruca` | VARCHAR | Denormalized — filter dimensions |
| `tot_benes` | BIGINT | Distinct patients. Minimum 11 — see suppression below |
| `tot_srvcs` | DOUBLE | **Not an integer.** Measured minimum 5.5; service counts are fractional |
| `tot_bene_day_srvcs` | BIGINT | Patient-days |
| `avg_submitted_charge` | DOUBLE | What the provider billed |
| `avg_medicare_allowed` | DOUBLE | What Medicare allows |
| `avg_medicare_payment` | DOUBLE | What Medicare actually paid |
| `avg_medicare_standardized` | DOUBLE | Payment with geographic cost adjustment removed — the column for cross-region comparison |
| `tot_srvcs_pct`, `tot_benes_pct`, `avg_medicare_standardized_pct`, `total_medicare_payment_pct` | DOUBLE | Precomputed percentile rank within the peer group, rounded to basis points — see below. NULL when the group has fewer than 30 rows |
| `year` | BIGINT | Partition key. Not stored in the file — the Hive directory `year=2023/` carries it, which is the convention and stops the column existing twice on read. DuckDB infers it as BIGINT from the path |

The `avg_*` columns are **per-service averages, not totals**. Total Medicare payment for
a row is `tot_srvcs * avg_medicare_payment` — there is no total column. This distinction
decides what an outlier is: a provider can be ordinary on price and extreme on volume.
`total_medicare_payment_pct` exists precisely because that product is where the volume
outlier shows up, and a rank is the one thing the app cannot compute from a single row.

## `fact_part_d_drug`

**26,794,878 rows** for 2023. Grain: one row per (`npi`, `brand_name`, `generic_name`).

| Column | Type | Notes |
|---|---|---|
| `npi` | VARCHAR | → `dim_provider` |
| `brand_name` | VARCHAR | → `dim_drug`. **The peer key** — see below |
| `generic_name` | VARCHAR | → `dim_drug`. Kept for rollup and browsing |
| `specialty` | VARCHAR | Denormalized — sort key |
| `state` | VARCHAR | Denormalized — filter dimension |
| `specialty_source` | VARCHAR | Whether specialty came from the claim or the NPI registry |
| `tot_claims` | BIGINT | Never null. Minimum 11 |
| `tot_30day_fills` | DOUBLE | |
| `tot_day_supply` | BIGINT | |
| `tot_drug_cost` | DOUBLE | Never null. Already a total, unlike Part B |
| `tot_benes` | BIGINT | **Null on 55.08% of rows** — see suppression below |
| `ge65_*` | various | The 65+ breakdown and its suppression flags |
| `tot_claims_pct`, `tot_drug_cost_pct`, `cost_per_claim_pct` | DOUBLE | Precomputed percentile rank within the peer group, rounded to basis points. No per-beneficiary measure is ranked, because `tot_benes` is suppressed on 55.08% of rows |
| `year` | BIGINT | Partition key — carried by the Hive directory, not stored in the file |

## Reference dimensions

| Table | Rows | Contents |
|---|---|---|
| `dim_hcpcs` | 6,405 | `hcpcs_code`, `hcpcs_desc`, `is_drug` (BOOLEAN, from CMS's Y/N indicator) |
| `dim_drug` | 3,144 | `brand_name`, `generic_name`. Grain is the **pair**, covering 3,027 brands and 1,779 generics: 63.8% of generics have exactly one brand; the rest average 1.77 and run to 50, so neither column alone is a key |
| `dim_geography` | 62 | `state`, `state_fips`, `category`, `is_us_state`. Category is one of `state` (50), `district` (DC), `territory` (AS GU MP PR VI), `freely_associated` (FM), `military` (AA AE AP), `unknown` (XX ZZ) |
| `dim_ruca` | 22 | `ruca`, `ruca_desc`. Part B only — Part D carries no RUCA |

`dim_geography` having 62 rows rather than 50 is the sort of thing that silently breaks a
join later. It is a table, with a category and a flag, so the non-states have to be handled
deliberately. The build asserts that exactly 50 rows land in the `state` category, because
the SQL's `ELSE` branch would otherwise label a code CMS adds later as a US state.

## `peer_stats` — the serving layer

One row per **peer group per measure**, holding the precomputed distribution. Long form:
adding a new measure is a new row, not a schema change, and the app has one query path
for both datasets.

| Column | Type | Notes |
|---|---|---|
| `dataset` | VARCHAR | `part_b` or `part_d` |
| `specialty` | VARCHAR | |
| `code` | VARCHAR | HCPCS code, or brand name for Part D |
| `place_of_service` | VARCHAR | Part B only; null for Part D |
| `measure` | VARCHAR | Part B: `tot_srvcs`, `tot_benes`, `avg_medicare_standardized`, `total_medicare_payment`. Part D: `tot_claims`, `tot_drug_cost`, `cost_per_claim` |
| `n_providers` | BIGINT | Distinct NPIs in the group |
| `n_rows` | BIGINT | Rows the breakpoints were computed over. Differs from `n_providers` only in Part D — see below |
| `p10`…`p99` | DOUBLE | Breakpoints: 10, 25, 50, 75, 90, 95, 99. `p50` is the median |
| `year` | BIGINT | Partition key — carried by the Hive directory |

**The measure names are a contract with the fact tables.** Measure `tot_srvcs` here is
`fact_part_b_service.tot_srvcs_pct` there: this table holds the distribution, that column
holds one provider's position in it, and the app's whole screen is those two things drawn
together. They are computed by different SQL over different artifacts, so
[`sql/checks/14_peer_stats.sql`](../sql/checks/14_peer_stats.sql) asserts they agree on
exactly which groups are scoreable — otherwise a provider could be marked on a distribution
that was never built, silently.

**The rank is `cume_dist()`** — the empirical CDF, "this fraction of peers is at or below
you." That is the number a user can read off a sentence, and it is the exact inverse of the
interpolated breakpoints stored here. `percent_rank()` would have been the more familiar
function name and assigns 0 to the group minimum, which reads worse and buys nothing.

**Only groups with `n_rows >= 30` are written**, and the matching fact-table `*_pct`
columns are NULL below that threshold, so the policy holds wherever the data goes rather
than only in whichever query remembers to filter. Measured cost: it excludes 77.9% of
Part B peer groups but only **2.40% of rows** at the full peer key, and 73.2% of Part D
groups / **0.91% of rows**. Refusing to rank a provider against a handful of peers is
nearly free here.

**Why `n_providers` and `n_rows` are both stored.** In Part D the grain includes
`generic_name` but the peer key does not, so a prescriber with two generics under one brand
contributes two rows to the same peer group. The threshold and the percentiles are on rows,
because that is what the coverage figures above were measured on; `n_providers` is carried
so the difference is visible instead of implied. Part B's peer key is a superset of its
grain, so the two are always equal there.

This is the artifact that **ships inside the repo**, at `data/serving/`, while the fact
tables go to the gitignored `data/parquet/` and out as a Release asset (ADR 0001, second
amendment). One directory per delivery mechanism. Size in
[`build_report.md`](build_report.md).

## Peer keys

The comparison set an outlier is scored against.

| Dataset | Peer key | Why |
|---|---|---|
| Part B | `(specialty, hcpcs_code, place_of_service)` | Place of service is part of the natural grain and costs almost nothing: 1.95% → 2.40% of rows unscoreable |
| Part D | `(specialty, brand_name)` | Costs 0.91% of rows against the generic key's 0.71% — 53,018 more rows unscoreable — and buys a much tighter within-group cost spread: p90 of `p99/p50` falls 9.74 → 5.86, max 409.43 → 172.50. The build recomputes both figures every run |

**State is not in the peer key.** Adding it makes 19.35% of Part B rows unscoreable,
against 1.95% for the base key — a tenth of the dataset, to buy a comparison that
`avg_medicare_standardized` already makes valid by removing geographic cost adjustment.
State remains a filter and display dimension.

**RUCA is the better geographic refinement if one is wanted** — 4.84% rather than 19.35%,
and it captures the urban/rural patient density that plausibly drives utilization rather
than an administrative boundary. Planned as a documented secondary view, not the default.

## Two suppression rules, and why they are not symmetric

CMS censors small counts to prevent patient re-identification. The two datasets do it
differently, and the difference matters more than it first appears.

**Part D blanks a column.** `tot_benes` is null on 14,757,509 of 26,794,878 rows
(**55.08%**), and `ge65_tot_benes` on 88%. `tot_claims` and `tot_drug_cost` are never
null. This missingness is visible — and it is not random, it is systematically the small
prescriber-drug pairs. **Any per-beneficiary Part D metric silently deletes the bottom
half of the distribution.** Build Part D metrics on claims and cost.

**Part B removes the row.** Measured: `min(tot_benes)` is exactly 11 with zero rows
below, and the low end runs 477,670 rows at 11, 423,446 at 12, 380,812 at 13 —
monotonically decreasing. A natural distribution rises toward the low end; this one peaks
at the threshold and stops. The distribution has been cut.

The consequence is that **Part B's censoring is invisible**. It produces no nulls at all.
A provider who performs a procedure 8 times does not appear as missing data — they appear
as a provider who does not perform it. Low-volume providers are systematically
under-represented and nothing in the file says so.

The app must disclose this, because a user reading a provider profile is looking at a
censored record. We cannot currently size the bias: the Part-B-only population is
confounded with specialty (surgeons and radiologists legitimately do not prescribe), so
disclosure is the honest option, not quantification.

## Scoring implications

Recorded here because they constrain what `peer_stats` needs to hold.

**Mean and standard deviation are unusable.** `mean/median` is 6.4 for Part B `tot_srvcs`
and 12.4 / 12.7 for Part D drug cost and cost-per-claim. The extremes are genuine — one
Part B row carries $299M in total payment, one Part D row $81.8M in drug cost. A z-score
would measure the tail against itself. Scoring is **percentile rank**, which is why
`peer_stats` stores breakpoints rather than mean and standard deviation.

**Median and MAD were considered and dropped.** A modified z-score,
`0.6745 · (x − median) / MAD`, is the usual robust alternative, and the notebook's first
reading proposed it alongside percentile rank. It does not survive a measured fact from
the same notebook: within the 25 largest Part B peer groups, `p99/p50` on standardized
payment is 1.00–1.23, because Medicare fixes the rate. In groups that tight, MAD is zero
or near it and the score is undefined or explosive — precisely on the measure where a
user is most likely to ask. Percentile rank is already robust to the heavy tail, needs no
zero-MAD special case, and is the number the dashboard actually displays. One scoring
method, not two.

**In Part B the signal is utilization, not price.** Within the 25 largest peer groups,
`p99/p50` on standardized payment is 1.00–1.23 — Medicare sets the rate — while the same
ratio on service count runs 4.1–14.2.

Where price *does* vary within a group, that is itself worth surfacing: Diagnostic
Radiology / 71046 (chest X-ray) sits at 3.34 against ~1.1 for everything around it.

## How this is built

[`src/cms_outliers/sql/build.py`](../src/cms_outliers/sql/build.py) turns the raw CSVs into
this model. The model itself is SQL, one file per table in
[`sql/build/`](../sql/build); the integrity checks are in
[`sql/checks/`](../sql/checks); and every number the run measured lands in
[`build_report.md`](build_report.md), which is regenerated rather than edited.

```
uv run python -m cms_outliers.sql.build --year 2023
uv run python -m cms_outliers.sql.build --year 2023 --source samples   # 5k-row fixture
```

It runs in **two passes**, because the two stages have different inputs and different
destinations:

1. The facts and dimensions, from the raw CSVs, to `data/parquet/<table>/year=2023/` —
   gitignored, published as a GitHub Release asset.
2. `peer_stats`, from the Parquet pass 1 just wrote, to `data/serving/` — committed, so a
   Streamlit deploy gets it from the clone. Reading the sorted columnar facts instead of the
   CSVs means this pass touches only the columns it aggregates.

Types are declared rather than sniffed, which makes them assertions: DuckDB's own detection
over all 36M rows reads NPI as BIGINT and RUCA as DOUBLE, and both would be quietly wrong
here.

## What the precomputed ranks cost, and why they are rounded

Storing each row's peer rank is what makes the drill-down path a lookup instead of a scan.
The ranks are **rounded to four decimal places** — basis points, one digit finer than the
app displays. That is a storage decision, and it was the largest single one in the build.

Unrounded, `cume_dist()` over a large peer group hands nearly every row its own DOUBLE.
Measured on `fact_part_d_drug.cost_per_claim_pct`, 26.8M rows:

| Variant | Size | Distinct values |
|---|---:|---:|
| Full DOUBLE precision | 128.1 MB | 19,581,847 |
| Rounded to basis points | 43.3 MB | 10,001 |
| Rounded to basis points, then `FLOAT` | 45.9 MB | 10,001 |
| Rounded to 0.1% | 11.2 MB | 1,001 |

Two things in that table are worth more than the 66% saving:

**`FLOAT` is worse than a rounded `DOUBLE`.** Halving the physical width made the file
*bigger*. What pays here is the dictionary — 10,001 repeated values encode to almost
nothing regardless of how wide each one is — and a narrower type buys nothing while
disturbing the encoding. Reaching for the smaller type is the obvious move and the wrong one.

**Before rounding, the columns differed threefold for a reason that is invisible in the
schema.** `tot_claims_pct` was 43 MB against `cost_per_claim_pct`'s 128 MB, off the same
rows with the same function. Claims are small integers with many ties, so `cume_dist()`
returns few distinct values per group; cost is continuous, so it returns one per row. How
compressible a precomputed rank is depends on the tie structure of the measure underneath
it. Rounding erases that difference, because it imposes ties on everything: the seven rank
columns now sit between 14 and 53 MB with no outlier.

Net effect: the rank columns are **19.3% of `fact_part_b_service` and 19.6% of
`fact_part_d_drug`**, down from 30% and 36%, and the whole core layer fell from 1,146 MB to
941 MB. Rounding to 0.1% instead would save roughly 90 MB more, and is the lever to pull if
the Release asset ever gets close to its 2 GB limit.

## Resolved by the build

- **Both fact grains are unique.** Zero duplicate keys on (`npi`, `hcpcs_code`,
  `place_of_service`) and on (`npi`, `brand_name`, `generic_name`). CMS documents these
  grains; now they are checked on every build rather than trusted.
- **Provider attributes agree across datasets.** Zero name and zero city disagreements
  across the 706,614 shared NPIs, on exact string equality — normalizing case and
  whitespace changes nothing. No NPI carries more than one name or city within a dataset
  either, so `dim_provider` collapsing the fact rows loses nothing.
- **The two artifacts agree on who is scoreable.** The fact tables' non-null `*_pct` groups
  and `peer_stats`' rows match exactly, on both datasets, so the app cannot mark a provider
  on a distribution that was never built.
- **Sizes**: 304.7 MB (Part B) and 636.6 MB (Part D) with ranks included, from 6.5 GB of
  CSV, plus a 3.3 MB `peer_stats`.
- **The brand peer key costs more than the ADR first said** — 0.91% of Part D rows against
  the generic key's 0.71%, not "essentially unchanged." Recomputed every build.

## Open

- Bytes read per drill-down query against the remote Parquet — see ADR 0001.
