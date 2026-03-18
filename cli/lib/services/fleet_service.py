"""Fleet service — async generator for fleet operations."""

import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from lib.cli.rdst_cli import TargetsConfig
from lib.fleet.models import FleetMember
from lib.services.types import (
    FleetConnectivityEvent,
    FleetErrorEvent,
    FleetEvent,
    FleetImportCompleteEvent,
    FleetImportProgressEvent,
    FleetListEvent,
    FleetStatusEvent,
)


class FleetService:
    """Service layer for fleet operations. All methods are async generators yielding FleetEvent."""

    def __init__(self, config: Optional[TargetsConfig] = None):
        self._config = config

    def _get_config(self) -> TargetsConfig:
        if self._config is None:
            self._config = TargetsConfig()
            self._config.load()
        return self._config

    async def import_fleet(
        self,
        csv_file: str,
        password_env: str = "FLEET_PASS",
        default_group: Optional[str] = None,
        default_tags: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> AsyncGenerator[FleetEvent, None]:
        """Import fleet targets from CSV file."""
        from lib.fleet.csv_importer import parse_csv

        yield FleetStatusEvent(
            type="status", phase="import", message=f"Reading {csv_file}..."
        )

        members, errors = parse_csv(
            csv_file,
            password_env=password_env,
            default_group=default_group,
            default_tags=default_tags,
        )

        if errors and not members:
            for err in errors:
                yield FleetErrorEvent(
                    type="error",
                    message=f"Row {err['row']} ({err['name']}): {err['error']}",
                    phase="import",
                )
            yield FleetImportCompleteEvent(
                type="import_complete",
                success=False,
                imported=0,
                skipped=0,
                errors=len(errors),
                target_names=[],
            )
            return

        cfg = self._get_config()
        imported = 0
        skipped = 0
        imported_names: List[str] = []

        for i, member in enumerate(members):
            existing = cfg.get(member.name)
            if existing:
                yield FleetImportProgressEvent(
                    type="import_progress",
                    current=i + 1,
                    total=len(members),
                    target_name=member.name,
                    status="skipped",
                    message=f"Target '{member.name}' already exists",
                )
                skipped += 1
                continue

            if dry_run:
                yield FleetImportProgressEvent(
                    type="import_progress",
                    current=i + 1,
                    total=len(members),
                    target_name=member.name,
                    status="importing",
                    message=f"[dry-run] Would import '{member.name}' ({member.engine}, {member.host})",
                )
                imported += 1
                imported_names.append(member.name)
                continue

            cfg.upsert(member.name, member.to_target_config())
            imported += 1
            imported_names.append(member.name)

            yield FleetImportProgressEvent(
                type="import_progress",
                current=i + 1,
                total=len(members),
                target_name=member.name,
                status="importing",
                message=f"Imported '{member.name}' ({member.engine}, {member.host}:{member.port})",
            )

        if not dry_run and imported > 0:
            cfg.save()

        # Report parse errors
        for err in errors:
            yield FleetErrorEvent(
                type="error",
                message=f"Row {err['row']} ({err['name']}): {err['error']}",
                phase="import",
            )

        yield FleetImportCompleteEvent(
            type="import_complete",
            success=True,
            imported=imported,
            skipped=skipped,
            errors=len(errors),
            target_names=imported_names,
        )

    async def list_fleet(
        self,
        group: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> AsyncGenerator[FleetEvent, None]:
        """List fleet members — all database targets (excludes ReadySet caches)."""
        cfg = self._get_config()
        target_names = cfg.list_fleet_targets(group=group, tag=tag)

        members: List[Dict[str, Any]] = []
        for name in target_names:
            tc = cfg.get(name)
            if tc is None:
                continue
            members.append(
                {
                    "name": name,
                    "engine": tc.get("engine", ""),
                    "host": tc.get("host", ""),
                    "port": tc.get("port", 0),
                    "database": tc.get("database", ""),
                    "group": tc.get("group"),
                    "tags": tc.get("tags", []),
                    "instance_class": tc.get("instance_class"),
                    "target_type": tc.get("target_type", "database"),
                    "region": tc.get("region"),
                }
            )

        groups = cfg.list_groups()

        yield FleetListEvent(type="fleet_list", members=members, groups=groups)

    async def check_status(
        self,
        group: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> AsyncGenerator[FleetEvent, None]:
        """Check connectivity for fleet targets."""
        cfg = self._get_config()
        target_names = cfg.list_fleet_targets(group=group, tag=tag)

        if not target_names:
            yield FleetErrorEvent(
                type="error",
                message="No targets found. Import targets first: rdst fleet import --from fleet.csv",
                phase="status",
            )
            return

        yield FleetStatusEvent(
            type="status",
            phase="status",
            message=f"Checking connectivity for {len(target_names)} targets...",
        )

        for name in target_names:
            yield FleetConnectivityEvent(
                type="connectivity", target_name=name, status="checking"
            )

            tc = cfg.get(name)
            if tc is None:
                yield FleetConnectivityEvent(
                    type="connectivity",
                    target_name=name,
                    status="failed",
                    error="Target config not found",
                )
                continue

            try:
                start = time.monotonic()
                version = self._check_connection(tc)
                latency = (time.monotonic() - start) * 1000

                yield FleetConnectivityEvent(
                    type="connectivity",
                    target_name=name,
                    status="ok",
                    latency_ms=round(latency, 1),
                    server_version=version,
                )
            except Exception as e:
                yield FleetConnectivityEvent(
                    type="connectivity",
                    target_name=name,
                    status="failed",
                    error=str(e),
                )

    def _check_connection(self, target_config: Dict[str, Any]) -> str:
        """Check DB connectivity and return server version. Raises on failure."""
        from lib.db_connection import create_direct_connection

        conn = create_direct_connection(target_config, connect_timeout=3)
        try:
            cursor = conn.cursor()
            engine = target_config.get("engine", "postgresql")
            if engine == "postgresql":
                cursor.execute("SELECT version()")
            else:
                cursor.execute("SELECT VERSION()")
            row = cursor.fetchone()
            if row is None:
                version = "unknown"
            elif isinstance(row, dict):
                # DictCursor (MySQL)
                version = str(list(row.values())[0])
            else:
                version = str(row[0])
            cursor.close()
            return version.split("\n")[0][:80]
        finally:
            conn.close()
