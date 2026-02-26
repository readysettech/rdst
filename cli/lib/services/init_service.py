"""Service for init workflow checks and validation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple
import os

from lib.cli.rdst_cli import TargetsConfig
from lib.llm_manager.claude_provider import AnthropicModel

from .password_resolver import resolve_password
from .anthropic_env import has_anthropic_api_key
from .types import (
    InitStatus,
    InitValidationResult,
    InitCompleteEvent,
    InitErrorEvent,
    InitEvent,
    InitLlmValidationEvent,
    InitStatusEvent,
    InitTargetValidationEvent,
)


class InitService:
    """Stateless service for init workflow."""

    _DEFAULT_CLAUDE_MODEL = AnthropicModel.SONNET_4_5.value

    def _load_config(self) -> TargetsConfig:
        """Load TargetsConfig from CLI module."""

        cfg = TargetsConfig()
        cfg.load()
        return cfg

    def _is_llm_configured(self, cfg: Any) -> bool:
        """Check if LLM is configured and ready for use."""
        self._ensure_llm_provider_for_anthropic(cfg)
        llm = cfg.get_llm_config() or {}
        provider = llm.get("provider")
        if provider == "claude":
            return has_anthropic_api_key()
        return False

    def _ensure_llm_provider_for_anthropic(self, cfg: Any) -> None:
        """Auto-bootstrap Claude provider when API key is present.

        Web onboarding can run without explicit CLI setup. If no provider has been
        selected yet but ANTHROPIC_API_KEY exists, persist a minimal Claude config
        so validation/status checks behave like CLI `rdst configure llm`.
        """
        llm = cfg.get_llm_config() or {}
        if llm.get("provider"):
            return
        if not has_anthropic_api_key():
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
        """Check if init completed, list targets, check LLM status."""
        cfg = self._load_config()
        default_target = cfg.get_default()

        targets: List[Dict[str, Any]] = []
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
        self, target_names: Optional[List[str]] = None
    ) -> InitValidationResult:
        """Test all target connections + LLM."""
        cfg = self._load_config()
        names = target_names or cfg.list_targets()

        results: List[Dict[str, Any]] = []
        updated_targets: List[Tuple[str, Dict[str, Any]]] = []

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

        return InitValidationResult(
            target_results=results,
            llm_result=llm_result,
        )

    def check_llm(self, cfg: Optional[Any] = None) -> Dict[str, Any]:
        """Check if ANTHROPIC_API_KEY is set and test API."""
        cfg = cfg or self._load_config()
        self._ensure_llm_provider_for_anthropic(cfg)
        llm = cfg.get_llm_config() or {}
        provider = llm.get("provider")

        if provider != "claude":
            return {"success": False, "error": "LLM not configured"}

        if not has_anthropic_api_key():
            return {
                "success": False,
                "error": "Anthropic API key not set (ANTHROPIC_API_KEY or RDST_TRIAL_TOKEN). Run 'rdst init' to configure.",
            }

        try:
            from lib.llm_manager.llm_manager import LLMManager

            llm_mgr = LLMManager(defaults={"max_tokens": 8, "temperature": 0.0})
            llm_mgr.query(
                system_message="You are a terse assistant.",
                user_query="ping",
                context=None,
                max_tokens=8,
                temperature=0.0,
            )
            model = llm.get("model", "claude-sonnet-4-20250514")
            return {"success": True, "model": model}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def mark_complete(self) -> bool:
        """Mark init as completed in config."""
        cfg = self._load_config()
        cfg.mark_init_completed(version=None)
        cfg.save()
        return True

    def _test_target(self, target: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """Use DataManager to validate connectivity and report detailed failure reasons."""
        from lib.data_manager.data_manager import DataManager, ConnectionConfig
        from lib.data_manager_service import DMSDbType, DataManagerQueryType
        import datetime

        engine = (target.get("engine") or "").lower()
        host = target.get("host")
        port = int(target.get("port") or 0)
        database = target.get("database")
        user = target.get("user")
        password_env = target.get("password_env")
        tls = bool(target.get("tls", False))

        password = os.environ.get(password_env) if password_env else None

        if engine == "postgres" or engine == "psql":
            engine = "postgresql"

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

        db_type = DMSDbType.PostgreSQL if engine == "postgresql" else DMSDbType.MySql
        default_port = 5432 if engine == "postgresql" else 3306
        port = port or default_port

        try:
            cfg = ConnectionConfig(
                host=host or "",
                port=port,
                database=database or "",
                username=user or "",
                password=password or "",
                db_type=db_type,
                ssl_mode=("require" if tls else "prefer"),
                connect_timeout=3,
                query_type=DataManagerQueryType.UPSTREAM,
            )
            dm = DataManager(
                connection_config={DataManagerQueryType.UPSTREAM: cfg},
                global_logger=self._make_logger(),
                command_sets=["system_info"],
                data_directory="./.rdst-init",
                max_workers=1,
                available_commands=None,
                instance_s3_data_folder="",
                s3_operation=None,
            )
            ok = dm.connect(DataManagerQueryType.UPSTREAM)
            state = dm.get_connection_state(DataManagerQueryType.UPSTREAM)

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

            try:
                dm.disconnect(DataManagerQueryType.UPSTREAM)
            except Exception:
                pass

            if ok and state.get("success"):
                return True, "Connected", verification

            err = state.get("error") or "Unknown connection error"
            return False, self._clean_error_message(str(err)), verification
        except Exception as e:
            verification = {
                "attempted": True,
                "success": False,
                "error": str(e),
                "verified_at": datetime.datetime.utcnow().isoformat() + "Z",
                "engine": engine,
                "host": host,
                "port": port,
                "database": database,
            }
            return False, self._clean_error_message(str(e)), verification

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
        """Clean up error messages to be more concise and user-friendly."""
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

    # ========================================================================
    # Event APIs for shared CLI + Web workflows
    # ========================================================================

    async def get_status_events(self) -> AsyncGenerator[InitEvent, None]:
        """Stream init status events."""
        try:
            yield InitStatusEvent(
                type="status", message="Loading initialization status"
            )
            status = self.get_status()
            yield InitCompleteEvent(type="complete", success=True, status=status)
        except Exception as e:
            yield InitErrorEvent(type="error", message=str(e))

    async def validate_all_events(
        self, target_names: Optional[List[str]] = None
    ) -> AsyncGenerator[InitEvent, None]:
        """Stream init validation events."""
        try:
            yield InitStatusEvent(
                type="status", message="Validating configured targets"
            )
            validation = self.validate_all(target_names)

            for item in validation.target_results:
                yield InitTargetValidationEvent(
                    type="target_validation",
                    name=item.get("name", ""),
                    success=bool(item.get("success")),
                    error=item.get("error"),
                )

            yield InitLlmValidationEvent(
                type="llm_validation", result=validation.llm_result
            )
            yield InitCompleteEvent(
                type="complete",
                success=True,
                validation=validation,
            )
        except Exception as e:
            yield InitErrorEvent(type="error", message=str(e))

    async def mark_complete_events(self) -> AsyncGenerator[InitEvent, None]:
        """Stream init completion events."""
        try:
            yield InitStatusEvent(type="status", message="Marking init as completed")
            success = self.mark_complete()
            yield InitCompleteEvent(type="complete", success=success)
        except Exception as e:
            yield InitErrorEvent(type="error", message=str(e))
