"""The queries behind the one screen.

Kept separate from the Streamlit layer so they can be run and checked without a browser,
and so the SQL is readable in one place.

Two data sources, matching the two paths in ADR 0001:

* ``peer_stats`` — 3.3 MB, in the repo. Answers "what does this peer group look like."
* the fact tables — large, local for now and remote later. Answer "where does this
  provider sit in it," using the rank precomputed at build time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE_DIR = REPO_ROOT / "data" / "parquet"
SERVING_DIR = REPO_ROOT / "data" / "serving"

# Measure -> the fact column holding its precomputed rank, and how to compute the value
# itself. The rank column names are the contract with peer_stats.measure (docs/schema.md).
MEASURES = {
    "part_b": {
        "tot_srvcs": ("tot_srvcs_pct", "tot_srvcs", "Services delivered"),
        "tot_benes": ("tot_benes_pct", "tot_benes", "Distinct patients"),
        "avg_medicare_standardized": (
            "avg_medicare_standardized_pct",
            "avg_medicare_standardized",
            "Standardized payment per service",
        ),
        "total_medicare_payment": (
            "total_medicare_payment_pct",
            "tot_srvcs * avg_medicare_payment",
            "Total Medicare payment",
        ),
    },
    "part_d": {
        "tot_claims": ("tot_claims_pct", "tot_claims", "Claims"),
        "tot_drug_cost": ("tot_drug_cost_pct", "tot_drug_cost", "Total drug cost"),
        "cost_per_claim": (
            "cost_per_claim_pct",
            "tot_drug_cost / tot_claims",
            "Cost per claim",
        ),
    },
}

DATASETS = {
    "part_b": ("fact_part_b_service", "hcpcs_code", "Procedure (HCPCS)"),
    "part_d": ("fact_part_d_drug", "brand_name", "Drug (brand)"),
}


@dataclass(frozen=True)
class PeerGroup:
    dataset: str
    specialty: str
    code: str
    place_of_service: str | None = None


# Small enough to live in the repo, so the hosted app has them.
SERVING_TABLES = ("peer_stats", "dim_hcpcs", "dim_drug")
# The build artifact: absent on a clone until someone runs the build.
CORE_TABLES = ("fact_part_b_service", "fact_part_d_drug", "dim_provider")


def _exists(directory: Path, table: str) -> bool:
    return any((directory / table).glob("**/*.parquet"))


def connect() -> duckdb.DuckDBPyConnection:
    """Register a view for each table whose Parquet is actually present.

    `peer_stats` ships in the repo, so it is always there. The fact and dimension tables are
    a build artifact — 941 MB, gitignored, published separately — so on a fresh clone or a
    hosted deploy they are simply absent. Registering views over missing files would make
    every query fail at the point of use rather than once, here, so what is missing is
    discovered up front and `has_facts()` lets the screen adapt.
    """
    con = duckdb.connect()
    for table in SERVING_TABLES:
        path = (SERVING_DIR / table / "**" / "*.parquet").as_posix()
        con.execute(
            f"CREATE OR REPLACE VIEW {table} AS "
            f"SELECT * FROM read_parquet('{path}', hive_partitioning = true)"
        )
    for table in CORE_TABLES:
        if not _exists(CORE_DIR, table):
            continue
        path = (CORE_DIR / table / "**" / "*.parquet").as_posix()
        con.execute(
            f"CREATE OR REPLACE VIEW {table} AS "
            f"SELECT * FROM read_parquet('{path}', hive_partitioning = true)"
        )
    return con


def has_facts(con) -> bool:
    """Whether the fact layer is present, so provider-level drill-down is possible."""
    registered = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    return {"fact_part_b_service", "fact_part_d_drug", "dim_provider"} <= registered


def specialties(con, dataset: str):
    """Specialties that have at least one scoreable peer group, so the picker cannot
    offer a choice that leads to an empty screen."""
    return [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT specialty FROM peer_stats WHERE dataset = ? ORDER BY 1",
            [dataset],
        ).fetchall()
    ]


def codes(con, dataset: str, specialty: str):
    """Codes available for a specialty, largest peer group first, with a label.

    The human-readable label lives in a dimension table, which is part of the fact layer and
    so may be absent. Without it the code itself is the label — a HCPCS code is opaque and a
    brand name is not, but neither case is worth failing over.
    """
    dim = "dim_hcpcs" if dataset == "part_b" else "dim_drug"
    if not _exists(SERVING_DIR, dim):
        return con.execute(
            """
            SELECT code,
                   any_value(place_of_service) AS pos,
                   code AS label,
                   max(n_providers) AS n_providers
            FROM peer_stats
            WHERE dataset = ? AND specialty = ?
            GROUP BY code
            ORDER BY n_providers DESC
            """,
            [dataset, specialty],
        ).df()

    label = "h.hcpcs_desc" if dataset == "part_b" else "string_agg(DISTINCT d.generic_name, ', ')"
    join = (
        "LEFT JOIN dim_hcpcs h ON h.hcpcs_code = p.code"
        if dataset == "part_b"
        else "LEFT JOIN dim_drug d ON d.brand_name = p.code"
    )
    group_by = "p.code, p.place_of_service, h.hcpcs_desc" if dataset == "part_b" else "p.code"
    return con.execute(
        f"""
        SELECT p.code,
               {"any_value(p.place_of_service)" if dataset == "part_b" else "NULL"} AS pos,
               {label} AS label,
               max(p.n_providers) AS n_providers
        FROM peer_stats p {join}
        WHERE p.dataset = ? AND p.specialty = ?
        GROUP BY {group_by}
        ORDER BY n_providers DESC
        """,
        [dataset, specialty],
    ).df()


def distribution(con, group: PeerGroup, measure: str):
    """The peer group's breakpoints. Reads peer_stats only — never a fact table."""
    return con.execute(
        """
        SELECT n_providers, n_rows, p10, p25, p50, p75, p90, p95, p99
        FROM peer_stats
        WHERE dataset = ? AND specialty = ? AND code = ? AND measure = ?
          AND place_of_service IS NOT DISTINCT FROM ?
        """,
        [group.dataset, group.specialty, group.code, measure, group.place_of_service],
    ).df()


def outliers(con, group: PeerGroup, measure: str, state: str | None = None, limit: int = 25):
    """The providers at the top of the peer group, with their precomputed percentile.

    No percentile is computed here: the rank was written at build time, so this is a
    filtered read of a sorted file plus a join to the provider dimension.
    """
    fact, code_col, _ = DATASETS[group.dataset]
    rank_col, value_expr, _ = MEASURES[group.dataset][measure]
    pos_clause = (
        "AND f.place_of_service IS NOT DISTINCT FROM ?" if group.dataset == "part_b" else ""
    )
    params = [group.specialty, group.code]
    if group.dataset == "part_b":
        params.append(group.place_of_service)
    state_clause = ""
    if state:
        state_clause = "AND f.state = ?"
        params.append(state)

    return con.execute(
        f"""
        SELECT p.last_or_org_name AS provider,
               p.first_name,
               p.city,
               f.state,
               {value_expr} AS value,
               100 * f.{rank_col} AS percentile
        FROM {fact} f
        JOIN dim_provider p USING (npi)
        WHERE f.specialty = ? AND f.{code_col} = ? {pos_clause} {state_clause}
          AND f.{rank_col} IS NOT NULL
        ORDER BY value DESC
        LIMIT {int(limit)}
        """,
        params,
    ).df()


def provider_detail(con, group: PeerGroup, measure: str, npi: str):
    """One provider's value and precomputed rank inside this peer group."""
    fact, code_col, _ = DATASETS[group.dataset]
    rank_col, value_expr, _ = MEASURES[group.dataset][measure]
    pos_clause = (
        "AND f.place_of_service IS NOT DISTINCT FROM ?" if group.dataset == "part_b" else ""
    )
    params = [group.specialty, group.code]
    if group.dataset == "part_b":
        params.append(group.place_of_service)
    params.append(npi)
    return con.execute(
        f"""
        SELECT p.npi, p.last_or_org_name, p.first_name, p.credentials, p.city, f.state,
               {value_expr} AS value,
               100 * f.{rank_col} AS percentile
        FROM {fact} f
        JOIN dim_provider p USING (npi)
        WHERE f.specialty = ? AND f.{code_col} = ? {pos_clause} AND f.npi = ?
        """,
        params,
    ).df()


def candidates(con, group: PeerGroup, measure: str, state: str | None = None, limit: int = 200):
    """Providers in the group, highest first, for the picker. Includes NPI."""
    fact, code_col, _ = DATASETS[group.dataset]
    rank_col, value_expr, _ = MEASURES[group.dataset][measure]
    pos_clause = (
        "AND f.place_of_service IS NOT DISTINCT FROM ?" if group.dataset == "part_b" else ""
    )
    params = [group.specialty, group.code]
    if group.dataset == "part_b":
        params.append(group.place_of_service)
    state_clause = ""
    if state:
        state_clause = "AND f.state = ?"
        params.append(state)
    return con.execute(
        f"""
        SELECT f.npi,
               p.last_or_org_name AS provider,
               p.first_name,
               p.city,
               f.state,
               {value_expr} AS value,
               100 * f.{rank_col} AS percentile
        FROM {fact} f
        JOIN dim_provider p USING (npi)
        WHERE f.specialty = ? AND f.{code_col} = ? {pos_clause} {state_clause}
          AND f.{rank_col} IS NOT NULL
        ORDER BY value DESC
        LIMIT {int(limit)}
        """,
        params,
    ).df()


def states(con, group: PeerGroup):
    fact, code_col, _ = DATASETS[group.dataset]
    return [
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT state FROM {fact} WHERE specialty = ? AND {code_col} = ? ORDER BY 1",
            [group.specialty, group.code],
        ).fetchall()
    ]
