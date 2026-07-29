# CMS Outlier Detection

A portfolio project built on public CMS (Centers for Medicare & Medicaid Services) data, aimed at healthcare and medtech-adjacent roles.

## What this is

An exploration of Medicare provider-level data (Part B billing and Part D prescribing) to surface outliers — providers whose utilization, cost, or prescribing patterns differ significantly from their peers (same specialty, same geography). The project has two parts:

1. **SQL / data engineering** — real multi-table joins (providers, claims, drug/procedure reference data, geography), window functions for peer-relative outlier scoring, and a documented query optimization case study (naive query vs. indexed/partitioned, with `EXPLAIN ANALYZE` evidence).
2. **Streamlit app** — an interactive dashboard where a user picks a specialty and procedure/drug and sees where a given provider, state, or peer group sits in the distribution, with geographic and year-over-year views.

## Status

Explored 5,000-row samples of both datasets (2023) and pulled the full 2023 files locally (~9.7M rows / 2.9 GB for Part B, ~26.8M rows / 3.6 GB for Part D). Next: SQL schema design. See `data/Data.md`.

## Project structure

```
pyproject.toml, uv.lock      Python project managed with uv
src/cms_outliers/
  data/                      dataset catalog lookup + sample-pull script
  sql/                       (planned) SQL loaders/query runners
  app/                       (planned) Streamlit app
sql/                         raw .sql files + the optimization writeup (not Python)
data/
  raw/                       full/bulk downloads — gitignored, not yet pulled
  samples/                   small committed samples for exploration/dev
notebooks/                   exploratory analysis (traceable record of what we looked at)
tests/
```

## Data source

Public CMS data (data.cms.gov): the **Medicare Physician & Other Practitioners by Provider and Service** (Part B) and **Medicare Part D Prescribers by Provider and Drug** datasets. Full details on structure, download process, and the columns in each — see `data/Data.md`.
