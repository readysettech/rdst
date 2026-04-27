"""Fleet command — multi-target management and fleet-wide operations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from shared.anthropic_env import has_anthropic_api_key
from shared.cli.types import RdstResult
from shared.config.targets import TargetsConfig
from shared.db_connection import close_connection, create_direct_connection
from shared.secret_store_service import SecretStoreService
from shared.ui import ElapsedMessage, Status, get_console

from features.audit.capture_service import CaptureService, _parse_duration
from features.audit.service import AuditService
from features.fleet import (
    FleetAuditSnapshot,
    SnapshotStore,
    build_fleet_insights_prompt,
    detect_aws_credentials,
    discover_rds_instances,
)
from features.fleet.service import FleetService
from shared.json_parse import parse_llm_json


class FleetCommand:
    """Orchestrates fleet subcommands: import, discover, list, status, audit, diff, snapshots."""

    def execute(self, subcommand: str, args: argparse.Namespace) -> RdstResult:
        """Dispatch to the appropriate fleet subcommand."""
        handler = getattr(self, f"_handle_{subcommand.replace('-', '_')}", None)
        if handler is None:
            return RdstResult(False, f"Unknown fleet subcommand: {subcommand}")
        return handler(args)

    # =========================================================================
    # Configure (interactive wizard)
    # =========================================================================

    def _handle_configure(self, args: argparse.Namespace) -> RdstResult:
        """Interactive fleet configuration wizard."""
        csv_file = getattr(args, "csv_file", None)
        discover = getattr(args, "discover", False)

        # If --from provided, delegate to import
        if csv_file:
            return self._handle_import(args)

        # If --discover provided, delegate to discover
        if discover:
            return self._handle_discover(args)

        # Interactive menu
        from shared.ui import Prompt
        console = get_console()
        console.print("\n[bold]Fleet Configuration[/bold]\n")
        console.print("How would you like to add targets?")
        console.print("  [bold][1][/bold] Import from CSV file")
        console.print("  [bold][2][/bold] Discover from AWS (auto-find RDS/Aurora instances)\n")
        console.print("[dim]To add a single target: rdst configure add --target <name> --host <host> ...[/dim]\n")

        try:
            choice = Prompt.ask("Choose", choices=["1", "2"], default="2")
        except (EOFError, KeyboardInterrupt):
            return RdstResult(False, "Cancelled")

        if choice == "1":
            from shared.ui import StyledPanel, DataTable, Text

            # CSV format panel
            csv_example = (
                "name,host,port,database,user,engine,password_env\n"
                "prod-pg,db.example.com,5432,myapp,postgres,postgresql,PROD_DB_PASS\n"
                "prod-mysql,mysql.example.com,3306,myapp,admin,mysql,MYSQL_PASS"
            )
            console.print()
            console.print(StyledPanel(
                f"[bold]Required columns:[/bold] name, host, engine\n"
                f"[bold]Optional columns:[/bold] port, database, user, group, tags, password_env, password_secret_arn\n\n"
                f"[bold]Example:[/bold]\n{csv_example}",
                title="CSV Format",
            ))

            # Password options panel
            console.print(StyledPanel(
                "[bold]Option 1: Environment variable[/bold]\n"
                "Put the env var NAME in the CSV (not the actual password).\n"
                "Then export the password in your shell before running RDST.\n\n"
                "  CSV column:  password_env = PROD_DB_PASS\n"
                "  Your shell:  export PROD_DB_PASS=\"my-actual-password\"\n\n"
                "[bold]Option 2: AWS Secrets Manager[/bold]\n"
                "Put the full Secrets Manager ARN in the CSV.\n"
                "RDST will fetch the password automatically at runtime.\n\n"
                "  CSV column:  password_secret_arn = arn:aws:secretsmanager:us-east-1:123456:secret:my-db-pass",
                title="Password Configuration",
            ))

            # Next step
            console.print(StyledPanel(
                "Create your CSV file, then run:\n\n"
                "  [cyan]rdst fleet configure --from /path/to/fleet.csv[/cyan]",
                title="Next Step",
            ))
            return RdstResult(True, "")

        if choice == "2":
            regions = Prompt.ask("AWS regions (comma-separated)", default="us-east-1")
            if not regions:
                return RdstResult(False, "At least one region required")
            import copy
            discover_args = copy.copy(args)
            discover_args.regions = regions
            return self._handle_discover(discover_args)

        return RdstResult(False, f"Invalid choice: {choice}")

    def _configure_manual(self, args: argparse.Namespace) -> RdstResult:
        """Manually add a fleet target via interactive prompts."""
        from shared.ui import Prompt
        console = get_console()
        default_group = getattr(args, "group", None)

        try:
            console.print("\n[bold]Add Database Target[/bold]\n")
            engine = Prompt.ask("Engine", choices=["postgresql", "mysql"], default="postgresql")

            host = Prompt.ask("Host (e.g. db.example.com)")
            if not host:
                return RdstResult(False, "Host is required")

            default_port = "5432" if engine == "postgresql" else "3306"
            port = int(Prompt.ask("Port", default=default_port))

            default_user = "postgres" if engine == "postgresql" else "admin"
            console.print("[dim]Tip: Use a read-only database user for safety. RDST only runs SELECT queries.[/dim]")
            user = Prompt.ask("Username", default=default_user)
            database = Prompt.ask("Database name")
            if not database:
                return RdstResult(False, "Database name is required")

            group = Prompt.ask("Group (optional)", default=default_group or "", show_default=bool(default_group))
            if not group:
                group = None

            # Generate target name from host
            name = host.split(".")[0].replace("_", "-")
            name = Prompt.ask("Target name", default=name)

            # Password
            console.print(f"\n  How should RDST get the password for [bold]{user}[/bold]?")
            console.print("    [bold][1][/bold] Enter password now")
            console.print("    [bold][2][/bold] AWS Secrets Manager ARN")
            console.print("    [bold][3][/bold] Skip — configure later\n")

            pw_method = Prompt.ask("    Choose", choices=["1", "2", "3"], default="1")

        except (EOFError, KeyboardInterrupt):
            return RdstResult(False, "Cancelled")

        # Save the target
        cfg = TargetsConfig()
        cfg.load()

        env_name = f"{name.upper().replace('-', '_')}_PASS"
        target_config = {
            "engine": engine,
            "host": host,
            "port": port,
            "user": user,
            "database": database,
            "password_env": env_name,
        }
        if group:
            target_config["group"] = group

        if pw_method == "1":
            password = Prompt.ask("    Password", password=True, default="", show_default=False)
            if password:
                stored_in_keyring = self._try_store_in_keyring(env_name, password)
                os.environ[env_name] = password
                if stored_in_keyring:
                    console.print(f"    [green]Password saved to OS keyring (persists across sessions)[/green]")
                else:
                    console.print(f"    [green]Password set for this session via {env_name}[/green]")
                    console.print(f"    [dim]For future sessions: export {env_name}=\"your-password\"[/dim]")
        elif pw_method == "2":
            arn = Prompt.ask("    Secrets Manager ARN", default="", show_default=False)
            if arn:
                target_config["password_secret_arn"] = arn
                console.print(f"    [green]Set to Secrets Manager[/green]")

        cfg.upsert(name, target_config)
        cfg.save()

        console.print(f"\n[green]Target '{name}' added.[/green]")

        # Test connection
        console.print("[dim]Testing connection...[/dim]")
        try:
            tc = cfg.get(name)
            conn = create_direct_connection(tc)
            close_connection(conn, tc.get("engine", "postgresql"))
            console.print(f"[green]Connection successful.[/green]")
        except Exception as e:
            console.print(f"[yellow]Connection test failed: {e}[/yellow]")
            console.print(f"[dim]Target saved. Check credentials and try: rdst fleet status[/dim]")

        # Ask if they want to add another
        try:
            another = Prompt.ask("\nAdd another target?", choices=["y", "n"], default="n")
            if another == "y":
                return self._configure_manual(args)
        except (EOFError, KeyboardInterrupt):
            pass

        return RdstResult(True, f"Target '{name}' configured")

    # =========================================================================
    # Import
    # =========================================================================

    def _handle_import(self, args: argparse.Namespace) -> RdstResult:
        """Import fleet targets from CSV."""
        csv_file = getattr(args, "csv_file", None)
        if not csv_file:
            return RdstResult(False, "CSV file required: rdst fleet import --from fleet.csv")

        password_env = getattr(args, "password_env", "FLEET_PASS")
        group = getattr(args, "group", None)
        tags = getattr(args, "tags", None) or []
        dry_run = getattr(args, "dry_run", False)

        service = FleetService()
        console = get_console()

        result_data = {"imported": 0, "skipped": 0, "errors": 0}

        async def _run():
            async for event in service.import_fleet(
                csv_file=csv_file,
                password_env=password_env,
                default_group=group,
                default_tags=tags,
                dry_run=dry_run,
            ):
                if event.type == "status":
                    console.print(f"[dim]{event.message}[/dim]")
                elif event.type == "import_progress":
                    icon = {"importing": "[green]+[/green]", "skipped": "[yellow]~[/yellow]", "error": "[red]x[/red]"}.get(event.status, " ")
                    console.print(f"  {icon} [{event.current}/{event.total}] {event.message}")
                elif event.type == "error":
                    console.print(f"  [red]Error:[/red] {event.message}")
                elif event.type == "import_complete":
                    result_data["imported"] = event.imported
                    result_data["skipped"] = event.skipped
                    result_data["errors"] = event.errors

        asyncio.run(_run())

        imported = result_data["imported"]
        skipped = result_data["skipped"]
        errors = result_data["errors"]
        prefix = "[dry-run] " if dry_run else ""
        console.print(
            f"\n{prefix}[bold]Import complete:[/bold] {imported} imported, {skipped} skipped, {errors} errors"
        )
        return RdstResult(True, f"{imported} targets imported", data=result_data)

    # =========================================================================
    # List
    # =========================================================================

    def _handle_list(self, args: argparse.Namespace) -> RdstResult:
        """List fleet members."""
        group = getattr(args, "group", None)
        tag = getattr(args, "tag", None)
        output_json = getattr(args, "output_json", False)

        service = FleetService()
        console = get_console()
        members_data: List[dict] = []

        async def _run():
            async for event in service.list_fleet(group=group, tag=tag):
                if event.type == "fleet_list":
                    members_data.extend(event.members)

        asyncio.run(_run())

        if output_json:
            console.print(json.dumps(members_data, indent=2))
            return RdstResult(True, data={"members": members_data})

        if not members_data:
            console.print("[dim]No fleet targets found.[/dim]")
            console.print("[dim]Import targets: rdst fleet import --from fleet.csv[/dim]")
            return RdstResult(True, "No targets")

        from shared.ui import DataTable

        rows = []
        for m in members_data:
            tags_str = ", ".join(m.get("tags") or [])
            rows.append((
                m["name"],
                m["engine"],
                m["host"],
                str(m["port"]),
                m.get("database", ""),
                m.get("group") or "-",
                tags_str or "-",
                m.get("target_type", "database"),
            ))
        table = DataTable(
            title=f"Fleet Targets ({len(members_data)})",
            columns=["Name", "Engine", "Host", "Port", "Database", "Group", "Tags", "Type"],
            rows=rows,
        )
        console.print(table)
        return RdstResult(True, f"{len(members_data)} targets", data={"members": members_data})

    # =========================================================================
    # Status
    # =========================================================================

    def _handle_status(self, args: argparse.Namespace) -> RdstResult:
        """Check fleet connectivity."""
        group = getattr(args, "group", None)
        tag = getattr(args, "tag", None)
        output_json = getattr(args, "output_json", False)

        service = FleetService()
        console = get_console()
        results: List[dict] = []

        async def _run():
            async for event in service.check_status(group=group, tag=tag):
                if event.type == "status":
                    console.print(f"[dim]{event.message}[/dim]")
                elif event.type == "connectivity":
                    if event.status == "checking":
                        continue
                    results.append({
                        "target": event.target_name,
                        "status": event.status,
                        "latency_ms": event.latency_ms,
                        "server_version": event.server_version,
                        "error": event.error,
                    })
                    if event.status == "ok":
                        console.print(
                            f"  [green]\u2713[/green] {event.target_name} "
                            f"[dim]({event.latency_ms:.0f}ms)[/dim]"
                        )
                    else:
                        console.print(
                            f"  [red]\u2717[/red] {event.target_name} "
                            f"[red]{event.error}[/red]"
                        )
                elif event.type == "error":
                    console.print(f"[red]{event.message}[/red]")

        asyncio.run(_run())

        if output_json:
            console.print(json.dumps(results, indent=2))

        ok = sum(1 for r in results if r["status"] == "ok")
        failed = sum(1 for r in results if r["status"] == "failed")
        console.print(f"\n[bold]{ok} ok[/bold], [bold]{failed} failed[/bold]")

        return RdstResult(
            ok=failed == 0,
            message=f"{ok} ok, {failed} failed",
            data={"results": results},
        )

    # =========================================================================
    # Stubs for later phases
    # =========================================================================

    def _handle_discover(self, args: argparse.Namespace) -> RdstResult:
        """Discover RDS instances from AWS."""
        regions_str = getattr(args, "regions", None)
        if not regions_str:
            return RdstResult(False, "Regions required: rdst fleet discover --regions us-east-1,us-west-2")

        regions = [r.strip() for r in regions_str.split(",")]
        engine_filter = getattr(args, "engine_filter", "all")
        if engine_filter == "all":
            engine_filter = None
        name_pattern = getattr(args, "name_pattern", None)
        password_env = getattr(args, "password_env", "FLEET_PASS")
        default_user = getattr(args, "user", None)
        default_group = getattr(args, "group", None)
        dry_run = getattr(args, "dry_run", False)

        # Check AWS credentials first
        has_creds, message = detect_aws_credentials()
        console = get_console()

        if not has_creds:
            console.print(f"[yellow]{message}[/yellow]")
            return RdstResult(False, "AWS credentials required for discovery")

        console.print(f"[dim]Discovering RDS instances in {', '.join(regions)}...[/dim]")

        cfg = TargetsConfig()
        cfg.load()

        # Build a set of existing hostnames for dedup
        existing_hosts = set()
        for tname in cfg.list_targets():
            tc = cfg.get(tname)
            if tc:
                existing_hosts.add(tc.get("host", "").lower())

        imported = 0
        skipped = 0
        discovered_names = []

        try:
            for member in discover_rds_instances(
                regions=regions,
                engine_filter=engine_filter,
                name_pattern=name_pattern,
                password_env=password_env,
                default_user=default_user,
                default_group=default_group,
            ):
                # Check by name OR by hostname
                existing = cfg.get(member.name)
                host_exists = member.host.lower() in existing_hosts
                if existing or host_exists:
                    console.print(f"  [yellow]~[/yellow] {member.name} (already exists)")
                    skipped += 1
                    continue

                # Format Aurora targets with role tag
                role_tag = ""
                if "writer" in member.tags:
                    role_tag = " [bold cyan][writer][/bold cyan]"
                elif "reader" in member.tags:
                    role_tag = " [dim][reader][/dim]"

                if dry_run:
                    console.print(
                        f"  [green]+[/green] {member.name}{role_tag} "
                        f"({member.engine}, {member.instance_class or 'unknown'}) [dry-run]"
                    )
                else:
                    cfg.upsert(member.name, member.to_target_config())
                    console.print(
                        f"  [green]+[/green] {member.name}{role_tag} "
                        f"({member.engine}, {member.instance_class or 'unknown'})"
                    )
                    discovered_names.append(member.name)
                imported += 1

            if not dry_run and imported > 0:
                cfg.save()

        except ImportError as e:
            return RdstResult(False, str(e))
        except Exception as e:
            console.print(f"[red]Discovery error: {e}[/red]")

        prefix = "[dry-run] " if dry_run else ""
        console.print(f"\n{prefix}[bold]{imported} discovered, {skipped} already existed[/bold]")

        # Interactive credential setup for newly discovered instances
        if imported > 0 and not dry_run:
            self._setup_credentials_after_discover(console, cfg, discovered_names)

        return RdstResult(True, f"{imported} discovered")

    def _setup_credentials_after_discover(self, console, cfg, discovered_names: list) -> None:
        """Interactive credential setup for newly discovered instances."""
        from shared.ui import Prompt

        console.print(f"\n[bold]Credential Setup[/bold]")
        console.print(f"Set up passwords for {len(discovered_names)} discovered instance(s).\n")

        console.print("  [bold][1][/bold] All instances share the same password")
        console.print("  [bold][2][/bold] Set credentials for each instance individually")
        console.print("  [bold][3][/bold] Skip — I'll configure passwords later\n")

        try:
            choice = Prompt.ask("Choose", choices=["1", "2", "3"], default="1")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Skipped credential setup.[/dim]")
            return

        # Track env vars that need exporting (not stored in keyring)
        env_vars_needed: dict[str, list[str]] = {}  # env_name -> [target_names]

        if choice == "1":
            # Shared credentials
            try:
                console.print("[dim]Tip: Use a read-only database user for safety. RDST only runs SELECT queries.[/dim]")
                shared_user = Prompt.ask(
                    "Username for all instances (enter to keep AWS defaults)",
                    default="", show_default=False,
                )
                password = Prompt.ask(
                    "Password",
                    password=True,
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]Skipped credential setup.[/dim]")
                return
            env_name = "FLEET_DB_PASS"
            stored_in_keyring = False
            if password:
                stored_in_keyring = self._try_store_in_keyring(env_name, password)
                os.environ[env_name] = password
            for name in discovered_names:
                tc = cfg.get(name)
                if tc:
                    tc["password_env"] = env_name
                    if shared_user:
                        tc["user"] = shared_user
                    cfg.upsert(name, tc)
            cfg.save()
            console.print(f"\n[green]All {len(discovered_names)} targets configured[/green]")
            if stored_in_keyring:
                console.print(f"[green]Password saved to OS keyring (persists across sessions)[/green]")
            else:
                env_vars_needed[env_name] = list(discovered_names)

        elif choice == "2":
            # Per-instance setup
            console.print("[dim]Tip: Use a read-only database user for safety. RDST only runs SELECT queries.[/dim]")
            changed = 0
            for name in discovered_names:
                tc = cfg.get(name)
                if not tc:
                    continue
                engine = tc.get("engine", "?")
                host = tc.get("host", "?")
                user = tc.get("user", "?")
                console.print(f"\n[bold]{name}[/bold] ({engine}, {host})")

                # Username
                try:
                    new_user = Prompt.ask(
                        f"  DB username",
                        default=user,
                    )
                except (EOFError, KeyboardInterrupt):
                    if changed > 0:
                        cfg.save()
                        console.print(f"\n[dim]Stopped. {changed} target(s) saved.[/dim]")
                    else:
                        console.print("\n[dim]Stopped.[/dim]")
                    break
                if new_user != user:
                    tc["user"] = new_user
                    user = new_user

                # Password method
                console.print(f"  How should RDST get the password for [bold]{user}[/bold]?")
                console.print("    [bold][1][/bold] Enter password")
                console.print("    [bold][2][/bold] AWS Secrets Manager ARN")
                console.print("    [bold][3][/bold] Skip — configure later")

                try:
                    method = Prompt.ask("    Choose", choices=["1", "2", "3"], default="1")
                except (EOFError, KeyboardInterrupt):
                    if changed > 0:
                        cfg.save()
                        console.print(f"\n[dim]Stopped. {changed} target(s) saved.[/dim]")
                    else:
                        console.print("\n[dim]Stopped.[/dim]")
                    break

                if method == "1":
                    default_env = f"{name.upper().replace('-', '_')}_PASS"
                    password = Prompt.ask("    Password", password=True, default="", show_default=False)
                    env_name = default_env
                    stored_in_keyring = False
                    if password:
                        stored_in_keyring = self._try_store_in_keyring(env_name, password)
                        os.environ[env_name] = password
                    tc["password_env"] = env_name
                    cfg.upsert(name, tc)
                    changed += 1
                    if stored_in_keyring:
                        console.print(f"    [green]Password saved to OS keyring[/green]")
                    else:
                        env_vars_needed.setdefault(env_name, []).append(name)
                        console.print(f"    [green]Password set for this session[/green]")

                elif method == "2":
                    arn = Prompt.ask("    Secrets Manager ARN", default="", show_default=False)
                    if arn:
                        tc["password_secret_arn"] = arn
                        cfg.upsert(name, tc)
                        changed += 1
                        arn_display = f"{arn[:50]}..." if len(arn) > 50 else arn
                        console.print(f"    [green]Set to Secrets Manager: {arn_display}[/green]")
                    else:
                        console.print("    [yellow]Skipped (no ARN provided)[/yellow]")

                else:
                    cfg.upsert(name, tc)  # Still save username change if any
                    if new_user != tc.get("user", user):
                        changed += 1
                    console.print("    [dim]Skipped password[/dim]")

            if changed > 0:
                cfg.save()
                console.print(f"\n[green]{changed} target(s) updated[/green]")

        else:
            console.print("[dim]Skipped credential setup. Configure later with: rdst configure edit <target>[/dim]")

        # Print env var summary for targets not using keyring
        if env_vars_needed:
            console.print(f"\n[bold]Environment Variables Required[/bold]")
            console.print("[dim]Add these to your shell profile to persist across sessions:[/dim]\n")
            for env_name, targets in env_vars_needed.items():
                target_list = ", ".join(targets)
                console.print(f'  export {env_name}="your-password"  [dim]# {target_list}[/dim]')
            console.print()

        # Next steps breadcrumbs
        groups = set()
        for name in discovered_names:
            tc = cfg.get(name)
            if tc and tc.get("group"):
                groups.add(tc["group"])

        console.print(f"\n[bold]Next Steps[/bold]")
        console.print(f"  [dim]1. Verify connectivity:[/dim]  [cyan]rdst fleet status[/cyan]")
        if groups:
            sorted_groups = sorted(groups)
            for i, group in enumerate(sorted_groups):
                step = i + 2
                console.print(f"  [dim]{step}. Audit {group}:[/dim]{'  ' if len(group) < 16 else ' '}[cyan]rdst fleet audit --group {group} --duration 2m[/cyan]")
        else:
            console.print(f"  [dim]2. Run audit:[/dim]           [cyan]rdst audit --target {discovered_names[0]} --duration 2m[/cyan]")

    @staticmethod
    def _try_store_in_keyring(key_name: str, password: str) -> bool:
        """Try to store password in OS keyring. Returns True if successful."""
        try:
            store = SecretStoreService()
            if not store.is_available():
                return False
            store.set_secret(key_name, password)
            # Verify it stuck
            stored = store.get_secret(key_name)
            return stored == password
        except Exception:
            return False

    def _preflight_cache_check(self, console, cfg, targets, auto_yes: bool = False) -> bool:
        """Check if Readyset caches exist for fleet targets. Prompt once to deploy if needed.

        Returns True if caches are available (existing or newly deployed).
        """
        import shutil
        import sys

        if not shutil.which("docker"):
            console.print("[dim]  Skipping Readyset cache testing (Docker not installed)[/dim]")
            return False
        if not auto_yes and not sys.stdin.isatty():
            return False

        try:
            from features.cache.service import CacheService
            from features.cache.models import CacheInput, CacheOptions
            import asyncio
        except ImportError:
            return False

        service = CacheService()

        # Check which targets need caches deployed or restarted
        targets_needing_deploy = []
        targets_needing_restart = []
        targets_running = []
        for target_name in targets:
            cache_target = service._resolve_cache_target(target_name)
            if not cache_target:
                targets_needing_deploy.append(target_name)
                continue
            # Config exists — check if container is actually running
            _cache_name, cache_config = cache_target
            container = cache_config.get("container_name", f"rdst-readyset-{target_name}")
            try:
                import subprocess
                result = subprocess.run(
                    ["docker", "inspect", "-f", "{{.State.Running}}", container],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and "true" in result.stdout.lower():
                    targets_running.append(target_name)
                else:
                    targets_needing_restart.append((target_name, container))
            except Exception:
                targets_needing_restart.append((target_name, container))

        if not targets_needing_deploy and not targets_needing_restart:
            return True  # All targets have running caches

        # Build prompt message
        from shared.ui import Prompt
        actions = []
        if targets_needing_deploy:
            actions.append(f"deploy {len(targets_needing_deploy)}")
        if targets_needing_restart:
            actions.append(f"restart {len(targets_needing_restart)}")
        action_str = " and ".join(actions)

        console.print()
        if auto_yes:
            deploy = "y"
            console.print(f"[dim]  Auto-deploying Readyset cache(s) for benchmarking (-y)[/dim]")
        else:
            console.print(
                "[dim]  Readyset can benchmark your captured queries to show how much"
                "\n  faster they'd run with caching. This deploys a local Docker"
                "\n  container per target that proxies your database.[/dim]"
            )
            try:
                deploy = Prompt.ask(
                    f"  {action_str.capitalize()} Readyset cache(s) to test query caching?",
                    choices=["y", "n"], default="y"
                )
            except (EOFError, KeyboardInterrupt):
                deploy = "n"

        if deploy.lower() != "y":
            return len(targets_running) > 0

        ready = len(targets_running)

        # Restart stopped containers (fall back to fresh deploy if removed)
        import subprocess
        for target_name, container in targets_needing_restart:
            console.print(f"[dim]  Starting {container}...[/dim]")
            start_result = subprocess.run(
                ["docker", "start", container], capture_output=True, text=True, timeout=30,
            )
            if start_result.returncode == 0:
                ready += 1
            else:
                # Container was removed — deploy fresh
                console.print(f"[dim]  Container not found, deploying fresh for {target_name}...[/dim]")
                targets_needing_deploy.append(target_name)

        # Deploy new containers
        for target_name in targets_needing_deploy:
            console.print(f"[dim]  Deploying Readyset for {target_name}...[/dim]")
            try:
                async def _deploy(tn=target_name):
                    async for event in service.deploy(
                        CacheInput(target=tn),
                        CacheOptions(mode="docker"),
                    ):
                        if event.type == "deploy_complete":
                            return event.success
                    return False

                if asyncio.run(_deploy()):
                    ready += 1
                    console.print(f"[green]  Readyset deployed for {target_name}[/green]")
                else:
                    console.print(f"[yellow]  Failed to deploy for {target_name}[/yellow]")
            except Exception as e:
                console.print(f"[yellow]  Deploy failed for {target_name}: {e}[/yellow]")

        # Wait for RS instances to initialize
        if targets_needing_restart or targets_needing_deploy:
            import time as _t
            _t.sleep(3)

        return ready > 0

    # =========================================================================
    # Fleet Summary Mode (default UX)
    # =========================================================================

    def _render_fleet_summary(self, console, successful, failed_results, fleet_insights_data):
        """Render slim fleet summary — the default fleet audit output."""
        total = len(successful) + len(failed_results)
        console.print(f"\n  [bold]Fleet Audit[/bold] ({total} targets)\n  {'─' * 50}")

        # Compact target table
        for r in successful:
            sizing = (r.get("sizing") or {}).get("verdict", "?").replace("_", " ").upper()
            cache = (r.get("cache_opportunity") or {}).get("score", 0)
            name = r.get("target_name", "?")
            engine = r.get("engine", "?")[:5]
            console.print(f"  [green]OK[/green]  {name:<30} {engine:<6} {sizing:<16} cache={cache}/100")

        for r in failed_results:
            name = r.get("target_name", "?")
            console.print(f"  [red]FAIL[/red] {name:<30} {r.get('error', 'unknown')[:40]}")

        # Fleet insights — new shape (health_score, executive_summary, top_findings, next_steps)
        if fleet_insights_data and isinstance(fleet_insights_data, dict):
            score = fleet_insights_data.get("health_score")
            label = (fleet_insights_data.get("health_label") or "").upper()
            if score is not None:
                try:
                    s = int(score)
                    col = "green" if s >= 75 else ("cyan" if s >= 60 else ("yellow" if s >= 40 else "red"))
                    console.print(f"\n  [bold]Health Score:[/bold] [{col}]{s}/100 {label}[/{col}]")
                except (TypeError, ValueError):
                    pass
            summary = fleet_insights_data.get("executive_summary")
            if summary:
                console.print(f"  {summary}")
            tops = fleet_insights_data.get("top_findings") or []
            if tops:
                sev_color = {"crit": "red", "warn": "yellow", "info": "cyan", "ok": "green"}
                console.print()
                console.print(f"  [bold]Top Findings[/bold]")
                for f in tops[:3]:
                    sev = (f.get("severity") or "info").lower()
                    col = sev_color.get(sev, "dim")
                    console.print(f"    [{col}]●[/{col}] [bold]{f.get('title','')}[/bold] — {f.get('body','')}")

        console.print()

    def _generate_fleet_html_report(self, all_results, fleet_insights_data, group_name):
        """Generate unified 3-section fleet HTML report."""
        try:
            from dataclasses import asdict, is_dataclass
            from features.audit.report.report import render_report_html

            results = []
            for r in all_results:
                results.append(asdict(r) if is_dataclass(r) else r)
            return render_report_html(
                results,
                fleet_insights=fleet_insights_data,
                title=group_name or "fleet",
            )
        except Exception:
            return None

    @staticmethod
    def _save_report_locally(snapshot_id, html_content):
        """Save HTML report to ~/.rdst/reports/."""
        try:
            from pathlib import Path
            reports_dir = Path.home() / ".rdst" / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            report_path = reports_dir / f"{snapshot_id}.html"
            report_path.write_text(html_content)
        except Exception:
            pass

    def _email_fleet_report(self, console, html_content, group_name):
        """Prompt for email confirmation and send the fleet report via hosted link."""
        import re
        from shared.config.targets import TargetsConfig
        from shared.ui import Prompt, Confirm

        cfg = TargetsConfig()
        cfg.load()
        primary_email = cfg.get_email()
        _email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

        recipient = None
        if primary_email and _email_re.match(primary_email):
            console.print(f"\n  Sending report to [bold]{primary_email}[/bold]")
            try:
                alt = (Prompt.ask(
                    "  Press Enter to continue or type a different email",
                    default="", show_default=False,
                ) or "").strip()
            except (EOFError, KeyboardInterrupt):
                console.print(f"\n  [dim]Skipped. View locally: rdst fleet snapshots[/dim]")
                return
            if alt:
                if _email_re.match(alt):
                    recipient = alt
                else:
                    console.print("  [yellow]Invalid email address. Sending to the original.[/yellow]")
                    recipient = primary_email
            else:
                recipient = primary_email
        else:
            console.print(
                "\n  Enter your email to receive the full fleet report."
                "\n  Press Ctrl+C to skip.\n"
            )
            while True:
                try:
                    typed = (Prompt.ask("  Email", default="", show_default=False) or "").strip()
                except (EOFError, KeyboardInterrupt):
                    typed = ""
                    break
                if not typed:
                    break
                if _email_re.match(typed):
                    recipient = typed
                    break
                console.print("  [yellow]Invalid email address. Try again or press Ctrl+C to skip.[/yellow]")

            if not recipient:
                console.print(f"  [dim]Skipped. View locally: rdst fleet snapshots[/dim]")
                return

        report_token = cfg.get_token_for_email(recipient)
        if not cfg.get_email():
            cfg.set_email(recipient)
            cfg.save()

        try:
            from features.audit.email_service import EmailService
            svc = EmailService()
            subject = f"RDST Fleet Audit Report: {group_name}"

            send_spinner = Status(
                ElapsedMessage("Sending report...", time.monotonic()),
                spinner="dots",
                console=console,
            )
            send_spinner.start()

            spinner = None

            def _on_status(msg):
                nonlocal spinner
                spinner = Status(
                    ElapsedMessage(msg, time.monotonic()),
                    spinner="dots",
                    console=console,
                )
                spinner.start()

            result = svc.send_report_with_verification(
                email=recipient,
                html_body=html_content,
                subject=subject,
                report_token=report_token,
                on_status=_on_status,
                mode="link",
            )

            send_spinner.stop()
            if spinner:
                spinner.stop()

            if result.get("stale_token"):
                cfg.remove_email(recipient)
                cfg.save()

            if result.get("success"):
                new_token = result.get("report_token")
                if new_token:
                    cfg.add_verified_email(recipient, new_token)
                current_primary = cfg.get_email()
                if recipient and recipient != current_primary and current_primary:
                    try:
                        make_default = Confirm.ask(
                            f"  Make [bold]{recipient}[/bold] your default for future audits?",
                            default=False,
                        )
                    except (EOFError, KeyboardInterrupt):
                        make_default = False
                    if make_default:
                        cfg.set_primary_email(recipient)
                cfg.save()
                if result.get("queued"):
                    console.print(
                        f"  [green]Verification email sent to {recipient}[/green]\n"
                        f"  [dim]Click the link in your inbox — your report will arrive automatically.[/dim]"
                    )
                else:
                    console.print(f"  [green]Report link sent to {recipient}[/green]")
            else:
                error = result.get("error", "unknown error")
                console.print(f"  [yellow]{error}[/yellow]")
                console.print(f"  [dim]View locally: rdst fleet snapshots[/dim]")
        except Exception as e:
            console.print(f"  [yellow]Email delivery failed: {e}[/yellow]")
            console.print(f"  [dim]View locally: rdst fleet snapshots[/dim]")

    @staticmethod
    def _track_fleet_audit_report(all_results, fleet_insights_data):
        """Send PostHog event for fleet audit report completion."""
        try:
            from shared.telemetry import telemetry
            from shared.config.targets import TargetsConfig
            from features.audit.report.report import compute_fleet_savings

            cfg = TargetsConfig()
            cfg.load()
            email = cfg.get_email() or "unknown"

            successful = [r for r in all_results if not r.get("error")]
            fs = compute_fleet_savings(successful)
            fi = fleet_insights_data or {}

            properties = {
                "email": email,
                "total_targets": len(all_results),
                "successful_targets": len(successful),
                "failed_targets": len(all_results) - len(successful),
                "fleet_health_score": fi.get("health_score"),
                "fleet_health_label": fi.get("health_label"),
                "total_current_cost_usd": fs.get("total_current_usd"),
                "total_savings_usd": fs.get("total_savings_usd"),
                "pct_reduction": fs.get("pct_reduction"),
                "has_fleet_insights": bool(fi.get("executive_summary")),
                "report_delivery": "link",
                "flow_stage": "advanced",
            }
            # Always fire to PostHog for analytics
            telemetry.track("fleet_audit_report_generated", properties)

            # Fire first_fleet_audit only once per device (triggers Slack).
            stats = telemetry._get_stats()
            if not stats.get("first_fleet_audit_fired"):
                slack_props = {
                    **properties,
                    "display_name": f"First Fleet Audit: {len(successful)} targets",
                    "auth_type": "own_key",
                    "source": "fleet_audit",
                    "email_tier": "—",
                }
                telemetry.track("first_fleet_audit", slack_props)
                telemetry._increment_stat("first_fleet_audit_fired", 1)
        except Exception:
            pass

    def _render_fleet_insights(self, console, raw_text: str) -> None:
        """Render fleet LLM JSON output to the terminal.

        Matches the new fleet prompt shape: health_score, health_label,
        score_rationale, executive_summary, top_findings, fleet_findings,
        next_steps. Each next_step may include `commands[]` and
        `estimated_savings_usd` — those render inline so the user can copy/run.
        """
        from shared.ui import StyledPanel

        try:
            data = parse_llm_json(raw_text)
        except Exception as exc:
            console.print(f"\n[red]Failed to parse fleet insights JSON: {exc}[/red]")
            console.print(f"[dim]Raw response (first 200 chars): {raw_text[:200]}[/dim]")
            return
        if not isinstance(data, dict):
            return

        score = data.get("health_score")
        label = (data.get("health_label") or "").upper()
        rationale = data.get("score_rationale") or ""
        if score is not None:
            try:
                s = int(score)
            except (TypeError, ValueError):
                s = None
            if s is not None:
                col = "green" if s >= 75 else ("cyan" if s >= 60 else ("yellow" if s >= 40 else "red"))
                console.print()
                console.print(f"[bold]Fleet Health:[/bold] [{col}]{s}/100 {label}[/{col}]")
                if rationale:
                    console.print(f"  [dim]{rationale}[/dim]")

        summary = data.get("executive_summary") or ""
        if summary:
            console.print(StyledPanel(summary, title="Fleet Summary"))

        sev_color = {"crit": "red", "warn": "yellow", "info": "cyan", "ok": "green"}

        top_findings = data.get("top_findings") or []
        if top_findings:
            console.print()
            console.print("[bold]Top Findings[/bold]")
            for f in top_findings[:3]:
                sev = (f.get("severity") or "info").lower()
                col = sev_color.get(sev, "dim")
                title = f.get("title") or ""
                body = f.get("body") or ""
                console.print(f"  [{col}]●[/{col}] [bold]{title}[/bold]")
                if body:
                    console.print(f"    [dim]{body}[/dim]")

        fleet_findings = data.get("fleet_findings") or data.get("findings") or []
        if fleet_findings:
            console.print()
            console.print("[bold]Findings[/bold]")
            for f in fleet_findings:
                sev = (f.get("severity") or "info").lower()
                col = sev_color.get(sev, "dim")
                title = f.get("title") or ""
                body = f.get("body") or ""
                console.print(f"  [{col}]●[/{col}] [bold]{title}[/bold]")
                if body:
                    console.print(f"    {body}")

        next_steps = data.get("next_steps") or data.get("recommended_actions") or []
        if next_steps:
            console.print()
            console.print("[bold]Next Steps[/bold]")
            for i, step in enumerate(next_steps, 1):
                rank = step.get("rank") or i
                title = step.get("title") or ""
                body = step.get("body") or ""
                console.print(f"  [bold cyan]{rank}.[/bold cyan] [bold]{title}[/bold]")
                if body:
                    console.print(f"    {body}")
                for cmd in (step.get("commands") or [])[:4]:
                    console.print(f"    [white on grey15] {cmd} [/white on grey15]")
                save = step.get("estimated_savings_usd")
                if save:
                    try:
                        sv = float(save)
                        if sv > 0:
                            console.print(f"    [green]Estimated savings: ${sv:,.0f}/mo[/green]")
                    except (TypeError, ValueError):
                        pass

    # =========================================================================
    # Fleet Audit
    # =========================================================================

    def _handle_audit(self, args: argparse.Namespace) -> RdstResult:
        """Run audit across fleet targets concurrently."""
        group = getattr(args, "group", None)
        tag = getattr(args, "tag", None)
        save_name = getattr(args, "save_name", None)
        no_save = getattr(args, "no_save", False)
        output_json = getattr(args, "output_json", False)
        insights = not getattr(args, "no_insights", False)
        duration_str = getattr(args, "duration", None)
        verbose = getattr(args, "verbose", False)
        auto_yes = getattr(args, "auto_yes", False)
        # --verbose is the only way to opt out of email — terminal-only mode.
        no_email = verbose

        # Auto-generate save name unless --no-save
        if not save_name and not no_save:
            import datetime as _dt
            save_name = f"fleet_{_dt.datetime.now():%Y%m%d_%H%M%S}"
        cfg = TargetsConfig()
        cfg.load()

        # Fleet = all database targets (excludes Readyset caches), filtered by group/tag
        targets = cfg.list_fleet_targets(group=group, tag=tag)
        if not targets:
            return RdstResult(False, "No targets found")

        service = AuditService(config=cfg)
        console = get_console()

        if insights:
            if not has_anthropic_api_key():
                console.print(
                    "[yellow]No LLM API key configured. The fleet audit report requires AI analysis.[/yellow]\n"
                    "[dim]Set up your LLM provider now:[/dim]\n"
                )
                try:
                    from features.configure.cli.wizard import ConfigurationWizard
                    wizard = ConfigurationWizard()
                    from shared.config.targets import TargetsConfig as _TC
                    _cfg = _TC(); _cfg.load()
                    wizard.configure_llm(_cfg, {})
                    _cfg.save()
                    if not has_anthropic_api_key():
                        return RdstResult(False, "LLM key still not set. Run: rdst configure llm")
                except (EOFError, KeyboardInterrupt):
                    return RdstResult(False, "LLM setup cancelled. Run: rdst configure llm")
                except Exception as e:
                    return RdstResult(
                        False,
                        f"Could not launch LLM setup: {e}\n"
                        "Run manually: rdst configure llm",
                    )
            try:
                from shared.llm_manager import LLMManager
                llm = LLMManager()
                llm.generate_response("Say OK", max_tokens=1, temperature=0.0)
            except Exception as e:
                return RdstResult(
                    False,
                    f"ANTHROPIC_API_KEY is invalid or the API is unreachable: {e}\n"
                    "Fix the key and re-run.",
                )
        all_results: List[dict] = []

        # Parse duration if provided
        duration_seconds = None
        if duration_str:
            duration_seconds = _parse_duration(duration_str)

        # Pre-flight: connectivity check — skip targets we can't reach
        if not output_json:
            console.print(f"[dim]Checking connectivity for {len(targets)} targets...[/dim]")
        reachable = []
        unreachable = []
        for target_name in targets:
            tc = cfg.get(target_name)
            if not tc:
                unreachable.append((target_name, "Target config not found"))
                continue
            try:
                from shared.db_connection import create_direct_connection
                conn = create_direct_connection(tc, connect_timeout=5)
                conn.close()
                reachable.append(target_name)
                if not output_json:
                    console.print(f"  [green]✓[/green] {target_name}")
            except Exception as e:
                err = str(e)
                if "Password not available" in err:
                    pw_env = tc.get("password_env", "?")
                    reason = f"{pw_env} not set"
                elif "Access denied" in err or "Authentication failed" in err:
                    reason = "wrong password or username"
                else:
                    reason = err[:80]
                unreachable.append((target_name, reason))
                if not output_json:
                    console.print(f"  [red]✗[/red] {target_name}: {reason}")

        if unreachable and not output_json:
            console.print(f"\n[yellow]{len(unreachable)} target(s) unreachable — skipping them.[/yellow]")
        if not reachable:
            return RdstResult(False, "No targets reachable. Check passwords and connectivity.")
        targets = reachable

        # Pre-flight display
        n = len(targets)
        if not output_json:
            estimate = "quick audit" if not duration_seconds else f"~{duration_seconds}s capture per target"
            console.print(f"\n[bold]Auditing {n} targets...[/bold] ({estimate})")

        # Pre-flight: check/deploy Readyset caches for all targets (one prompt)
        caches_available = False
        if duration_seconds and not output_json:
            caches_available = self._preflight_cache_check(console, cfg, targets, auto_yes=auto_yes)

        # Run audits concurrently
        max_workers = min(n, 10)
        from shared.ui import Progress, SpinnerColumn, TextColumn, BarColumn, MofNCompleteColumn

        # Shared status dict for threads to report progress
        import threading
        target_status = {}
        status_lock = threading.Lock()

        def _update_status(name: str, msg: str):
            with status_lock:
                target_status[name] = msg

        def _audit_one_with_status(target_name: str) -> dict:
            _update_status(target_name, "collecting metrics...")
            tc = cfg.get(target_name)
            if not tc:
                return {"target_name": target_name, "error": f"Target '{target_name}' not found"}
            try:
                result = service.audit_target(target_name, tc)
            except Exception as e:
                return {"target_name": target_name, "error": str(e)}

            if isinstance(result, dict):
                result_dict = result
                result_dict.setdefault("target_name", target_name)
            else:
                from dataclasses import asdict
                try:
                    result_dict = asdict(result)
                except TypeError:
                    result_dict = result.__dict__ if hasattr(result, '__dict__') else {"target_name": target_name}

            if duration_seconds:
                _update_status(target_name, f"capturing queries ({duration_seconds}s)...")
                try:
                    ws = CaptureService()
                    workload_result = {}
                    cumulative_tq = result_dict.get("top_queries") or []

                    async def _capture():
                        nonlocal workload_result
                        async for event in ws.run_capture(
                            target_name=target_name,
                            duration_seconds=duration_seconds,
                            snapshot_only=False,
                            source="auto",
                            limit=50,
                            run_analysis=insights,
                            cumulative_top_queries=cumulative_tq,
                            audit_result=result_dict,
                            save_capture=False,
                        ):
                            if event.type == "capture_progress":
                                elapsed = getattr(event, 'elapsed_seconds', 0)
                                _update_status(target_name, f"capturing... {elapsed:.0f}s/{duration_seconds}s")
                            elif event.type == "status" and getattr(event, 'phase', '') == "analysis":
                                _update_status(target_name, "running final analysis...")
                            elif event.type == "complete":
                                workload_result = event.summary or {}
                                if hasattr(event, 'analysis') and event.analysis:
                                    workload_result["analysis"] = event.analysis

                    asyncio.run(_capture())
                    if workload_result:
                        result_dict["workload"] = workload_result
                except Exception as e:
                    result_dict["workload_error"] = str(e)
            else:
                _update_status(target_name, "done")

            return result_dict

        if output_json:
            # No progress display for JSON mode
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_audit_one_with_status, t): t for t in targets}
                for future in as_completed(futures):
                    result = future.result()
                    all_results.append(result)
        else:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TextColumn("[dim]{task.fields[status]}[/dim]"),
                console=console,
            ) as progress:
                overall_task = progress.add_task(
                    "Fleet audit", total=n, status="starting..."
                )
                target_tasks = {}
                for t in targets:
                    tid = progress.add_task(f"  {t}", total=1, status="waiting")
                    target_tasks[t] = tid

                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {}
                    for t in targets:
                        progress.update(target_tasks[t], status="starting...")
                        futures[executor.submit(_audit_one_with_status, t)] = t

                    # Poll for status updates while waiting for completions
                    import time as _time
                    completed_targets = set()
                    while len(completed_targets) < n:
                        # Update in-progress target statuses
                        with status_lock:
                            for t, msg in target_status.items():
                                if t not in completed_targets:
                                    progress.update(target_tasks[t], status=msg)

                        # Check for completed futures (non-blocking)
                        done = [f for f in futures if f.done() and futures[f] not in completed_targets]
                        for future in done:
                            target_name = futures[future]
                            completed_targets.add(target_name)
                            result = future.result()
                            all_results.append(result)

                            if result.get("error"):
                                progress.update(target_tasks[target_name], completed=1, status=f"[red]ERROR[/red]")
                            else:
                                sizing = (result.get("sizing") or {}).get("verdict", "unknown")
                                cache = (result.get("cache_opportunity") or {}).get("score", 0)
                                verdict_colors = {
                                    "under_provisioned": "red",
                                    "oversized": "yellow",
                                    "right_sized": "green",
                                }
                                vc = verdict_colors.get(sizing, "dim")
                                progress.update(
                                    target_tasks[target_name],
                                    completed=1,
                                    status=f"[{vc}]{sizing.replace('_', ' ')}[/{vc}] | cache {cache}/100",
                                )
                            progress.update(overall_task, advance=1, status=f"{len(all_results)}/{n} complete")

                        if len(completed_targets) < n:
                            _time.sleep(0.5)

        # Run Readyset cache benchmarking on captured queries (sequential)
        if caches_available and duration_seconds:
            try:
                from features.audit.cli.command import AuditCommand
                audit_cmd = AuditCommand()
                for r in all_results:
                    if r.get("error"):
                        continue
                    wl = r.get("workload") or {}
                    queries = wl.get("queries") or []
                    if not queries:
                        continue
                    target_name = r.get("target_name", "")
                    if not output_json:
                        console.print(f"[dim]  Testing {target_name} queries against Readyset...[/dim]")
                    rs_results = audit_cmd._run_readyset_testing(
                        console, target_name, queries, max_queries=20,
                    )
                    if rs_results:
                        r["readyset_results"] = rs_results
            except Exception:
                pass

        # Save snapshots
        individual_ids = {}
        if save_name and all_results:
            import datetime as _dt
            from dataclasses import asdict

            store = SnapshotStore()

            # Save individual per-target snapshots (skip failed ones)
            individual_ids = {}
            for r in all_results:
                if r.get("error"):
                    continue
                target_name = r.get("target_name", "unknown")
                ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
                snap_id = f"audit_{target_name}_{ts}"
                store.save_raw(snap_id, r)
                individual_ids[target_name] = snap_id

            # Save fleet-level combined snapshot. fleet_insights gets attached
            # after the LLM call below — we re-save then. This pre-save covers
            # the case where the LLM call fails or is disabled.
            snapshot = FleetAuditSnapshot(
                snapshot_id=save_name,
                name=save_name,
                created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
                targets_audited=len(all_results),
                targets_failed=sum(1 for r in all_results if r.get("error")),
                results=all_results,
            )
            path = store.save_raw(save_name, asdict(snapshot))
            self._pending_fleet_snapshot = snapshot
            self._pending_fleet_save_name = save_name
            self._pending_fleet_store = store
            if not output_json:
                console.print(f"\n[dim]Snapshot saved. View later: rdst fleet snapshots[/dim]")

        # Compute successful/failed once, reuse everywhere
        successful = [r for r in all_results if not r.get("error")]
        failed_results = [r for r in all_results if r.get("error")]

        # LLM Fleet Insights (only if there are successful results)
        fleet_insights_text = None
        fleet_spinner = None
        if insights and successful:
            if not output_json:
                fleet_spinner = Status(
                    ElapsedMessage("Generating fleet insights...", time.monotonic()),
                    spinner="dots",
                    console=console,
                )
                fleet_spinner.start()
            try:
                from shared.llm_manager import LLMManager

                prompt = build_fleet_insights_prompt(successful)
                llm = LLMManager()
                result = llm.generate_response(prompt, max_tokens=4096, temperature=0.0)
                if fleet_spinner:
                    fleet_spinner.stop()
                fleet_insights_text = result.get("response", "")
            except Exception as e:
                if fleet_spinner:
                    fleet_spinner.stop()
                if not output_json:
                    console.print(f"[red]LLM insights failed: {e}[/red]")

        if output_json:
            output_data = {"results": all_results}
            if fleet_insights_text:
                output_data["insights"] = fleet_insights_text
            print(json.dumps(output_data, indent=2, default=str))
            return RdstResult(True, message="")

        # Parse fleet insights early so compact summary can use them
        fleet_insights_data: dict | None = None
        if fleet_insights_text:
            try:
                fleet_insights_data = parse_llm_json(fleet_insights_text) or {}
            except Exception:
                fleet_insights_data = {"raw": fleet_insights_text}
            try:
                pending = getattr(self, "_pending_fleet_snapshot", None)
                if pending and fleet_insights_data:
                    pending.fleet_insights = fleet_insights_data
                    self._pending_fleet_store.save_raw(self._pending_fleet_save_name, asdict(pending))
            except Exception:
                pass

        ok = len(successful)
        if failed_results:
            console.print(f"\n[bold]{ok} audited[/bold], [red]{len(failed_results)} failed:[/red]")
            for r in failed_results:
                error_msg = r.get("error", "unknown error")
                # Make common errors more actionable
                if "Password not available" in error_msg:
                    target_name = r.get("target_name", "?")
                    target_cfg = cfg.get(target_name) or {}
                    pw_env = target_cfg.get("password_env", "FLEET_DB_PASS")
                    console.print(f"  [red]{target_name}[/red]: password not set — export {pw_env}=\"your-password\"")
                elif "Authentication failed" in error_msg:
                    console.print(f"  [red]{r.get('target_name', '?')}[/red]: wrong password or username")
                elif "Unable to locate credentials" in error_msg:
                    target_name = r.get("target_name", "?")
                    target_cfg = cfg.get(target_name) or {}
                    if target_cfg.get("password_secret_arn"):
                        console.print(f"  [red]{target_name}[/red]: AWS credentials not found — run: aws sso login")
                    else:
                        pw_env = target_cfg.get("password_env", "FLEET_DB_PASS")
                        console.print(f"  [red]{target_name}[/red]: password not set — export {pw_env}=\"your-password\"")
                else:
                    console.print(f"  [red]{r.get('target_name', '?')}[/red]: {error_msg[:80]}")
        else:
            console.print(f"\n[bold]{ok} audited[/bold]")

        # Verbose: full fleet terminal report. Default: compact summary only
        # (the full report goes via email).
        if verbose:
            # Full fleet overview table + per-target panels + fleet insights
            if successful:
                from shared.ui import DataTable, StyledPanel

                metric_rows = []
                for r in successful:
                    m = r.get("metrics") or {}
                    sizing = (r.get("sizing") or {}).get("verdict", "?")
                    cache = (r.get("cache_opportunity") or {}).get("score", 0)
                    size_str = f"{m.get('database_size_mb', 0):.0f} MB"
                    conn_str = f"{m.get('active_connections', 0)}/{m.get('max_connections', 0)} ({m.get('connection_utilization_pct', 0):.0f}%)"
                    rw = f"{m.get('read_pct', 0):.0f}R / {m.get('write_pct', 0):.0f}W"
                    metric_rows.append((
                        r.get("target_name", "?"),
                        r.get("engine", "?")[:5],
                        m.get("server_version", "?")[:20],
                        size_str,
                        conn_str,
                        f"{m.get('cache_hit_rate', 0):.0f}%",
                        rw,
                        str(m.get("tracked_query_count", 0)),
                    ))
                console.print(DataTable(
                    title="Fleet Overview",
                    columns=["Target", "Engine", "Version", "Size", "Connections", "Cache Hit", "R/W", "Total Queries"],
                    rows=metric_rows,
                ))

                for r in successful:
                    m = r.get("metrics") or {}
                    sizing = r.get("sizing") or {}
                    cache = r.get("cache_opportunity") or {}
                    tq = r.get("top_queries") or []
                    verdict = sizing.get("verdict", "unknown").replace("_", " ")
                    cache_score = cache.get("score", 0)
                    lines = f"[bold]Sizing:[/bold] {verdict}"
                    if sizing.get("explanation"):
                        lines += f" — {sizing['explanation']}"
                    lines += f"\n[bold]Cache Opportunity:[/bold] {cache_score}/100"
                    if tq:
                        lines += f"\n[bold]Top Queries:[/bold] {len(tq)} tracked"
                        for q in tq[:3]:
                            sql = (q.get("normalized_query") or "")[:60]
                            lines += f"\n  [{q.get('query_hash', '')[:8]}] calls={q.get('calls', 0)}, avg={q.get('avg_time_ms', 0):.1f}ms — {sql}"
                    console.print(StyledPanel(lines, title=f"{r.get('target_name', '?')} ({r.get('engine', '?')})"))

            if fleet_insights_text:
                self._render_fleet_insights(console, fleet_insights_text)
        else:
            # Default: compact summary — one line per target
            self._render_fleet_summary(console, successful, failed_results, fleet_insights_data)

        # Show fleet + individual report links
        console.print()
        if save_name:
            console.print(f"  [bold]Fleet Report[/bold]")
            console.print(f"    [cyan]rdst fleet audit show {save_name}[/cyan]")
        if successful:
            console.print(f"  [bold]Individual Target Reports[/bold]")
            for r in successful:
                target = r.get("target_name", "?")
                snap_id = individual_ids.get(target)
                if snap_id:
                    console.print(f"    {target}: [cyan]rdst audit show {snap_id}[/cyan]")

        # Default flow: generate fleet HTML + email. Skipped under --verbose.
        if successful and not verbose:
            html_content = self._generate_fleet_html_report(
                successful, fleet_insights_data, group or "fleet",
            )
            if html_content:
                snapshot_id = save_name or "fleet_audit"
                self._save_report_locally(snapshot_id, html_content)
                try:
                    self._email_fleet_report(console, html_content, group or "fleet")
                except Exception:
                    pass
                try:
                    self._track_fleet_audit_report(successful, fleet_insights_data)
                except Exception:
                    pass
        elif successful:
            try:
                self._track_fleet_audit_report(successful, fleet_insights_data)
            except Exception:
                pass

        return RdstResult(True, message="")

    # =========================================================================
    # Diff
    # =========================================================================

    def _handle_diff(self, args: argparse.Namespace) -> RdstResult:
        """Compare two fleet audit snapshots."""
        snap1 = getattr(args, "snapshot1", None)
        snap2 = getattr(args, "snapshot2", None)
        output_json = getattr(args, "output_json", False)

        if not snap1 or not snap2:
            return RdstResult(False, "Two snapshot IDs required: rdst fleet diff <snap1> <snap2>")

        from dataclasses import asdict

        store = SnapshotStore()
        diff = store.diff(snap1, snap2)

        if diff is None:
            return RdstResult(False, f"Could not load snapshots '{snap1}' and/or '{snap2}'")

        if output_json:
            print(json.dumps(asdict(diff), indent=2, default=str))
            return RdstResult(True, message="")

        console = get_console()
        console.print(f"\n[bold]Fleet Diff:[/bold] {snap1} -> {snap2}")

        if diff.new_targets:
            console.print(f"  [green]New targets:[/green] {', '.join(diff.new_targets)}")
        if diff.removed_targets:
            console.print(f"  [red]Removed targets:[/red] {', '.join(diff.removed_targets)}")

        if diff.entries:
            from shared.ui import DataTable

            rows = []
            for e in diff.entries:
                change = f"{e.change_pct:+.1f}%" if e.change_pct is not None else ""
                rows.append((e.target_name, e.field_name, str(e.old_value), str(e.new_value), change))

            table = DataTable(
                title="Changes",
                columns=["Target", "Metric", "Before", "After", "Change"],
                rows=rows,
            )
            console.print(table)
        else:
            console.print("  [dim]No metric changes detected.[/dim]")

        return RdstResult(True, message="Diff complete")

    # =========================================================================
    # Snapshots
    # =========================================================================

    def _handle_snapshots(self, args: argparse.Namespace) -> RdstResult:
        """List saved audit snapshots."""
        output_json = getattr(args, "output_json", False)

        store = SnapshotStore()
        snapshots = store.list_snapshots()

        if output_json:
            print(json.dumps(snapshots, indent=2))
            return RdstResult(True, message="")

        console = get_console()
        if not snapshots:
            console.print("[dim]No fleet audits saved yet.[/dim]")
            return RdstResult(True, message="No snapshots")

        from shared.ui import DataTable

        rows = [(s["name"], s["created_at"][:19], str(s["targets_audited"])) for s in snapshots]
        table = DataTable(
            title=f"Fleet Audits ({len(snapshots)})",
            columns=["Run ID", "Date", "Targets"],
            rows=rows,
        )
        console.print(table)
        console.print(f"\n[dim]Compare two runs: rdst fleet diff <run_id_1> <run_id_2>[/dim]")
        return RdstResult(True, message="")
