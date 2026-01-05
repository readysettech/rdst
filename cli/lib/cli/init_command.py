from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import os

from .rdst_cli import RdstResult, TargetsConfig
from .configuration_wizard import ConfigurationWizard
# Use DataManager for connectivity checks
from lib.data_manager.data_manager import DataManager, ConnectionConfig
from lib.data_manager_service import DMSDbType, DataManagerQueryType

try:
    from rich.console import Console
    _RICH_AVAILABLE = True
except Exception:  # pragma: no cover
    Console = None  # type: ignore
    _RICH_AVAILABLE = False


@dataclass
class TargetTestResult:
    name: str
    ok: bool
    message: str = ""


class InitCommand:
    """Implements `rdst init` first-run wizard.

    Responsibilities:
    - Detect existing config, handle --force
    - Guide user to add one or more targets (reuses ConfigurationWizard)
    - Prompt for LLM key vs shared pool and persist in config
    - Test connectivity for each target
    - Optionally run `top` if default validated
    - Save configuration to ~/.rdst/config.toml (TargetsConfig)
    """

    def __init__(self, console: Optional[Console] = None, cli=None):
        self.console = console if console and _RICH_AVAILABLE else (Console() if _RICH_AVAILABLE else None)
        self._has_rich = _RICH_AVAILABLE and self.console is not None
        self.cli = cli  # Optional RdstCLI instance for invoking other commands (e.g., top)

    # ---- Public API ----
    def run(self, force: bool = False, interactive: Optional[bool] = None) -> RdstResult:
        """Run the init flow.

        Args:
            force: If True, run setup even if config already exists.
            interactive: Force interactive prompts. If None, auto-detect TTY.
        """
        cfg = TargetsConfig()
        cfg.load()

        # Determine if init previously completed (explicit flag)
        init_completed = False
        try:
            init_completed = cfg.is_init_completed()
        except Exception:
            init_completed = False

        # Determine interactivity
        if interactive is None:
            interactive = self._is_tty()

        if init_completed and not force:
            # Show existing configured targets and exit early
            wizard = ConfigurationWizard(console=self.console)
            list_res = wizard.configure_targets("list", cfg)
            found = len(cfg.list_targets())
            msg = list_res.message or f"Found {found} configured target(s). Use --force to re-run setup."
            self._print("Init", "Existing configuration detected. Use --force to re-run setup.")
            return RdstResult(True, msg, data={"targets": cfg.list_targets(), "default": cfg.get_default(), "init_completed": True})

        if not interactive:
            # Check if this is the first-time setup or a forced re-run
            has_existing_config = bool(cfg.list_targets())
            if has_existing_config and force:
                return RdstResult(False, "Interactive mode is required when using --force. Run: rdst init --interactive --force")
            else:
                return RdstResult(False, "Interactive mode is required for first-time setup. Run: rdst init --interactive")

        # Welcome
        self._welcome()

        # If forced, allow re-running setup but preserve existing targets/default/llm
        # Only reset the init completion flag so the wizard runs again.
        if force:
            try:
                cfg._data.setdefault("init", {})
                cfg._data["init"]["completed"] = False
            except Exception:
                # If something goes wrong, do not destructively reset the config
                pass

        # Step 1: Targets overview and selection (first run UX)
        targets_added = self._step_targets_first_run(cfg)
        if not targets_added:
            return RdstResult(False, "No targets configured")

        # Step 2: LLM setup
        self._print("", "")  # Add some spacing
        self._print("Step 2", "Configure Anthropic API")
        self._print("", "RDST uses Anthropic's Claude for AI-powered query analysis")

        # Use the centralized LLM configuration wizard
        wizard = ConfigurationWizard(console=self.console)
        wizard.configure_llm(cfg, {})

        # Save before tests so users see saved state even if tests fail
        cfg.save()
        self._print("Saved", f"Configuration saved to {cfg.path}")

        # Step 3: Validate configuration
        test_results = self._step_validate(cfg)

        # Optional: run top on default if validated and user agrees
        self._maybe_run_top(cfg, test_results)

        # Mark init completion and save
        try:
            cfg.mark_init_completed(version=None)
            cfg.save()
        except Exception:
            # Non-fatal if we fail to mark completion, but inform user
            self._print("Init", "Warning: could not record init completion flag")

        # Final summary
        self._success_summary(cfg, test_results)
        return RdstResult(True, f"Setup complete. Try running: rdst top")

    # ---- Steps ----
    def _step_targets_first_run(self, cfg: TargetsConfig) -> bool:
        """First-run targets flow: show table, pick default from existing, or add new.
        Returns True if at least one target exists after this step.
        """
        wizard = ConfigurationWizard(console=self.console)
        added_any = False

        while True:
            # Always show current targets in a table for clarity
            wizard.configure_targets("list", cfg)
            targets = cfg.list_targets()
            has_default = bool(cfg.get_default())

            if not targets:
                # No targets yet: force adding at least one
                self._print("Setup", "No targets configured yet. Let's add one.")
                res = wizard.configure_targets("add", cfg)
                if not res.ok:
                    # User cancelled or failed; break out and report
                    break
                added_any = True
                continue

            # There are targets; allow selecting default or adding another
            options = [
                "Set default from existing targets",
                "Add another target",
                "Done configuring targets"
            ]
            choice = self._select("Targets setup options", options, default_idx=0 if not has_default else 2)
            if choice == options[0]:
                selection = self._select("Select default target", targets, default_idx=0)
                if selection:
                    cfg.set_default(selection)
                    cfg.save()
                    self._print("Default", f"'{selection}' set as default target")
                # Loop back to allow adding another or finishing
                continue
            elif choice == options[1]:
                res = wizard.configure_targets("add", cfg)
                if res.ok:
                    added_any = True
                # Loop regardless; user can add multiple
                continue
            else:  # Done configuring targets
                break

        # Ensure that if multiple targets and still no default, prompt once more
        targets = cfg.list_targets()
        if targets and not cfg.get_default():
            default = self._select("Select default target", targets, default_idx=0)
            if default:
                cfg.set_default(default)
                cfg.save()
                self._print("Default", f"'{default}' set as default target")

        return bool(cfg.list_targets())

    def _step_validate(self, cfg: TargetsConfig) -> List[TargetTestResult]:
        results: List[TargetTestResult] = []
        targets = cfg.list_targets()
        default_name = cfg.get_default()

        for name in targets:
            self._print("Validating", f"Connecting to target '{name}'...")
            target_config = cfg.get(name) or {}
            ok, msg = self._test_target(target_config)
            # Persist a simple boolean flag used by 'rdst configure list'
            try:
                target_config["endpoint_verified"] = bool(ok)
                # Backward-compat: some views may still read 'verified'
                target_config["verified"] = bool(ok)
            except Exception:
                pass
            results.append(TargetTestResult(name=name, ok=ok, message=msg))
            status = "success" if ok else "failed"
            color_style = "green" if ok else "red"
            self._print("Result", f"Target {name}: {status}{' - ' + msg if msg else ''}", style=color_style)
        
            # Update the target configuration with verification results
            cfg.upsert(name, target_config)

        # Save the updated configuration with verification results
        cfg.save()

        # LLM access check (Anthropic API with ANTHROPIC_API_KEY)
        llm = (cfg._data or {}).get("llm", {})
        if llm.get("provider") == "claude":
            import os
            has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))

            if has_api_key:
                try:
                    from lib.llm_manager.llm_manager import LLMManager
                    llm_mgr = LLMManager(defaults={"max_tokens": 8, "temperature": 0.0})

                    self._print("Anthropic", "Testing API connection...")
                    resp = llm_mgr.query(
                        system_message="You are a terse assistant.",
                        user_query="ping",
                        context=None,
                        max_tokens=8,
                        temperature=0.0,
                    )
                    model = llm.get("model", "claude-sonnet-4-20250514")
                    self._print("Anthropic", f"Configured and reachable ({model})")
                except Exception as e:
                    self._print("Anthropic", f"Connection test failed: {e}", style="red")
            else:
                # API key not set - remind user
                self._print("Anthropic", "ANTHROPIC_API_KEY not set", style="yellow")
                self._print("", "")
                self._print("Setup Required", "Set your Anthropic API key:", style="yellow")
                self._print("", "  export ANTHROPIC_API_KEY=\"sk-ant-...\"", style="yellow")
                self._print("", "")
                self._print("", "Get an API key at: https://console.anthropic.com/", style="yellow")
        else:
            self._print("Anthropic", "Not configured (run 'rdst configure llm')")
        return results

    def _maybe_run_top(self, cfg: TargetsConfig, results: List[TargetTestResult]) -> None:
        default_name = cfg.get_default()
        if not default_name:
            return
        # Check default validated
        default_ok = any(r.name == default_name and r.ok for r in results)
        if not default_ok:
            return
        if self.cli is None:
            return
        if self._confirm("Run 'rdst top' now for the default target?", default=False):
            try:
                self.cli.top(limit=20)
            except Exception:
                pass

        # ---- Summary ----
        
    def _success_summary(self, cfg: TargetsConfig, results: List[TargetTestResult]) -> None:
        try:
            default_name = cfg.get_default()
        except Exception:
            default_name = None
        total = len(results) if results else 0
        oks = [r for r in (results or []) if r.ok]
        fails = [r for r in (results or []) if not r.ok]
        self._print("Summary", f"Validated {total} target(s): {len(oks)} ok, {len(fails)} failed")
        if default_name:
            self._print("Default", f"{default_name}")
        if fails:
            for r in fails:
                msg = f"{r.name}: {r.message}" if r.message else r.name
                self._print("Failed", msg)

        # Breadcrumb: show next steps with colors
        # Colors: rdst=white, subcommand=green, values/quoted=blue, descriptions=dim
        self._print("", "")
        if self._has_rich:
            self.console.print("[cyan]Next Steps:[/cyan]")
            # Check if ANTHROPIC_API_KEY is set
            import os
            has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
            if not has_api_key:
                self.console.print("  [yellow]export[/yellow] ANTHROPIC_API_KEY=[blue]\"sk-ant-...\"[/blue]  [dim]Required for AI analysis[/dim]")
            if default_name:
                self.console.print(f"  rdst [green]top[/green] --target [blue]{default_name}[/blue]              [dim]Monitor slow queries[/dim]")
                self.console.print(f"  rdst [green]analyze[/green] -q [blue]\"SELECT ...\"[/blue] --target [blue]{default_name}[/blue]   [dim]Analyze a query[/dim]")
                self.console.print(f"  rdst [green]schema init[/green] --target [blue]{default_name}[/blue]      [dim]Set up semantic layer for natural language queries[/dim]")
            else:
                self.console.print("  rdst [green]top[/green]                    [dim]Monitor slow queries[/dim]")
                self.console.print("  rdst [green]analyze[/green] -q [blue]\"SELECT ...\"[/blue]   [dim]Analyze a query[/dim]")
        else:
            self._print("Next Steps", "")
            if default_name:
                self._print("", f"  rdst top --target {default_name}              Monitor slow queries")
                self._print("", f"  rdst analyze -q \"SELECT ...\" --target {default_name}   Analyze a query")
                self._print("", f"  rdst schema init --target {default_name}      Set up semantic layer for natural language queries")
            else:
                self._print("", "  rdst top                    Monitor slow queries")
                self._print("", "  rdst analyze -q \"SELECT ...\"   Analyze a query")
        self._print("", "")

    def _make_logger(self):
        class _Logger:
            def __init__(self, printer):
                self._p = printer
            def debug(self, msg, *args, **kwargs):
                # Keep quiet - don't show debug messages
                pass
            def info(self, msg, *args, **kwargs):
                # Keep quiet during connection testing - we show our own result
                pass
            def warning(self, msg, *args, **kwargs):
                # Keep quiet - S3 sync and other warnings aren't relevant for RDST
                pass
            def error(self, msg, *args, **kwargs):
                # Keep quiet - we handle errors gracefully and show our own message
                pass
        # Provide a quiet logger that suppresses DataManager's verbose output
        return _Logger(lambda title, message: None)

        # ---- Connectivity ----
    def _test_target(self, target: Dict[str, Any]) -> (bool, str):
        """Use DataManager to validate connectivity and report detailed failure reasons.
        Records the verification result with the target for later use.
        """
        engine = (target.get("engine") or "").lower()
        host = target.get("host")
        port = int(target.get("port") or 0)
        database = target.get("database")
        user = target.get("user")
        password_env = target.get("password_env")
        tls = bool(target.get("tls", False))

        password = os.environ.get(password_env) if password_env else None

        # Map engine to DMSDbType and defaults
        if engine == "postgres" or engine == "psql":
            engine = "postgresql"

        if engine not in ("postgresql", "mysql"):
            return False, f"Unsupported engine: {engine}"

        db_type = DMSDbType.PostgreSQL if engine == "postgresql" else DMSDbType.MySql
        default_port = 5432 if engine == "postgresql" else 3306
        port = port or default_port

        try:
            # Build a ConnectionConfig for UPSTREAM
            cfg = ConnectionConfig(
                host=host or "",
                port=port,
                database=database or "",
                username=user or "",
                password=password or "",
                db_type=db_type,
                ssl_mode=("require" if tls else "disable"),
                connect_timeout=3,
                query_type=DataManagerQueryType.UPSTREAM,
            )
            # Instantiate DataManager with a minimal setup; no S3 needed
            dm = DataManager(
                connection_config={DataManagerQueryType.UPSTREAM: cfg},
                global_logger=self._make_logger(),
                command_sets=["system_info"],  # minimal
                data_directory="./.rdst-init",
                max_workers=1,
                available_commands=None,
                instance_s3_data_folder="",  # Non-None to satisfy DataManager guard
                s3_operation=None,
                # disable_s3_sync=True
            )
            # Explicitly connect to record attempt and get error details
            ok = dm.connect(DataManagerQueryType.UPSTREAM)
            state = dm.get_connection_state(DataManagerQueryType.UPSTREAM)
            
            # Record the verification result in the target
            import datetime
            verification_result = {
                "attempted": state.get("attempted", False),
                "success": state.get("success", False),
                "error": state.get("error"),
                "verified_at": datetime.datetime.utcnow().isoformat() + "Z",
                "engine": engine,
                "host": host,
                "port": port,
                "database": database
            }
            target["verification"] = verification_result
        
            # Clean up connection
            try:
                dm.disconnect(DataManagerQueryType.UPSTREAM)
            except Exception:
                pass
            
            if ok and state.get("success"):
                return True, "Connected"
            # Prefer detailed error if present, but clean it up
            err = state.get("error") or "Unknown connection error"
            return False, self._clean_error_message(str(err))
        except Exception as e:
            # Record the exception in the target as well
            import datetime
            verification_result = {
                "attempted": True,
                "success": False,
                "error": str(e),
                "verified_at": datetime.datetime.utcnow().isoformat() + "Z",
                "engine": engine,
                "host": host,
                "port": port,
                "database": database
            }
            target["verification"] = verification_result
            return False, self._clean_error_message(str(e))

    def _clean_error_message(self, err: str) -> str:
        """Clean up error messages to be more concise and user-friendly."""
        # Extract key error info from verbose PostgreSQL/MySQL errors
        err = err.strip()

        # Connection refused
        if "Connection refused" in err:
            return "Connection refused (is the server running?)"

        # Authentication failed
        if "password authentication failed" in err.lower():
            return "Authentication failed (check password)"
        if "Access denied" in err:
            return "Access denied (check credentials)"

        # Host not found
        if "could not translate host name" in err.lower() or "Name or service not known" in err:
            return "Host not found"

        # Timeout
        if "timeout" in err.lower():
            return "Connection timeout"

        # Database not found
        if "does not exist" in err and "database" in err.lower():
            return "Database not found"

        # SSL errors
        if "SSL" in err or "ssl" in err:
            return "SSL connection error"

        # If it's a multi-line error, just take the first line
        if "\n" in err:
            err = err.split("\n")[0].strip()

        # Truncate if still too long
        if len(err) > 80:
            err = err[:77] + "..."

        return err

    # ---- Utilities ----
    def _welcome(self) -> None:
        intro_lines = [
            "Welcome to Readyset Diagnostics & SQL Tuning (rdst).",
            "",
            "Let's get you set up in a few steps.",
        ]
        self._print_lines(intro_lines)

    def _print_lines(self, lines: List[str]) -> None:
        for line in lines:
            self._print_raw(line)

    def _print(self, title: str, message: str, style: Optional[str] = None) -> None:
        if self._has_rich and style:
            self.console.print(f"[{style}]{title}: {message}[/{style}]")
        elif self._has_rich:
            self.console.print(f"{title}: {message}")
        else:
            print(f"{title}: {message}")

    def _print_raw(self, message: str) -> None:
        if self._has_rich:
            self.console.print(message)
        else:
            print(message)

    def _prompt(self, label: str, default: Optional[str] = None) -> str:
        if self._has_rich:
            from rich.prompt import Prompt
            return Prompt.ask(label, default=default, show_default=bool(default))
        prompt = label
        if default:
            prompt += f" [{default}]"
        prompt += ": "
        resp = input(prompt).strip()
        return resp or (default or "")

    def _confirm(self, question: str, default: bool = True) -> bool:
        if self._has_rich:
            from rich.prompt import Confirm
            return Confirm.ask(question, default=default)
        suffix = " [Y/n]" if default else " [y/N]"
        resp = input(f"{question}{suffix}: ").strip().lower()
        return resp in ("y", "yes") if resp else default

    def _select(self, prompt: str, choices: List[str], default_idx: int = 0) -> Optional[str]:
        # Very simple selector
        if self._has_rich:
            # Render a numbered list and ask for index
            items = "\n".join([f"  [{i+1}] {c}" for i, c in enumerate(choices)])
            self._print_raw(prompt)
            self._print_raw(items)
            idx_s = self._prompt("Select option", default=str(default_idx + 1))
        else:
            print(prompt)
            for i, c in enumerate(choices, start=1):
                print(f"  [{i}] {c}")
            idx_s = self._prompt("Select option", default=str(default_idx + 1))
        try:
            idx = int(idx_s) - 1
        except Exception:
            idx = default_idx
        if idx < 0 or idx >= len(choices):
            idx = default_idx
        return choices[idx]

    def _llm_section_exists(self, cfg: TargetsConfig) -> bool:
        return bool((getattr(cfg, "_data", {}) or {}).get("llm"))

    def _is_tty(self) -> bool:
        try:
            import sys
            return sys.stdin.isatty()
        except Exception:
            return False
