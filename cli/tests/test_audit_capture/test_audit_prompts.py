"""Tests for workload and fleet LLM prompts."""

from features.audit.prompts import build_audit_capture_prompt
from features.fleet.llm import build_fleet_insights_prompt


class TestWorkloadPrompt:
    def _make_run_data(self):
        return {
            "db_engine": "postgresql",
            "started_at": "2026-03-17T12:00:00Z",
            "ended_at": "2026-03-17T12:30:00Z",
            "duration_seconds": 1800,
            "source": "live",
            "total_queries": 5000,
            "total_query_time_ms": 150000.0,
            "snapshot_start": {
                "server_version": "PostgreSQL 15.4",
                "database_size_mb": 1024.0,
                "cache_hit_ratio": 99.2,
            },
            "snapshot_end": {
                "cache_hit_ratio": 98.5,
            },
            "delta_stats": {
                "xact_commit": 5000,
                "blks_read": 1200,
                "tup_returned": 50000,
            },
            "queries": [
                {
                    "query_hash": "abc123",
                    "normalized_query": "SELECT * FROM users WHERE id = ?",
                    "calls": 2000,
                    "total_time_ms": 60000.0,
                    "avg_time_ms": 30.0,
                    "pct_total_time": 40.0,
                },
                {
                    "query_hash": "def456",
                    "normalized_query": "SELECT count(*) FROM orders WHERE status = ?",
                    "calls": 1000,
                    "total_time_ms": 45000.0,
                    "avg_time_ms": 45.0,
                    "pct_total_time": 30.0,
                },
            ],
            "table_stats_end": [
                {"table_name": "users", "seq_scan": 500, "idx_scan": 10000, "n_live_tup": 100000},
                {"table_name": "orders", "seq_scan": 2000, "idx_scan": 500, "n_live_tup": 500000, "n_dead_tup": 5000},
            ],
            "intermediate_snapshots": [
                {"elapsed_seconds": 300, "cache_hit_ratio": 99.1, "active_connections": 5, "transactions_per_sec": 150, "lock_wait_count": 0},
                {"elapsed_seconds": 600, "cache_hit_ratio": 97.5, "active_connections": 15, "transactions_per_sec": 250, "lock_wait_count": 2},
            ],
        }

    def test_prompt_has_all_sections(self):
        prompt = build_audit_capture_prompt(self._make_run_data())
        assert "WORKLOAD SUMMARY" in prompt
        assert "DATABASE STATS DELTA" in prompt
        assert "TOP" in prompt
        assert "TIME SERIES" in prompt
        assert "abc123" in prompt[:8] or "abc123" in prompt

    def test_prompt_includes_queries(self):
        prompt = build_audit_capture_prompt(self._make_run_data())
        assert "SELECT * FROM users" in prompt
        assert "SELECT count(*) FROM orders" in prompt

    def test_prompt_token_budget(self):
        prompt = build_audit_capture_prompt(self._make_run_data())
        # Rough token estimate: ~4 chars per token
        estimated_tokens = len(prompt) / 4
        assert estimated_tokens < 8000, f"Prompt too large: ~{estimated_tokens:.0f} tokens"

    def test_empty_data(self):
        prompt = build_audit_capture_prompt({
            "db_engine": "mysql",
            "started_at": "2026-03-17T12:00:00Z",
            "ended_at": "2026-03-17T12:00:00Z",
            "duration_seconds": 0,
            "source": "snapshot",
            "total_queries": 0,
            "total_query_time_ms": 0,
            "queries": [],
        })
        assert "no queries captured" in prompt
        assert "mysql" in prompt


class TestFleetInsightsPrompt:
    def test_basic_prompt(self):
        results = [
            {
                "target_name": "prod-db",
                "engine": "postgresql",
                "instance_class": "db.r6g.xlarge",
                "metrics": {
                    "active_connections": 50,
                    "max_connections": 200,
                    "connection_utilization_pct": 25.0,
                    "cache_hit_rate": 99.5,
                    "read_pct": 90.0,
                    "write_pct": 10.0,
                    "database_size_mb": 1024,
                    "server_version": "PostgreSQL 15.4",
                },
                "sizing": {"verdict": "right_sized"},
                "cache_opportunity": {"score": 70, "level": "high"},
            },
        ]
        prompt = build_fleet_insights_prompt(results)
        assert "prod-db" in prompt
        assert "postgresql" in prompt
        assert "right_sized" in prompt

    def test_error_result(self):
        results = [
            {"target_name": "broken-db", "error": "Connection refused"},
        ]
        prompt = build_fleet_insights_prompt(results)
        assert "broken-db" in prompt
        assert "ERROR" in prompt
