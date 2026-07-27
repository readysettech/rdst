"""Fleet service — async generator for fleet operations."""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any

from shared.config.targets import TargetsConfig
from shared.db_connection import create_direct_connection
from shared.password_resolver import resolve_password
from shared.secret_store_service import SecretStoreService

from .csv_importer import parse_csv
from .events import (
    FleetConnectivityEvent,
    FleetDiscoverEvent,
    FleetErrorEvent,
    FleetEvent,
    FleetImportCompleteEvent,
    FleetImportProgressEvent,
    FleetListEvent,
    FleetStatusEvent,
)
from .models import FleetMember


def fleet_member_shape(
    name: str,
    target_config: dict[str, Any],
    secret_store: SecretStoreService | None = None,
) -> dict[str, Any]:
    """Return the public member shape used by the fleet targets API.

    `secret_store` is reused across members so a listing opens the OS
    keychain once instead of once per target."""
    return {
        "name": name,
        "engine": target_config.get("engine", ""),
        "host": target_config.get("host", ""),
        "port": target_config.get("port", 0),
        "database": target_config.get("database", ""),
        "user": target_config.get("user", ""),
        "password_env": target_config.get("password_env", ""),
        "has_password": resolve_password(target_config, secret_store).available,
        "group": target_config.get("group"),
        "tags": target_config.get("tags", []),
        "instance_class": target_config.get("instance_class"),
        "target_type": target_config.get("target_type", "database"),
        "region": target_config.get("region"),
        "tls": target_config.get("tls", False),
        "read_only": target_config.get("read_only", False),
    }


def _existing_hosts(config: TargetsConfig) -> set[str]:
    """Lowercased hosts of every configured target, for dedupe on import."""
    hosts: set[str] = set()
    for name in config.list_targets():
        target_config = config.get(name)
        if target_config:
            hosts.add(target_config.get("host", "").lower())
    return hosts


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

    async def discover_preview(
        self,
        regions: list[str],
        *,
        engine_filter: str | None = None,
        profile: str | None = None,
    ) -> dict:
        """Discover RDS/Aurora instances without importing anything.

        Returns the full member list (with an `already_exists` flag per
        member) so the caller can choose which targets to add.
        """
        from .auth import detect_aws_credentials
        from .discovery import discover_rds_instances

        has_creds, message = await asyncio.to_thread(detect_aws_credentials, profile)
        if not has_creds:
            return {"members": [], "errors": [message]}

        discovery_errors: list[str] = []

        def _discover_all() -> list[FleetMember]:
            return list(
                discover_rds_instances(
                    regions=regions,
                    engine_filter=engine_filter,
                    errors=discovery_errors,
                    profile=profile,
                )
            )

        members = await asyncio.to_thread(_discover_all)

        config = self._get_config()
        existing_hosts = _existing_hosts(config)

        shaped = []
        for member in members:
            shaped.append({
                **dataclasses.asdict(member),
                "already_exists": bool(
                    config.get(member.name) or member.host.lower() in existing_hosts
                ),
            })
        return {"members": shaped, "errors": discovery_errors}

    def add_members(self, members: list[dict]) -> dict:
        """Add pre-discovered members as targets, skipping existing ones."""
        allowed = {f.name for f in dataclasses.fields(FleetMember)}
        config = self._get_config()
        existing_hosts = _existing_hosts(config)

        imported_names: list[str] = []
        skipped = 0
        for raw in members:
            member = FleetMember(**{k: v for k, v in raw.items() if k in allowed})
            if config.get(member.name) or member.host.lower() in existing_hosts:
                skipped += 1
                continue
            config.upsert(member.name, member.to_target_config())
            imported_names.append(member.name)
        if imported_names:
            config.save()
        return {
            "imported": len(imported_names),
            "skipped": skipped,
            "target_names": imported_names,
        }

    async def discover(
        self,
        regions: list[str],
        *,
        engine_filter: str | None = None,
        name_pattern: str | None = None,
        password_env: str = "FLEET_PASS",
        default_user: str | None = None,
        default_group: str | None = None,
        default_database: str | None = None,
        dry_run: bool = False,
        profile: str | None = None,
    ):
        """Discover RDS/Aurora instances from AWS and add them as targets.

        Uses the local AWS credential chain (env vars, ~/.aws, SSO), or the
        named AWS profile when `profile` is given. Emits import_progress per
        instance and import_complete with the totals, matching the CSV
        import event flow.
        """
        from .auth import detect_aws_credentials
        from .discovery import discover_rds_instances

        has_creds, message = await asyncio.to_thread(detect_aws_credentials, profile)
        if not has_creds:
            yield FleetErrorEvent(type="error", message=message, phase="discover")
            return

        yield FleetStatusEvent(
            type="status",
            phase="discover",
            message=f"Discovering RDS instances in {', '.join(regions)}...",
        )

        discovery_errors: list[str] = []

        def _discover_all() -> list[FleetMember]:
            return list(
                discover_rds_instances(
                    regions=regions,
                    engine_filter=engine_filter,
                    name_pattern=name_pattern,
                    password_env=password_env,
                    default_user=default_user,
                    default_group=default_group,
                    default_database=default_database,
                    errors=discovery_errors,
                    profile=profile,
                )
            )

        members = await asyncio.to_thread(_discover_all)

        for error_message in discovery_errors:
            yield FleetErrorEvent(type="error", message=error_message, phase="discover")

        if not members and discovery_errors:
            # Nothing found and at least one region failed: an empty result
            # is not trustworthy, so don't report success.
            yield FleetImportCompleteEvent(
                type="import_complete",
                success=False,
                imported=0,
                skipped=0,
                errors=len(discovery_errors),
                target_names=[],
            )
            return

        yield FleetDiscoverEvent(
            type="discover",
            instances_found=len(members),
            regions_searched=regions,
            message=f"Found {len(members)} instance(s)",
        )

        config = self._get_config()
        existing_hosts = _existing_hosts(config)

        imported = 0
        skipped = 0
        imported_names: list[str] = []
        total = len(members)

        for index, member in enumerate(members):
            role = next((t for t in ("writer", "reader") if t in member.tags), None)
            detail = f"{member.engine}, {member.instance_class or 'unknown'}"
            if role:
                detail = f"{role}, {detail}"

            if config.get(member.name) or member.host.lower() in existing_hosts:
                skipped += 1
                yield FleetImportProgressEvent(
                    type="import_progress",
                    current=index + 1,
                    total=total,
                    target_name=member.name,
                    status="skipped",
                    message=f"Target '{member.name}' already exists",
                )
                continue

            if not dry_run:
                config.upsert(member.name, member.to_target_config())
            imported += 1
            imported_names.append(member.name)
            prefix = "[dry-run] Would import" if dry_run else "Discovered"
            yield FleetImportProgressEvent(
                type="import_progress",
                current=index + 1,
                total=total,
                target_name=member.name,
                status="importing",
                message=f"{prefix} '{member.name}' ({detail})",
            )

        if not dry_run and imported > 0:
            config.save()

        yield FleetImportCompleteEvent(
            type="import_complete",
            success=True,
            imported=imported,
            skipped=skipped,
            errors=0,
            target_names=imported_names,
        )

    async def list_fleet(self, group: str | None = None, tag: str | None = None):
        """List fleet members — all database targets."""
        config = self._get_config()
        target_names = config.list_fleet_targets(group=group, tag=tag)

        def _shape_members() -> list[dict[str, Any]]:
            secret_store = SecretStoreService()
            members: list[dict[str, Any]] = []
            for name in target_names:
                target_config = config.get(name)
                if target_config is None:
                    continue
                members.append(fleet_member_shape(name, target_config, secret_store))
            return members

        # Password resolution can block on the OS keychain, so it stays off
        # the event loop.
        members = await asyncio.to_thread(_shape_members)

        yield FleetListEvent(type="fleet_list", members=members, groups=config.list_groups())

    async def check_status(
        self,
        group: str | None = None,
        tag: str | None = None,
        targets: list[str] | None = None,
    ):
        """Check connectivity for fleet targets."""
        config = self._get_config()
        configured_names = config.list_fleet_targets(group=group, tag=tag)
        if targets is None:
            target_names = configured_names
        else:
            configured = set(configured_names)
            target_names = [
                name for name in dict.fromkeys(targets) if name in configured
            ]

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

            password_env = target_config.get("password_env")
            if password_env and not resolve_password(target_config).available:
                yield FleetConnectivityEvent(
                    type="connectivity",
                    target_name=name,
                    status="failed",
                    error=f"Enter the password for '{name}' again.",
                    code="TARGET_PASSWORD_REQUIRED",
                    password_env=password_env,
                )
                continue

            try:
                started = time.monotonic()
                version = await asyncio.to_thread(self._check_connection, target_config)
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
