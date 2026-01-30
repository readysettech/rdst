"""Unit tests for RDST web env restore preflight helper."""

from unittest.mock import Mock, patch

import rdst


def test_restore_web_required_env_vars_reports_restored_and_missing():
    mock_secret_store = Mock()
    mock_secret_store.restore_required.return_value = {
        "restored": ["PROD_DB_PASSWORD"],
        "missing": ["ANTHROPIC_API_KEY"],
        "errors": [],
    }

    mock_service = Mock()
    mock_service.get_required_names_for_restore.return_value = [
        "PROD_DB_PASSWORD",
        "ANTHROPIC_API_KEY",
    ]
    mock_service.secret_store = mock_secret_store

    with patch(
        "lib.services.env_requirements_service.EnvRequirementsService",
        return_value=mock_service,
    ):
        with patch.dict(
            "os.environ",
            {"PROD_DB_PASSWORD": "restored"},
            clear=True,
        ):
            restored, missing, errors = rdst._restore_web_required_env_vars()

    assert restored == ["PROD_DB_PASSWORD"]
    assert missing == ["ANTHROPIC_API_KEY"]
    assert errors == []


def test_restore_web_required_env_vars_handles_failures_without_blocking():
    with patch(
        "lib.services.env_requirements_service.EnvRequirementsService",
        side_effect=RuntimeError("boom"),
    ):
        restored, missing, errors = rdst._restore_web_required_env_vars()

    assert restored == []
    assert missing == []
    assert len(errors) == 1
    assert "Preflight env restore failed" in errors[0]


def test_clear_web_required_env_vars_reports_cleared_and_missing():
    mock_secret_store = Mock()
    mock_secret_store.clear_required.return_value = {
        "cleared": ["PROD_DB_PASSWORD"],
        "missing": ["ANTHROPIC_API_KEY"],
        "errors": [],
    }

    mock_service = Mock()
    mock_service.get_allowed_secret_names.return_value = [
        "PROD_DB_PASSWORD",
        "ANTHROPIC_API_KEY",
    ]
    mock_service.secret_store = mock_secret_store

    with patch(
        "lib.services.env_requirements_service.EnvRequirementsService",
        return_value=mock_service,
    ):
        cleared, missing, errors = rdst._clear_web_required_env_vars()

    assert cleared == ["PROD_DB_PASSWORD"]
    assert missing == ["ANTHROPIC_API_KEY"]
    assert errors == []


def test_clear_web_required_env_vars_handles_failures_without_blocking():
    with patch(
        "lib.services.env_requirements_service.EnvRequirementsService",
        side_effect=RuntimeError("boom"),
    ):
        cleared, missing, errors = rdst._clear_web_required_env_vars()

    assert cleared == []
    assert missing == []
    assert len(errors) == 1
    assert "Keyring clear failed" in errors[0]
