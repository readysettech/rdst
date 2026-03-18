"""AWS RDS instance discovery using botocore."""

import fnmatch
import logging
from typing import Any, Dict, Generator, List, Optional

from lib.fleet.models import FleetMember

logger = logging.getLogger(__name__)

# AWS engine -> rdst engine mapping
_ENGINE_MAP = {
    "postgres": "postgresql",
    "aurora-postgresql": "postgresql",
    "mysql": "mysql",
    "aurora-mysql": "mysql",
    "aurora": "mysql",  # Legacy aurora = MySQL-compatible
    "mariadb": "mysql",
}

# Engines we skip
_SKIP_ENGINES = {"oracle-ee", "oracle-se2", "sqlserver-ee", "sqlserver-se", "sqlserver-ex", "sqlserver-web"}

# Valid instance statuses
_VALID_STATUSES = {"available", "backing-up", "modifying", "storage-optimization"}


def discover_rds_instances(
    regions: List[str],
    engine_filter: Optional[str] = None,
    name_pattern: Optional[str] = None,
    password_env: str = "FLEET_PASS",
    default_user: Optional[str] = None,
    default_group: Optional[str] = None,
) -> Generator[FleetMember, None, None]:
    """Discover RDS instances across regions using botocore.

    Yields FleetMember objects for each discovered instance.
    Raises ImportError if botocore is not installed.
    """
    from lib.fleet.aws_auth import get_rds_client

    for region in regions:
        logger.info("Discovering RDS instances in %s...", region)
        try:
            client = get_rds_client(region)

            # Track Aurora cluster IDs so we don't duplicate individual instances
            aurora_cluster_ids = set()

            # 1. Discover Aurora clusters (writer + each reader as separate targets)
            try:
                cluster_paginator = client.get_paginator("describe_db_clusters")
                for page in cluster_paginator.paginate():
                    for cluster in page.get("DBClusters", []):
                        cluster_members = discover_aurora_cluster(
                            cluster,
                            client=client,
                            region=region,
                            engine_filter=engine_filter,
                            name_pattern=name_pattern,
                            password_env=password_env,
                            default_user=default_user,
                        )
                        if cluster_members:
                            aurora_cluster_ids.add(cluster.get("DBClusterIdentifier", ""))
                            for m in cluster_members:
                                yield m
            except Exception as e:
                logger.debug("Could not list Aurora clusters in %s: %s", region, e)

            # 2. Discover regular RDS instances (skip Aurora instances — already covered)
            paginator = client.get_paginator("describe_db_instances")
            for page in paginator.paginate():
                for instance in page.get("DBInstances", []):
                    cluster_id = instance.get("DBClusterIdentifier", "")
                    if cluster_id and cluster_id in aurora_cluster_ids:
                        continue

                    member = _parse_instance(
                        instance,
                        region=region,
                        engine_filter=engine_filter,
                        name_pattern=name_pattern,
                        password_env=password_env,
                        default_user=default_user,
                        default_group=default_group,
                    )
                    if member is not None:
                        yield member

        except Exception as e:
            error_msg = str(e)
            if "AccessDeniedException" in error_msg or "not authorized" in error_msg.lower():
                logger.error(
                    "Cannot list RDS instances in %s. "
                    "Ensure your role has rds:DescribeDBInstances permission.",
                    region,
                )
            elif "ExpiredTokenException" in error_msg:
                logger.error(
                    "AWS credentials expired. Refresh with: aws sso login"
                )
            else:
                logger.error("Error discovering instances in %s: %s", region, e)


def discover_aurora_cluster(
    cluster: Dict[str, Any],
    client,
    region: str,
    engine_filter: Optional[str],
    name_pattern: Optional[str],
    password_env: str,
    default_user: Optional[str],
) -> List[FleetMember]:
    """Parse an Aurora cluster into multiple FleetMembers — writer + each reader.

    Returns a list of FleetMembers grouped by cluster ID.
    The writer uses the cluster endpoint. Each reader uses its instance endpoint.
    """
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
    database = cluster.get("DatabaseName") or cluster_id
    user = default_user or cluster.get("MasterUsername", "postgres" if rdst_engine == "postgresql" else "admin")

    if not writer_host:
        return []

    members = []

    # Identify writer and reader instances
    cluster_members = cluster.get("DBClusterMembers", [])
    writer_instance_id = None
    reader_instance_ids = []
    for m in cluster_members:
        if m.get("IsClusterWriter"):
            writer_instance_id = m.get("DBInstanceIdentifier")
        else:
            reader_instance_ids.append(m.get("DBInstanceIdentifier"))

    # Writer target — uses cluster endpoint
    members.append(FleetMember(
        name=f"{cluster_id}-writer",
        engine=rdst_engine,
        host=writer_host,
        port=port,
        database=database,
        user=user,
        password_env=password_env,
        group=cluster_id,
        tags=["aurora", "writer"],
        instance_class=None,
        region=region,
    ))

    # Reader targets — look up each reader's instance endpoint
    if reader_instance_ids:
        try:
            for reader_id in reader_instance_ids:
                resp = client.describe_db_instances(
                    Filters=[{"Name": "db-instance-id", "Values": [reader_id]}]
                )
                for inst in resp.get("DBInstances", []):
                    endpoint = inst.get("Endpoint", {})
                    reader_host = endpoint.get("Address", "")
                    if reader_host:
                        members.append(FleetMember(
                            name=reader_id,
                            engine=rdst_engine,
                            host=reader_host,
                            port=endpoint.get("Port", port),
                            database=database,
                            user=user,
                            password_env=password_env,
                            group=cluster_id,
                            tags=["aurora", "reader"],
                            instance_class=inst.get("DBInstanceClass"),
                            region=region,
                        ))
        except Exception as e:
            logger.debug("Could not look up Aurora reader instances: %s", e)

    return members


def _parse_instance(
    instance: Dict[str, Any],
    region: str,
    engine_filter: Optional[str],
    name_pattern: Optional[str],
    password_env: str,
    default_user: Optional[str],
    default_group: Optional[str],
) -> Optional[FleetMember]:
    """Parse an RDS instance into a FleetMember. Returns None if filtered out."""
    aws_engine = instance.get("Engine", "")
    status = instance.get("DBInstanceStatus", "")
    db_id = instance.get("DBInstanceIdentifier", "")

    # Skip unsupported engines
    if aws_engine in _SKIP_ENGINES:
        return None

    # Map engine
    rdst_engine = _ENGINE_MAP.get(aws_engine)
    if rdst_engine is None:
        logger.debug("Skipping unsupported engine: %s (%s)", aws_engine, db_id)
        return None

    # Apply engine filter
    if engine_filter and engine_filter != "all" and rdst_engine != engine_filter:
        return None

    # Apply name pattern filter
    if name_pattern and not fnmatch.fnmatch(db_id, name_pattern):
        return None

    # Skip non-available instances
    if status not in _VALID_STATUSES:
        logger.debug("Skipping instance %s with status %s", db_id, status)
        return None

    # Extract endpoint
    endpoint = instance.get("Endpoint", {})
    host = endpoint.get("Address", "")
    port = endpoint.get("Port", 5432 if rdst_engine == "postgresql" else 3306)

    if not host:
        return None

    # Extract database name
    database = instance.get("DBName") or db_id

    # Determine user
    user = default_user or instance.get("MasterUsername", "postgres" if rdst_engine == "postgresql" else "admin")

    # Instance class
    instance_class = instance.get("DBInstanceClass")

    # Group
    group = default_group or region

    # Tags from AWS
    tags = []
    for tag in instance.get("TagList", []):
        tags.append(f"{tag['Key']}={tag['Value']}")

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
