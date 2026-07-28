"""AWS RDS instance discovery using botocore."""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from .auth import get_rds_client
from features.fleet.models import FleetMember

logger = logging.getLogger(__name__)

_ENGINE_MAP = {
    "postgres": "postgresql",
    "aurora-postgresql": "postgresql",
    "mysql": "mysql",
    "aurora-mysql": "mysql",
    "aurora": "mysql",
    "mariadb": "mysql",
}

_SKIP_ENGINES = {
    "oracle-ee",
    "oracle-se2",
    "sqlserver-ee",
    "sqlserver-se",
    "sqlserver-ex",
    "sqlserver-web",
}

_VALID_STATUSES = {"available", "backing-up", "modifying", "storage-optimization"}


def discover_rds_instances(
    regions: list[str],
    engine_filter: str | None = None,
    name_pattern: str | None = None,
    password_env: str = "FLEET_PASS",
    default_user: str | None = None,
    default_group: str | None = None,
    default_database: str | None = None,
    errors: list[str] | None = None,
    profile: str | None = None,
):
    """Discover RDS instances across regions using botocore.

    A failed region does not stop discovery in the remaining regions. When
    `errors` is provided, a human-readable message per failed region is
    appended so callers can distinguish "region is empty" from "region
    could not be listed".
    """
    # Stamp members with the account they came from so later flows (audit
    # preflight, credential checks) can detect a signed-in-account mismatch.
    account_tag = None
    try:
        from .auth import get_botocore_session

        sts = get_botocore_session(profile=profile).create_client("sts")
        account_id = sts.get_caller_identity().get("Account")
        if account_id:
            account_tag = f"aws-account:{account_id}"
    except Exception:
        pass

    for region in regions:
        logger.info("Discovering RDS instances in %s...", region)
        try:
            client = get_rds_client(region, profile=profile)
            aurora_cluster_ids = set()

            try:
                cluster_paginator = client.get_paginator("describe_db_clusters")
                for page in cluster_paginator.paginate():
                    for cluster in page.get("DBClusters", []):
                        cluster_members = discover_aurora_cluster(
                            cluster=cluster,
                            client=client,
                            region=region,
                            engine_filter=engine_filter,
                            name_pattern=name_pattern,
                            password_env=password_env,
                            default_user=default_user,
                            default_group=default_group,
                            default_database=default_database,
                        )
                        if cluster_members:
                            aurora_cluster_ids.add(cluster.get("DBClusterIdentifier", ""))
                            for member in cluster_members:
                                if account_tag:
                                    member.tags.append(account_tag)
                                yield member
            except Exception as exc:
                logger.debug("Could not list Aurora clusters in %s: %s", region, exc)

            paginator = client.get_paginator("describe_db_instances")
            for page in paginator.paginate():
                for instance in page.get("DBInstances", []):
                    cluster_id = instance.get("DBClusterIdentifier", "")
                    if cluster_id and cluster_id in aurora_cluster_ids:
                        continue
                    member = _parse_instance(
                        instance=instance,
                        region=region,
                        engine_filter=engine_filter,
                        name_pattern=name_pattern,
                        password_env=password_env,
                        default_user=default_user,
                        default_group=default_group,
                        default_database=default_database,
                    )
                    if member is not None:
                        if account_tag:
                            member.tags.append(account_tag)
                        yield member
        except Exception as exc:
            error_message = str(exc)
            if "AccessDeniedException" in error_message or "not authorized" in error_message.lower():
                message = (
                    f"{region}: cannot list RDS instances. Ensure your role has "
                    "rds:DescribeDBInstances permission."
                )
            elif "ExpiredToken" in error_message:
                message = (
                    f"{region}: AWS credentials expired. Refresh with: aws sso login"
                )
            else:
                message = f"{region}: error discovering instances: {exc}"
            logger.error(message)
            if errors is not None:
                errors.append(message)


def discover_aurora_cluster(
    cluster: dict[str, Any],
    client,
    region: str,
    engine_filter: str | None,
    name_pattern: str | None,
    password_env: str,
    default_user: str | None,
    default_group: str | None = None,
    default_database: str | None = None,
) -> list[FleetMember]:
    """Parse an Aurora cluster into multiple FleetMembers.

    Members are grouped by the cluster identifier (an explicit
    `default_group` overrides) and tagged with their cluster role."""
    aws_engine = cluster.get("Engine", "")
    status = cluster.get("Status", "")
    cluster_id = cluster.get("DBClusterIdentifier", "")

    rdst_engine = _ENGINE_MAP.get(aws_engine)
    if rdst_engine is None:
        return []
    if engine_filter and engine_filter != "all" and rdst_engine != engine_filter:
        return []
    if name_pattern and not fnmatch.fnmatch(cluster_id, name_pattern):
        return []
    if status not in _VALID_STATUSES:
        return []

    writer_host = cluster.get("Endpoint", "")
    port = cluster.get("Port", 5432 if rdst_engine == "postgresql" else 3306)
    # CLI override > RDS-reported DBName > cluster ID (last is usually wrong;
    # cluster IDs aren't valid DB names — pass --default-database to fix).
    database = default_database or cluster.get("DatabaseName") or cluster_id
    user = default_user or cluster.get(
        "MasterUsername", "postgres" if rdst_engine == "postgresql" else "admin"
    )
    if not writer_host:
        return []

    group = default_group or cluster_id
    members = [
        FleetMember(
            name=f"{cluster_id}-writer",
            engine=rdst_engine,
            host=writer_host,
            port=port,
            database=database,
            user=user,
            password_env=password_env,
            group=group,
            tags=["aurora", "writer", "role:writer"],
            instance_class=None,
            region=region,
        )
    ]

    reader_instance_ids = [
        member.get("DBInstanceIdentifier")
        for member in cluster.get("DBClusterMembers", [])
        if not member.get("IsClusterWriter")
    ]
    if reader_instance_ids:
        try:
            for reader_id in reader_instance_ids:
                response = client.describe_db_instances(
                    Filters=[{"Name": "db-instance-id", "Values": [reader_id]}]
                )
                for instance in response.get("DBInstances", []):
                    endpoint = instance.get("Endpoint", {})
                    reader_host = endpoint.get("Address", "")
                    if reader_host:
                        members.append(
                            FleetMember(
                                name=reader_id,
                                engine=rdst_engine,
                                host=reader_host,
                                port=endpoint.get("Port", port),
                                database=database,
                                user=user,
                                password_env=password_env,
                                group=group,
                                tags=["aurora", "reader", "role:reader"],
                                instance_class=instance.get("DBInstanceClass"),
                                region=region,
                            )
                        )
        except Exception as exc:
            logger.debug("Could not look up Aurora reader instances: %s", exc)

    return members


def _parse_instance(
    instance: dict[str, Any],
    region: str,
    engine_filter: str | None,
    name_pattern: str | None,
    password_env: str,
    default_user: str | None,
    default_group: str | None,
    default_database: str | None = None,
) -> FleetMember | None:
    """Parse an RDS instance into a FleetMember."""
    aws_engine = instance.get("Engine", "")
    status = instance.get("DBInstanceStatus", "")
    db_id = instance.get("DBInstanceIdentifier", "")

    if aws_engine in _SKIP_ENGINES:
        return None

    rdst_engine = _ENGINE_MAP.get(aws_engine)
    if rdst_engine is None:
        logger.debug("Skipping unsupported engine: %s (%s)", aws_engine, db_id)
        return None
    if engine_filter and engine_filter != "all" and rdst_engine != engine_filter:
        return None
    if name_pattern and not fnmatch.fnmatch(db_id, name_pattern):
        return None
    if status not in _VALID_STATUSES:
        logger.debug("Skipping instance %s with status %s", db_id, status)
        return None

    endpoint = instance.get("Endpoint", {})
    host = endpoint.get("Address", "")
    port = endpoint.get("Port", 5432 if rdst_engine == "postgresql" else 3306)
    if not host:
        return None

    database = default_database or instance.get("DBName") or db_id
    user = default_user or instance.get(
        "MasterUsername", "postgres" if rdst_engine == "postgresql" else "admin"
    )
    instance_class = instance.get("DBInstanceClass")
    # Aurora members group by their cluster. Standalone instances stay
    # ungrouped: a region is metadata, not a group identity, and a lone
    # instance should not manufacture one.
    group = default_group or instance.get("DBClusterIdentifier")
    tags = [f"{tag['Key']}={tag['Value']}" for tag in instance.get("TagList", [])]

    return FleetMember(
        name=db_id,
        engine=rdst_engine,
        host=host,
        port=port,
        database=database,
        user=user,
        password_env=password_env,
        group=group,
        tags=tags,
        instance_class=instance_class,
        region=region,
    )
