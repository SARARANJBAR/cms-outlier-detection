# Data

## Source

All data comes from **data.cms.gov**, CMS's official open data portal.

Full catalog (lists every dataset CMS publishes, with API/CSV links): https://data.cms.gov/data.json

We're using two provider-level datasets:

| Dataset | What it is | Grain |
|---|---|---|
| Medicare Physician & Other Practitioners – by Provider and Service | Part B billing: procedures/services performed by providers | one row per (provider NPI, HCPCS procedure code, place of service), per year |
| Medicare Part D Prescribers – by Provider and Drug | Part D prescribing: drugs prescribed by providers | one row per (provider NPI, drug), per year |

## Data Structure on CMS's side

- Each **calendar year is a separate dataset**, with its own UUID. There is no `Year` column in the row data itself, so the year has to be tracked based on which endpoint was queried. 2024 is CMS's most recent "latest" release as of this writing.

## How to download

[catalog.py](../src/cms_outliers/data/catalog.py) resolves the correct per-year API URL for a dataset by fetching `data.json` live and matching on title + year. [pull_samples.py](../src/cms_outliers/data/pull_samples.py) paginates the JSON API (`size`/`offset`) to pull a fixed number of rows and writes them to `data/samples/<dataset>_<year>_sample.csv`.

Run it with:

```
uv run python -m cms_outliers.data.pull_samples --dataset part_b --year 2023 --rows 5000
uv run python -m cms_outliers.data.pull_samples --dataset part_d --year 2023 --rows 5000
```

- `data/raw/` — full/bulk downloads (not yet pulled). **Gitignored** — too large to commit.
- `data/samples/` — small samples used to explore the data together and for local dev/tests. **Committed to git.**

### Samples pulled

| File | Dataset | Year | Rows | Source API |
|---|---|---|---|---|
| `data/samples/part_b_2023_sample.csv` | Part B (Physician & Other Practitioners by Provider and Service) | 2023 | 5,000 | `https://data.cms.gov/data-api/v1/dataset/0e9f2f2b-7bf9-451a-912c-e02e654dd725/data` |
| `data/samples/part_d_2023_sample.csv` | Part D (Prescribers by Provider and Drug) | 2023 | 5,000 | `https://data.cms.gov/data-api/v1/dataset/e54db557-cd82-4e91-a0fe-61aad5865d69/data` |

note, though 2024 release is the latest, CMS's own `modified` timestamps showed it had been revised as recently as 2026-05. 2023 looked more final as of 2026-07.

### Columns

**Part B** — one row per (rendering provider, HCPCS procedure code, place of service)

| Column(s) | Meaning |
|---|---|
| `Rndrng_NPI` | Provider's National Provider Identifier (unique ID) |
| `Rndrng_Prvdr_Last_Org_Name`, `First_Name`, `MI`, `Crdntls` | Provider name and credentials (e.g. "M.D.") |
| `Rndrng_Prvdr_Ent_Cd` | I = individual, O = organization |
| `Rndrng_Prvdr_Type` | Specialty (e.g. "Internal Medicine") |
| `Rndrng_Prvdr_St1`, `St2`, `City`, `State_Abrvtn`, `State_FIPS`, `Zip5`, `Cntry` | Provider address |
| `Rndrng_Prvdr_RUCA`, `RUCA_Desc` | Rural-Urban Commuting Area code — how urban/rural the provider's location is |
| `Rndrng_Prvdr_Mdcr_Prtcptg_Ind` | Y/N — participates in Medicare assignment |
| `HCPCS_Cd`, `HCPCS_Desc` | The billing code for this row and its description |
| `HCPCS_Drug_Ind` | Whether the code is for a drug |
| `Place_Of_Srvc` | F = facility, O = office |
| `Tot_Benes` | Distinct patients |
| `Tot_Srvcs` | Service count |
| `Tot_Bene_Day_Srvcs` | Patient-days |
| `Avg_Sbmtd_Chrg` | What the provider billed |
| `Avg_Mdcr_Alowd_Amt` | What Medicare allows |
| `Avg_Mdcr_Pymt_Amt` | What Medicare actually paid |
| `Avg_Mdcr_Stdzd_Amt` | Payment standardized to remove geographic cost-of-living adjustments — for fair cross-region comparison |

**Part D** — one row per (prescriber, drug)

| Column(s) | Meaning |
|---|---|
| `Prscrbr_NPI` | Prescriber's National Provider Identifier |
| `Prscrbr_Last_Org_Name`, `First_Name` | Prescriber name |
| `Prscrbr_City`, `State_Abrvtn`, `State_FIPS` | Prescriber location |
| `Prscrbr_Type` | Specialty |
| `Prscrbr_Type_Src` | Whether the specialty came from the claim itself or the NPI registry |
| `Brnd_Name`, `Gnrc_Name` | The drug — brand and generic name |
| `Tot_Clms` | Claim count, all patients |
| `Tot_30day_Fills` | Fills normalized to 30-day equivalents |
| `Tot_Day_Suply` | Total days' supply |
| `Tot_Drug_Cst` | Total cost |
| `Tot_Benes` | Distinct patients — blank if suppressed (<11 patients, see Exploration below) |
| `GE65_Tot_Clms`, `GE65_Tot_30day_Fills`, `GE65_Tot_Day_Suply`, `GE65_Tot_Drug_Cst`, `GE65_Tot_Benes` | Same metrics restricted to patients age 65+ |
| `GE65_Sprsn_Flag`, `GE65_Bene_Sprsn_Flag` | Suppression flags for the 65+ breakdown |

### Exploration

See [01_explore_samples.ipynb](../notebooks/01_explore_samples.ipynb) for cardinality, null/suppression patterns, and other data quality findings.
