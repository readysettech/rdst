"""CSV fleet importer — parses CSV files into FleetMember objects."""

import csv
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .models import FleetMember


# Pattern to extract region from RDS hostname
_RDS_HOSTNAME_RE = re.compile(
    r"\.([a-z]{2}-[a-z]+-\d+)\.rds\.amazonaws\.com$"
)


def detect_region_from_hostname(hostname: str) -> Optional[str]:
    """Extract AWS region from an RDS hostname.

    Example: prod-db.abc123.us-east-1.rds.amazonaws.com -> us-east-1
    """
    m = _RDS_HOSTNAME_RE.search(hostname)
    return m.group(1) if m else None


def _normalize_engine(raw: str) -> Optional[str]:
    """Normalize engine names to 'postgresql' or 'mysql'."""
    s = raw.strip().lower()
    if s in ("postgres", "postgresql", "psql", "pg"):
        return "postgresql"
    if s in ("mysql", "mariadb"):
        return "mysql"
    return None


def _parse_tags(raw: str) -> List[str]:
    """Parse a comma-separated or JSON-style tag string."""
    if not raw:
        return []
    # Strip surrounding quotes and brackets
    s = raw.strip().strip('"').strip("'").strip("[").strip("]")
    return [t.strip().strip('"').strip("'") for t in s.split(",") if t.strip()]


def parse_csv(
    csv_path: str,
    password_env: str = "FLEET_PASS",
    default_group: Optional[str] = None,
    default_tags: Optional[List[str]] = None,
) -> Tuple[List[FleetMember], List[Dict[str, Any]]]:
    """Parse a CSV file into FleetMember objects.

    Required columns: name, host, port, database, user, engine
    Optional columns: group, tags, instance_class, tls, read_only

    Returns:
        Tuple of (valid_members, errors) where errors is a list of
        {row: int, name: str, error: str} dicts.
    """
    path = Path(csv_path)
    if not path.exists():
        return [], [{"row": 0, "name": "", "error": f"File not found: {csv_path}"}]

    members: List[FleetMember] = []
    errors: List[Dict[str, Any]] = []

    with open(path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if reader.fieldnames is None:
            return [], [{"row": 0, "name": "", "error": "Empty CSV or no header row"}]

        # Normalize header names (lowercase, strip whitespace)
        reader.fieldnames = [h.strip().lower() for h in reader.fieldnames]

        required_fields = {"name", "host", "engine"}
        missing = required_fields - set(reader.fieldnames)
        if missing:
            return [], [
                {
                    "row": 0,
                    "name": "",
                    "error": f"Missing required columns: {', '.join(sorted(missing))}",
                }
            ]

        for row_num, row in enumerate(reader, start=2):  # Row 1 is header
            name = (row.get("name") or "").strip()
            host = (row.get("host") or "").strip()
            engine_raw = (row.get("engine") or "").strip()

            if not name or not host or not engine_raw:
                errors.append(
                    {
                        "row": row_num,
                        "name": name or "(unnamed)",
                        "error": "Missing required field (name, host, or engine)",
                    }
                )
                continue

            engine = _normalize_engine(engine_raw)
            if engine is None:
                errors.append(
                    {
                        "row": row_num,
                        "name": name,
                        "error": f"Unsupported engine: {engine_raw}",
                    }
                )
                continue

            # Parse port with sensible defaults
            port_raw = (row.get("port") or "").strip()
            try:
                port = int(port_raw) if port_raw else (5432 if engine == "postgresql" else 3306)
            except ValueError:
                errors.append(
                    {"row": row_num, "name": name, "error": f"Invalid port: {port_raw}"}
                )
                continue

            database = (row.get("database") or "").strip() or name
            user = (row.get("user") or "").strip() or (
                "postgres" if engine == "postgresql" else "admin"
            )

            # Group: explicit > default > auto-detect from hostname
            group = (row.get("group") or "").strip() or default_group
            if not group:
                group = detect_region_from_hostname(host)

            # Tags: merge row tags with default tags
            row_tags = _parse_tags(row.get("tags", ""))
            all_tags = list(set(row_tags + (default_tags or [])))

            # Per-row password_env overrides the global default
            row_password_env = (row.get("password_env") or "").strip()
            effective_password_env = row_password_env or password_env

            # Optional fields
            instance_class = (row.get("instance_class") or "").strip() or None
            tls_raw = (row.get("tls") or "").strip().lower()
            tls = tls_raw in ("true", "1", "yes")
            read_only_raw = (row.get("read_only") or "").strip().lower()
            read_only = read_only_raw in ("true", "1", "yes")

            # Auto-detect region
            region = detect_region_from_hostname(host)

            # Secrets Manager ARN (optional, overrides password_env)
            secret_arn = (row.get("password_secret_arn") or row.get("secret_arn") or "").strip() or None

            members.append(
                FleetMember(
                    name=name,
                    engine=engine,
                    host=host,
                    port=port,
                    database=database,
                    user=user,
                    password_env=effective_password_env,
                    group=group,
                    tags=sorted(all_tags),
                    instance_class=instance_class,
                    region=region,
                    tls=tls,
                    read_only=read_only,
                    password_secret_arn=secret_arn,
                )
            )

    return members, errors
