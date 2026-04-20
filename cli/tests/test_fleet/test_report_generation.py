"""Unit tests for HTML report generation.

Tests that report_data factories and html_report renderers produce correct
output from mock audit data. No database or network required.
"""

import pytest


# ============================================================================
# Mock Data Factories
# ============================================================================


def _mock_audit_result(**overrides):
    """Create a mock audit result dict matching AuditService output."""
    base = {
        "target_name": "test-db",
        "engine": "postgresql",
        "host": "test-db.abc123.us-east-1.rds.amazonaws.com",
        "region": "us-east-1",
        "instance_class": "db.r6g.xlarge",
        "group": "test-cluster",
        "metrics": {
            "server_version": "PostgreSQL 15.4",
            "database_size_mb": 42300.0,
            "active_connections": 128,
            "max_connections": 500,
            "connection_utilization_pct": 25.6,
            "cache_hit_rate": 99.82,
            "shared_buffers_mb": 8192,
            "read_pct": 87.0,
            "write_pct": 13.0,
            "tracked_query_count": 1247,
            "is_replica": False,
            "uptime_seconds": 86400.0,
        },
        "sizing": {
            "verdict": "oversized",
            "explanation": "Low connection utilization with high cache hit rate.",
            "current_monthly_cost_usd": 397.0,
            "suggested_instance_class": "db.r6g.large",
            "suggested_monthly_cost_usd": 199.0,
            "potential_savings_usd": 198.0,
        },
        "cache_opportunity": {
            "score": 82,
            "level": "high",
            "explanation": "High read percentage with many cacheable queries.",
        },
        "top_queries": [
            {
                "query_hash": "a3f2c1b8d9e1",
                "query_text": "SELECT u.id, u.name FROM users u WHERE u.active = true",
                "calls": 12400,
                "avg_time_ms": 1.2,
                "pct_total_time": 18.3,
                "total_time_ms": 14880.0,
            },
            {
                "query_hash": "b7d9e2a1f3c4",
                "query_text": "SELECT p.* FROM products p JOIN categories c ON p.category_id = c.id",
                "calls": 8700,
                "avg_time_ms": 3.4,
                "pct_total_time": 14.1,
                "total_time_ms": 29580.0,
            },
        ],
    }
    base.update(overrides)
    return base


def _mock_rs_results():
    """Mock ReadySet test results."""
    return [
        {
            "query_hash": "a3f2c1b8d9e1",
            "query_text": "SELECT u.id...",
            "cacheable": True,
            "baseline_ms": 1.2,
            "readyset_ms": 0.08,
            "speedup": 15.0,
        },
        {
            "query_hash": "b7d9e2a1f3c4",
            "query_text": "SELECT p.*...",
            "cacheable": False,
            "not_cacheable_reason": "Contains NOW() function",
        },
    ]


def _mock_insights():
    """Mock LLM insights data."""
    return {
        "health_score": 72,
        "readyset_verdict": "strong_candidate",
        "readyset_summary": "Strong caching candidate.",
        "estimated_cacheable_pct": 80,
        "index_recommendations": [
            {
                "table": "orders",
                "columns": "user_id, created_at",
                "reason": "Frequently filtered",
                "impact": "High",
                "sql": "CREATE INDEX idx_orders_user ON orders (user_id, created_at);",
            },
        ],
        "optimization_priorities": [
            {
                "priority": 1,
                "action": "Deploy ReadySet",
                "category": "Caching",
                "effort": "low",
                "impact": "high",
            },
        ],
        "key_findings": ["87% read traffic", "4 of 5 queries cacheable"],
    }


# ============================================================================
# Report Data Tests
# ============================================================================


class TestBuildSingleReport:
    def test_basic_build(self):
        from features.audit.report.report_data import build_single_report
        report = build_single_report(_mock_audit_result())
        assert report.metadata.target_name == "test-db"
        assert report.metadata.engine == "postgresql"
        assert report.overview.server_version == "PostgreSQL 15.4"
        assert report.health.sizing_verdict == "oversized"
        assert report.cache_opportunity.score == 82
        assert len(report.top_queries) == 2

    def test_with_readyset_results(self):
        from features.audit.report.report_data import build_single_report
        report = build_single_report(_mock_audit_result(), rs_results=_mock_rs_results())
        assert len(report.readyset_results) == 2
        assert report.readyset_results[0].cacheable is True
        assert report.readyset_results[0].speedup == 15.0
        assert report.readyset_results[1].cacheable is False
        assert report.cache_opportunity.cacheable_query_count == 1

    def test_with_insights(self):
        from features.audit.report.report_data import build_single_report
        report = build_single_report(_mock_audit_result(), insights=_mock_insights())
        assert report.insights is not None
        assert report.insights.readyset_verdict == "strong_candidate"
        assert len(report.insights.index_recommendations) == 1
        assert len(report.insights.optimization_priorities) == 1

    def test_empty_metrics(self):
        from features.audit.report.report_data import build_single_report
        result = _mock_audit_result(metrics={}, sizing={}, cache_opportunity={}, top_queries=[])
        report = build_single_report(result)
        assert report.overview.server_version == ""
        assert report.health.sizing_verdict == "unknown"
        assert report.cache_opportunity.score == 0
        assert len(report.top_queries) == 0

    def test_none_fields(self):
        from features.audit.report.report_data import build_single_report
        result = _mock_audit_result(metrics=None, sizing=None, cache_opportunity=None)
        report = build_single_report(result)
        assert report.overview.database_size_mb == 0.0


class TestBuildFleetReport:
    def test_basic_fleet(self):
        from features.audit.report.report_data import build_fleet_report
        results = [_mock_audit_result(), _mock_audit_result(target_name="test-db-2")]
        report = build_fleet_report(results, group_name="test-cluster")
        assert report.metadata.target_name == "test-cluster"
        assert report.metadata.report_type == "fleet"
        assert report.total_targets == 2
        assert report.targets_succeeded == 2
        assert report.targets_failed == 0
        assert len(report.targets) == 2

    def test_fleet_with_failures(self):
        from features.audit.report.report_data import build_fleet_report
        results = [
            _mock_audit_result(),
            {"target_name": "failed-db", "engine": "mysql", "host": "x", "error": "Connection refused"},
        ]
        report = build_fleet_report(results)
        assert report.targets_succeeded == 1
        assert report.targets_failed == 1
        assert report.targets[1].error == "Connection refused"

    def test_fleet_with_insights(self):
        from features.audit.report.report_data import build_fleet_report
        fleet_insights = {
            "fleet_health_summary": "Fleet is healthy.",
            "fleet_readyset_summary": "Good caching opportunity.",
            "immediate_actions": ["Deploy ReadySet"],
            "estimated_monthly_savings_usd": 450,
            "per_target": [
                {"target_name": "test-db", "readyset_verdict": "strong_candidate", "readyset_summary": "Great.", "estimated_cacheable_pct": 80, "key_findings": ["High read"]},
            ],
        }
        report = build_fleet_report([_mock_audit_result()], fleet_insights=fleet_insights)
        assert report.fleet_health_summary == "Fleet is healthy."
        assert report.estimated_monthly_savings_usd == 450
        assert report.targets[0].readyset_verdict == "strong_candidate"


# ============================================================================
# HTML Render Tests
# ============================================================================


class TestRenderSingleTargetHtml:
    def test_valid_html(self):
        from features.audit.report.report_data import build_single_report
        from features.audit.report.html_report import render_single_target_html
        report = build_single_report(_mock_audit_result())
        html = render_single_target_html(report)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_has_all_sections(self):
        from features.audit.report.report_data import build_single_report
        from features.audit.report.html_report import render_single_target_html
        report = build_single_report(_mock_audit_result(), insights=_mock_insights())
        html = render_single_target_html(report)
        assert "Database Overview" in html
        assert "Health" in html
        assert "Top Queries" in html or "Captured Queries" in html
        assert "Index Recommendations" in html
        assert "Optimization Priorities" in html
        assert "Next Steps" in html

    def test_readyset_data_in_queries_table(self):
        from features.audit.report.report_data import build_single_report
        from features.audit.report.html_report import render_single_target_html
        report = build_single_report(_mock_audit_result(), rs_results=_mock_rs_results())
        html = render_single_target_html(report)
        assert "Captured Queries" in html
        assert "Readyset" in html
        assert "Speedup" in html
        assert "15.0x" in html or "15x" in html

    def test_readyset_section_absent_when_no_results(self):
        from features.audit.report.report_data import build_single_report
        from features.audit.report.html_report import render_single_target_html
        report = build_single_report(_mock_audit_result())
        html = render_single_target_html(report)
        assert "ReadySet Performance" not in html

    def test_cover_has_kpis(self):
        from features.audit.report.report_data import build_single_report
        from features.audit.report.html_report import render_single_target_html
        report = build_single_report(_mock_audit_result())
        html = render_single_target_html(report)
        assert "OVERSIZED" in html  # sizing verdict
        assert "$397" in html  # cost in cover
        assert "test-db" in html  # target name

    def test_queries_in_table(self):
        from features.audit.report.report_data import build_single_report
        from features.audit.report.html_report import render_single_target_html
        report = build_single_report(_mock_audit_result())
        html = render_single_target_html(report)
        assert "a3f2c1b8d9e1" in html
        assert "12,400" in html  # calls formatted with commas

    def test_footer_present(self):
        from features.audit.report.report_data import build_single_report
        from features.audit.report.html_report import render_single_target_html
        report = build_single_report(_mock_audit_result())
        html = render_single_target_html(report)
        assert "RDST" in html
        assert "Readyset Diagnostics" in html

    def test_html_escapes_special_chars(self):
        from features.audit.report.report_data import build_single_report
        from features.audit.report.html_report import render_single_target_html
        result = _mock_audit_result(target_name="test<script>alert(1)</script>")
        report = build_single_report(result)
        html = render_single_target_html(report)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestRenderFleetHtml:
    def test_valid_html(self):
        from features.audit.report.report_data import build_fleet_report
        from features.audit.report.html_report import render_fleet_html
        report = build_fleet_report([_mock_audit_result()], group_name="test")
        html = render_fleet_html(report)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_fleet_overview_section(self):
        from features.audit.report.report_data import build_fleet_report
        from features.audit.report.html_report import render_fleet_html
        results = [_mock_audit_result(), _mock_audit_result(target_name="db-2")]
        report = build_fleet_report(results, group_name="test")
        html = render_fleet_html(report)
        assert "Fleet Overview" in html
        assert "test-db" in html
        assert "db-2" in html

    def test_fleet_with_failed_target(self):
        from features.audit.report.report_data import build_fleet_report
        from features.audit.report.html_report import render_fleet_html
        results = [
            _mock_audit_result(),
            {"target_name": "broken", "engine": "mysql", "host": "x", "error": "Auth failed"},
        ]
        report = build_fleet_report(results)
        html = render_fleet_html(report)
        assert "Auth failed" in html
        assert "FAILED" in html
