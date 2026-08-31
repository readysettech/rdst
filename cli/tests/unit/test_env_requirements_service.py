"""Unit tests for env requirement resolution service."""

from unittest.mock import Mock, patch

from shared.env_requirements_service import EnvRequirementsService
from shared.account_session import SESSION_SECRET_NAMES


class FakeSecretStore:
    def __init__(self, values=None):
        self.values = values or {}

    def get_secret(self, name: str):
        return self.values.get(name)

    def is_available(self) -> bool:
        return True


def _mock_config():
    cfg = Mock()
    cfg.list_targets.return_value = ["prod", "staging", "shared"]
    cfg.get.side_effect = lambda name: {
        "prod": {"password_env": "PROD_DB_PASSWORD"},
        "staging": {"password_env": "STAGE_DB_PASSWORD"},
        "shared": {"password_env": "PROD_DB_PASSWORD"},
    }.get(name, {})
    cfg.get_trial_config.return_value = {}
    return cfg


def test_get_requirements_resolves_sources(monkeypatch):
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)
    monkeypatch.delenv("STAGE_DB_PASSWORD", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    service = EnvRequirementsService(
        secret_store=FakeSecretStore(
            {
                "PROD_DB_PASSWORD": "persisted-prod",
                "ANTHROPIC_API_KEY": "persisted-anthropic",
            }
        )
    )

    with patch.object(service, "_load_config", return_value=_mock_config()):
        requirements = service.get_requirements()

    prod_req = next(
        r for r in requirements if r["kind"] == "target_password" and r["accepted_names"] == ["PROD_DB_PASSWORD"]
    )
    stage_req = next(
        r for r in requirements if r["kind"] == "target_password" and r["accepted_names"] == ["STAGE_DB_PASSWORD"]
    )
    anthropic_req = next(r for r in requirements if r["kind"] == "anthropic_api_key")

    assert prod_req["source"] == "secure_store"
    assert prod_req["satisfied"] is True
    assert prod_req["target"] is None  # Shared env var across multiple targets

    assert stage_req["source"] == "missing"
    assert stage_req["satisfied"] is False
    assert stage_req["target"] == "staging"

    assert anthropic_req["source"] == "secure_store"
    assert anthropic_req["satisfied"] is True


def test_anthropic_requirement_satisfied_by_process_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "in-process")

    service = EnvRequirementsService(secret_store=FakeSecretStore())
    with patch.object(service, "_load_config", return_value=_mock_config()):
        requirements = service.get_requirements()

    anthropic_req = next(r for r in requirements if r["kind"] == "anthropic_api_key")
    assert anthropic_req["source"] == "process_env"
    assert anthropic_req["satisfied"] is True

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_anthropic_requirement_ignores_legacy_trial_env(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("RDST_TRIAL_TOKEN", "in-trial")

    service = EnvRequirementsService(secret_store=FakeSecretStore())
    with patch.object(service, "_load_config", return_value=_mock_config()):
        requirements = service.get_requirements()

    anthropic_req = next(r for r in requirements if r["kind"] == "anthropic_api_key")
    assert anthropic_req["source"] == "missing"
    assert anthropic_req["satisfied"] is False

    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)


def test_anthropic_requirement_ignores_legacy_trial_keyring_entry(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)

    service = EnvRequirementsService(
        secret_store=FakeSecretStore({"RDST_TRIAL_TOKEN": "stored-trial-token"})
    )
    with patch.object(service, "_load_config", return_value=_mock_config()):
        requirements = service.get_requirements()

    anthropic_req = next(r for r in requirements if r["kind"] == "anthropic_api_key")
    assert anthropic_req["source"] == "missing"
    assert anthropic_req["satisfied"] is False


def test_anthropic_requirement_satisfied_by_api_key_in_keyring(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)

    service = EnvRequirementsService(
        secret_store=FakeSecretStore({"ANTHROPIC_API_KEY": "stored-anthropic-key"})
    )
    with patch.object(service, "_load_config", return_value=_mock_config()):
        requirements = service.get_requirements()

    anthropic_req = next(r for r in requirements if r["kind"] == "anthropic_api_key")
    assert anthropic_req["source"] == "secure_store"
    assert anthropic_req["satisfied"] is True


def test_readyset_account_requires_a_refreshable_session(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    service = EnvRequirementsService(secret_store=FakeSecretStore())

    with (
        patch.object(service, "_load_config", return_value=_mock_config()),
        patch("shared.env_requirements_service.access_token", return_value="fresh-token"),
    ):
        requirement = next(
            item
            for item in service.get_requirements()
            if item["kind"] == "anthropic_api_key"
        )

    assert requirement["satisfied"] is True
    assert requirement["source"] == "readyset_account"


def test_expired_unrefreshable_account_does_not_open_gate(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    service = EnvRequirementsService(secret_store=FakeSecretStore())

    with (
        patch.object(service, "_load_config", return_value=_mock_config()),
        patch("shared.env_requirements_service.access_token", return_value=None),
    ):
        requirement = next(
            item
            for item in service.get_requirements()
            if item["kind"] == "anthropic_api_key"
        )

    assert requirement["satisfied"] is False
    assert requirement["source"] == "missing"


def test_anthropic_requirement_ignores_config_backed_active_trial(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)

    cfg = _mock_config()
    cfg.get_trial_config.return_value = {"status": "active", "token": "trial-token"}

    service = EnvRequirementsService(secret_store=FakeSecretStore())
    with patch.object(service, "_load_config", return_value=cfg):
        requirements = service.get_requirements()

    anthropic_req = next(r for r in requirements if r["kind"] == "anthropic_api_key")
    assert anthropic_req["source"] == "missing"
    assert anthropic_req["satisfied"] is False


def test_anthropic_requirement_ignores_config_backed_exhausted_trial(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("RDST_TRIAL_TOKEN", raising=False)

    cfg = _mock_config()
    cfg.get_trial_config.return_value = {"status": "exhausted", "token": "trial-token"}

    service = EnvRequirementsService(secret_store=FakeSecretStore())
    with patch.object(service, "_load_config", return_value=cfg):
        requirements = service.get_requirements()

    anthropic_req = next(r for r in requirements if r["kind"] == "anthropic_api_key")
    assert anthropic_req["source"] == "missing"
    assert anthropic_req["satisfied"] is False


def test_anthropic_env_var_precedence_over_config_trial(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")

    cfg = _mock_config()
    cfg.get_trial_config.return_value = {"status": "active", "token": "trial-token"}

    service = EnvRequirementsService(secret_store=FakeSecretStore())
    with patch.object(service, "_load_config", return_value=cfg):
        requirements = service.get_requirements()

    anthropic_req = next(r for r in requirements if r["kind"] == "anthropic_api_key")
    assert anthropic_req["source"] == "process_env"
    assert anthropic_req["satisfied"] is True

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


def test_get_allowed_names_includes_targets_and_anthropic():
    service = EnvRequirementsService(secret_store=FakeSecretStore())

    with patch.object(service, "_load_config", return_value=_mock_config()):
        names = service.get_allowed_secret_names()

    assert "PROD_DB_PASSWORD" in names
    assert "STAGE_DB_PASSWORD" in names
    assert "RDST_TRIAL_TOKEN" not in names
    assert "ANTHROPIC_API_KEY" in names

    with patch.object(service, "_load_config", return_value=_mock_config()):
        clearable_names = service.get_clearable_secret_names()
    assert "RDST_TRIAL_TOKEN" in clearable_names
    assert set(SESSION_SECRET_NAMES).issubset(clearable_names)

    with patch.object(service, "_load_config", return_value=_mock_config()):
        restored_names = service.get_required_names_for_restore()
    assert set(SESSION_SECRET_NAMES).issubset(restored_names)


def test_target_with_direct_password_shows_config_source(monkeypatch):
    """When a target has a direct password in config, source should be 'config'."""
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)

    cfg = Mock()
    cfg.list_targets.return_value = ["prod"]
    cfg.get.side_effect = lambda name: {
        "prod": {"password": "direct-secret", "password_env": "PROD_DB_PASSWORD"},
    }.get(name, {})

    service = EnvRequirementsService(secret_store=FakeSecretStore())
    with patch.object(service, "_load_config", return_value=cfg):
        requirements = service.get_requirements()

    prod_req = next(
        r for r in requirements if r["kind"] == "target_password"
    )
    assert prod_req["source"] == "config"
    assert prod_req["satisfied"] is True


def test_target_password_from_keychain(monkeypatch):
    """When password_env is only in keychain, source should be 'secure_store'."""
    monkeypatch.delenv("PROD_DB_PASSWORD", raising=False)

    cfg = Mock()
    cfg.list_targets.return_value = ["prod"]
    cfg.get.side_effect = lambda name: {
        "prod": {"password_env": "PROD_DB_PASSWORD"},
    }.get(name, {})

    service = EnvRequirementsService(
        secret_store=FakeSecretStore({"PROD_DB_PASSWORD": "keychain-val"})
    )
    with patch.object(service, "_load_config", return_value=cfg):
        requirements = service.get_requirements()

    prod_req = next(
        r for r in requirements if r["kind"] == "target_password"
    )
    assert prod_req["source"] == "secure_store"
    assert prod_req["satisfied"] is True


def test_target_without_password_source_is_missing(monkeypatch):
    monkeypatch.delenv("RDST_IMPORTED_PASSWORD", raising=False)
    cfg = Mock()
    cfg.list_targets.return_value = ["imported"]
    cfg.get.return_value = {
        "engine": "postgresql",
        "host": "db.example.com",
        "user": "app",
    }

    service = EnvRequirementsService(secret_store=FakeSecretStore())
    with patch.object(service, "_load_config", return_value=cfg):
        requirements = service.get_requirements()

    password_req = next(
        requirement
        for requirement in requirements
        if requirement["kind"] == "target_password"
    )
    assert password_req == {
        "kind": "target_password",
        "accepted_names": ["RDST_IMPORTED_PASSWORD"],
        "target": "imported",
        "satisfied": False,
        "source": "missing",
    }
