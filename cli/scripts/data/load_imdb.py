#!/usr/bin/env python3
# /// script
# dependencies = ["psycopg2-binary", "pymysql"]
# ///
"""
IMDB Dataset Loader.

Downloads official IMDb TSV.gz files and bulk-loads them into
PostgreSQL or MySQL via a database URL.

Dependencies: psycopg2-binary (for PostgreSQL) or pymysql (for MySQL)
    pip install psycopg2-binary pymysql

Usage:
    python load_imdb.py "postgresql://user:pass@localhost:5432/mydb"
    python load_imdb.py "mysql://user:pass@localhost:3306/mydb" --limit 100000
    python load_imdb.py "postgresql://..." --datasets title.basics,title.ratings,name.basics
    python load_imdb.py "postgresql://..." --all --drop
"""

import argparse
import gzip
import io
import logging
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# Dataset definitions

IMDB_BASE_URL = "https://datasets.imdbws.com"

DATASETS = {
    "title.basics": {
        "url": f"{IMDB_BASE_URL}/title.basics.tsv.gz",
        "table": "title_basics",
        "pk": "tconst",
    },
    "title.ratings": {
        "url": f"{IMDB_BASE_URL}/title.ratings.tsv.gz",
        "table": "title_ratings",
        "pk": "tconst",
    },
    "name.basics": {
        "url": f"{IMDB_BASE_URL}/name.basics.tsv.gz",
        "table": "name_basics",
        "pk": "nconst",
    },
    "title.principals": {
        "url": f"{IMDB_BASE_URL}/title.principals.tsv.gz",
        "table": "title_principals",
        "pk": None,  # synthetic PK
    },
    "title.akas": {
        "url": f"{IMDB_BASE_URL}/title.akas.tsv.gz",
        "table": "title_akas",
        "pk": None,  # synthetic PK
    },
    "title.episode": {
        "url": f"{IMDB_BASE_URL}/title.episode.tsv.gz",
        "table": "title_episode",
        "pk": "tconst",
    },
    "title.crew": {
        "url": f"{IMDB_BASE_URL}/title.crew.tsv.gz",
        "table": "title_crew",
        "pk": "tconst",
    },
}

DEFAULT_DATASETS = list(DATASETS.keys())

# Schema DDL (lowercase column names for both engines)

PG_DDL = {
    "title_basics": """
CREATE TABLE IF NOT EXISTS title_basics (
    tconst text PRIMARY KEY,
    titletype text,
    primarytitle text,
    originaltitle text,
    isadult boolean,
    startyear integer,
    endyear integer,
    runtimeminutes integer,
    genres text
)""",
    "title_ratings": """
CREATE TABLE IF NOT EXISTS title_ratings (
    tconst text PRIMARY KEY,
    averagerating double precision,
    numvotes integer
)""",
    "name_basics": """
CREATE TABLE IF NOT EXISTS name_basics (
    nconst text PRIMARY KEY,
    primaryname text,
    birthyear integer,
    deathyear integer,
    primaryprofession text,
    knownfortitles text
)""",
    "title_principals": """
CREATE TABLE IF NOT EXISTS title_principals (
    id serial PRIMARY KEY,
    tconst text,
    ordering integer,
    nconst text,
    category text,
    job text,
    characters text
)""",
    "title_akas": """
CREATE TABLE IF NOT EXISTS title_akas (
    id serial PRIMARY KEY,
    titleid text,
    ordering integer,
    title text,
    region text,
    language text,
    types text,
    attributes text,
    isoriginaltitle boolean
)""",
    "title_episode": """
CREATE TABLE IF NOT EXISTS title_episode (
    tconst text PRIMARY KEY,
    parenttconst text,
    seasonnumber integer,
    episodenumber integer
)""",
    "title_crew": """
CREATE TABLE IF NOT EXISTS title_crew (
    tconst text PRIMARY KEY,
    directors text,
    writers text
)""",
}

MY_DDL = {
    "title_basics": """
CREATE TABLE IF NOT EXISTS title_basics (
    tconst varchar(255) NOT NULL PRIMARY KEY,
    titletype varchar(255),
    primarytitle varchar(512),
    originaltitle varchar(512),
    isadult tinyint(1),
    startyear int,
    endyear int,
    runtimeminutes int,
    genres varchar(255)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "title_ratings": """
CREATE TABLE IF NOT EXISTS title_ratings (
    tconst varchar(255) NOT NULL PRIMARY KEY,
    averagerating double,
    numvotes int
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "name_basics": """
CREATE TABLE IF NOT EXISTS name_basics (
    nconst varchar(255) NOT NULL PRIMARY KEY,
    primaryname varchar(512),
    birthyear int,
    deathyear int,
    primaryprofession text,
    knownfortitles text
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "title_principals": """
CREATE TABLE IF NOT EXISTS title_principals (
    id int NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tconst varchar(255),
    ordering int,
    nconst varchar(255),
    category varchar(255),
    job text,
    characters text
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "title_akas": """
CREATE TABLE IF NOT EXISTS title_akas (
    id int NOT NULL AUTO_INCREMENT PRIMARY KEY,
    titleid varchar(255),
    ordering int,
    title text,
    region varchar(10),
    language varchar(10),
    types varchar(255),
    attributes text,
    isoriginaltitle tinyint(1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "title_episode": """
CREATE TABLE IF NOT EXISTS title_episode (
    tconst varchar(255) NOT NULL PRIMARY KEY,
    parenttconst varchar(255),
    seasonnumber int,
    episodenumber int
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "title_crew": """
CREATE TABLE IF NOT EXISTS title_crew (
    tconst varchar(255) NOT NULL PRIMARY KEY,
    directors text,
    writers text
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
}

# Columns per table (excluding synthetic 'id'), in TSV header order.
# Used for PG COPY and MySQL INSERT column lists.
TABLE_COLUMNS = {
    "title_basics": [
        "tconst", "titletype", "primarytitle", "originaltitle",
        "isadult", "startyear", "endyear", "runtimeminutes", "genres",
    ],
    "title_ratings": ["tconst", "averagerating", "numvotes"],
    "name_basics": [
        "nconst", "primaryname", "birthyear", "deathyear",
        "primaryprofession", "knownfortitles",
    ],
    "title_principals": [
        "tconst", "ordering", "nconst", "category", "job", "characters",
    ],
    "title_akas": [
        "titleid", "ordering", "title", "region", "language",
        "types", "attributes", "isoriginaltitle",
    ],
    "title_episode": ["tconst", "parenttconst", "seasonnumber", "episodenumber"],
    "title_crew": ["tconst", "directors", "writers"],
}

# Columns that need type conversion (everything else is text/varchar).
BOOL_COLUMNS = {"isadult", "isoriginaltitle"}
INT_COLUMNS = {
    "startyear", "endyear", "runtimeminutes", "numvotes",
    "birthyear", "deathyear", "ordering", "seasonnumber", "episodenumber",
}
FLOAT_COLUMNS = {"averagerating"}

log = logging.getLogger("load_imdb")

# Secondary indexes (created after bulk load for speed)

PG_INDEXES = [
    # title_principals: the biggest table (~60M rows), joins on tconst/nconst
    "CREATE INDEX IF NOT EXISTS idx_tp_tconst ON title_principals (tconst)",
    "CREATE INDEX IF NOT EXISTS idx_tp_nconst ON title_principals (nconst)",
    "CREATE INDEX IF NOT EXISTS idx_tp_category ON title_principals (category)",
    # title_akas: join to title_basics via titleid
    "CREATE INDEX IF NOT EXISTS idx_ta_titleid ON title_akas (titleid)",
    "CREATE INDEX IF NOT EXISTS idx_ta_region ON title_akas (region)",
    # title_episode: find episodes by parent series
    "CREATE INDEX IF NOT EXISTS idx_te_parent ON title_episode (parenttconst)",
    # title_basics: common filter/group columns
    "CREATE INDEX IF NOT EXISTS idx_tb_startyear ON title_basics (startyear)",
    "CREATE INDEX IF NOT EXISTS idx_tb_titletype ON title_basics (titletype)",
    # title_ratings: filter by popularity
    "CREATE INDEX IF NOT EXISTS idx_tr_numvotes ON title_ratings (numvotes)",
    "CREATE INDEX IF NOT EXISTS idx_tr_rating ON title_ratings (averagerating)",
]

MY_INDEXES = [
    "CREATE INDEX idx_tp_tconst ON title_principals (tconst)",
    "CREATE INDEX idx_tp_nconst ON title_principals (nconst)",
    "CREATE INDEX idx_tp_category ON title_principals (category)",
    "CREATE INDEX idx_ta_titleid ON title_akas (titleid)",
    "CREATE INDEX idx_ta_region ON title_akas (region)",
    "CREATE INDEX idx_te_parent ON title_episode (parenttconst)",
    "CREATE INDEX idx_tb_startyear ON title_basics (startyear)",
    "CREATE INDEX idx_tb_titletype ON title_basics (titletype)",
    "CREATE INDEX idx_tr_numvotes ON title_ratings (numvotes)",
    "CREATE INDEX idx_tr_rating ON title_ratings (averagerating)",
]

# Download helpers


def _download_progress(block_num, block_size, total_size):
    """Callback for urllib.request.urlretrieve progress."""
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100, downloaded * 100 // total_size)
        mb = downloaded / (1024 * 1024)
        total_mb = total_size / (1024 * 1024)
        print(f"\r  {mb:.1f}/{total_mb:.1f} MB ({pct}%)", end="", flush=True)


def download_dataset(dataset_name, download_dir, skip_download=False):
    """Download a single dataset TSV.gz file, returning the local path."""
    info = DATASETS[dataset_name]
    filename = f"{dataset_name}.tsv.gz"
    filepath = download_dir / filename

    if filepath.exists():
        log.info("  cached: %s", filepath)
        return filepath

    if skip_download:
        log.error("  %s not found in cache and --skip-download set", filename)
        return None

    log.info("  downloading %s ...", info["url"])
    urllib.request.urlretrieve(info["url"], filepath, reporthook=_download_progress)
    print()  # newline after progress
    return filepath


# PostgreSQL loader (COPY FROM STDIN)


def _pg_copy_transform_line(line, columns):
    """Transform a TSV line for PostgreSQL COPY.

    COPY with FORMAT csv, DELIMITER E'\\t' handles \\N as NULL natively,
    but we still need to convert booleans (1/0 → t/f) and leave \\N intact
    for integer/float columns (PG handles that).

    Returns a transformed TSV line (bytes) or None to skip the row.
    """
    fields = line.split("\t")
    if len(fields) != len(columns):
        return None  # skip malformed

    out = []
    for col, val in zip(columns, fields):
        if val == "\\N":
            out.append("\\N")
        elif col in BOOL_COLUMNS:
            out.append("t" if val == "1" else "f" if val == "0" else "\\N")
        else:
            out.append(val)
    return "\t".join(out)


def load_pg(conn, table_name, filepath, columns, limit):
    """Load data into PostgreSQL using COPY FROM STDIN."""
    col_list = ", ".join(columns)
    copy_sql = (
        f"COPY {table_name} ({col_list}) FROM STDIN "
        f"WITH (FORMAT text, DELIMITER E'\\t', NULL '\\N')"
    )

    rows = 0
    t0 = time.monotonic()

    # We need autocommit off for COPY
    old_autocommit = conn.autocommit
    conn.autocommit = False

    try:
        cur = conn.cursor()

        with gzip.open(filepath, "rt", encoding="utf-8") as f:
            _header = f.readline()  # skip TSV header

            # Build an in-memory buffer, flushing every 100k lines via copy_expert
            buf = io.StringIO()
            buf_rows = 0
            flush_every = 100_000

            for line in f:
                line = line.rstrip("\n")
                transformed = _pg_copy_transform_line(line, columns)
                if transformed is None:
                    continue

                buf.write(transformed)
                buf.write("\n")
                buf_rows += 1
                rows += 1

                if buf_rows >= flush_every:
                    buf.seek(0)
                    cur.copy_expert(copy_sql, buf)
                    conn.commit()
                    buf = io.StringIO()
                    buf_rows = 0
                    elapsed = time.monotonic() - t0
                    log.info("  %s: %d rows (%.1fs)", table_name, rows, elapsed)

                if limit and rows >= limit:
                    break

            # flush remainder
            if buf_rows > 0:
                buf.seek(0)
                cur.copy_expert(copy_sql, buf)
                conn.commit()

        cur.close()
    finally:
        conn.autocommit = old_autocommit

    return rows


# MySQL loader (batch INSERT IGNORE)


def _parse_value(col, val):
    """Parse a single TSV value into a Python type for MySQL executemany."""
    if val == "\\N" or val == "":
        return None
    if col in BOOL_COLUMNS:
        return 1 if val == "1" else 0
    if col in INT_COLUMNS:
        try:
            return int(val)
        except (ValueError, TypeError):
            return None
    if col in FLOAT_COLUMNS:
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
    return val


def load_mysql(conn, table_name, filepath, columns, limit):
    """Load data into MySQL using batch INSERT IGNORE."""
    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))
    insert_sql = f"INSERT IGNORE INTO {table_name} ({col_list}) VALUES ({placeholders})"

    batch_size = 5000
    rows = 0
    t0 = time.monotonic()

    cur = conn.cursor()

    with gzip.open(filepath, "rt", encoding="utf-8") as f:
        _header = f.readline()  # skip TSV header

        batch = []
        for line in f:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != len(columns):
                continue

            row_vals = tuple(_parse_value(col, val) for col, val in zip(columns, fields))
            batch.append(row_vals)
            rows += 1

            if len(batch) >= batch_size:
                cur.executemany(insert_sql, batch)
                conn.commit()
                batch = []

                if rows % 100_000 == 0:
                    elapsed = time.monotonic() - t0
                    log.info("  %s: %d rows (%.1fs)", table_name, rows, elapsed)

            if limit and rows >= limit:
                break

        if batch:
            cur.executemany(insert_sql, batch)
            conn.commit()

    cur.close()
    return rows


def parse_database_url(url):
    """Parse a database URL into engine name and connection kwargs."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()

    if scheme.startswith("postgres"):
        engine = "postgresql"
    elif scheme.startswith("mysql"):
        engine = "mysql"
    else:
        raise ValueError(f"Unsupported scheme: {scheme}. Use postgresql:// or mysql://")

    return engine, {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or (5432 if engine == "postgresql" else 3306),
        "user": parsed.username,
        "password": parsed.password,
        "database": (parsed.path or "/").lstrip("/"),
    }


def create_connection(engine, params):
    """Create a direct database connection."""
    if engine == "postgresql":
        import psycopg2

        conn = psycopg2.connect(
            host=params["host"],
            port=params["port"],
            user=params["user"],
            password=params["password"],
            database=params["database"],
            connect_timeout=30,
        )
        conn.autocommit = True
        return conn
    elif engine == "mysql":
        import pymysql

        return pymysql.connect(
            host=params["host"],
            port=int(params["port"]),
            user=params["user"],
            password=params["password"],
            database=params["database"],
            connect_timeout=30,
            autocommit=True,
        )
    else:
        raise ValueError(f"Unsupported engine: {engine}")


def main():
    parser = argparse.ArgumentParser(
        description="Load IMDb datasets into PostgreSQL or MySQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("database_url", help="Database URL (postgresql://... or mysql://...)")
    parser.add_argument("--datasets", help="Comma-separated dataset names (default: all 7)")
    parser.add_argument("--all", action="store_true", help="(default) Load all 7 datasets")
    parser.add_argument("--limit", type=int, default=0, help="Max rows per table (0 = unlimited)")
    parser.add_argument("--drop", action="store_true", help="Drop and recreate tables before loading")
    parser.add_argument("--download-dir", help="Override download cache directory")
    parser.add_argument("--skip-download", action="store_true", help="Use cached files only")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    engine, params = parse_database_url(args.database_url)
    log.info("Database: %s @ %s:%s/%s", engine, params["host"], params["port"], params["database"])

    # Determine datasets
    if args.all:
        dataset_names = list(DATASETS.keys())
    elif args.datasets:
        dataset_names = [d.strip() for d in args.datasets.split(",")]
        for d in dataset_names:
            if d not in DATASETS:
                parser.error(f"Unknown dataset: {d}. Valid: {', '.join(DATASETS.keys())}")
    else:
        dataset_names = DEFAULT_DATASETS

    log.info("Datasets: %s", ", ".join(dataset_names))
    if args.limit:
        log.info("Row limit per table: %d", args.limit)

    # Download cache directory
    download_dir = Path(args.download_dir) if args.download_dir else Path.home() / ".rdst" / "cache" / "imdb"
    download_dir.mkdir(parents=True, exist_ok=True)
    log.info("Download cache: %s", download_dir)

    # Download all datasets first
    log.info("--- Downloading ---")
    file_map = {}
    for ds in dataset_names:
        fp = download_dataset(ds, download_dir, skip_download=args.skip_download)
        if fp:
            file_map[ds] = fp
        else:
            log.warning("Skipping %s (not available)", ds)

    if not file_map:
        log.error("No datasets available to load.")
        sys.exit(1)

    # Connect to database
    conn = create_connection(engine, params)
    log.info("Connected to %s", engine)

    ddl = PG_DDL if engine == "postgresql" else MY_DDL
    load_fn = load_pg if engine == "postgresql" else load_mysql

    # Create/drop tables
    cur = conn.cursor()
    for ds in dataset_names:
        if ds not in file_map:
            continue
        table = DATASETS[ds]["table"]

        if args.drop:
            log.info("Dropping table %s", table)
            cur.execute(f"DROP TABLE IF EXISTS {table}")

        log.info("Creating table %s (if not exists)", table)
        cur.execute(ddl[table])
    cur.close()

    # Load data
    log.info("--- Loading ---")
    summary = {}
    total_t0 = time.monotonic()

    for ds in dataset_names:
        if ds not in file_map:
            continue
        table = DATASETS[ds]["table"]
        columns = TABLE_COLUMNS[table]
        filepath = file_map[ds]

        log.info("Loading %s -> %s", ds, table)
        t0 = time.monotonic()
        row_count = load_fn(conn, table, filepath, columns, args.limit or None)
        elapsed = time.monotonic() - t0
        summary[table] = (row_count, elapsed)
        log.info("  done: %d rows in %.1fs", row_count, elapsed)

    # Create secondary indexes (after bulk load for faster inserts)
    idx_stmts = PG_INDEXES if engine == "postgresql" else MY_INDEXES
    log.info("--- Creating indexes ---")
    cur = conn.cursor()
    for stmt in idx_stmts:
        idx_name = stmt.split("INDEX")[1].split("ON")[0].strip().replace("IF NOT EXISTS ", "")
        log.info("  %s", idx_name)
        t0 = time.monotonic()
        try:
            cur.execute(stmt)
        except Exception as e:
            # MySQL errors on duplicate index names; skip gracefully
            log.warning("  skipped %s: %s", idx_name, e)
        elapsed = time.monotonic() - t0
        if elapsed > 1:
            log.info("    %.1fs", elapsed)
    cur.close()

    conn.close()

    # Print summary
    total_elapsed = time.monotonic() - total_t0
    print()
    print("=== Summary ===")
    total_rows = 0
    for table, (count, elapsed) in summary.items():
        rate = count / elapsed if elapsed > 0 else 0
        print(f"  {table:25s}  {count:>12,} rows  {elapsed:>6.1f}s  ({rate:,.0f} rows/s)")
        total_rows += count
    print(f"  {'TOTAL':25s}  {total_rows:>12,} rows  {total_elapsed:>6.1f}s")


if __name__ == "__main__":
    main()
