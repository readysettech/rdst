"""Providers service - cloud-provider sign-in and database discovery.

Discovery previews and imports for AWS RDS/Aurora and the account-wide
providers (Supabase, Neon, DigitalOcean). Separate from FleetService, which
owns multi-target fleet operations.
"""

from __future__ import annotations

import asyncio
import dataclasses
from importlib import import_module

from shared.config.targets import TargetsConfig
from shared.password_resolver import store_target_password
from shared.secret_store_service import SecretStoreService

from features.fleet.events import (
    FleetDiscoverEvent,
    FleetErrorEvent,
    FleetImportCompleteEvent,
    FleetImportProgressEvent,
    FleetStatusEvent,
)
from features.fleet.models import FleetMember

# Whole-account providers: discovery needs no region list, only a credential.
# Resolved at call time so the discovery functions stay patchable.
ACCOUNT_PROVIDERS = {
    "supabase": (".supabase", "discover_supabase_projects"),
    "neon": (".neon", "discover_neon_projects"),
    "digitalocean": (".digitalocean", "discover_digitalocean_clusters"),
}


def _existing_hosts(config: TargetsConfig) -> set[str]:
    """Lowercased hosts of every configured target, for dedupe on import."""
    hosts: set[str] = set()
    for name in config.list_targets():
        target_config = config.get(name)
        if target_config:
            hosts.add(target_config.get("host", "").lower())
    return hosts


class ProvidersService:
    """Service layer for provider sign-in and database discovery."""

    def __init__(
        self,
        config: TargetsConfig | None = None,
        secret_store: SecretStoreService | None = None,
    ):
        self._config = config
        self._secret_store = secret_store

    def _get_config(self) -> TargetsConfig:
        if self._config is None:
            self._config = TargetsConfig()
            self._config.load()
        return self._config

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
        return {"members": self._shape_preview(members), "errors": discovery_errors}

    def _shape_preview(self, members: list[FleetMember]) -> list[dict]:
        """Serialize discovered members and flag the ones already configured."""
        config = self._get_config()
        existing_hosts = _existing_hosts(config)

        shaped = []
        for member in members:
            public_member = dataclasses.asdict(member)
            public_member.pop("password", None)
            shaped.append({
                **public_member,
                "already_exists": bool(
                    config.get(member.name) or member.host.lower() in existing_hosts
                ),
            })
        return shaped

    async def discover_preview_account(self, provider: str) -> dict:
        """Discover one account provider's databases without importing them."""
        module_name, function_name = ACCOUNT_PROVIDERS[provider]
        discover = getattr(import_module(module_name, __package__), function_name)

        discovery_errors: list[str] = []
        members = await asyncio.to_thread(discover, discovery_errors)
        return {"members": self._shape_preview(members), "errors": discovery_errors}

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
            target_config = member.to_target_config()
            stored_env = store_target_password(
                member.name,
                member.password,
                member.password_env,
                secret_store=self._secret_store,
            )
            if stored_env:
                target_config["password_env"] = stored_env
            config.upsert(member.name, target_config)
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
        password_env: str | None = None,
        password: str | None = None,
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
                target_config = member.to_target_config()
                stored_env = store_target_password(
                    member.name,
                    password,
                    member.password_env,
                    secret_store=self._secret_store,
                )
                if stored_env:
                    target_config["password_env"] = stored_env
                config.upsert(member.name, target_config)
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
