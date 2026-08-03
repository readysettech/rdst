"""Service for init workflow checks and validation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, AsyncGenerator
import datetime

from shared.anthropic_env import get_anthropic_source, has_anthropic_api_key
from shared.config.targets import TargetsConfig
from shared.llm import AnthropicModel, create_llm_manager
from shared.password_resolver import resolve_password, resolve_password_value
from shared.shell import environment_assignment

from .events import (
    InitCompleteEvent,
    InitErrorEvent,
    InitEvent,
    InitLlmValidationEvent,
    InitStatusEvent,
    InitTargetValidationEvent,
)
from .models import InitStatus, InitValidationResult


class InitService:
    """Stateless service for init workflow."""

    _DEFAULT_CLAUDE_MODEL = AnthropicModel.SONNET_4_5.value

    def _load_config(self) -> TargetsConfig:
        cfg = TargetsConfig()
        cfg.load()
        return cfg

    def _is_llm_configured(self, cfg: Any) -> bool:
        self._ensure_llm_provider_for_anthropic(cfg)
        llm = cfg.get_llm_config() or {}
        provider = llm.get("provider")
        if provider == "claude":
            return has_anthropic_api_key(cfg=cfg)
        return False

    def _ensure_llm_provider_for_anthropic(self, cfg: Any) -> None:
        llm = cfg.get_llm_config() or {}
        if llm.get("provider"):
            return
        if not has_anthropic_api_key(cfg=cfg):
            return

        cfg.set_llm_config(
            {
                "provider": "claude",
                "model": self._DEFAULT_CLAUDE_MODEL,
                "hint": "Using Claude Sonnet 4.6",
            }
        )
        cfg.save()

    def get_status(self) -> InitStatus:
        cfg = self._load_config()
        default_target = cfg.get_default()

        targets: list[dict[str, Any]] = []
        for name in cfg.list_targets():
            target_data = cfg.get(name) or {}
            targets.append(
                {
                    "name": name,
                    "engine": target_data.get("engine", "postgresql"),
                    "has_password": resolve_password(target_data).available,
                    "is_default": name == default_target,
                }
            )

        return InitStatus(
            initialized=cfg.is_init_completed(),
            targets=targets,
            default_target=default_target,
            llm_configured=self._is_llm_configured(cfg),
        )

    def validate_all(
        self, target_names: list[str] | None = None
    ) -> InitValidationResult:
        cfg = self._load_config()
        names = target_names or cfg.list_targets()

        results: list[dict[str, Any]] = []
        updated_targets: list[tuple[str, dict[str, Any]]] = []

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(names)))) as executor:
            futures = {}
            for name in names:
                target_config = dict(cfg.get(name) or {})
                futures[executor.submit(self._test_target, target_config)] = (
                    name,
                    target_config,
                )

            for future in as_completed(futures):
                name, target_config = futures[future]
                ok, msg, verification = future.result()

                target_config["endpoint_verified"] = bool(ok)
                target_config["verified"] = bool(ok)
                target_config["verification"] = verification

                updated_targets.append((name, target_config))

                if ok:
                    results.append({"name": name, "success": True})
                else:
                    results.append({"name": name, "success": False, "error": msg})

        for name, target_config in updated_targets:
            cfg.upsert(name, target_config)
        cfg.save()

        llm_result = self.check_llm(cfg)
        return InitValidationResult(target_results=results, llm_result=llm_result)

    def check_llm(self, cfg: Any | None = None) -> dict[str, Any]:
        cfg = cfg or self._load_config()
        self._ensure_llm_provider_for_anthropic(cfg)
        llm = cfg.get_llm_config() or {}
        provider = llm.get("provider")
        source = get_anthropic_source(cfg=cfg)

        if provider != "claude":
            return {"success": False, "error": "LLM not configured"}

        if source == "trial_exhausted":
            return {
                "success": False,
                "error": (
                    "Trial credits exhausted.\n\n"
                    "To continue using RDST:\n"
                    "  1. Get your own key: https://console.anthropic.com/\n"
                    f"  2. Set it: {environment_assignment('ANTHROPIC_API_KEY', 'sk-ant-...')}\n\n"
                    "Want more trial credits? Email hello@readyset.io"
                ),
            }

        if source == "missing":
            return {
                "success": False,
                "error": (
                    "Anthropic API key not set (ANTHROPIC_API_KEY or RDST_TRIAL_TOKEN). "
                    "Run 'rdst init' to configure."
                ),
            }

        try:
            llm_mgr = create_llm_manager(defaults={"max_tokens": 8, "temperature": 0.0})
            llm_mgr.query(
                system_message="You are a terse assistant.",
                user_query="ping",
                context=None,
                max_tokens=8,
                temperature=0.0,
            )
            model = llm.get("model", "claude-sonnet-4-20250514")
            return {"success": True, "model": model}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def mark_complete(self) -> bool:
        cfg = self._load_config()
        cfg.mark_init_completed(version=None)
        cfg.save()
        return True

    def _test_target(self, target: dict[str, Any]) -> tuple[bool, str, dict[str, Any]]:
        from shared.db_connection import normalize_engine_name, probe_target_connection

        engine = normalize_engine_name(target.get("engine") or "")
        host = target.get("host")
        port = int(target.get("port") or 0)
        database = target.get("database")

        if engine not in ("postgresql", "mysql"):
            verification = {
                "attempted": True,
                "success": False,
                "error": f"Unsupported engine: {engine}",
                "verified_at": datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "engine": engine,
                "host": host,
                "port": port,
                "database": database,
            }
            return False, f"Unsupported engine: {engine}", verification

        default_port = 5432 if engine == "postgresql" else 3306
        port = port or default_port

        try:
            state = probe_target_connection(target, connect_timeout=3)

            verification = {
                "attempted": state.get("attempted", False),
                "success": state.get("success", False),
                "error": state.get("error"),
                "verified_at": datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
                "engine": engine,
                "host": host,
                "port": port,
                "database": database,
            }

            if state.get("success"):
                return True, "Connected", verification

            err = state.get("error") or "Unknown connection error"
            return False, self._clean_error_message(str(err)), verification
        except Exception as exc:
            verification = {
                "attempted": True,
                "success": False,
                "error": str(exc),
                "verified_at": datetime.datetime.utcnow().isoformat() + "Z",
                "engine": engine,
                "host": host,
                "port": port,
                "database": database,
            }
            return False, self._clean_error_message(str(exc)), verification

    def _make_logger(self):
        class _Logger:
            def debug(self, msg, *args, **kwargs):
                pass

            def info(self, msg, *args, **kwargs):
                pass

            def warning(self, msg, *args, **kwargs):
                pass

            def error(self, msg, *args, **kwargs):
                pass

        return _Logger()

    def _clean_error_message(self, err: str) -> str:
        err = err.strip()
        if "Connection refused" in err:
            return "Connection refused (is the server running?)"
        if "password authentication failed" in err.lower():
            return "Authentication failed (check password)"
        if "Access denied" in err:
            return "Access denied (check credentials)"
        if (
            "could not translate host name" in err.lower()
            or "Name or service not known" in err
        ):
            return "Host not found"
        if "timeout" in err.lower():
            return "Connection timeout"
        if "does not exist" in err and "database" in err.lower():
            return "Database not found"
        if "SSL" in err or "ssl" in err:
            return "SSL connection error"
        if "\n" in err:
            err = err.split("\n")[0].strip()
        if len(err) > 80:
            err = err[:77] + "..."
        return err

    async def get_status_events(self) -> AsyncGenerator[InitEvent, None]:
        try:
            yield InitStatusEvent(type="status", message="Loading initialization status")
            status = self.get_status()
            yield InitCompleteEvent(type="complete", success=True, status=status)
        except Exception as exc:
            yield InitErrorEvent(type="error", message=str(exc))

    async def validate_all_events(
        self, target_names: list[str] | None = None
    ) -> AsyncGenerator[InitEvent, None]:
        try:
            yield InitStatusEvent(type="status", message="Validating configured targets")
            validation = self.validate_all(target_names)
            for item in validation.target_results:
                yield InitTargetValidationEvent(
                    type="target_validation",
                    name=item.get("name", ""),
                    success=bool(item.get("success")),
                    error=item.get("error"),
                )
            yield InitLlmValidationEvent(type="llm_validation", result=validation.llm_result)
            yield InitCompleteEvent(type="complete", success=True, validation=validation)
        except Exception as exc:
            yield InitErrorEvent(type="error", message=str(exc))

    async def mark_complete_events(self) -> AsyncGenerator[InitEvent, None]:
        try:
            yield InitStatusEvent(type="status", message="Marking init as completed")
            success = self.mark_complete()
            yield InitCompleteEvent(type="complete", success=success)
        except Exception as exc:
            yield InitErrorEvent(type="error", message=str(exc))
