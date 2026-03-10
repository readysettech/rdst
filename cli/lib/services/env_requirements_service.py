"""Resolve required environment variables for RDST web workflows."""

from __future__ import annotations

import os
from typing import Any, Dict, List

from .password_resolver import resolve_password
from .secret_store_service import SecretStoreService


class EnvRequirementsService:
    """Build readiness model for required env vars."""

    ANTHROPIC_API_KEY_NAME = "ANTHROPIC_API_KEY"
    TRIAL_TOKEN_NAME = "RDST_TRIAL_TOKEN"
    ANTHROPIC_ACCEPTED_NAMES = [ANTHROPIC_API_KEY_NAME, TRIAL_TOKEN_NAME]

    def __init__(self, secret_store: SecretStoreService | None = None):
        self.secret_store = secret_store or SecretStoreService()

    def _load_config(self) -> Any:
        from lib.cli.rdst_cli import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()
        return cfg

    def _target_env_mapping(self, cfg: Any) -> Dict[str, Dict[str, Any]]:
        """Return {password_env: {"targets": [...], "target_data": <first target's data>}}."""
        mapping: Dict[str, Dict[str, Any]] = {}

        for target in cfg.list_targets():
            target_data = cfg.get(target) or {}
            password_env = (target_data.get("password_env") or "").strip()
            if not password_env:
                continue
            if password_env not in mapping:
                mapping[password_env] = {"targets": [], "target_data": target_data}
            mapping[password_env]["targets"].append(target)

        return mapping

    def _resolve_anthropic_source(self, cfg: Any) -> str:
        if os.environ.get(self.ANTHROPIC_API_KEY_NAME):
            return "process_env"
        if self.secret_store.get_secret(self.ANTHROPIC_API_KEY_NAME):
            return "secure_store"
        if os.environ.get(self.TRIAL_TOKEN_NAME):
            return "trial"
        if self.secret_store.get_secret(self.TRIAL_TOKEN_NAME):
            return "trial"
        try:
            if cfg.is_trial_active():
                return "trial"
            trial = cfg.get_trial_config()
            if trial.get("token") and trial.get("status") == "exhausted":
                return "trial_exhausted"
        except Exception:
            pass
        return "missing"

    def get_requirements(self) -> List[Dict[str, Any]]:
        cfg = self._load_config()
        requirements: List[Dict[str, Any]] = []
        mapping = self._target_env_mapping(cfg)

        for env_name in sorted(mapping.keys()):
            entry = mapping[env_name]
            targets = entry["targets"]
            resolution = resolve_password(entry["target_data"], self.secret_store)
            requirements.append(
                {
                    "kind": "target_password",
                    "accepted_names": [env_name],
                    "target": targets[0] if len(targets) == 1 else None,
                    "satisfied": resolution.available,
                    "source": resolution.source,
                }
            )

        anthropic_source = self._resolve_anthropic_source(cfg)
        requirements.append(
            {
                "kind": "anthropic_api_key",
                "accepted_names": list(self.ANTHROPIC_ACCEPTED_NAMES),
                "target": None,
                "satisfied": anthropic_source not in ("missing", "trial_exhausted"),
                "source": anthropic_source,
            }
        )

        return requirements

    def get_allowed_secret_names(self) -> List[str]:
        cfg = self._load_config()
        names = set(self.ANTHROPIC_ACCEPTED_NAMES)
        names.update(self._target_env_mapping(cfg).keys())
        return sorted(names)

    def get_required_names_for_restore(self) -> List[str]:
        return self.get_allowed_secret_names()
