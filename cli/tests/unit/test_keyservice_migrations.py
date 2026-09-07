"""Keep the keyservice usage_log INSERT and its migrations in sync.

D1 is SQLite, and the worker wraps usage tracking in a broad
try/except, so a drifted column list would fail silently in
production and drop billing rows. Applying the real migration files
to SQLite and running the worker's INSERT column shape catches that
before deploy.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
MIGRATIONS_DIR = Path(__file__).parents[2] / "keyservice" / "migrations"
WRANGLER_CONFIG = MIGRATIONS_DIR.parent / "wrangler.toml"

# The public GitHub mirror publishes the CLI without the keyservice; there is
# nothing for this drift guard to check in such a checkout. This has to come
# before the keyservice imports below, which cannot resolve there either.
if not MIGRATIONS_DIR.is_dir():
    pytest.skip(
        "keyservice sources are not part of this checkout",
        allow_module_level=True,
    )

sys.path.insert(0, str(MIGRATIONS_DIR.parent / "src"))
import hosted_admin_dashboard  # noqa: E402
import hosted_inference  # noqa: E402

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


def test_inference_attribution_joins_usage_without_changing_existing_tables():
    db = sqlite3.connect(":memory:")
    for path in sorted(MIGRATIONS_DIR.glob("0*.sql")):
        db.executescript(path.read_text(encoding="utf-8"))

    columns = {
        row[1]
        for row in db.execute(
            "PRAGMA table_info(inference_request_attribution)"
        ).fetchall()
    }
    assert {
        "request_id",
        "workflow_id",
        "feature",
        "operation",
        "provider_cost_usd",
        "analytics_status",
    } <= columns

    db.execute(
        "INSERT INTO inference_accounts "
        "(user_id, limit_microusd, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("user-1", 250_000, 1, 1),
    )
    db.execute(
        "INSERT INTO inference_reservations "
        "(request_id, user_id, period_start, reserved_microusd, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("request-1", "user-1", "2026-09-01", 50_000, 1, 2),
    )
    db.execute(
        "INSERT INTO inference_request_attribution "
        "(request_id, workflow_id, feature, operation, surface, client_version, "
        "installation_id, prompt_version, reasoning_effort, max_tokens_requested, "
        "analytics_disabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "request-1",
            "workflow-1",
            "ask",
            "sql_generation",
            "cli",
            "0.1.0",
            "",
            "",
            "high",
            800,
            0,
            1,
        ),
    )
    row = db.execute(
        "SELECT feature, operation, max_tokens_requested "
        "FROM inference_request_attribution WHERE request_id = ?",
        ("request-1",),
    ).fetchone()
    assert row == ("ask", "sql_generation", 800)


def test_inference_attribution_migration_backfills_existing_reservations():
    db = sqlite3.connect(":memory:")
    for path in sorted(MIGRATIONS_DIR.glob("000[1-6]_*.sql")):
        db.executescript(path.read_text(encoding="utf-8"))

    db.execute(
        "INSERT INTO inference_accounts "
        "(user_id, limit_microusd, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("user-1", 250_000, 1, 1),
    )
    db.execute(
        "INSERT INTO inference_reservations "
        "(request_id, user_id, period_start, reserved_microusd, actual_microusd, "
        "status, created_at, expires_at, settled_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "old-request",
            "user-1",
            "2026-09-01",
            50_000,
            12_500,
            "settled",
            1,
            2,
            3,
        ),
    )
    db.execute(
        "INSERT INTO inference_usage "
        "(request_id, user_id, model, provider, prompt_tokens, completion_tokens, "
        "reasoning_tokens, cost_microusd, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "old-request",
            "user-1",
            hosted_inference.MODEL,
            "deepinfra",
            100,
            30,
            20,
            12_500,
            3,
        ),
    )

    db.executescript(
        (MIGRATIONS_DIR / "0008_inference_attribution.sql").read_text(
            encoding="utf-8"
        )
    )

    row = db.execute(
        "SELECT feature, operation, analytics_disabled, outcome, completed_at, "
        "analytics_status FROM inference_request_attribution"
    ).fetchone()
    assert row == ("other", "other", 1, "success", 3, "disabled")

    db.row_factory = sqlite3.Row
    report = db.execute(
        hosted_inference.USER_USAGE_BREAKDOWN_SQL,
        (hosted_inference.MODEL, "user-1", "2026-09-01", hosted_inference.MODEL),
    ).fetchone()
    assert report["feature"] == "other"
    assert report["operation"] == "other"
    assert report["requests"] == 1
    assert report["quota_charge_microusd"] == 12_500


def test_cost_report_keeps_failed_requests_without_usage_rows():
    db = sqlite3.connect(":memory:")
    for path in sorted(MIGRATIONS_DIR.glob("0*.sql")):
        db.executescript(path.read_text(encoding="utf-8"))

    db.execute(
        "INSERT INTO inference_accounts "
        "(user_id, limit_microusd, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("user-1", 250_000, 1, 1),
    )
    db.execute(
        "INSERT INTO inference_reservations "
        "(request_id, user_id, period_start, reserved_microusd, actual_microusd, "
        "status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("request-failed", "user-1", "2026-09-01", 50_000, 50_000,
         "settled", 1, 2),
    )
    db.execute(
        "INSERT INTO inference_request_attribution "
        "(request_id, workflow_id, feature, operation, surface, reasoning_effort, "
        "max_tokens_requested, analytics_disabled, outcome, http_status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("request-failed", "workflow-1", "ask", "sql_generation", "cli", "high",
         800, 0, "error", 502, 1),
    )

    row = db.execute(
        "SELECT COUNT(*) AS requests, "
        "SUM(CASE WHEN a.outcome = 'error' THEN 1 ELSE 0 END) AS failed_requests, "
        "SUM(CASE WHEN r.status = 'settled' "
        "THEN COALESCE(r.actual_microusd, r.reserved_microusd) ELSE 0 END) AS charge, "
        "SUM(CASE WHEN a.provider_cost_usd IS NULL THEN 1 ELSE 0 END) AS unknown_cost "
        "FROM inference_request_attribution AS a "
        "JOIN inference_reservations AS r USING (request_id) "
        "LEFT JOIN inference_usage AS u USING (request_id)"
    ).fetchone()
    assert row == (1, 1, 50_000, 1)


def test_inference_rejections_do_not_require_fake_reservations():
    db = sqlite3.connect(":memory:")
    for path in sorted(MIGRATIONS_DIR.glob("0*.sql")):
        db.executescript(path.read_text(encoding="utf-8"))

    db.execute(
        "INSERT INTO inference_accounts "
        "(user_id, email, limit_microusd, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("user-1", "person@example.com", 250_000, 1, 1),
    )
    db.execute(
        "INSERT INTO inference_rejections "
        "(rejection_id, request_id, user_id, period_start, reason, workflow_id, "
        "feature, operation, surface, limit_microusd, committed_microusd, "
        "held_microusd, remaining_microusd, analytics_disabled, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "rejection-1",
            "request-1",
            "user-1",
            "2026-09-01",
            "user_monthly_cap",
            "workflow-1",
            "ask",
            "sql_generation",
            "desktop",
            250_000,
            225_000,
            0,
            25_000,
            0,
            1,
        ),
    )

    row = db.execute(
        "SELECT reason, feature, operation, remaining_microusd "
        "FROM inference_rejections"
    ).fetchone()
    assert row == ("user_monthly_cap", "ask", "sql_generation", 25_000)
    assert db.execute("SELECT COUNT(*) FROM inference_reservations").fetchone() == (0,)


def test_user_usage_breakdown_reconciles_settled_charges_and_active_holds():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    for path in sorted(MIGRATIONS_DIR.glob("0*.sql")):
        db.executescript(path.read_text(encoding="utf-8"))

    db.execute(
        "INSERT INTO inference_accounts "
        "(user_id, limit_microusd, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("user-1", 250_000, 1, 1),
    )
    for request_id, status, reserved, actual in (
        ("settled", "settled", 50_000, 12_500),
        ("held", "unknown", 50_000, None),
    ):
        db.execute(
            "INSERT INTO inference_reservations "
            "(request_id, user_id, period_start, reserved_microusd, "
            "actual_microusd, status, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, "user-1", "2026-09-01", reserved, actual, status, 1, 2),
        )
        db.execute(
            "INSERT INTO inference_request_attribution "
            "(request_id, workflow_id, feature, operation, surface, "
            "max_tokens_requested, analytics_disabled, outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (request_id, request_id, "ask", "sql_generation", "cli", 800, 0,
             "success" if status == "settled" else "error", 1),
        )

    row = db.execute(
        hosted_inference.USER_USAGE_BREAKDOWN_SQL,
        (hosted_inference.MODEL, "user-1", "2026-09-01", hosted_inference.MODEL),
    ).fetchone()
    assert dict(row) == {
        "feature": "ask",
        "operation": "sql_generation",
        "model": hosted_inference.MODEL,
        "requests": 2,
        "successes": 1,
        "failures": 1,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "quota_charge_microusd": 62_500,
        "held_microusd": 50_000,
        "known_provider_cost_usd": None,
        "unknown_provider_cost_requests": 2,
    }


def test_admin_account_overview_reports_hosted_usage_caps_and_rejections():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    for path in sorted(MIGRATIONS_DIR.glob("0*.sql")):
        db.executescript(path.read_text(encoding="utf-8"))

    db.execute(
        "INSERT INTO inference_accounts "
        "(user_id, email, limit_microusd, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("user-1", "person@example.com", 250_000, 1, 2),
    )
    db.execute(
        "INSERT INTO inference_reservations "
        "(request_id, user_id, period_start, reserved_microusd, actual_microusd, "
        "status, created_at, expires_at, settled_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("request-1", "user-1", "2026-09-01", 50_000, 12_500,
         "settled", 10, 20, 15),
    )
    db.execute(
        "INSERT INTO inference_request_attribution "
        "(request_id, workflow_id, feature, operation, surface, "
        "max_tokens_requested, analytics_disabled, outcome, provider_cost_usd, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("request-1", "workflow-1", "ask", "sql_generation", "desktop",
         800, 0, "success", 0.0125, 10),
    )
    db.execute(
        "INSERT INTO inference_usage "
        "(request_id, user_id, model, provider, prompt_tokens, completion_tokens, "
        "reasoning_tokens, cost_microusd, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("request-1", "user-1", hosted_inference.MODEL,
         "deepinfra", 100, 30, 20, 12_500, 15),
    )
    db.execute(
        "INSERT INTO inference_rejections "
        "(rejection_id, request_id, user_id, period_start, reason, workflow_id, "
        "feature, operation, surface, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("rejection-1", "request-2", "user-1", "2026-09-01",
         "user_monthly_cap", "workflow-2", "ask", "clarification", "desktop", 16),
    )

    row = db.execute(
        hosted_inference.ADMIN_ACCOUNT_OVERVIEW_SQL,
        ("2026-09-01", "2026-09-01", "2026-09-01"),
    ).fetchone()
    assert row["email"] == "person@example.com"
    assert row["committed_microusd"] == 12_500
    assert row["held_microusd"] == 0
    assert row["requests"] == 1
    assert row["successes"] == 1
    assert row["prompt_tokens"] == 100
    assert row["reasoning_tokens"] == 20
    assert row["known_provider_cost_usd"] == 0.0125
    assert row["user_cap_rejections"] == 1


def test_hosted_admin_dashboard_has_no_legacy_trial_controls():
    document = hosted_admin_dashboard.render()
    assert "RDST Hosted AI" in document
    assert "Provider spend" in document
    assert "Spend by feature and operation" in document
    assert "PostHog account" in document
    assert "Trial Users" not in document
    assert "Max Trial Users" not in document


def test_worker_passes_execution_context_for_background_analytics():
    source_dir = MIGRATIONS_DIR.parent / "src"
    index_source = (source_dir / "index.py").read_text(encoding="utf-8")
    bridge_source = (source_dir / "asgi_bridge.py").read_text(encoding="utf-8")

    assert "execution_ctx.waitUntil(delivery)" in index_source
    assert "asgi.fetch(app, request.js_object, self.env, self.ctx)" in index_source
    assert '"ctx": ctx' in bridge_source
