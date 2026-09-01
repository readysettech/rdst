"""One-time cleanup for credentials retired by newer RDST releases."""

from __future__ import annotations

import os
from typing import Any

from shared.config.targets import TargetsConfig
from shared.secret_store_service import SecretStoreService

LEGACY_TRIAL_SECRET = "RDST_TRIAL_TOKEN"


def retire_legacy_trial_credentials(
    *,
    secret_store: Any | None = None,
    config: TargetsConfig | None = None,
) -> dict[str, Any]:
    """Remove the retired local trial credential without touching other state.

    The migration is deliberately idempotent. Updated clients no longer route
    inference through trial tokens, while older clients and Keyservice's legacy
    compatibility routes remain unchanged.
    """

    result: dict[str, Any] = {
        "environment_removed": os.environ.pop(LEGACY_TRIAL_SECRET, None) is not None,
        "keyring_removed": False,
        "config_removed": False,
        "errors": [],
    }

    try:
        store = secret_store or SecretStoreService()
        cleared = store.clear_required([LEGACY_TRIAL_SECRET])
        result["keyring_removed"] = LEGACY_TRIAL_SECRET in cleared.get("cleared", [])
        if cleared.get("errors"):
            result["errors"].append("keyring")
    except Exception:
        result["errors"].append("keyring")

    try:
        cfg = config or TargetsConfig()
        cfg.load()
        trial = cfg.get_trial_config()
        legacy_email = str(trial.get("email") or "").strip()
        if legacy_email and not cfg.get_email():
            # Early trial clients kept their only report/contact address in
            # [trial]. Preserve that non-secret identity without overriding a
            # newer primary email or carrying the retired token forward.
            cfg.set_email(legacy_email)
        if cfg.clear_trial_config():
            cfg.save()
            result["config_removed"] = True
    except Exception:
        result["errors"].append("config")

    return result


__all__ = ["LEGACY_TRIAL_SECRET", "retire_legacy_trial_credentials"]
