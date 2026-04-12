#!/usr/bin/env python3
# /// script
# dependencies = ["py7zr", "psycopg2-binary", "pymysql"]
# ///
"""
StackExchange Dataset Loader.

Downloads StackExchange data dump .7z archives from archive.org and
bulk-loads them into PostgreSQL or MySQL via a database URL.

Usage:
    uv run load_stackoverflow.py "postgresql://user:pass@localhost:5432/mydb"
    uv run load_stackoverflow.py "mysql://user:pass@localhost:3306/mydb" --limit 100000
    uv run load_stackoverflow.py "postgresql://..." --site dba.stackexchange.com
    uv run load_stackoverflow.py "postgresql://..." --tables posts,users,tags --drop
"""

import argparse
import io
import logging
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Table definitions
# ---------------------------------------------------------------------------

ARCHIVE_BASE_URL = "https://archive.org/download/stackexchange"

# Map of table name -> XML filename and column definitions.
# Columns are (xml_attr, sql_col, type) where type is one of:
#   "int", "smallint", "ts", "text", "title"
TABLES = {
    "posts": {
        "xml_file": "Posts.xml",
        "columns": [
            ("Id", "id", "int"),
            ("PostTypeId", "posttypeid", "smallint"),
            ("AcceptedAnswerId", "acceptedanswerid", "int"),
            ("ParentId", "parentid", "int"),
            ("CreationDate", "creationdate", "ts"),
            ("Score", "score", "int"),
            ("ViewCount", "viewcount", "int"),
            ("Body", "body", "text"),
            ("OwnerUserId", "owneruserid", "int"),
            ("Title", "title", "title"),
            ("Tags", "tags", "title"),
            ("AnswerCount", "answercount", "int"),
            ("CommentCount", "commentcount", "int"),
            ("FavoriteCount", "favoritecount", "int"),
            ("ClosedDate", "closeddate", "ts"),
            ("LastActivityDate", "lastactivitydate", "ts"),
            ("LastEditDate", "lasteditdate", "ts"),
        ],
    },
    "users": {
        "xml_file": "Users.xml",
        "columns": [
            ("Id", "id", "int"),
            ("Reputation", "reputation", "int"),
            ("CreationDate", "creationdate", "ts"),
            ("DisplayName", "displayname", "title"),
            ("LastAccessDate", "lastaccessdate", "ts"),
            ("Location", "location", "title"),
            ("AboutMe", "aboutme", "text"),
            ("Views", "views", "int"),
            ("UpVotes", "upvotes", "int"),
            ("DownVotes", "downvotes", "int"),
            ("AccountId", "accountid", "int"),
        ],
    },
    "comments": {
        "xml_file": "Comments.xml",
        "columns": [
            ("Id", "id", "int"),
            ("PostId", "postid", "int"),
            ("Score", "score", "int"),
            ("Text", "text", "text"),
            ("CreationDate", "creationdate", "ts"),
            ("UserId", "userid", "int"),
        ],
    },
    "votes": {
        "xml_file": "Votes.xml",
        "columns": [
            ("Id", "id", "int"),
            ("PostId", "postid", "int"),
            ("VoteTypeId", "votetypeid", "smallint"),
            ("CreationDate", "creationdate", "ts"),
        ],
    },
    "tags": {
        "xml_file": "Tags.xml",
        "columns": [
            ("Id", "id", "int"),
            ("TagName", "tagname", "title"),
            ("Count", "count", "int"),
            ("ExcerptPostId", "excerptpostid", "int"),
            ("WikiPostId", "wikipostid", "int"),
        ],
    },
    "badges": {
        "xml_file": "Badges.xml",
        "columns": [
            ("Id", "id", "int"),
            ("UserId", "userid", "int"),
            ("Name", "name", "title"),
            ("Date", "date", "ts"),
            ("Class", "class", "smallint"),
            ("TagBased", "tagbased", "title"),
        ],
    },
    "postlinks": {
        "xml_file": "PostLinks.xml",
        "columns": [
            ("Id", "id", "int"),
            ("CreationDate", "creationdate", "ts"),
            ("PostId", "postid", "int"),
            ("RelatedPostId", "relatedpostid", "int"),
            ("LinkTypeId", "linktypeid", "smallint"),
        ],
    },
    "posthistory": {
        "xml_file": "PostHistory.xml",
        "columns": [
            ("Id", "id", "int"),
            ("PostHistoryTypeId", "posthistorytypeid", "smallint"),
            ("PostId", "postid", "int"),
            ("RevisionGUID", "revisionguid", "title"),
            ("CreationDate", "creationdate", "ts"),
            ("UserId", "userid", "int"),
            ("Text", "text", "text"),
        ],
    },
}

DEFAULT_TABLES = ["posts", "users", "comments", "votes", "tags", "badges", "postlinks"]

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

PG_DDL = {
    "posts": """
CREATE TABLE IF NOT EXISTS posts (
    id integer PRIMARY KEY,
    posttypeid smallint,
    acceptedanswerid integer,
    parentid integer,
    creationdate timestamp,
    score integer,
    viewcount integer,
    body text,
    owneruserid integer,
    title text,
    tags text,
    answercount integer,
    commentcount integer,
    favoritecount integer,
    closeddate timestamp,
    lastactivitydate timestamp,
    lasteditdate timestamp
)""",
    "users": """
CREATE TABLE IF NOT EXISTS users (
    id integer PRIMARY KEY,
    reputation integer,
    creationdate timestamp,
    displayname text,
    lastaccessdate timestamp,
    location text,
    aboutme text,
    views integer,
    upvotes integer,
    downvotes integer,
    accountid integer
)""",
    "comments": """
CREATE TABLE IF NOT EXISTS comments (
    id integer PRIMARY KEY,
    postid integer,
    score integer,
    text text,
    creationdate timestamp,
    userid integer
)""",
    "votes": """
CREATE TABLE IF NOT EXISTS votes (
    id integer PRIMARY KEY,
    postid integer,
    votetypeid smallint,
    creationdate timestamp
)""",
    "tags": """
CREATE TABLE IF NOT EXISTS tags (
    id integer PRIMARY KEY,
    tagname text,
    count integer,
    excerptpostid integer,
    wikipostid integer
)""",
    "badges": """
CREATE TABLE IF NOT EXISTS badges (
    id integer PRIMARY KEY,
    userid integer,
    name text,
    date timestamp,
    class smallint,
    tagbased text
)""",
    "postlinks": """
CREATE TABLE IF NOT EXISTS postlinks (
    id integer PRIMARY KEY,
    creationdate timestamp,
    postid integer,
    relatedpostid integer,
    linktypeid smallint
)""",
    "posthistory": """
CREATE TABLE IF NOT EXISTS posthistory (
    id integer PRIMARY KEY,
    posthistorytypeid smallint,
    postid integer,
    revisionguid text,
    creationdate timestamp,
    userid integer,
    text text
)""",
}

MY_DDL = {
    "posts": """
CREATE TABLE IF NOT EXISTS posts (
    id int NOT NULL PRIMARY KEY,
    posttypeid smallint,
    acceptedanswerid int,
    parentid int,
    creationdate datetime,
    score int,
    viewcount int,
    body mediumtext,
    owneruserid int,
    title varchar(512),
    tags varchar(512),
    answercount int,
    commentcount int,
    favoritecount int,
    closeddate datetime,
    lastactivitydate datetime,
    lasteditdate datetime
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "users": """
CREATE TABLE IF NOT EXISTS users (
    id int NOT NULL PRIMARY KEY,
    reputation int,
    creationdate datetime,
    displayname varchar(512),
    lastaccessdate datetime,
    location varchar(512),
    aboutme mediumtext,
    views int,
    upvotes int,
    downvotes int,
    accountid int
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "comments": """
CREATE TABLE IF NOT EXISTS comments (
    id int NOT NULL PRIMARY KEY,
    postid int,
    score int,
    text mediumtext,
    creationdate datetime,
    userid int
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "votes": """
CREATE TABLE IF NOT EXISTS votes (
    id int NOT NULL PRIMARY KEY,
    postid int,
    votetypeid smallint,
    creationdate datetime
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "tags": """
CREATE TABLE IF NOT EXISTS tags (
    id int NOT NULL PRIMARY KEY,
    tagname varchar(255),
    count int,
    excerptpostid int,
    wikipostid int
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "badges": """
CREATE TABLE IF NOT EXISTS badges (
    id int NOT NULL PRIMARY KEY,
    userid int,
    name varchar(255),
    date datetime,
    class smallint,
    tagbased varchar(64)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "postlinks": """
CREATE TABLE IF NOT EXISTS postlinks (
    id int NOT NULL PRIMARY KEY,
    creationdate datetime,
    postid int,
    relatedpostid int,
    linktypeid smallint
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    "posthistory": """
CREATE TABLE IF NOT EXISTS posthistory (
    id int NOT NULL PRIMARY KEY,
    posthistorytypeid smallint,
    postid int,
    revisionguid varchar(64),
    creationdate datetime,
    userid int,
    text mediumtext
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
}

log = logging.getLogger("load_stackoverflow")

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def _download_progress(block_num, block_size, total_size):
    """Callback for urllib.request.urlretrieve progress."""
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100, downloaded * 100 // total_size)
        mb = downloaded / (1024 * 1024)
        total_mb = total_size / (1024 * 1024)
        print(f"\r  {mb:.1f}/{total_mb:.1f} MB ({pct}%)", end="", flush=True)
    else:
        mb = downloaded / (1024 * 1024)
        print(f"\r  {mb:.1f} MB", end="", flush=True)


def _probe_url(url):
    """Check if a URL exists (HEAD request). Returns True if 200, False otherwise."""
    req = urllib.request.Request(url, method="HEAD")
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status == 200
    except Exception:
        return False


def _download_file(url, filepath):
    """Download a URL to a local file with progress reporting."""
    log.info("  downloading %s ...", url)
    try:
        urllib.request.urlretrieve(url, filepath, reporthook=_download_progress)
    except Exception:
        if filepath.exists():
            filepath.unlink()
        raise
    print()  # newline after progress


def is_split_site(site, download_dir, skip_download=False):
    """Determine if a site uses per-table .7z files (like stackoverflow.com)
    vs a single combined .7z (like dba.stackexchange.com).

    Returns True if the site is split into per-table archives.
    """
    # If we already have the combined archive cached, use it
    combined = download_dir / f"{site}.7z"
    if combined.exists():
        return False

    # If we have any per-table archive cached, it's split
    for table_def in TABLES.values():
        xml_name = table_def["xml_file"].replace(".xml", "")
        per_table = download_dir / f"{site}-{xml_name}.7z"
        if per_table.exists():
            return True

    if skip_download:
        return False

    # Probe: try the combined archive first (cheaper for small sites)
    combined_url = f"{ARCHIVE_BASE_URL}/{site}.7z"
    if _probe_url(combined_url):
        return False

    # Must be split (like stackoverflow.com)
    return True


def download_combined_archive(site, download_dir, skip_download=False):
    """Download a single combined .7z archive for a site. Returns path or None."""
    filename = f"{site}.7z"
    filepath = download_dir / filename

    if filepath.exists():
        size_mb = filepath.stat().st_size / (1024 * 1024)
        log.info("  cached: %s (%.1f MB)", filepath, size_mb)
        return filepath

    if skip_download:
        log.error("  %s not found in cache and --skip-download set", filename)
        return None

    _download_file(f"{ARCHIVE_BASE_URL}/{filename}", filepath)
    return filepath


def download_table_archive(site, table_def, download_dir, skip_download=False):
    """Download a per-table .7z archive (e.g. stackoverflow.com-Posts.7z).
    Returns path or None."""
    xml_name = table_def["xml_file"].replace(".xml", "")
    filename = f"{site}-{xml_name}.7z"
    filepath = download_dir / filename

    if filepath.exists():
        size_mb = filepath.stat().st_size / (1024 * 1024)
        log.info("  cached: %s (%.1f MB)", filepath, size_mb)
        return filepath

    if skip_download:
        log.error("  %s not found in cache and --skip-download set", filename)
        return None

    _download_file(f"{ARCHIVE_BASE_URL}/{filename}", filepath)
    return filepath


# ---------------------------------------------------------------------------
# 7z extraction + XML parsing
# ---------------------------------------------------------------------------


def extract_and_parse(archive_path, xml_filename, table_def, limit, keep_xml, download_dir):
    """Extract a single XML file from the .7z and yield rows as dicts.

    Each row is a dict mapping sql_col -> value (string or None).
    Uses iterparse for constant memory usage.

    Handles both combined archives (xml_filename is one of many files inside)
    and per-table archives (the .7z contains just the XML file).
    """
    import py7zr

    xml_path = download_dir / xml_filename

    # Extract if not already present
    if not xml_path.exists():
        log.info("  extracting %s from %s ...", xml_filename, archive_path.name)
        t0 = time.monotonic()
        with py7zr.SevenZipFile(archive_path, "r") as z:
            names = z.getnames()
            if xml_filename in names:
                z.extract(path=download_dir, targets=[xml_filename])
            elif len(names) == 1:
                # Per-table archive: single file inside, extract and rename
                z.extractall(path=download_dir)
                extracted = download_dir / names[0]
                if extracted.name != xml_filename and extracted.exists():
                    extracted.rename(xml_path)
            else:
                log.error("  %s not found in archive (contents: %s)", xml_filename, names)
                return
        elapsed = time.monotonic() - t0
        if xml_path.exists():
            size_mb = xml_path.stat().st_size / (1024 * 1024)
            log.info("  extracted %s (%.1f MB, %.1fs)", xml_filename, size_mb, elapsed)
        else:
            log.error("  %s not found in archive", xml_filename)
            return
    else:
        size_mb = xml_path.stat().st_size / (1024 * 1024)
        log.info("  using cached %s (%.1f MB)", xml_filename, size_mb)

    # Build attr->col mapping
    columns = table_def["columns"]
    col_names = [col for _, col, _ in columns]

    count = 0
    try:
        for event, elem in ET.iterparse(str(xml_path), events=("end",)):
            if elem.tag != "row":
                elem.clear()
                continue

            row = {}
            for attr, col, ctype in columns:
                val = elem.attrib.get(attr)
                row[col] = val  # None if missing

            yield row
            count += 1

            if limit and count >= limit:
                break

            # Free memory
            elem.clear()
    finally:
        if not keep_xml and xml_path.exists():
            log.debug("  removing %s", xml_path)
            xml_path.unlink()


# ---------------------------------------------------------------------------
# PostgreSQL loader (COPY FROM STDIN)
# ---------------------------------------------------------------------------


def _pg_escape(val):
    """Escape a string value for PG COPY text format.

    In text format: backslash is special, tab separates fields, newline ends rows.
    """
    if val is None:
        return "\\N"
    # Order matters: escape backslash first
    val = val.replace("\\", "\\\\")
    val = val.replace("\t", "\\t")
    val = val.replace("\n", "\\n")
    val = val.replace("\r", "\\r")
    return val


def load_pg(conn, table_name, table_def, archive_path, download_dir, limit, keep_xml):
    """Load data into PostgreSQL using COPY FROM STDIN."""
    columns = table_def["columns"]
    col_names = [col for _, col, _ in columns]
    col_list = ", ".join(col_names)
    copy_sql = (
        f"COPY {table_name} ({col_list}) FROM STDIN "
        f"WITH (FORMAT text, DELIMITER E'\\t', NULL '\\N')"
    )

    rows = 0
    t0 = time.monotonic()

    old_autocommit = conn.autocommit
    conn.autocommit = False

    try:
        cur = conn.cursor()
        buf = io.StringIO()
        buf_rows = 0
        flush_every = 100_000

        for row in extract_and_parse(
            archive_path, table_def["xml_file"], table_def, limit, keep_xml, download_dir
        ):
            # Build TSV line
            fields = []
            for _, col, ctype in columns:
                val = row.get(col)
                if val is None:
                    fields.append("\\N")
                elif ctype in ("int", "smallint"):
                    # Validate numeric
                    try:
                        int(val)
                        fields.append(val)
                    except (ValueError, TypeError):
                        fields.append("\\N")
                elif ctype == "ts":
                    # Timestamps come as "2009-07-01T00:00:00.000" — PG handles this
                    fields.append(_pg_escape(val))
                else:
                    fields.append(_pg_escape(val))

            buf.write("\t".join(fields))
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

        # Flush remainder
        if buf_rows > 0:
            buf.seek(0)
            cur.copy_expert(copy_sql, buf)
            conn.commit()

        cur.close()
    finally:
        conn.autocommit = old_autocommit

    return rows


# ---------------------------------------------------------------------------
# MySQL loader (batch INSERT IGNORE)
# ---------------------------------------------------------------------------


def _mysql_parse_value(val, ctype):
    """Convert a string value to the appropriate Python type for MySQL."""
    if val is None:
        return None
    if ctype in ("int", "smallint"):
        try:
            return int(val)
        except (ValueError, TypeError):
            return None
    if ctype == "ts":
        # "2009-07-01T00:00:00.000" -> "2009-07-01 00:00:00"
        if val and "T" in val:
            val = val.replace("T", " ")
            # Trim sub-second precision for MySQL datetime
            if "." in val:
                val = val[: val.index(".")]
        return val
    return val


def load_mysql(conn, table_name, table_def, archive_path, download_dir, limit, keep_xml):
    """Load data into MySQL using batch INSERT IGNORE."""
    columns = table_def["columns"]
    col_names = [col for _, col, _ in columns]
    col_list = ", ".join(col_names)
    placeholders = ", ".join(["%s"] * len(col_names))
    insert_sql = f"INSERT IGNORE INTO {table_name} ({col_list}) VALUES ({placeholders})"

    batch_size = 5000
    rows = 0
    t0 = time.monotonic()

    cur = conn.cursor()

    batch = []
    for row in extract_and_parse(
        archive_path, table_def["xml_file"], table_def, limit, keep_xml, download_dir
    ):
        row_vals = tuple(
            _mysql_parse_value(row.get(col), ctype)
            for _, col, ctype in columns
        )
        batch.append(row_vals)
        rows += 1

        if len(batch) >= batch_size:
            cur.executemany(insert_sql, batch)
            conn.commit()
            batch = []

            if rows % 100_000 == 0:
                elapsed = time.monotonic() - t0
                log.info("  %s: %d rows (%.1fs)", table_name, rows, elapsed)

    if batch:
        cur.executemany(insert_sql, batch)
        conn.commit()

    cur.close()
    return rows


# ---------------------------------------------------------------------------
# Database helpers (same pattern as load_imdb.py)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Load StackExchange data dumps into PostgreSQL or MySQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("database_url", help="Database URL (postgresql://... or mysql://...)")
    parser.add_argument(
        "--site", default="dba.stackexchange.com",
        help="StackExchange site name (default: dba.stackexchange.com)",
    )
    parser.add_argument("--tables", help="Comma-separated table names (default: all 7 except posthistory)")
    parser.add_argument("--limit", type=int, default=0, help="Max rows per table (0 = unlimited)")
    parser.add_argument("--drop", action="store_true", help="Drop and recreate tables before loading")
    parser.add_argument("--download-dir", help="Override download cache directory")
    parser.add_argument("--skip-download", action="store_true", help="Use cached files only")
    parser.add_argument("--keep-xml", action="store_true", help="Don't delete extracted XMLs after loading")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    engine, params = parse_database_url(args.database_url)
    log.info("Database: %s @ %s:%s/%s", engine, params["host"], params["port"], params["database"])
    log.info("Site: %s", args.site)

    # Determine tables
    if args.tables:
        table_names = [t.strip().lower() for t in args.tables.split(",")]
        for t in table_names:
            if t not in TABLES:
                parser.error(f"Unknown table: {t}. Valid: {', '.join(TABLES.keys())}")
    else:
        table_names = DEFAULT_TABLES

    log.info("Tables: %s", ", ".join(table_names))
    if args.limit:
        log.info("Row limit per table: %d", args.limit)

    # Download cache directory
    download_dir = (
        Path(args.download_dir)
        if args.download_dir
        else Path.home() / ".rdst" / "cache" / "stackexchange"
    )
    download_dir.mkdir(parents=True, exist_ok=True)
    log.info("Download cache: %s", download_dir)

    # Determine archive layout: combined (one .7z) or split (per-table .7z)
    log.info("--- Downloading ---")
    split = is_split_site(args.site, download_dir, skip_download=args.skip_download)
    combined_archive = None

    if split:
        log.info("Site uses per-table archives (e.g. %s-Posts.7z)", args.site)
        # Download per-table archives upfront
        archive_map = {}
        for table in table_names:
            table_def = TABLES[table]
            path = download_table_archive(
                args.site, table_def, download_dir, skip_download=args.skip_download
            )
            if path:
                archive_map[table] = path
            else:
                log.warning("Skipping %s (archive not available)", table)
        if not archive_map:
            log.error("No archives available. Exiting.")
            sys.exit(1)
    else:
        log.info("Site uses a combined archive (%s.7z)", args.site)
        combined_archive = download_combined_archive(
            args.site, download_dir, skip_download=args.skip_download
        )
        if not combined_archive:
            log.error("Archive not available. Exiting.")
            sys.exit(1)
        archive_map = {table: combined_archive for table in table_names}

    # Connect to database
    conn = create_connection(engine, params)
    log.info("Connected to %s", engine)

    ddl = PG_DDL if engine == "postgresql" else MY_DDL

    # Create/drop tables
    cur = conn.cursor()
    for table in table_names:
        if table not in archive_map:
            continue
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

    if engine == "postgresql":
        load_fn = load_pg
    else:
        load_fn = load_mysql

    for table in table_names:
        if table not in archive_map:
            continue
        table_def = TABLES[table]
        archive_path = archive_map[table]
        log.info("Loading %s (from %s)", table, archive_path.name)
        t0 = time.monotonic()
        row_count = load_fn(
            conn, table, table_def, archive_path, download_dir,
            args.limit or None, args.keep_xml,
        )
        elapsed = time.monotonic() - t0
        summary[table] = (row_count, elapsed)
        log.info("  done: %d rows in %.1fs", row_count, elapsed)

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
