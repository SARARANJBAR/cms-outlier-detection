"""Build the typed, sorted Parquet layer from the raw CMS CSVs.

Reads the raw CSVs, applies the model in docs/schema.md, writes Hive-partitioned Parquet
under data/parquet/, runs the integrity checks that schema.md says are owed, and writes
every number it measured to docs/build_report.md.

The model itself lives in sql/build/ and the checks in sql/checks/. This module is only
the runner: to see what the tables are, read the SQL.

Two things worth knowing about the output layout:

* One Parquet file per table per year, at ``<table>/year=<year>/data.parquet``. The Hive
  path is written by hand rather than with ``PARTITION_BY``, because that shards each
  table across files and the fact tables have to be uploadable as single GitHub Release
  assets (ADR 0001, second amendment). Writing the path ourselves also guarantees the
  ``ORDER BY`` survives into the file, which is what zone-map pruning depends on.
* ``year`` is not stored inside the files. It is the partition key and the directory
  carries it, which is the Hive convention and avoids the column existing twice on read.

Usage:
    uv run python -m cms_outliers.sql.build --year 2023
    uv run python -m cms_outliers.sql.build --year 2023 --source samples
"""

from __future__ import annotations

import argparse
import platform
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from string import Template

import duckdb

REPO_ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = REPO_ROOT / "sql"
TMP_DIR = REPO_ROOT / "data" / ".duckdb_tmp"

# A sample build writes somewhere else entirely, on purpose: it must not be able to
# overwrite the real Parquet, and its numbers must not land in a committed doc.
SOURCES = {
    "full": (REPO_ROOT / "data" / "raw", "{dataset}_{year}_full.csv"),
    "samples": (REPO_ROOT / "data" / "samples", "{dataset}_{year}_sample.csv"),
}
OUT_DIRS = {
    "full": REPO_ROOT / "data" / "parquet",
    "samples": REPO_ROOT / "data" / "parquet-samples",
}
REPORT_PATHS = {
    "full": REPO_ROOT / "docs" / "build_report.md",
    "samples": OUT_DIRS["samples"] / "build_report.md",
}


@dataclass(frozen=True)
class Table:
    """A table to build. ``order_by`` is the physical sort order of the Parquet file."""

    name: str
    sql_file: str
    order_by: str


@dataclass(frozen=True)
class Check:
    """A query returning one row of named metrics, plus which of them are assertions."""

    name: str
    sql_file: str
    against_parquet: bool = True
    expect_zero: tuple[str, ...] = ()
    expect_at_most: dict[str, int] = field(default_factory=dict)


# Dimensions first, so a fact table's build failure does not leave a half-built model.
# The fact sort keys lead with the peer key from docs/schema.md, which is what makes a
# single-peer-group filter contiguous on disk.
TABLES = (
    Table("dim_provider", "10_dim_provider.sql", "npi"),
    Table("dim_hcpcs", "11_dim_hcpcs.sql", "hcpcs_code"),
    Table("dim_drug", "12_dim_drug.sql", "brand_name, generic_name"),
    Table("dim_geography", "13_dim_geography.sql", "state"),
    Table("dim_ruca", "14_dim_ruca.sql", "ruca"),
    Table(
        "fact_part_b_service",
        "20_fact_part_b_service.sql",
        "specialty, hcpcs_code, place_of_service",
    ),
    Table("fact_part_d_drug", "21_fact_part_d_drug.sql", "specialty, brand_name"),
)

CHECKS = (
    Check(
        "fact_part_b_service grain",
        "10_grain_fact_part_b_service.sql",
        expect_zero=("duplicate_keys",),
    ),
    Check(
        "fact_part_d_drug grain",
        "11_grain_fact_part_d_drug.sql",
        expect_zero=("duplicate_keys",),
    ),
    Check(
        "provider attributes",
        "12_provider_attributes.sql",
        against_parquet=False,  # needs the raw columns, which the model drops
        expect_zero=(
            "part_b_npis_multi_specialty",
            "part_d_npis_multi_specialty",
            "specialty_disagreements",
        ),
    ),
    Check(
        "reference dimensions",
        "13_reference_dims.sql",
        expect_zero=("hcpcs_codes_multi_desc",),
        # Not "exactly 50": a sample will contain fewer states. More than 50 means an
        # unrecognised code fell through dim_geography's ELSE branch.
        expect_at_most={"geography_rows_labelled_state": 50},
    ),
)

# Lever 1 of the optimization writeup: the same peer-group query over CSV and over
# Parquet. The peer group is chosen from the data rather than hardcoded, so the
# comparison keeps working on another year's file.
TIMED = {
    "fact_part_b_service": (
        "SELECT specialty, hcpcs_code FROM fact_part_b_service "
        "GROUP BY 1, 2 ORDER BY count(*) DESC LIMIT 1",
        "SELECT count(*) AS n, quantile_cont(avg_medicare_standardized, 0.9) AS p90 "
        "FROM fact_part_b_service WHERE specialty = ? AND hcpcs_code = ?",
    ),
    "fact_part_d_drug": (
        "SELECT specialty, brand_name FROM fact_part_d_drug "
        "GROUP BY 1, 2 ORDER BY count(*) DESC LIMIT 1",
        "SELECT count(*) AS n, quantile_cont(tot_drug_cost / tot_claims, 0.9) AS p90 "
        "FROM fact_part_d_drug WHERE specialty = ? AND brand_name = ?",
    ),
}


def read_sql(*parts: str, **params: str) -> str:
    """Read a .sql file, substituting $placeholders. Unknown ones raise, by design."""
    text = SQL_DIR.joinpath(*parts).read_text()
    return Template(text).substitute(params) if params else text


def parquet_path(source: str, table: str, year: int) -> Path:
    return OUT_DIRS[source] / table / f"year={year}" / "data.parquet"


def csv_path(source: str, dataset: str, year: int) -> Path:
    directory, pattern = SOURCES[source]
    return directory / pattern.format(dataset=dataset, year=year)


def connect(source: str, year: int) -> duckdb.DuckDBPyConnection:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    # Sorting 26.8M rows can exceed memory; without this DuckDB has nowhere to spill.
    con.execute(f"SET temp_directory = '{TMP_DIR.as_posix()}'")
    con.execute(
        read_sql(
            "build",
            "00_raw_views.sql",
            part_b_csv=csv_path(source, "part_b", year).as_posix(),
            part_d_csv=csv_path(source, "part_d", year).as_posix(),
        )
    )
    return con


def use_csv_views(con: duckdb.DuckDBPyConnection) -> None:
    """Point every table name at the CSV-backed model."""
    for table in TABLES:
        con.execute(read_sql("build", table.sql_file))


def use_parquet_views(con: duckdb.DuckDBPyConnection, source: str, year: int) -> None:
    """Point every table name at the Parquet that was just written."""
    for table in TABLES:
        path = OUT_DIRS[source] / table.name / "**" / "*.parquet"
        con.execute(
            f"CREATE OR REPLACE VIEW {table.name} AS "
            f"SELECT * FROM read_parquet('{path.as_posix()}', hive_partitioning = true)"
        )


def build(con: duckdb.DuckDBPyConnection, source: str, year: int) -> list[dict]:
    results = []
    for table in TABLES:
        out = parquet_path(source, table.name, year)
        out.parent.mkdir(parents=True, exist_ok=True)
        print(f"building {table.name} -> {out.relative_to(REPO_ROOT)}")

        started = time.perf_counter()
        con.execute(
            f"COPY (SELECT * FROM {table.name} ORDER BY {table.order_by}) "
            f"TO '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd)"
        )
        elapsed = time.perf_counter() - started

        # Row count and row-group count come from the footer, not a scan.
        rows = con.execute(f"SELECT count(*) FROM read_parquet('{out.as_posix()}')").fetchone()[0]
        row_groups = con.execute(
            f"SELECT count(DISTINCT row_group_id) FROM parquet_metadata('{out.as_posix()}')"
        ).fetchone()[0]

        results.append(
            {
                "table": table.name,
                "rows": rows,
                "bytes": out.stat().st_size,
                "row_groups": row_groups,
                "seconds": elapsed,
                "order_by": table.order_by,
            }
        )
        print(f"  {rows:,} rows, {out.stat().st_size / 1e6:,.1f} MB, {elapsed:,.1f}s")
    return results


def column_footprint(con: duckdb.DuckDBPyConnection, source: str, year: int) -> list[dict]:
    """Compressed bytes per column of each fact table, read from the Parquet footer.

    Two things depend on these numbers. The denormalization in docs/schema.md is argued
    on the claim that a column of ~175 repeated specialty strings compresses to almost
    nothing, and this is where that claim is either true or not. And projection pushdown
    (lever 4) can only save what a column actually costs, so this is the ceiling on it.
    """
    results = []
    for table in (t for t in TABLES if t.name.startswith("fact_")):
        path = parquet_path(source, table.name, year)
        rows = con.execute(
            "SELECT path_in_schema AS column_name, sum(total_compressed_size) AS bytes "
            f"FROM parquet_metadata('{path.as_posix()}') "
            "GROUP BY column_name ORDER BY bytes DESC"
        ).fetchall()
        total = sum(r[1] for r in rows)
        for column_name, byte_count in rows:
            results.append(
                {
                    "table": table.name,
                    "column": column_name,
                    "bytes": byte_count,
                    "share": byte_count / total,
                }
            )
    return results


def run_checks(
    con: duckdb.DuckDBPyConnection, source: str, year: int
) -> tuple[list[dict], list[str]]:
    results: list[dict] = []
    failures: list[str] = []
    for check in CHECKS:
        print(f"checking {check.name}")
        if check.against_parquet:
            use_parquet_views(con, source, year)
        else:
            use_csv_views(con)

        cursor = con.execute(read_sql("checks", check.sql_file))
        columns = [d[0] for d in cursor.description]
        row = cursor.fetchone()

        for column, value in zip(columns, row, strict=True):
            status = ""
            if column in check.expect_zero:
                status = "ok" if value == 0 else "FAIL (expected 0)"
            elif column in check.expect_at_most:
                limit = check.expect_at_most[column]
                status = "ok" if value <= limit else f"FAIL (expected <= {limit})"
            if status.startswith("FAIL"):
                failures.append(f"{check.name}: {column} = {value:,} — {status}")
            results.append(
                {"check": check.name, "metric": column, "value": value, "status": status}
            )
            print(f"  {column} = {value:,} {status}")

    use_parquet_views(con, source, year)
    return results, failures


def measure_lever_1(con: duckdb.DuckDBPyConnection, source: str, year: int) -> list[dict]:
    """Time one peer-group query against CSV and against Parquet. Warm cache, both."""
    results = []
    for table, (pick_sql, query_sql) in TIMED.items():
        use_parquet_views(con, source, year)
        keys = con.execute(pick_sql).fetchone()

        def timed(sql: str, params: tuple) -> float:
            con.execute(sql, params).fetchall()  # warm the cache
            started = time.perf_counter()
            con.execute(sql, params).fetchall()
            return time.perf_counter() - started

        parquet_seconds = timed(query_sql, keys)
        use_csv_views(con)
        csv_seconds = timed(query_sql, keys)
        use_parquet_views(con, source, year)

        dataset = "part_b" if "part_b" in table else "part_d"
        results.append(
            {
                "table": table,
                "peer_group": " / ".join(str(k) for k in keys),
                "csv_bytes": csv_path(source, dataset, year).stat().st_size,
                "parquet_bytes": parquet_path(source, table, year).stat().st_size,
                "csv_seconds": csv_seconds,
                "parquet_seconds": parquet_seconds,
            }
        )
        print(f"lever 1 {table}: CSV {csv_seconds:.3f}s vs Parquet {parquet_seconds:.3f}s")
    return results


def write_report(
    source: str,
    year: int,
    threads: int,
    tables: list[dict],
    columns: list[dict],
    checks: list[dict],
    levers: list[dict],
    failures: list[str],
) -> None:
    lines = [
        "# Build report",
        "",
        "Generated by `src/cms_outliers/sql/build.py`. Do not edit by hand — rerun the",
        "build instead. Every number here was measured on the run recorded below, which",
        "is what project rule 7 asks for.",
        "",
        f"- Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Source: `{source}`, year {year}",
        f"- DuckDB {duckdb.__version__}, {threads} threads, "
        f"{platform.system()} {platform.machine()}",
        "",
        "## Artifacts",
        "",
        "| Table | Rows | Parquet bytes | Row groups | Build seconds | Sorted by |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in tables:
        lines.append(
            f"| `{row['table']}` | {row['rows']:,} | {row['bytes']:,} | "
            f"{row['row_groups']:,} | {row['seconds']:.1f} | `{row['order_by']}` |"
        )

    lines += [
        "",
        "## Fact-table footprint by column",
        "",
        "Compressed bytes per column, from the Parquet footer. The denormalized columns",
        "(`specialty`, `state`, `ruca`) are the ones docs/schema.md argues are nearly free;",
        "this is where that argument is checked. It is also the ceiling on what projection",
        "pushdown can save, since a query can only avoid reading what a column costs.",
        "",
        "| Table | Column | Bytes | Share of file |",
        "|---|---|---:|---:|",
    ]
    for row in columns:
        lines.append(
            f"| `{row['table']}` | `{row['column']}` | {row['bytes']:,} | {row['share']:.1%} |"
        )

    lines += [
        "",
        "## Checks",
        "",
        "Rows marked `ok` are assertions that passed; blank means the number is reported,",
        "not asserted. See `sql/checks/` for what each one is for.",
        "",
        "| Check | Metric | Value | Assertion |",
        "|---|---|---:|---|",
    ]
    for row in checks:
        lines.append(
            f"| {row['check']} | `{row['metric']}` | {row['value']:,} | {row['status'] or ''} |"
        )

    lines += [
        "",
        "## Lever 1 — CSV to typed Parquet",
        "",
        "One peer-group query, the same SQL over each format, on the largest peer group in",
        "the data. **Both timings are warm** — the files had been read earlier in the same",
        "run. The cold-cache comparison, and the bytes-read measurements for the layout",
        "levers, belong to `notebooks/03_optimization.ipynb`.",
        "",
        "| Table | Peer group | CSV bytes | Parquet bytes | CSV seconds | Parquet seconds |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in levers:
        lines.append(
            f"| `{row['table']}` | {row['peer_group']} | {row['csv_bytes']:,} | "
            f"{row['parquet_bytes']:,} | {row['csv_seconds']:.3f} | "
            f"{row['parquet_seconds']:.3f} |"
        )

    if failures:
        lines += ["", "## Failed assertions", ""]
        lines += [f"- {failure}" for failure in failures]

    lines.append("")
    report_path = REPORT_PATHS[source]
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines))
    print(f"wrote {report_path.relative_to(REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--source", default="full", choices=sorted(SOURCES))
    args = parser.parse_args()

    for dataset in ("part_b", "part_d"):
        path = csv_path(args.source, dataset, args.year)
        if not path.exists():
            parser.error(f"missing {path} — pull it with cms_outliers.data.pull_full")

    con = connect(args.source, args.year)
    threads = con.execute("SELECT current_setting('threads')").fetchone()[0]

    use_csv_views(con)
    tables = build(con, args.source, args.year)
    columns = column_footprint(con, args.source, args.year)
    checks, failures = run_checks(con, args.source, args.year)
    levers = measure_lever_1(con, args.source, args.year)

    write_report(args.source, args.year, threads, tables, columns, checks, levers, failures)

    if failures:
        print("\nFAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        sys.exit(1)
    print("\nBuild OK")


if __name__ == "__main__":
    main()
