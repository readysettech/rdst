"""A trial user who corrects a wrong gate email must become the real identity.

The trial dialog prefills the gate email but stays editable; verification runs
against what the user types. On successful activation the verified address is
promoted to the primary [[emails]] identity so telemetry follows the human.
"""

from __future__ import annotations

import asyncio

import features.trial.service as trial_service_mod
from features.trial.service import TrialService
from shared.config.targets import TargetsConfig
from shared.secret_store_service import SecretStoreService


def test_activation_promotes_corrected_email_to_primary(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    seed = TargetsConfig(path=str(config_path))
    seed.load()
    seed.set_email("wrong@throwaway.test")  # what the user typed at the gate
    seed.save()

    monkeypatch.setattr(
        trial_service_mod, "TargetsConfig", lambda: TargetsConfig(path=str(config_path))
    )

    svc = TrialService(secret_store=SecretStoreService())
    result = asyncio.run(
        svc.activate(token="x" * 12, email="real@company.com", email_tier="business")
    )
    assert result.success

    reloaded = TargetsConfig(path=str(config_path))
    reloaded.load()
    # Verified trial address wins: it is now the primary identity.
    assert reloaded.get_email() == "real@company.com"
    assert reloaded.get_trial_config().get("email") == "real@company.com"
