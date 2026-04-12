"""Fleet service — async generator for fleet operations."""

from __future__ import annotations

import time
from typing import Any

from shared.config.targets import TargetsConfig
from shared.db_connection import create_direct_connection

from .csv_importer import parse_csv
from .events import (
    FleetConnectivityEvent,
    FleetErrorEvent,
    FleetEvent,
    FleetImportCompleteEvent,
    FleetImportProgressEvent,
    FleetListEvent,
    FleetStatusEvent,
)
from .models import FleetMember


class FleetService:
    """Service layer for fleet operations."""

    def __init__(self, config: TargetsConfig | None = None):
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
        default_group: str | None = None,
        default_tags: list[str] | None = None,
        dry_run: bool = False,
    ):
        """Import fleet targets from CSV file."""
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
            for error in errors:
                yield FleetErrorEvent(
                    type="error",
                    message=f"Row {error['row']} ({error['name']}): {error['error']}",
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

        config = self._get_config()
        imported = 0
        skipped = 0
        imported_names: list[str] = []

        for index, member in enumerate(members):
            existing = config.get(member.name)
            if existing:
                yield FleetImportProgressEvent(
                    type="import_progress",
                    current=index + 1,
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
                    current=index + 1,
                    total=len(members),
                    target_name=member.name,
                    status="importing",
                    message=f"[dry-run] Would import '{member.name}' ({member.engine}, {member.host})",
                )
                imported += 1
                imported_names.append(member.name)
                continue

            config.upsert(member.name, member.to_target_config())
            imported += 1
            imported_names.append(member.name)
            yield FleetImportProgressEvent(
                type="import_progress",
                current=index + 1,
                total=len(members),
                target_name=member.name,
                status="importing",
                message=f"Imported '{member.name}' ({member.engine}, {member.host}:{member.port})",
            )

        if not dry_run and imported > 0:
            config.save()

        for error in errors:
            yield FleetErrorEvent(
                type="error",
                message=f"Row {error['row']} ({error['name']}): {error['error']}",
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

    async def list_fleet(self, group: str | None = None, tag: str | None = None):
        """List fleet members — all database targets."""
        config = self._get_config()
        target_names = config.list_fleet_targets(group=group, tag=tag)

        members: list[dict[str, Any]] = []
        for name in target_names:
            target_config = config.get(name)
            if target_config is None:
                continue
            members.append(
                {
                    "name": name,
                    "engine": target_config.get("engine", ""),
                    "host": target_config.get("host", ""),
                    "port": target_config.get("port", 0),
                    "database": target_config.get("database", ""),
                    "group": target_config.get("group"),
                    "tags": target_config.get("tags", []),
                    "instance_class": target_config.get("instance_class"),
                    "target_type": target_config.get("target_type", "database"),
                    "region": target_config.get("region"),
                }
            )

        yield FleetListEvent(type="fleet_list", members=members, groups=config.list_groups())

    async def check_status(self, group: str | None = None, tag: str | None = None):
        """Check connectivity for fleet targets."""
        config = self._get_config()
        target_names = config.list_fleet_targets(group=group, tag=tag)

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
            target_config = config.get(name)
            if target_config is None:
                yield FleetConnectivityEvent(
                    type="connectivity",
                    target_name=name,
                    status="failed",
                    error="Target config not found",
                )
                continue

            try:
                started = time.monotonic()
                version = self._check_connection(target_config)
                latency = (time.monotonic() - started) * 1000
                yield FleetConnectivityEvent(
                    type="connectivity",
                    target_name=name,
                    status="ok",
                    latency_ms=round(latency, 1),
                    server_version=version,
                )
            except Exception as exc:
                yield FleetConnectivityEvent(
                    type="connectivity",
                    target_name=name,
                    status="failed",
                    error=str(exc),
                )

    def _check_connection(self, target_config: dict[str, Any]) -> str:
        """Check DB connectivity and return server version."""
        connection = create_direct_connection(target_config, connect_timeout=3)
        try:
            cursor = connection.cursor()
            engine = target_config.get("engine", "postgresql")
            cursor.execute("SELECT version()" if engine == "postgresql" else "SELECT VERSION()")
            row = cursor.fetchone()
            if row is None:
                version = "unknown"
            elif isinstance(row, dict):
                version = str(list(row.values())[0])
            else:
                version = str(row[0])
            cursor.close()
            return version.split("\n")[0][:80]
        finally:
            connection.close()
