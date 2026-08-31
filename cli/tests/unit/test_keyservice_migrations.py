"""Keep the keyservice usage_log INSERT and its migrations in sync.

D1 is SQLite, and the worker wraps usage tracking in a broad
try/except, so a drifted column list would fail silently in
production and drop billing rows. Applying the real migration files
to SQLite and running the worker's INSERT column shape catches that
before deploy.
"""

import sqlite3
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
MIGRATIONS_DIR = Path(__file__).parents[2] / "keyservice" / "migrations"
WRANGLER_CONFIG = MIGRATIONS_DIR.parent / "wrangler.toml"

# The public GitHub mirror publishes the CLI without the keyservice; there is
# nothing for this drift guard to check in such a checkout.
if not MIGRATIONS_DIR.is_dir():
    pytest.skip(
        "keyservice sources are not part of this checkout",
        allow_module_level=True,
    )

# Mirrors the INSERT in keyservice/src/index.py. Update both together.
USAGE_LOG_INSERT = (
    "INSERT INTO usage_log (email, model, input_tokens, output_tokens, "
    "cost_cents, created_at, ip_address, duration_ms, max_tokens_requested) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def test_migrations_apply_cleanly_in_order():
    db = sqlite3.connect(":memory:")
    applied = []
    for path in sorted(MIGRATIONS_DIR.glob("0*.sql")):
        db.executescript(path.read_text(encoding="utf-8"))
        applied.append(path.name)
    assert applied, "no migration files found"


def test_local_wrangler_config_binds_the_migrated_database():
    config = tomllib.loads(WRANGLER_CONFIG.read_text(encoding="utf-8"))
    local_databases = config.get("d1_databases", [])

    assert any(
        database.get("binding") == "DB"
        and database.get("database_name") == "rdst-keyservice-db-local"
        and database.get("migrations_dir") == "migrations"
        for database in local_databases
    )


def test_usage_log_insert_matches_migrated_schema():
    db = sqlite3.connect(":memory:")
    for path in sorted(MIGRATIONS_DIR.glob("0*.sql")):
        db.executescript(path.read_text(encoding="utf-8"))
    db.execute(
        USAGE_LOG_INSERT,
        ("t@example.com", "model", 7051, 4745, 9.23,
         "2026-08-19T20:48:38Z", "1.2.3.4", 74000, 6144),
    )
    row = db.execute(
        "SELECT duration_ms, max_tokens_requested FROM usage_log"
    ).fetchone()
    assert row == (74000, 6144)


def test_account_auth_and_inference_use_only_new_tables():
    db = sqlite3.connect(":memory:")
    legacy_migrations = sorted(MIGRATIONS_DIR.glob("000[1-4]_*.sql"))
    for path in legacy_migrations:
        db.executescript(path.read_text(encoding="utf-8"))

    legacy_tables = ("users", "registration_attempts", "settings", "usage_log",
                     "oauth_logins", "oauth_start_attempts")
    before = {
        table: db.execute(f"PRAGMA table_info({table})").fetchall()
        for table in legacy_tables
    }

    db.executescript(
        (MIGRATIONS_DIR / "0006_account_auth_and_inference.sql").read_text(
            encoding="utf-8")
    )

    after = {
        table: db.execute(f"PRAGMA table_info({table})").fetchall()
        for table in legacy_tables
    }
    assert after == before

    new_tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "account_auth_logins",
        "account_auth_attempts",
        "inference_accounts",
        "inference_reservations",
        "inference_usage",
        "inference_rate_events",
        "inference_rate_buckets",
        "inference_settings",
    } <= new_tables
