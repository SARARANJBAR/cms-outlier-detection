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

The cost is duplication across ~36M rows. In a row store that would be expensive; in
columnar Parquet, a column holding one of 175 repeated strings compresses to close to
nothing. Measure the actual footprint at build time rather than trusting that claim.

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
| `first_name`, `middle_initial` | VARCHAR | both | |
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
datasets for the 706,614 overlapping NPIs has **not** been measured. The build script
must count disagreements and report them rather than silently picking one side.

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
| `year` | INTEGER | Partition key. Not present in the source; set from which file was pulled |

The `avg_*` columns are **per-service averages, not totals**. Total Medicare payment for
a row is `tot_srvcs * avg_medicare_payment` — there is no total column. This distinction
decides what an outlier is: a provider can be ordinary on price and extreme on volume.

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
| `year` | INTEGER | Partition key |

## Reference dimensions

| Table | Rows | Contents |
|---|---|---|
| `dim_hcpcs` | 6,405 | Code, description, drug indicator |
| `dim_drug` | 3,027 brands / 1,779 generics | Brand → generic mapping. 63.8% of generics have exactly one brand; the rest average 1.77 and run to 50 |
| `dim_geography` | 62 | State abbreviation, FIPS, and a flag for the 12 values that are not US states — territories, military codes, foreign |
| `dim_ruca` | 22 | RUCA code and description |

`dim_geography` having 62 rows rather than 50 is the sort of thing that silently breaks a
join later. It is a table, with a flag, so the non-states have to be handled deliberately.

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
| `measure` | VARCHAR | e.g. `tot_srvcs`, `avg_medicare_standardized`, `cost_per_claim` |
| `n_providers` | BIGINT | Peer group size |
| `p10`…`p99` | DOUBLE | Breakpoints: 10, 25, 50, 75, 90, 95, 99. `p50` is the median |
| `year` | INTEGER | |

**Only groups with `n_providers >= 30` are written.** Measured cost of that rule: it
excludes 77.9% of Part B peer groups but only **1.95% of rows**, and 73.2% / **0.71%**
for Part D. Refusing to rank a provider against a handful of peers is nearly free here,
so we do it rather than publishing a rank that means nothing.

Size of this table is unknown until it is built. Measure it — this is the artifact that
**ships inside the repo** and answers the app's main query, while the fact tables are
published as a Release asset and read remotely (ADR 0001, second amendment).

## Peer keys

The comparison set an outlier is scored against.

| Dataset | Peer key | Why |
|---|---|---|
| Part B | `(specialty, hcpcs_code, place_of_service)` | Place of service is part of the natural grain and costs almost nothing: 1.95% → 2.40% of rows unscoreable |
| Part D | `(specialty, brand_name)` | Coverage is unchanged vs. the generic key, but within-group cost spread tightens where it matters: p90 of `p99/p50` falls 9.74 → 5.86, max 409.43 → 172.50 |

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

## Open

- Whether the fact-table grains are actually unique — (`npi`, `hcpcs_code`,
  `place_of_service`) and (`npi`, `brand_name`, `generic_name`) — is documented by CMS
  but unverified. The build script must assert it rather than assume.
- Whether provider name and city agree across datasets for the 706,614 shared NPIs.
- Measured size of `peer_stats` (ships in-repo) and of the fact-table Parquet (published
  as a Release asset), plus bytes read per drill-down query — see ADR 0001.
