"""Unit tests for features.configure.cli.wizard.

Verifies the wizard no longer auto-deploys a Readyset cache during configure
(deploy is now its own command and shouldn't be coupled to configure).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestConfigureWizardNoCacheDeploy:

    def test_wizard_module_does_not_import_deploy_command(self):
        import features.configure.cli.wizard as wiz
        src = Path(wiz.__file__).read_text(encoding="utf-8")
        assert "from features.cache.cli.deploy import DeployCommand" not in src
        assert "DeployCommand()" not in src
        assert '"cache_deployed":' not in src

    def test_wizard_can_be_imported(self):
        from features.configure.cli.wizard import ConfigurationWizard
        assert ConfigurationWizard is not None

    def test_menu_removal_uses_lifecycle_aware_service(self, monkeypatch):
        from features.configure.cli.wizard import ConfigurationWizard

        removed: list[str] = []

        class Config:
            def list_targets(self):
                return ["app"]

            def get(self, name):
                return {"engine": "postgresql"} if name == "app" else None

            def remove(self, name):
                pytest.fail("wizard must not bypass ConfigureService")

            def save(self):
                pytest.fail("wizard must not bypass ConfigureService")

        class Service:
            async def remove_target(self, name):
                removed.append(name)
                yield type(
                    "Success",
                    (),
                    {
                        "type": "success",
                        "message": "removed",
                    },
                )()

        monkeypatch.setattr("features.configure.service.ConfigureService", Service)
        wizard = ConfigurationWizard()
        monkeypatch.setattr(wizard, "_show_target_details", lambda *args: None)
        monkeypatch.setattr(wizard, "_show_success", lambda *args: None)

        result = wizard._remove_target(
            Config(), {"name": "app", "confirm": True}
        )

        assert result.ok is True
        assert removed == ["app"]
