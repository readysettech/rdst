"""Unit tests for RDST web env restore preflight helper."""

import argparse
from types import SimpleNamespace
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


def _web_args(ui: str = "auto") -> argparse.Namespace:
    return argparse.Namespace(
        command="web",
        host="127.0.0.1",
        port=8787,
        reload=False,
        ui=ui,
        clear=False,
    )


def _fake_uvicorn() -> SimpleNamespace:
    return SimpleNamespace(run=Mock())


def test_web_auto_mode_serves_embedded_frontend(tmp_path):
    dist_dir = tmp_path / "web_dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("ok", encoding="utf-8")
    fake_uvicorn = _fake_uvicorn()

    with patch.object(rdst, "_resolve_embedded_web_dist_dir", return_value=dist_dir):
        with patch.object(
            rdst, "_restore_web_required_env_vars", return_value=([], [], [])
        ):
            with patch("lib.api.app.create_app", return_value=Mock()) as create_app:
                with patch.dict("sys.modules", {"uvicorn": fake_uvicorn}):
                    with patch.dict("os.environ", {}, clear=True):
                        result = rdst.execute_command(Mock(), _web_args())
                        assert rdst.os.environ["RDST_WEB_SERVE_STATIC"] == "1"
                        assert rdst.os.environ["RDST_WEB_DIST_DIR"] == str(dist_dir)

    assert result.ok is True
    create_app.assert_called_once_with(static_dist_dir=str(dist_dir))
    fake_uvicorn.run.assert_called_once()


def test_web_auto_mode_falls_back_to_api_only_without_embedded_frontend():
    fake_uvicorn = _fake_uvicorn()

    with patch.object(rdst, "_resolve_embedded_web_dist_dir", return_value=None):
        with patch.object(
            rdst, "_restore_web_required_env_vars", return_value=([], [], [])
        ):
            with patch("lib.api.app.create_app", return_value=Mock()) as create_app:
                with patch.dict("sys.modules", {"uvicorn": fake_uvicorn}):
                    with patch.dict(
                        "os.environ",
                        {"RDST_WEB_DIST_DIR": "/tmp/stale"},
                        clear=True,
                    ):
                        result = rdst.execute_command(Mock(), _web_args())
                        assert rdst.os.environ["RDST_WEB_SERVE_STATIC"] == "0"
                        assert "RDST_WEB_DIST_DIR" not in rdst.os.environ

    assert result.ok is True
    create_app.assert_called_once_with(static_dist_dir=None)
    fake_uvicorn.run.assert_called_once()


def test_web_dist_mode_requires_embedded_frontend():
    fake_uvicorn = _fake_uvicorn()

    with patch.object(rdst, "_resolve_embedded_web_dist_dir", return_value=None):
        with patch.dict("sys.modules", {"uvicorn": fake_uvicorn}):
            with patch.dict("os.environ", {}, clear=True):
                result = rdst.execute_command(Mock(), _web_args(ui="dist"))

    assert result.ok is False
    assert "Embedded RDST frontend not found" in result.message
