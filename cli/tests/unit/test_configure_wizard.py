"""Unit tests for features.configure.cli.wizard.

Verifies the wizard no longer auto-deploys a Readyset cache during configure
(deploy is now its own command and shouldn't be coupled to configure).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestConfigureWizardNoCacheDeploy:

    def test_wizard_module_does_not_import_deploy_command(self):
        import features.configure.cli.wizard as wiz
        src = Path(wiz.__file__).read_text()
        assert "from features.cache.cli.deploy import DeployCommand" not in src
        assert "DeployCommand()" not in src
        assert '"cache_deployed":' not in src

    def test_wizard_can_be_imported(self):
        from features.configure.cli.wizard import ConfigurationWizard
        assert ConfigurationWizard is not None
