"""Fleet service — async generator for fleet operations."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from shared.config.targets import TargetsConfig
from shared.db_connection import create_direct_connection
from shared.password_resolver import resolve_password
from shared.secret_store_service import SecretStoreService

from .csv_importer import parse_csv
from .events import (
    FleetConnectivityEvent,
    FleetErrorEvent,
    FleetImportCompleteEvent,
    FleetImportProgressEvent,
    FleetListEvent,
    FleetStatusEvent,
)


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
        "tls_verify": target_config.get("tls_verify", False),
        "tls_ca": target_config.get("tls_ca"),
        "read_only": target_config.get("read_only", False),
        "password_secret_arn": target_config.get("password_secret_arn"),
        "password_secret_key": target_config.get("password_secret_key"),
        "ssh": target_config.get("ssh"),
    }


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
                from features.allowlist.providers import provider_for_target

                if provider_for_target(target_config):
                    from features.configure.service import ConfigureService

                    named_config = {**target_config, "name": name}
                    result = await ConfigureService().perform_connection_test(
                        named_config,
                        force_fresh_tunnel=True,
                    )
                    yield FleetConnectivityEvent(
                        type="connectivity",
                        target_name=name,
                        status="failed",
                        error=result.get("message", "Connection failed"),
                        code=result.get("code"),
                        category=result.get("category"),
                        password_env=result.get("password_env"),
                    )
                    continue
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
                version, privileges = await asyncio.to_thread(
                    self._check_connection,
                    name,
                    target_config,
                    force_fresh_tunnel=True,
                )
                latency = (time.monotonic() - started) * 1000
                yield FleetConnectivityEvent(
                    type="connectivity",
                    target_name=name,
                    status="ok",
                    latency_ms=round(latency, 1),
                    server_version=version,
                    privileges=privileges,
                )
            except Exception as exc:
                if target_config.get("ssh"):
                    get_tunnel_manager().close(name)
                from features.allowlist.service import (
                    connection_failure_category,
                    provider_network_hint,
                )

                provider_category = connection_failure_category(target_config, exc)
                provider_network_failure = provider_category is not None
                error = str(exc)
                if provider_network_failure:
                    error = f"{error}. {provider_network_hint(target_config)}"
                yield FleetConnectivityEvent(
                    type="connectivity",
                    target_name=name,
                    status="failed",
                    error=error,
                    category=(
                        provider_category if provider_network_failure else None
                    ),
                )

    def _check_connection(
        self,
        target_name: str,
        target_config: dict[str, Any],
        *,
        force_fresh_tunnel: bool = False,
    ) -> tuple[str, dict]:
        """Check DB connectivity and return server version plus privileges."""
        named_config = {**target_config, "name": target_name}
        connection = create_direct_connection(
            named_config,
            connect_timeout=3,
            force_fresh_tunnel=force_fresh_tunnel,
        )
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
            from features.configure.privileges import detect_write_privileges

            privileges = detect_write_privileges(connection, engine)
            return version.split("\n")[0][:80], privileges
        finally:
            connection.close()
