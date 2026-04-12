"""Audit scoring — sizing verdict and cache opportunity calculations."""

from __future__ import annotations

from features.fleet.models import SizingVerdict

from .models import (
    AuditMetrics,
    CacheOpportunityScore,
    SizingAssessment,
)
from features.fleet.pricing import monthly_cost, suggest_downsize


def compute_sizing_verdict(
    metrics: AuditMetrics,
    instance_class: str | None = None,
) -> SizingAssessment:
    """Compute sizing verdict from collected metrics.

    Rules (per Gotham's design):
    - UNDER_PROVISIONED: connection_utilization > 80% OR cache_hit_rate < 95%
    - OVERSIZED: connection_utilization < 20% AND cache_hit_rate > 99%
    - RIGHT_SIZED: everything else
    """
    conn_util = metrics.connection_utilization_pct
    cache_hit = metrics.cache_hit_rate

    if conn_util > 80 or (cache_hit > 0 and cache_hit < 95):
        verdict = SizingVerdict.UNDER_PROVISIONED
        parts = []
        if conn_util > 80:
            parts.append(f"connection utilization high ({conn_util:.1f}%)")
        if cache_hit > 0 and cache_hit < 95:
            parts.append(f"cache hit rate low ({cache_hit:.1f}%)")
        explanation = "Under-provisioned: " + ", ".join(parts)
    elif conn_util < 20 and cache_hit > 99:
        verdict = SizingVerdict.OVERSIZED
        explanation = (
            f"Oversized: low connection utilization ({conn_util:.1f}%) "
            f"and high cache hit rate ({cache_hit:.1f}%)"
        )
    else:
        verdict = SizingVerdict.RIGHT_SIZED
        explanation = (
            f"Right-sized: connection utilization {conn_util:.1f}%, "
            f"cache hit rate {cache_hit:.1f}%"
        )

    assessment = SizingAssessment(verdict=verdict, explanation=explanation)

    # Add pricing if instance class is known
    if instance_class:
        try:
            current_cost = monthly_cost(instance_class)
            if current_cost is not None:
                assessment.current_monthly_cost_usd = current_cost

            if verdict == SizingVerdict.OVERSIZED:
                suggestion = suggest_downsize(instance_class)
                if suggestion:
                    assessment.suggested_instance_class = suggestion["instance_class"]
                    assessment.suggested_monthly_cost_usd = suggestion["monthly_cost"]
                    assessment.potential_savings_usd = round(
                        current_cost - suggestion["monthly_cost"], 2
                    ) if current_cost else None
        except Exception:
            pass

    return assessment


def compute_cache_opportunity(
    metrics: AuditMetrics,
) -> CacheOpportunityScore:
    """Compute ReadySet cache opportunity score (0-100).

    Factors:
    - +50 if read_pct > 90%, +30 if > 70%
    - +25 if cache_hit_pct < 99%
    - +15 if has tracked queries
    - +10 if is a replica
    """
    score = 0
    factors = {}

    # Read-heavy workload
    if metrics.read_pct > 90:
        score += 50
        factors["read_heavy"] = 50.0
    elif metrics.read_pct > 70:
        score += 30
        factors["moderately_read_heavy"] = 30.0

    # Low cache hit rate
    if 0 < metrics.cache_hit_rate < 99:
        score += 25
        factors["low_cache_hit"] = 25.0

    # Has tracked queries
    if metrics.tracked_query_count > 0:
        score += 15
        factors["has_tracked_queries"] = 15.0

    # Is a replica
    if metrics.is_replica:
        score += 10
        factors["is_replica"] = 10.0

    score = min(score, 100)

    if score >= 70:
        level = "high"
    elif score >= 40:
        level = "medium"
    else:
        level = "low"

    parts = []
    if metrics.read_pct > 0:
        parts.append(f"{metrics.read_pct:.0f}% reads")
    if metrics.cache_hit_rate > 0:
        parts.append(f"{metrics.cache_hit_rate:.1f}% cache hit")
    if metrics.tracked_query_count > 0:
        parts.append(f"{metrics.tracked_query_count} tracked queries")
    if metrics.is_replica:
        parts.append("replica")

    explanation = f"Score {score}/100 ({level})"
    if parts:
        explanation += ": " + ", ".join(parts)

    return CacheOpportunityScore(
        score=score,
        factors=factors,
        level=level,
        explanation=explanation,
    )
