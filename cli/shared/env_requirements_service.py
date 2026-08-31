"""Resolve required environment variables for RDST web workflows."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from shared.anthropic_env import get_anthropic_source
from shared.account_session import (
    SESSION_SECRET_NAMES,
    AccountSessionError,
    access_token,
)
from shared.password_resolver import resolve_password
from shared.secret_store_service import SecretStoreService
from shared.config.targets import TargetsConfig


class EnvRequirementsService:
    """Build readiness model for required env vars."""

    ANTHROPIC_API_KEY_NAME = "ANTHROPIC_API_KEY"
    LEGACY_SECRET_NAMES = ["RDST_TRIAL_TOKEN"]
    ANTHROPIC_ACCEPTED_NAMES = [ANTHROPIC_API_KEY_NAME]

    def __init__(self, secret_store: SecretStoreService | None = None):
        self.secret_store = secret_store or SecretStoreService()

    def _load_config(self) -> Any:
        cfg = TargetsConfig()
        cfg.load()
        return cfg

    def _target_env_mapping(self, cfg: Any) -> Dict[str, Dict[str, Any]]:
        """Return {password_env: {"targets": [...], "target_data": <first target's data>}}."""
        mapping: Dict[str, Dict[str, Any]] = {}
        target_names = cfg.list_targets()
        used_names = {
            (cfg.get(target) or {}).get("password_env", "").strip()
            for target in target_names
            if (cfg.get(target) or {}).get("password_env", "").strip()
        }

        for target in target_names:
            target_data = cfg.get(target) or {}
            password_env = (target_data.get("password_env") or "").strip()
            if not password_env:
                if target_data.get("password") or target_data.get("password_secret_arn"):
                    continue
                base_name = (
                    "RDST_"
                    + (re.sub(r"[^A-Z0-9]+", "_", target.upper()).strip("_") or "TARGET")
                    + "_PASSWORD"
                )
                password_env = base_name
                suffix = 2
                while password_env in used_names:
                    password_env = f"{base_name}_{suffix}"
                    suffix += 1
                used_names.add(password_env)
                target_data = {**target_data, "password_env": password_env}
            if password_env not in mapping:
                mapping[password_env] = {"targets": [], "target_data": target_data}
            mapping[password_env]["targets"].append(target)

        return mapping

    def bind_missing_target_password(self, env_name: str) -> None:
        """Persist a password_env pointer for targets that had no password source."""
        cfg = self._load_config()
        entry = self._target_env_mapping(cfg).get(env_name)
        if entry is None:
            return

        changed = False
        for target in entry["targets"]:
            target_data = cfg.get(target) or {}
            if (
                target_data.get("password")
                or target_data.get("password_env")
                or target_data.get("password_secret_arn")
            ):
                continue
            cfg.upsert(target, {**target_data, "password_env": env_name})
            changed = True
        if changed:
            cfg.save()

    def _resolve_anthropic_source(self, cfg: Any) -> str:
        return get_anthropic_source(secret_store=self.secret_store, cfg=cfg)

    def _account_signed_in(self) -> bool:
        """Validate or refresh the stored session before opening the AI gate."""
        try:
            return access_token(store=self.secret_store) is not None
        except AccountSessionError:
            return False

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
        account_signed_in = self._account_signed_in()
        anthropic_available = anthropic_source not in ("missing", "trial_exhausted")
        requirements.append(
            {
                "kind": "anthropic_api_key",
                "accepted_names": list(self.ANTHROPIC_ACCEPTED_NAMES),
                "target": None,
                "satisfied": account_signed_in or anthropic_available,
                "source": anthropic_source if anthropic_available else (
                    "readyset_account" if account_signed_in else anthropic_source
                ),
            }
        )

        return requirements

    def get_allowed_secret_names(self) -> List[str]:
        cfg = self._load_config()
        names = set(self.ANTHROPIC_ACCEPTED_NAMES)
        names.update(self._target_env_mapping(cfg).keys())
        return sorted(names)

    def get_required_names_for_restore(self) -> List[str]:
        return sorted(set(self.get_allowed_secret_names() + SESSION_SECRET_NAMES))

    def get_clearable_secret_names(self) -> List[str]:
        """Include retired credentials when explicitly clearing local state."""
        return sorted(
            set(
                self.get_allowed_secret_names()
                + self.LEGACY_SECRET_NAMES
                + SESSION_SECRET_NAMES
            )
        )
