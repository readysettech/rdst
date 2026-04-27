from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import os

from features.configure.cli.wizard import ConfigurationWizard
from features.init.events import (
    InitCompleteEvent,
    InitLlmValidationEvent,
    InitStatusEvent,
    InitTargetValidationEvent,
)
from features.init.service import InitService
from shared.cli.types import RdstResult
from shared.config.targets import TargetsConfig
from shared.llm import resolve_api_key
from shared.ui import (
    Confirm,
    MessagePanel,
    NextSteps,
    SelectPrompt,
    StatusLine,
    StyleTokens,
    Text,
    get_console,
)


@dataclass
class TargetTestResult:
    name: str
    ok: bool
    message: str = ""


class InitCommand:
    """Implements `rdst init` first-run wizard."""

    def __init__(self, console=None, cli=None):
        self.console = console or get_console()
        self.cli = cli

    def run(self, force: bool = False, interactive: bool | None = None) -> RdstResult:
        cfg = TargetsConfig()
        cfg.load()
        try:
            init_completed = cfg.is_init_completed()
        except Exception:
            init_completed = False

        if not interactive:
            interactive = self._is_tty()

        if init_completed and not force:
            wizard = ConfigurationWizard(console=self.console)
            list_res = wizard.configure_targets("list", cfg)
            found = len(cfg.list_targets())
            msg = (
                list_res.message
                or f"Found {found} configured target(s). Use --force to re-run setup."
            )
            self._print("Init", "Existing configuration detected. Use --force to re-run setup.")
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
            # Truly non-interactive — likely piped or running under CI without a TTY.
            return RdstResult(
                False,
                "rdst init needs an interactive terminal to walk through setup. "
                "Re-run from an interactive shell, or pre-populate ~/.rdst/config.toml.",
            )

        self._welcome()

        if force:
            try:
                cfg._data.setdefault("init", {})
                cfg._data["init"]["completed"] = False
            except Exception:
                pass

        targets_added = self._step_targets_first_run(cfg)
        if not targets_added:
            return RdstResult(False, "No targets configured")

        self._print("", "")
        self._print("Step 2", "Configure Anthropic API")
        self._print("", "RDST uses Anthropic's Claude for AI-powered query analysis")

        wizard = ConfigurationWizard(console=self.console)
        wizard.configure_llm(cfg, {})

        cfg.save()
        self._print("Saved", f"Configuration saved to {cfg.path}")

        test_results = self._step_validate(cfg)
        self._maybe_run_top(cfg, test_results)

        try:
            cfg.mark_init_completed(version=None)
            cfg.save()
        except Exception:
            self._print("Init", "Warning: could not record init completion flag")

        self._success_summary(cfg, test_results)
        return RdstResult(True, "Setup complete. Try running: rdst top")

    def _step_targets_first_run(self, cfg: TargetsConfig) -> bool:
        wizard = ConfigurationWizard(console=self.console)
        added_any = False
        while True:
            wizard.configure_targets("list", cfg)
            targets = cfg.list_targets()
            has_default = bool(cfg.get_default())
            if not targets:
                self._print("Setup", "No targets configured yet. Let's add one.")
                res = wizard.configure_targets("add", cfg)
                if not res.ok:
                    break
                added_any = True
                continue

            options = [
                "Set default from existing targets",
                "Add another target",
                "Done configuring targets",
            ]
            choice = self._select("Targets setup options", options, default_idx=0 if not has_default else 2)
            if choice == options[0]:
                selection = self._select("Select default target", targets, default_idx=0)
                if selection:
                    cfg.set_default(selection)
                    cfg.save()
                    self._print("Default", f"'{selection}' set as default target")
                continue
            if choice == options[1]:
                res = wizard.configure_targets("add", cfg)
                if res.ok:
                    added_any = True
                continue
            break

        targets = cfg.list_targets()
        if targets and not cfg.get_default():
            default = self._select("Select default target", targets, default_idx=0)
            if default:
                cfg.set_default(default)
                cfg.save()
                self._print("Default", f"'{default}' set as default target")
        return bool(cfg.list_targets()) or added_any

    def _step_validate(self, cfg: TargetsConfig) -> list[TargetTestResult]:
        import asyncio

        results: list[TargetTestResult] = []
        targets = cfg.list_targets()
        service = InitService()
        llm_result: dict[str, Any] = {}

        async def _run_validation() -> None:
            nonlocal llm_result
            async for event in service.validate_all_events(targets):
                if isinstance(event, InitStatusEvent):
                    self._print("Validate", event.message)
                elif isinstance(event, InitTargetValidationEvent):
                    results.append(TargetTestResult(name=event.name, ok=event.success, message=event.error or ""))
                    status = "success" if event.success else "failed"
                    color_style = StyleTokens.SUCCESS if event.success else StyleTokens.ERROR
                    self._print(
                        "Result",
                        f"Target {event.name}: {status}{' - ' + event.error if event.error else ''}",
                        style=color_style,
                    )
                elif isinstance(event, InitLlmValidationEvent):
                    llm_result = event.result or {}
                elif isinstance(event, InitCompleteEvent) and event.validation is not None:
                    llm_result = event.validation.llm_result or llm_result

        asyncio.run(_run_validation())

        if llm_result.get("success"):
            model = llm_result.get("model", "claude-sonnet-4-20250514")
            self._print("Anthropic", f"Configured and reachable ({model})")
        else:
            error = llm_result.get("error", "Unknown error")
            if error == "ANTHROPIC_API_KEY not set":
                has_key = False
                try:
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
                self._print("Anthropic", f"Connection test failed: {error}", style=StyleTokens.ERROR)

        return results

    def _maybe_run_top(self, cfg: TargetsConfig, results: list[TargetTestResult]) -> None:
        default_name = cfg.get_default()
        if not default_name:
            return
        default_ok = any(r.name == default_name and r.ok for r in results)
        if not default_ok or self.cli is None:
            return
        if self._confirm("Run 'rdst top' now for the default target?", default=False):
            try:
                self.cli.top(limit=20)
            except Exception:
                pass

    def _success_summary(self, cfg: TargetsConfig, results: list[TargetTestResult]) -> None:
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

        has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("RDST_TRIAL_TOKEN"))
        if not has_api_key:
            try:
                resolve_api_key()
                has_api_key = True
            except Exception:
                pass
        steps = []

        seen_password_envs: set[str] = set()
        for r in fails:
            target_cfg = cfg.get(r.name) or {}
            password_env = target_cfg.get("password_env")
            if not password_env or password_env in seen_password_envs:
                continue
            if os.environ.get(password_env):
                continue
            seen_password_envs.add(password_env)
            steps.append(
                (
                    f'[{StyleTokens.WARNING}]export[/{StyleTokens.WARNING}] {password_env}=[{StyleTokens.ACCENT}]"your-password"[/{StyleTokens.ACCENT}]',
                    f"Required for target {r.name}",
                )
            )

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
                    (f"rdst [{StyleTokens.SUCCESS}]top[/{StyleTokens.SUCCESS}]", "Monitor slow queries"),
                    (
                        f'rdst [{StyleTokens.SUCCESS}]analyze[/{StyleTokens.SUCCESS}] -q [{StyleTokens.ACCENT}]"SELECT ..."[/{StyleTokens.ACCENT}]',
                        "Analyze a query",
                    ),
                ]
            )
        self.console.print(NextSteps(steps))

    def _welcome(self) -> None:
        self.console.print(
            MessagePanel(
                "Welcome to Readyset Data and SQL Toolkit (rdst).\n\nLet's get you set up in a few steps.",
                variant="info",
                title="Welcome",
            )
        )

    def _print(self, title: str, message: str, style: str | None = None) -> None:
        if title:
            self.console.print(StatusLine(title, message, style=style))
            return
        if style:
            self.console.print(Text(message, style=style))
        else:
            self.console.print(message)

    def _confirm(self, question: str, default: bool = True) -> bool:
        return Confirm.ask(question, default=default)

    def _select(self, prompt_text: str, choices: list[str], default_idx: int = 0) -> str | None:
        return SelectPrompt.ask(
            prompt_text,
            options=choices,
            default=default_idx + 1,
            return_index=False,
        )

    def _is_tty(self) -> bool:
        try:
            import sys

            return sys.stdin.isatty()
        except Exception:
            return False
