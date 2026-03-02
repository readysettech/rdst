from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import os

from .rdst_cli import RdstResult, TargetsConfig
from .configuration_wizard import ConfigurationWizard

# Use DataManager for connectivity checks
from lib.services.init_service import InitService

# Import UI system - handles Rich availability internally
from lib.ui import (
    get_console,
    StyleTokens,
    Prompt,
    Confirm,
    SelectPrompt,
    MessagePanel,
    NextSteps,
    StatusLine,
    Text,
)


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

    def __init__(self, console=None, cli=None):
        self.console = console or get_console()
        self.cli = (
            cli  # Optional RdstCLI instance for invoking other commands (e.g., top)
        )

    # ---- Public API ----
    def run(
        self, force: bool = False, interactive: Optional[bool] = None
    ) -> RdstResult:
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
            msg = (
                list_res.message
                or f"Found {found} configured target(s). Use --force to re-run setup."
            )
            self._print(
                "Init", "Existing configuration detected. Use --force to re-run setup."
            )
            return RdstResult(
                True,
                msg,
                data={
                    "targets": cfg.list_targets(),
                    "default": cfg.get_default(),
                    "init_completed": True,
                },
            )

        if not interactive:
            # Check if this is the first-time setup or a forced re-run
            has_existing_config = bool(cfg.list_targets())
            if has_existing_config and force:
                return RdstResult(
                    False,
                    "Interactive mode is required when using --force. Run: rdst init --interactive --force",
                )
            else:
                return RdstResult(
                    False,
                    "Interactive mode is required for first-time setup. Run: rdst init --interactive",
                )

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
        return RdstResult(True, "Setup complete. Try running: rdst top")

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
                "Done configuring targets",
            ]
            choice = self._select(
                "Targets setup options",
                options,
                default_idx=0 if not has_default else 2,
            )
            if choice == options[0]:
                selection = self._select(
                    "Select default target", targets, default_idx=0
                )
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
        import asyncio
        from lib.services.types import (
            InitCompleteEvent,
            InitLlmValidationEvent,
            InitStatusEvent,
            InitTargetValidationEvent,
        )

        results: List[TargetTestResult] = []
        targets = cfg.list_targets()

        service = InitService()
        llm_result: Dict[str, Any] = {}

        async def _run_validation() -> None:
            nonlocal llm_result
            async for event in service.validate_all_events(targets):
                if isinstance(event, InitStatusEvent):
                    self._print("Validate", event.message)
                elif isinstance(event, InitTargetValidationEvent):
                    results.append(
                        TargetTestResult(
                            name=event.name,
                            ok=event.success,
                            message=event.error or "",
                        )
                    )
                    status = "success" if event.success else "failed"
                    color_style = (
                        StyleTokens.SUCCESS if event.success else StyleTokens.ERROR
                    )
                    self._print(
                        "Result",
                        f"Target {event.name}: {status}{' - ' + event.error if event.error else ''}",
                        style=color_style,
                    )
                elif isinstance(event, InitLlmValidationEvent):
                    llm_result = event.result or {}
                elif (
                    isinstance(event, InitCompleteEvent)
                    and event.validation is not None
                ):
                    llm_result = event.validation.llm_result or llm_result

        asyncio.run(_run_validation())

        if llm_result.get("success"):
            model = llm_result.get("model", "claude-sonnet-4-20250514")
            self._print("Anthropic", f"Configured and reachable ({model})")
        else:
            error = llm_result.get("error", "Unknown error")
            if error == "ANTHROPIC_API_KEY not set":
                # Check if trial token is available (key service integration)
                has_key = False
                try:
                    from lib.llm_manager.key_resolution import resolve_api_key
                    resolve_api_key()
                    has_key = True
                except Exception:
                    pass

                if has_key:
                    self._print("Anthropic", "Trial token configured")
                else:
                    self.console.print(
                        MessagePanel(
                            "No LLM API key configured.\n\n"
                            "Options:\n"
                            "  1. Run 'rdst configure llm' to sign up for a free trial (up to 925K tokens)\n"
                            '  2. Set your own key: export ANTHROPIC_API_KEY="sk-ant-..."',
                            variant="warning",
                            title="LLM Setup Required",
                            hint="Get an API key at: https://console.anthropic.com/",
                        )
                    )
            elif error == "LLM not configured":
                self._print("Anthropic", "Not configured (run 'rdst configure llm')")
            else:
                self._print(
                    "Anthropic",
                    f"Connection test failed: {error}",
                    style=StyleTokens.ERROR,
                )

        return results

    def _maybe_run_top(
        self, cfg: TargetsConfig, results: List[TargetTestResult]
    ) -> None:
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

    def _success_summary(
        self, cfg: TargetsConfig, results: List[TargetTestResult]
    ) -> None:
        try:
            default_name = cfg.get_default()
        except Exception:
            default_name = None
        total = len(results) if results else 0
        oks = [r for r in (results or []) if r.ok]
        fails = [r for r in (results or []) if not r.ok]
        self._print(
            "Summary",
            f"Validated {total} target(s): {len(oks)} ok, {len(fails)} failed",
        )
        if default_name:
            self._print("Default", f"{default_name}")
        if fails:
            for r in fails:
                msg = f"{r.name}: {r.message}" if r.message else r.name
                self._print("Failed", msg)

        # Breadcrumb: show next steps using NextSteps component
        has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("RDST_TRIAL_TOKEN"))
        if not has_api_key:
            try:
                from lib.llm_manager.key_resolution import resolve_api_key
                resolve_api_key()
                has_api_key = True
            except Exception:
                pass
        steps = []
        if not has_api_key:
            steps.append(
                (
                    f'[{StyleTokens.WARNING}]export[/{StyleTokens.WARNING}] ANTHROPIC_API_KEY=[{StyleTokens.ACCENT}]"sk-ant-..."[/{StyleTokens.ACCENT}]',
                    "Required for AI analysis",
                )
            )
        if default_name:
            steps.extend(
                [
                    (
                        f"rdst [{StyleTokens.SUCCESS}]top[/{StyleTokens.SUCCESS}] --target [{StyleTokens.ACCENT}]{default_name}[/{StyleTokens.ACCENT}]",
                        "Monitor slow queries",
                    ),
                    (
                        f'rdst [{StyleTokens.SUCCESS}]analyze[/{StyleTokens.SUCCESS}] -q [{StyleTokens.ACCENT}]"SELECT ..."[/{StyleTokens.ACCENT}] --target [{StyleTokens.ACCENT}]{default_name}[/{StyleTokens.ACCENT}]',
                        "Analyze a query",
                    ),
                    (
                        f"rdst [{StyleTokens.SUCCESS}]schema init[/{StyleTokens.SUCCESS}] --target [{StyleTokens.ACCENT}]{default_name}[/{StyleTokens.ACCENT}]",
                        "Set up semantic layer",
                    ),
                ]
            )
        else:
            steps.extend(
                [
                    (
                        f"rdst [{StyleTokens.SUCCESS}]top[/{StyleTokens.SUCCESS}]",
                        "Monitor slow queries",
                    ),
                    (
                        f'rdst [{StyleTokens.SUCCESS}]analyze[/{StyleTokens.SUCCESS}] -q [{StyleTokens.ACCENT}]"SELECT ..."[/{StyleTokens.ACCENT}]',
                        "Analyze a query",
                    ),
                ]
            )
        self.console.print(NextSteps(steps))

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

    # ---- Utilities ----
    def _welcome(self) -> None:
        self.console.print(
            MessagePanel(
                "Welcome to Readyset Data and SQL Toolkit (rdst).\n\nLet's get you set up in a few steps.",
                variant="info",
                title="Welcome",
            )
        )

    def _print_lines(self, lines: List[str]) -> None:
        for line in lines:
            self._print_raw(line)

    def _print(self, title: str, message: str, style: Optional[str] = None) -> None:
        if title:
            self.console.print(StatusLine(title, message, style=style))
            return

        if style:
            self.console.print(Text(message, style=style))
        else:
            self.console.print(message)

    def _print_raw(self, message: str) -> None:
        self.console.print(message)

    def _prompt(self, label: str, default: Optional[str] = None) -> str:
        return Prompt.ask(label, default=default or "", show_default=bool(default))

    def _confirm(self, question: str, default: bool = True) -> bool:
        return Confirm.ask(question, default=default)

    def _select(
        self, prompt_text: str, choices: List[str], default_idx: int = 0
    ) -> Optional[str]:
        # Use SelectPrompt component for numbered selection
        result = SelectPrompt.ask(
            prompt_text,
            options=choices,
            default=default_idx + 1,  # SelectPrompt uses 1-based indexing
            return_index=False,  # Return the actual choice string
        )
        return result

    def _llm_section_exists(self, cfg: TargetsConfig) -> bool:
        return bool((getattr(cfg, "_data", {}) or {}).get("llm"))

    def _is_tty(self) -> bool:
        try:
            import sys

            return sys.stdin.isatty()
        except Exception:
            return False
