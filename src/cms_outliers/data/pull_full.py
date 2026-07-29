"""Download a full CMS provider-level dataset (one calendar year) as CSV.

Uses CMS's own published bulk-download link rather than the paginated JSON
API, since a full year is tens of millions of rows. Files are large
(multi-GB) and are written to data/raw/, which is gitignored.

Usage:
    uv run python -m cms_outliers.data.pull_full --dataset part_b --year 2023
    uv run python -m cms_outliers.data.pull_full --dataset part_d --year 2023
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import requests

from cms_outliers.data.catalog import fetch_catalog, find_csv_url

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = REPO_ROOT / "data" / "raw"
CHUNK_SIZE = 1024 * 1024  # 1 MB
LOG_INTERVAL_SECONDS = 5


def download(url: str, out_path: Path) -> None:
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total_bytes = int(resp.headers.get("content-length", 0))

        written = 0
        last_log = time.monotonic()
        with open(out_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                f.write(chunk)
                written += len(chunk)
                now = time.monotonic()
                if now - last_log >= LOG_INTERVAL_SECONDS:
                    mb = written / (1024 * 1024)
                    if total_bytes:
                        pct = 100 * written / total_bytes
                        print(f"  {mb:,.0f} MB ({pct:.0f}%)")
                    else:
                        print(f"  {mb:,.0f} MB")
                    last_log = now

        print(f"  {written / (1024 * 1024):,.0f} MB total")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, choices=["part_b", "part_d"])
    parser.add_argument("--year", required=True, type=int)
    args = parser.parse_args()

    catalog = fetch_catalog()
    csv_url = find_csv_url(catalog, args.dataset, args.year)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{args.dataset}_{args.year}_full.csv"

    print(f"Downloading {csv_url}")
    print(f"  -> {out_path}")
    download(csv_url, out_path)
    print(f"Done: {out_path}")


if __name__ == "__main__":
    main()
