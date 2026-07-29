# CMS Outlier Detection

A portfolio project built on public CMS (Centers for Medicare & Medicaid Services) data, aimed at healthcare and medtech-adjacent roles.

## What this is

An exploration of Medicare provider-level data (prescribing or billing, TBD) to surface outliers — providers whose utilization, cost, or prescribing patterns differ significantly from their peers (same specialty, same geography). The project has two parts:

1. **SQL / data engineering** — real multi-table joins (providers, claims, drug/procedure reference data, geography), window functions for peer-relative outlier scoring, and a documented query optimization case study (naive query vs. indexed/partitioned, with `EXPLAIN ANALYZE` evidence).
2. **Streamlit app** — an interactive dashboard where a user picks a specialty and procedure/drug and sees where a given provider, state, or peer group sits in the distribution, with geographic and year-over-year views.

## Status

Just getting started. Currently exploring the raw CMS datasets to understand structure, scale, and quality before committing to a schema or architecture.

## Project structure

```
data/       raw and sample data pulled from CMS (see data/README.md once added)
sql/        schema, queries, and the optimization writeup
app/        the Streamlit application
```

## Data source

Public CMS data (Medicare Part B and/or Part D provider-level files). Specific dataset(s), download process, and licensing notes will be documented in `data/README.md` as we settle on them.
