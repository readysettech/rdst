"""Unit tests for the current unified audit/fleet HTML report renderer."""

from features.audit.report.report import compute_fleet_savings, render_report_html


def _mock_audit_result(**overrides):
    """Create a representative audit result dict."""
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
                "query_text": (
                    "SELECT p.* FROM products p "
                    "JOIN categories c ON p.category_id = c.id"
                ),
                "calls": 8700,
                "avg_time_ms": 3.4,
                "pct_total_time": 14.1,
                "total_time_ms": 29580.0,
            },
        ],
    }
    base["workload"] = {"queries": list(base["top_queries"])}
    base.update(overrides)
    return base


def _mock_rs_results():
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


def _mock_fleet_insights():
    return {
        "health_score": 72,
        "health_label": "Good",
        "executive_summary": "Fleet is healthy.",
        "fleet_findings": [
            {"severity": "ok", "title": "Healthy fleet", "body": "Stable load"}
        ],
        "next_steps": [
            {
                "rank": 1,
                "title": "Deploy Readyset",
                "body": "Validate caching in staging.",
            }
        ],
    }


class TestComputeFleetSavings:
    def test_basic_savings(self):
        savings = compute_fleet_savings([_mock_audit_result()])
        assert savings["total_current_usd"] == 397.0
        assert savings["rightsize_usd"] == 198.0
        assert savings["cache_infra_usd"] == 198.5
        assert savings["total_savings_usd"] == 0.0

    def test_replica_elimination(self):
        primary = _mock_audit_result()
        replica = _mock_audit_result(
            target_name="replica",
            metrics={**primary["metrics"], "is_replica": True},
        )
        savings = compute_fleet_savings([primary, replica])
        assert savings["replica_eliminate_usd"] == 397.0
        assert savings["total_savings_usd"] == 396.5

    def test_missing_pricing_returns_empty(self):
        result = _mock_audit_result(sizing={})
        assert compute_fleet_savings([result]) == {}


class TestRenderSingleTargetHtml:
    def test_valid_html(self):
        html = render_report_html([_mock_audit_result()])
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_has_core_sections(self):
        html = render_report_html([_mock_audit_result()])
        assert "Database Overview" in html
        assert "Detailed Analysis" in html
        assert "Sizing &amp; Savings" in html

    def test_readyset_data_in_queries_table(self):
        result = _mock_audit_result(readyset_results=_mock_rs_results())
        html = render_report_html([result])
        assert "Captured Queries &amp; Performance" in html
        assert "Readyset" in html
        assert "15.0x" in html or "15x" in html

    def test_readyset_section_absent_when_no_results(self):
        html = render_report_html([_mock_audit_result()])
        assert "Readyset Performance" not in html

    def test_cover_has_kpis(self):
        html = render_report_html([_mock_audit_result()])
        assert "OVERSIZED" in html
        assert "$397" in html
        assert "test-db" in html

    def test_queries_in_table(self):
        html = render_report_html([_mock_audit_result()])
        assert "a3f2c1b8d9e1" in html
        assert "12,400" in html

    def test_footer_present(self):
        html = render_report_html([_mock_audit_result()])
        assert "RDST" in html
        assert "Generated" in html

    def test_html_escapes_special_chars(self):
        result = _mock_audit_result(target_name="test<script>alert(1)</script>")
        html = render_report_html([result])
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_empty_metrics_render(self):
        result = _mock_audit_result(
            metrics={}, sizing={}, cache_opportunity={}, top_queries=[]
        )
        html = render_report_html([result])
        assert "test-db" in html
        assert "No results" not in html

    def test_none_fields_render(self):
        result = _mock_audit_result(
            metrics=None, sizing=None, cache_opportunity=None, top_queries=None
        )
        assert "</html>" in render_report_html([result])

    def test_workload_recommendations_render(self):
        result = _mock_audit_result(
            workload={
                "analysis": {
                    "index_recommendations": [
                        {
                            "table": "orders",
                            "reason": "Frequently filtered",
                            "sql": "CREATE INDEX idx_orders_user ON orders (user_id);",
                        }
                    ],
                    "optimization_priorities": [
                        {"action": "Deploy Readyset", "details": "Test in staging"}
                    ],
                }
            }
        )
        html = render_report_html([result])
        assert "Index Recommendations" in html
        assert "Deploy Readyset" in html


class TestRenderFleetHtml:
    def test_valid_html(self):
        html = render_report_html(
            [_mock_audit_result(), _mock_audit_result(target_name="db-2")],
            title="test-cluster",
        )
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_fleet_overview_section(self):
        results = [_mock_audit_result(), _mock_audit_result(target_name="db-2")]
        html = render_report_html(results, title="test")
        assert ">Overview<" in html
        assert "test-db" in html
        assert "db-2" in html

    def test_fleet_with_failed_target(self):
        results = [
            _mock_audit_result(),
            {
                "target_name": "broken",
                "engine": "mysql",
                "host": "x",
                "error": "Auth failed",
            },
        ]
        html = render_report_html(results)
        assert "broken" in html
        assert "Mysql" in html

    def test_fleet_insights_render(self):
        results = [_mock_audit_result(), _mock_audit_result(target_name="db-2")]
        html = render_report_html(results, fleet_insights=_mock_fleet_insights())
        assert "Fleet is healthy." in html
        assert "Healthy fleet" in html
        assert "Deploy Readyset" in html

    def test_empty_results_render(self):
        html = render_report_html([])
        assert html.startswith("<!DOCTYPE html>")
        assert "No results" in html
