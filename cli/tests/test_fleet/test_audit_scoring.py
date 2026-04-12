"""Tests for audit scoring logic."""

from features.audit.models import AuditMetrics
from features.audit.scoring import compute_cache_opportunity, compute_sizing_verdict
from features.fleet.models import SizingVerdict
from features.fleet.pricing import monthly_cost, estimate_class_from_shared_buffers, suggest_downsize


class TestSizingVerdict:
    def test_under_provisioned_high_connections(self):
        metrics = AuditMetrics(connection_utilization_pct=85.0, cache_hit_rate=99.5)
        result = compute_sizing_verdict(metrics)
        assert result.verdict == SizingVerdict.UNDER_PROVISIONED

    def test_under_provisioned_low_cache(self):
        metrics = AuditMetrics(connection_utilization_pct=50.0, cache_hit_rate=90.0)
        result = compute_sizing_verdict(metrics)
        assert result.verdict == SizingVerdict.UNDER_PROVISIONED

    def test_oversized(self):
        metrics = AuditMetrics(connection_utilization_pct=5.0, cache_hit_rate=99.8)
        result = compute_sizing_verdict(metrics)
        assert result.verdict == SizingVerdict.OVERSIZED

    def test_right_sized(self):
        metrics = AuditMetrics(connection_utilization_pct=50.0, cache_hit_rate=99.5)
        result = compute_sizing_verdict(metrics)
        assert result.verdict == SizingVerdict.RIGHT_SIZED

    def test_right_sized_boundary_connections(self):
        metrics = AuditMetrics(connection_utilization_pct=80.0, cache_hit_rate=99.5)
        result = compute_sizing_verdict(metrics)
        assert result.verdict == SizingVerdict.RIGHT_SIZED

    def test_right_sized_boundary_cache(self):
        metrics = AuditMetrics(connection_utilization_pct=50.0, cache_hit_rate=95.0)
        result = compute_sizing_verdict(metrics)
        assert result.verdict == SizingVerdict.RIGHT_SIZED

    def test_oversized_boundary(self):
        # conn < 20 AND cache > 99
        metrics = AuditMetrics(connection_utilization_pct=19.9, cache_hit_rate=99.1)
        result = compute_sizing_verdict(metrics)
        assert result.verdict == SizingVerdict.OVERSIZED

    def test_not_oversized_at_20_pct(self):
        metrics = AuditMetrics(connection_utilization_pct=20.0, cache_hit_rate=99.5)
        result = compute_sizing_verdict(metrics)
        assert result.verdict == SizingVerdict.RIGHT_SIZED

    def test_zero_cache_hit_not_under_provisioned(self):
        # cache_hit_rate=0 means we couldn't collect it, not that it's bad
        metrics = AuditMetrics(connection_utilization_pct=50.0, cache_hit_rate=0.0)
        result = compute_sizing_verdict(metrics)
        assert result.verdict == SizingVerdict.RIGHT_SIZED

    def test_with_instance_class_pricing(self):
        metrics = AuditMetrics(connection_utilization_pct=5.0, cache_hit_rate=99.8)
        result = compute_sizing_verdict(metrics, instance_class="db.r6g.xlarge")
        assert result.verdict == SizingVerdict.OVERSIZED
        assert result.current_monthly_cost_usd is not None
        assert result.current_monthly_cost_usd > 0


class TestCacheOpportunity:
    def test_high_read_pct(self):
        metrics = AuditMetrics(read_pct=95.0, cache_hit_rate=98.0, tracked_query_count=50)
        result = compute_cache_opportunity(metrics)
        assert result.score >= 70
        assert result.level == "high"

    def test_low_opportunity(self):
        metrics = AuditMetrics(read_pct=30.0, cache_hit_rate=99.9, tracked_query_count=0)
        result = compute_cache_opportunity(metrics)
        assert result.score < 40
        assert result.level == "low"

    def test_replica_bonus(self):
        m1 = AuditMetrics(read_pct=75.0, cache_hit_rate=98.5, is_replica=False)
        m2 = AuditMetrics(read_pct=75.0, cache_hit_rate=98.5, is_replica=True)
        r1 = compute_cache_opportunity(m1)
        r2 = compute_cache_opportunity(m2)
        assert r2.score > r1.score

    def test_score_capped_at_100(self):
        metrics = AuditMetrics(
            read_pct=95.0, cache_hit_rate=90.0, tracked_query_count=100, is_replica=True
        )
        result = compute_cache_opportunity(metrics)
        assert result.score <= 100

    def test_medium_opportunity(self):
        metrics = AuditMetrics(read_pct=80.0, cache_hit_rate=99.5, tracked_query_count=10)
        result = compute_cache_opportunity(metrics)
        assert 40 <= result.score < 70
        assert result.level == "medium"


class TestPricing:
    def test_known_instance(self):
        cost = monthly_cost("db.r6g.xlarge")
        assert cost is not None
        assert cost > 300  # ~$317/month

    def test_unknown_instance(self):
        cost = monthly_cost("db.zz99.mega")
        assert cost is None

    def test_estimate_from_shared_buffers(self):
        # 4GB shared_buffers -> ~16GB RAM -> should match a 16GB instance
        cls = estimate_class_from_shared_buffers(4096)
        assert cls is not None

    def test_suggest_downsize(self):
        suggestion = suggest_downsize("db.r6g.xlarge")
        assert suggestion is not None
        assert suggestion["instance_class"] == "db.r6g.large"
        assert suggestion["monthly_cost"] < monthly_cost("db.r6g.xlarge")

    def test_no_downsize_for_smallest(self):
        suggestion = suggest_downsize("db.t3.micro")
        assert suggestion is None
