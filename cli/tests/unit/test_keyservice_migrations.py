"""Keep the keyservice usage_log INSERT and its migrations in sync.

D1 is SQLite, and the worker wraps usage tracking in a broad
try/except, so a drifted column list would fail silently in
production and drop billing rows. Applying the real migration files
to SQLite and running the worker's INSERT column shape catches that
before deploy.
"""

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parents[2] / "keyservice" / "migrations"

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
