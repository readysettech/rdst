"""Resolve required environment variables for RDST web workflows."""

from __future__ import annotations

from typing import Any, Dict, List

from shared.account_session import (
    SESSION_SECRET_NAMES,
    AccountSessionError,
    access_token,
)
from shared.anthropic_env import (
    anthropic_key_rejected,
    get_anthropic_source,
    startup_anthropic_key,
)
from shared.config.targets import TargetsConfig
from shared.password_resolver import implicit_password_envs, resolve_password
from shared.secret_store_service import SecretStoreService


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
        implicit = implicit_password_envs({t: cfg.get(t) or {} for t in target_names})

        for target in target_names:
            target_data = cfg.get(target) or {}
            password_env = (target_data.get("password_env") or "").strip()
            if not password_env:
                if target not in implicit:
                    continue
                password_env = implicit[target]
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

    @staticmethod
    def _selected_ai_provider(cfg: Any) -> str | None:
        provider = (cfg.get_llm_provider() or "").lower()
        if provider in {"anthropic", "claude"}:
            return "claude"
        if provider == "readyset":
            return "readyset"
        return None

    def set_ai_provider(self, provider: str) -> None:
        """Persist the active AI provider after verifying its credential exists."""
        if provider not in {"claude", "readyset"}:
            raise ValueError(f"Unsupported AI provider: {provider}")

        cfg = self._load_config()
        if provider == "claude":
            anthropic_source = self._resolve_anthropic_source(cfg)
            if anthropic_source in ("missing", "trial_exhausted"):
                raise ValueError("Set an Anthropic API key before selecting Anthropic.")
        elif not self._account_signed_in():
            raise ValueError("Sign in to Readyset before selecting Readyset-hosted AI.")

        cfg.set_llm_provider(provider)
        cfg.save()

    def prefer_readyset_without_anthropic_key(self) -> None:
        """Select Readyset-hosted AI when Anthropic is selected and the key store says no key
        remains; a store that is locked or slow to answer changes nothing."""
        cfg = self._load_config()
        if self._selected_ai_provider(cfg) != "claude":
            return
        if self._resolve_anthropic_source(cfg) == "missing" and \
                self.secret_store.lacks_secret("ANTHROPIC_API_KEY"):
            cfg.set_llm_provider("readyset")
            cfg.save()

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
        # A configured key Anthropic rejected can't run AI, so it doesn't meet the requirement.
        anthropic_usable = anthropic_available and not anthropic_key_rejected(self.secret_store)
        selected_provider = self._selected_ai_provider(cfg)
        if selected_provider == "readyset":
            source = "readyset_account" if account_signed_in else "missing"
            satisfied = account_signed_in
        elif selected_provider == "claude":
            source = anthropic_source
            satisfied = anthropic_usable
        # AI runs on a saved key ahead of the account, so a rejected one still decides it.
        elif anthropic_available:
            selected_provider = "claude"
            source = anthropic_source
            satisfied = anthropic_usable
        elif account_signed_in:
            selected_provider = "readyset"
            source = "readyset_account"
            satisfied = True
        else:
            source = anthropic_source
            satisfied = False
        requirements.append(
            {
                "kind": "anthropic_api_key",
                "accepted_names": list(self.ANTHROPIC_ACCEPTED_NAMES),
                "target": None,
                "satisfied": satisfied,
                "source": source,
                "selected_provider": selected_provider,
                "anthropic_key_configured": anthropic_available,
                "readyset_account_connected": account_signed_in,
                "anthropic_key_in_startup_env": startup_anthropic_key() is not None,
            }
        )

        return requirements

    def get_allowed_secret_names(self, cfg: Any | None = None) -> List[str]:
        cfg = cfg or self._load_config()
        names = set(self.ANTHROPIC_ACCEPTED_NAMES)
        names.update(self._target_env_mapping(cfg).keys())
        return sorted(names)

    def get_required_names_for_restore(self) -> List[str]:
        """What startup reads from the keyring. Database passwords aren't among them: each is
        read when its database is used, so starting RDST or adding one database never reads
        (or, on a Mac, asks about) another's."""
        return sorted(set(self.ANTHROPIC_ACCEPTED_NAMES) | set(SESSION_SECRET_NAMES))

    def get_clearable_secret_names(self, cfg: Any | None = None) -> List[str]:
        """Include retired credentials when explicitly clearing local state."""
        return sorted(
            set(
                self.get_allowed_secret_names(cfg)
                + self.LEGACY_SECRET_NAMES
                + SESSION_SECRET_NAMES
            )
        )
