"""Pull a small row sample from a CMS provider-level dataset and save it as CSV.

Usage:
    uv run python -m cms_outliers.data.pull_samples --dataset part_b --year 2023 --rows 5000
    uv run python -m cms_outliers.data.pull_samples --dataset part_d --year 2023 --rows 5000
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import requests

from cms_outliers.data.catalog import fetch_catalog, find_api_url

PAGE_SIZE = 1000
REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLES_DIR = REPO_ROOT / "data" / "samples"


def pull_rows(api_url: str, total_rows: int) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while len(rows) < total_rows:
        size = min(PAGE_SIZE, total_rows - len(rows))
        resp = requests.get(api_url, params={"size": size, "offset": offset}, timeout=60)
        resp.raise_for_status()
        page = resp.json()
        if not page:
            break
        rows.extend(page)
        offset += len(page)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, choices=["part_b", "part_d"])
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--rows", default=5000, type=int)
    args = parser.parse_args()

    catalog = fetch_catalog()
    api_url = find_api_url(catalog, args.dataset, args.year)

    print(f"Pulling {args.rows} rows from {api_url}")
    rows = pull_rows(api_url, args.rows)
    print(f"Fetched {len(rows)} rows")

    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SAMPLES_DIR / f"{args.dataset}_{args.year}_sample.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
