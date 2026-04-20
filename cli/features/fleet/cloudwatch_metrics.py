"""CloudWatch metrics collection for AWS-discovered RDS targets.

Pulls CPU utilization and other key metrics when AWS credentials are available.
Gracefully returns None when AWS is not accessible.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


def collect_cloudwatch_cpu(
    instance_identifier: str,
    region: str = "us-east-1",
    hours: int = 168,  # 7 days default
    period_seconds: int = 3600,
) -> Optional[Dict[str, Any]]:
    """Pull CPU utilization from CloudWatch for an RDS instance.

    Args:
        instance_identifier: The RDS DB instance identifier (e.g., 'tanmay-rdst-test-reader-1')
        region: AWS region
        hours: How many hours of data to pull (default 24)
        period_seconds: Aggregation period (default 3600 = 1 hour)

    Returns:
        Dict with avg_cpu, max_cpu, min_cpu, datapoints list, or None if unavailable.
    """
    try:
        import boto3
        import os

        # Try sandbox profile first, then default credentials
        profile = os.environ.get("AWS_PROFILE", "sandbox")
        session = boto3.Session(profile_name=profile, region_name=region)
        cw = session.client("cloudwatch")

        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours)

        resp = cw.get_metric_statistics(
            Namespace="AWS/RDS",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "DBInstanceIdentifier", "Value": instance_identifier}],
            StartTime=start,
            EndTime=end,
            Period=period_seconds,
            Statistics=["Average", "Maximum", "Minimum"],
        )

        datapoints = resp.get("Datapoints", [])
        if not datapoints:
            return None

        sorted_dp = sorted(datapoints, key=lambda x: x["Timestamp"])
        avgs = [dp["Average"] for dp in sorted_dp]
        maxes = [dp["Maximum"] for dp in sorted_dp]

        return {
            "avg_cpu": round(sum(avgs) / len(avgs), 1),
            "max_cpu": round(max(maxes), 1),
            "min_cpu": round(min(dp["Minimum"] for dp in sorted_dp), 1),
            "hours": hours,
            "datapoints": [
                {
                    "timestamp": dp["Timestamp"].isoformat(),
                    "avg": round(dp["Average"], 1),
                    "max": round(dp["Maximum"], 1),
                }
                for dp in sorted_dp
            ],
        }
    except Exception:
        return None


def collect_rds_instance_info(
    instance_identifier: str,
    region: str = "us-east-1",
) -> Optional[Dict[str, Any]]:
    """Pull actual instance class and metadata from RDS API.

    Returns dict with instance_class, engine, engine_version, or None.
    """
    try:
        import boto3
        import os

        profile = os.environ.get("AWS_PROFILE", "sandbox")
        session = boto3.Session(profile_name=profile, region_name=region)
        rds = session.client("rds")

        resp = rds.describe_db_instances(DBInstanceIdentifier=instance_identifier)
        inst = resp["DBInstances"][0]

        return {
            "instance_class": inst["DBInstanceClass"],
            "engine": inst["Engine"],
            "engine_version": inst["EngineVersion"],
            "storage_type": inst.get("StorageType"),
            "allocated_storage_gb": inst.get("AllocatedStorage"),
            "multi_az": inst.get("MultiAZ", False),
            "performance_insights_enabled": inst.get("PerformanceInsightsEnabled", False),
        }
    except Exception:
        return None


def derive_rds_identifier(target_name: str, host: str) -> Optional[str]:
    """Try to derive the RDS DB instance identifier from host or target name.

    Aurora endpoints look like: tanmay-rdst-test.cluster-xxx.region.rds.amazonaws.com
    Instance endpoints look like: tanmay-rdst-test-reader-1.xxx.region.rds.amazonaws.com
    """
    if not host or "rds.amazonaws.com" not in host:
        return None

    # The identifier is the first part before the first dot
    # For cluster endpoints, this is the cluster name
    # For instance endpoints, this is the instance name
    identifier = host.split(".")[0]

    # For cluster endpoints (contain 'cluster-'), try the target name
    # since the cluster name might not match any instance
    if "cluster-" in host.split(".")[1] if "." in host else "":
        return None  # Can't determine instance from cluster endpoint

    return identifier
