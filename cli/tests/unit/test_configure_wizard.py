"""Unit tests for features.configure.cli.wizard.

Verifies the wizard no longer auto-deploys a Readyset cache during configure
(deploy is now its own command and shouldn't be coupled to configure).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from unittest.mock import Mock, patch

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


@pytest.mark.parametrize(
    ("module_name", "expected"),
    [
        (
            "pymysql",
            "Missing database driver: pymysql\nInstall with: pip install pymysql",
        ),
        (
            "paramiko",
            "SSH support is unavailable in this build because the paramiko module is missing.",
        ),
        ("dependency_from_plugin", "Missing required module: dependency_from_plugin."),
    ],
)
def test_connection_test_names_the_module_that_failed_to_import(
    monkeypatch, module_name, expected
):
    from features.configure.cli.wizard import ConfigurationWizard

    missing = ModuleNotFoundError(
        f"No module named '{module_name}'",
        name=module_name,
    )
    monkeypatch.setattr(
        "features.configure.cli.wizard.resolve_connection_params",
        Mock(side_effect=missing),
    )
    wizard = ConfigurationWizard(console=Mock())
    wizard._show_info = Mock()

    result = wizard._test_connection(
        {
            "engine": "mysql",
            "host": "db.example.com",
            "port": 3306,
            "database": "app",
            "user": "app",
            "password": "secret",
        }
    )

    assert result.ok is False
    assert result.message == expected


class TestConfigureWizardSsh:

    def test_ssh_step_builds_nested_config_from_discovered_key(self):
        from features.configure.cli.wizard import ConfigurationWizard

        wizard = ConfigurationWizard(console=Mock())
        wizard._confirm = Mock(return_value=True)
        wizard._prompt_text = Mock(
            side_effect=["jump.example.com", "2222", "ec2-user"]
        )
        wizard._interactive_select = Mock(return_value="Key file: /tmp/jump.pem")

        with patch(
            "features.configure.cli.wizard.discover_ssh_auth_options",
            return_value=[
                {
                    "kind": "file",
                    "label": "Key file: /tmp/jump.pem",
                    "key_path": "/tmp/jump.pem",
                }
            ],
        ):
            result = wizard._collect_ssh_settings({})

        assert result == {
            "ssh": {
                "host": "jump.example.com",
                "port": 2222,
                "user": "ec2-user",
                "key_path": "/tmp/jump.pem",
            }
        }

    def test_edit_can_remove_existing_ssh_config(self):
        from features.configure.cli.wizard import ConfigurationWizard

        wizard = ConfigurationWizard(console=Mock())
        wizard._confirm = Mock(return_value=False)

        assert wizard._collect_ssh_settings(
            {"ssh": {"host": "jump.example.com"}}
        ) == {"_remove_ssh": True}

    def test_ssh_agent_selection_omits_key_path(self):
        from features.configure.cli.wizard import ConfigurationWizard

        wizard = ConfigurationWizard(console=Mock())
        wizard._confirm = Mock(return_value=True)
        wizard._prompt_text = Mock(
            side_effect=["jump.example.com", "22", "ec2-user"]
        )
        wizard._interactive_select = Mock(return_value="SSH agent: ssh-ed25519 abc")

        with patch(
            "features.configure.cli.wizard.discover_ssh_auth_options",
            return_value=[
                {
                    "kind": "agent",
                    "label": "SSH agent: ssh-ed25519 abc",
                }
            ],
        ):
            result = wizard._collect_ssh_settings({})

        assert result["ssh"] == {
            "host": "jump.example.com",
            "port": 22,
            "user": "ec2-user",
        }

    def test_matching_ssh_config_entry_is_an_authentication_choice(self):
        from features.configure.cli.wizard import ConfigurationWizard

        wizard = ConfigurationWizard(console=Mock())
        wizard._confirm = Mock(return_value=True)
        wizard._prompt_text = Mock(
            side_effect=["jump-prod", "22", "ec2-user"]
        )
        wizard._interactive_select = Mock(
            return_value="SSH config Host jump-prod"
        )

        with patch(
            "features.configure.cli.wizard.TargetsConfig"
        ) as targets_config, patch(
            "features.configure.cli.wizard.discover_ssh_auth_options",
            return_value=[
                {
                    "kind": "config",
                    "label": "SSH config Host jump-prod",
                }
            ],
        ):
            targets_config.return_value.list_ssh_hosts.return_value = []
            result = wizard._collect_ssh_settings({})

        assert result["ssh"] == {
            "host": "jump-prod",
            "port": 22,
            "user": "ec2-user",
        }

    def test_ssh_step_prompts_for_manual_path_when_discovery_is_empty(self):
        from features.configure.cli.wizard import ConfigurationWizard

        wizard = ConfigurationWizard(console=Mock())
        wizard._confirm = Mock(return_value=True)
        wizard._prompt_text = Mock(
            side_effect=[
                "jump.example.com",
                "22",
                "ec2-user",
                "/keys/manual.pem",
            ]
        )
        wizard._interactive_select = Mock()

        with patch(
            "features.configure.cli.wizard.TargetsConfig"
        ) as targets_config, patch(
            "features.configure.cli.wizard.discover_ssh_auth_options",
            return_value=[],
        ):
            targets_config.return_value.list_ssh_hosts.return_value = []
            result = wizard._collect_ssh_settings({})

        assert result["ssh"]["key_path"] == "/keys/manual.pem"
        wizard._interactive_select.assert_not_called()

    def test_reusable_jump_host_still_prompts_for_authentication(self):
        from features.configure.cli.wizard import ConfigurationWizard

        wizard = ConfigurationWizard(console=Mock())
        wizard._confirm = Mock(return_value=True)
        wizard._interactive_select = Mock(
            side_effect=[
                "Saved jump host: production",
                "~/.ssh: production.pem",
            ]
        )
        wizard._prompt_text = Mock(
            side_effect=["jump.example.com", "22", "ec2-user"]
        )

        with patch(
            "features.configure.cli.wizard.TargetsConfig"
        ) as targets_config, patch(
            "features.configure.cli.wizard.discover_ssh_auth_options",
            return_value=[
                {
                    "kind": "file",
                    "label": "~/.ssh: production.pem",
                    "key_path": "/keys/production.pem",
                }
            ],
        ) as discover:
            profiles = targets_config.return_value
            profiles.list_ssh_hosts.return_value = ["production"]
            profiles.get_ssh_host.return_value = {
                "host": "jump.example.com",
                "port": 22,
                "user": "ec2-user",
            }
            result = wizard._collect_ssh_settings({})

        discover.assert_called_once_with("jump.example.com")
        assert wizard._interactive_select.call_args_list[-1].args[0] == (
            "Choose SSH authentication"
        )
        assert result["ssh"] == {
            "host": "jump.example.com",
            "port": 22,
            "user": "ec2-user",
            "key_path": "/keys/production.pem",
        }

    def test_ssh_flags_parse_and_build_nested_config(self):
        from features.configure.cli.wizard import ConfigurationWizard
        from shared.cli.parser_data import build_all_subparsers

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        build_all_subparsers(subparsers)
        args = parser.parse_args(
            [
                "configure",
                "add",
                "--target",
                "private-db",
                "--host",
                "db.internal",
                "--user",
                "app",
                "--database",
                "app",
                "--ssh-host",
                "jump.example.com",
                "--ssh-port",
                "2222",
                "--ssh-user",
                "ec2-user",
                "--ssh-key",
                "/keys/jump.pem",
            ]
        )

        config = ConfigurationWizard(console=Mock())._collect_config_from_args(
            vars(args),
            {},
            "private-db",
        )

        assert config["ssh"] == {
            "host": "jump.example.com",
            "port": 2222,
            "user": "ec2-user",
            "key_path": "/keys/jump.pem",
        }

    def test_key_discovery_scans_common_files_and_matching_host_config(
        self, tmp_path
    ):
        from features.configure.cli.wizard import discover_ssh_auth_options

        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        ed25519 = ssh_dir / "id_ed25519"
        ed25519.write_text(
            "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n"
            "-----END OPENSSH PRIVATE KEY-----\n",
            encoding="utf-8",
        )
        pem = ssh_dir / "jump.pem"
        pem.write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nfake\n"
            "-----END RSA PRIVATE KEY-----\n",
            encoding="utf-8",
        )
        (ssh_dir / "id_ed25519.pub").write_text("not private", encoding="utf-8")
        (ssh_dir / "config").write_text(
            "Host jump-prod\n"
            "  IdentityFile ~/.ssh/jump.pem\n"
            "Host other\n"
            "  IdentityFile ~/.ssh/other.pem\n",
            encoding="utf-8",
        )

        options = discover_ssh_auth_options(
            "jump-prod",
            ssh_dir=ssh_dir,
            agent_factory=lambda: Mock(get_keys=lambda: (), close=lambda: None),
        )

        paths = {option.get("key_path") for option in options}
        assert str(ed25519) in paths
        assert str(pem) in paths
        assert any(
            option["kind"] == "config" and "jump-prod" in option["label"]
            for option in options
        )


class TestConfigureWizardEngineDefaults:
    def test_engine_change_replaces_old_default_port(self):
        from features.configure.cli.wizard import ConfigurationWizard

        wizard = ConfigurationWizard(console=Mock())
        wizard._prompt_text = Mock(
            side_effect=["db.example.com", "3306", "app", "readonly"]
        )

        result = wizard._collect_connection_details(
            "mysql", {"engine": "postgresql", "port": 5432}
        )

        assert wizard._prompt_text.call_args_list[1].args[2] == "3306"
        assert result["port"] == 3306

    def test_engine_change_keeps_custom_port(self):
        from features.configure.cli.wizard import ConfigurationWizard

        wizard = ConfigurationWizard(console=Mock())
        wizard._prompt_text = Mock(
            side_effect=["db.example.com", "15432", "app", "readonly"]
        )

        result = wizard._collect_connection_details(
            "mysql", {"engine": "postgresql", "port": 15432}
        )

        assert wizard._prompt_text.call_args_list[1].args[2] == "15432"
        assert result["port"] == 15432


class TestConfigureWizardPasswords:
    def test_interactive_security_asks_for_password_not_environment_name(self):
        from features.configure.cli.wizard import ConfigurationWizard

        wizard = ConfigurationWizard(console=Mock())
        wizard._confirm = Mock(return_value=False)
        wizard._prompt_text = Mock()

        with patch(
            "features.configure.cli.wizard.getpass.getpass",
            return_value="database-secret",
        ):
            result = wizard._collect_security_settings("customer prod", {})

        assert result["password"] == "database-secret"
        assert result["password_env"] == "RDST_CUSTOMER_PROD_PASSWORD"
        wizard._prompt_text.assert_not_called()

    def test_connection_string_password_uses_automatic_internal_pointer(self):
        from features.configure.cli.wizard import ConfigurationWizard

        password = "database-" + "secret"
        uri = "postgresql://" + f"alice:{password}@db.example.com/app"
        config = ConfigurationWizard(console=Mock())._collect_config_from_args(
            {"connection_string": uri},
            {},
            "customer prod",
        )

        assert config["password"] == "database-secret"
        assert config["password_env"] == "RDST_CUSTOMER_PROD_PASSWORD"

    def test_password_env_flag_remains_an_explicit_power_user_override(self):
        from features.configure.cli.wizard import ConfigurationWizard

        pointer = ConfigurationWizard(console=Mock())._resolve_password_env(
            {"password_env": "CUSTOM_DB_PASSWORD"},
            {},
            "prod",
        )

        assert pointer == "CUSTOM_DB_PASSWORD"
