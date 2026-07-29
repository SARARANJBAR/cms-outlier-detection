"""Look up CMS dataset API endpoints from the live data.gov-style catalog.

We resolve dataset UUIDs dynamically from https://data.cms.gov/data.json
rather than hardcoding them, since CMS rotates UUIDs when a dataset is
republished.
"""

from __future__ import annotations

import requests

CATALOG_URL = "https://data.cms.gov/data.json"

# Human-friendly names -> exact dataset titles as published in the catalog.
DATASET_TITLES = {
    "part_b": "Medicare Physician & Other Practitioners - by Provider and Service",
    "part_d": "Medicare Part D Prescribers - by Provider and Drug",
}


def fetch_catalog() -> list[dict]:
    resp = requests.get(CATALOG_URL, timeout=60)
    resp.raise_for_status()
    return resp.json()["dataset"]


def find_api_url(catalog: list[dict], dataset: str, year: int) -> str:
    """Return the paginated JSON API URL for `dataset` (see DATASET_TITLES) and `year`."""
    title = DATASET_TITLES[dataset]
    matches = [item for item in catalog if item.get("title") == title]
    if not matches:
        raise ValueError(f"No catalog entry found with title {title!r}")

    year_start = f"{year}-01-01"
    for distribution in matches[0]["distribution"]:
        if distribution.get("format") != "API":
            continue
        if distribution.get("temporal", "").startswith(year_start):
            return distribution["accessURL"]

    raise ValueError(f"No API distribution found for {dataset!r} year {year}")
