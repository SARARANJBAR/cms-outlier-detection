# Data

## Source

All data comes from **data.cms.gov**, CMS's official open data portal.

- Full catalog (lists every dataset CMS publishes, with API/CSV links): https://data.cms.gov/data.json
- Part B landing page: https://data.cms.gov/provider-summary-by-type-of-service/medicare-physician-other-practitioners/medicare-physician-other-practitioners-by-provider-and-service
- Part D landing page: https://data.cms.gov/provider-summary-by-type-of-service/medicare-part-d-prescribers/medicare-part-d-prescribers-by-provider-and-drug

We're using two provider-level datasets:

| Dataset | What it is | Grain |
|---|---|---|
| Medicare Physician & Other Practitioners – by Provider and Service | Part B billing: procedures/services performed by providers | one row per (provider NPI, HCPCS procedure code, place of service), per year |
| Medicare Part D Prescribers – by Provider and Drug | Part D prescribing: drugs prescribed by providers | one row per (provider NPI, drug), per year |

## How the data is structured on CMS's side

- Each **calendar year is a separate dataset**, with its own UUID — there is no `Year` column in the row data itself, so the year has to be tracked based on which endpoint was queried.
- Years available: 2013–2024 for both datasets (2024 is CMS's most recent "latest" release as of this writing).
- Each yearly dataset exposes two distributions:
  - a paginated **JSON API** (`https://data.cms.gov/data-api/v1/dataset/{uuid}/data?size=N&offset=M`) — good for sampling small numbers of rows without downloading the whole file.
  - a **full CSV download** (multi-GB, ~10M rows/year for the Part B dataset) — this is what a real bulk pull would use, not the paginated API.

The per-year UUIDs are looked up from each dataset's entry in `data.json` (its `distribution` list) rather than hardcoded from memory, since CMS can rotate them on republish.

## How we download it

All pulls are done via a Python script (not manual clicks), so the process is repeatable and traceable.

- `src/cms_outliers/data/catalog.py` — resolves the correct per-year API URL for a dataset by fetching `data.json` live and matching on title + year. UUIDs are never hardcoded, since CMS rotates them on republish.
- `src/cms_outliers/data/pull_samples.py` — paginates the JSON API (`size`/`offset`) to pull a fixed number of rows and writes them to `data/samples/<dataset>_<year>_sample.csv`.

Run it with:

```
uv run python -m cms_outliers.data.pull_samples --dataset part_b --year 2023 --rows 5000
uv run python -m cms_outliers.data.pull_samples --dataset part_d --year 2023 --rows 5000
```

- `data/raw/` — full/bulk downloads (not yet pulled). **Gitignored** — too large to commit.
- `data/samples/` — small samples used to explore the data together and for local dev/tests. **Committed to git.**

### Samples pulled so far

| File | Dataset | Year | Rows | Source API |
|---|---|---|---|---|
| `data/samples/part_b_2023_sample.csv` | Part B (Physician & Other Practitioners by Provider and Service) | 2023 | 5,000 | `https://data.cms.gov/data-api/v1/dataset/0e9f2f2b-7bf9-451a-912c-e02e654dd725/data` |
| `data/samples/part_d_2023_sample.csv` | Part D (Prescribers by Provider and Drug) | 2023 | 5,000 | `https://data.cms.gov/data-api/v1/dataset/e54db557-cd82-4e91-a0fe-61aad5865d69/data` |

We chose 2023 over the "latest" 2024 release because CMS's own `modified` timestamps showed 2024 had been revised as recently as 2026-05, while 2023 had gone untouched since 2025-10 — 2023 looked more settled/final.

### Columns

**Part B** — one row per (rendering provider, HCPCS procedure code, place of service):
`Rndrng_NPI`, provider name/credentials/address/specialty (`Rndrng_Prvdr_*`), `HCPCS_Cd`/`HCPCS_Desc` (the procedure), `Place_Of_Srvc`, and utilization/payment measures: `Tot_Benes`, `Tot_Srvcs`, `Tot_Bene_Day_Srvcs`, `Avg_Sbmtd_Chrg`, `Avg_Mdcr_Alowd_Amt`, `Avg_Mdcr_Pymt_Amt`, `Avg_Mdcr_Stdzd_Amt`.

**Part D** — one row per (prescriber, drug):
`Prscrbr_NPI`, prescriber name/city/state/specialty (`Prscrbr_*`), `Brnd_Name`/`Gnrc_Name` (the drug), and utilization/cost measures: `Tot_Clms`, `Tot_30day_Fills`, `Tot_Day_Suply`, `Tot_Drug_Cst`, `Tot_Benes`, plus an "age 65+ beneficiary" breakdown (`GE65_*`) with suppression flags for small counts.
