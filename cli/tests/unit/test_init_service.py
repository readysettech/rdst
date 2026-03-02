"""
Unit tests for InitService.

Tests the initialization workflow service including status checking,
target validation, LLM configuration, and error handling.
"""

import pytest
import os
from unittest.mock import Mock, patch, MagicMock
from typing import Any, Dict, List

# Import from lib package (conftest.py adds rdst root to path)
from lib.services.types import InitStatus, InitValidationResult
from lib.services.init_service import InitService


class TestInitServiceInit:
    """Tests for InitService initialization."""

    def test_initialization(self):
        """Test service initializes correctly."""
        service = InitService()
        assert service is not None

    def test_has_required_methods(self):
        """Test service has required methods."""
        service = InitService()
        assert hasattr(service, "get_status")
        assert hasattr(service, "validate_all")
        assert hasattr(service, "check_llm")
        assert hasattr(service, "mark_complete")


class TestInitServiceGetStatus:
    """Tests for get_status() method."""

    @pytest.fixture
    def service(self):
        """Create InitService instance."""
        return InitService()

    @pytest.fixture
    def mock_config(self):
        """Create mock TargetsConfig."""
        cfg = Mock()
        cfg.is_init_completed.return_value = True
        cfg.get_default.return_value = "prod"
        cfg.list_targets.return_value = ["prod", "staging"]
        cfg.get.side_effect = lambda name: {
            "prod": {"engine": "postgresql", "password_env": "PROD_DB_PASS"},
            "staging": {"engine": "mysql", "password": "secret"},
        }.get(name)
        cfg.get_llm_config.return_value = {"provider": "claude"}
        return cfg

    def test_returns_init_status(self, service, mock_config):
        """Test get_status returns InitStatus."""
        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                status = service.get_status()

        assert isinstance(status, InitStatus)
        assert status.initialized is True

    def test_lists_all_targets(self, service, mock_config):
        """Test get_status lists all configured targets."""
        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                status = service.get_status()

        assert len(status.targets) == 2
        target_names = [t["name"] for t in status.targets]
        assert "prod" in target_names
        assert "staging" in target_names

    def test_identifies_default_target(self, service, mock_config):
        """Test get_status correctly identifies default target."""
        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                status = service.get_status()

        assert status.default_target == "prod"
        # Check is_default flag in targets
        prod_target = next(t for t in status.targets if t["name"] == "prod")
        assert prod_target["is_default"] is True

    def test_checks_password_from_env_var(self, service, mock_config):
        """Test get_status checks password from environment variable."""
        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.dict(
                os.environ, {"ANTHROPIC_API_KEY": "test-key", "PROD_DB_PASS": "secret"}
            ):
                status = service.get_status()

        prod_target = next(t for t in status.targets if t["name"] == "prod")
        assert prod_target["has_password"] is True

    def test_checks_password_direct(self, service, mock_config):
        """Test get_status checks direct password."""
        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                status = service.get_status()

        staging_target = next(t for t in status.targets if t["name"] == "staging")
        assert staging_target["has_password"] is True

    def test_checks_llm_configured_with_api_key(self, service, mock_config):
        """Test get_status checks LLM is configured."""
        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
                status = service.get_status()

        assert status.llm_configured is True

    def test_checks_llm_configured_with_trial_token(self, service, mock_config):
        """Test get_status accepts RDST_TRIAL_TOKEN."""
        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.dict(os.environ, {"RDST_TRIAL_TOKEN": "test-token"}, clear=True):
                status = service.get_status()

        assert status.llm_configured is True

    def test_llm_not_configured_without_api_key(self, service, mock_config):
        """Test get_status shows LLM not configured without API key."""
        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.dict(os.environ, {}, clear=True):
                with patch("lib.services.anthropic_env._has_active_trial", return_value=False):
                    # Remove ANTHROPIC_API_KEY
                    if "ANTHROPIC_API_KEY" in os.environ:
                        del os.environ["ANTHROPIC_API_KEY"]
                    status = service.get_status()

        assert status.llm_configured is False

    def test_auto_configures_llm_when_provider_missing_and_api_key_set(self, service):
        """Test get_status auto-configures Claude for web-only onboarding flow."""
        llm_config: Dict[str, Any] = {}
        cfg = Mock()
        cfg.is_init_completed.return_value = True
        cfg.get_default.return_value = "prod"
        cfg.list_targets.return_value = ["prod"]
        cfg.get.return_value = {"engine": "postgresql", "password": "secret"}
        cfg.get_llm_config.side_effect = lambda: dict(llm_config)
        cfg.set_llm_config.side_effect = lambda update: llm_config.update(update)
        cfg.save = Mock()

        with patch.object(service, "_load_config", return_value=cfg):
            with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
                status = service.get_status()

        assert status.llm_configured is True
        assert llm_config.get("provider") == "claude"
        cfg.set_llm_config.assert_called_once()
        cfg.save.assert_called_once()


class TestInitServiceValidateAll:
    """Tests for validate_all() method."""

    @pytest.fixture
    def service(self):
        """Create InitService instance."""
        return InitService()

    @pytest.fixture
    def mock_config(self):
        """Create mock TargetsConfig."""
        cfg = Mock()
        cfg.list_targets.return_value = ["prod"]
        cfg.get.return_value = {"engine": "postgresql", "host": "localhost"}
        cfg.upsert = Mock()
        cfg.save = Mock()
        cfg.get_llm_config.return_value = {"provider": "claude"}
        return cfg

    def test_returns_validation_result(self, service, mock_config):
        """Test validate_all returns InitValidationResult."""
        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.object(
                service, "_test_target", return_value=(True, "Connected", {})
            ):
                with patch.object(service, "check_llm", return_value={"success": True}):
                    result = service.validate_all()

        assert isinstance(result, InitValidationResult)
        assert hasattr(result, "target_results")
        assert hasattr(result, "llm_result")

    def test_validates_all_targets(self, service, mock_config):
        """Test validate_all validates all configured targets."""
        mock_config.list_targets.return_value = ["prod", "staging"]
        mock_config.get.side_effect = lambda name: {"engine": "postgresql"}

        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.object(
                service, "_test_target", return_value=(True, "Connected", {})
            ):
                with patch.object(service, "check_llm", return_value={"success": True}):
                    result = service.validate_all()

        assert len(result.target_results) == 2

    def test_validates_specific_targets(self, service, mock_config):
        """Test validate_all can validate specific targets only."""
        mock_config.list_targets.return_value = ["prod", "staging", "dev"]
        mock_config.get.side_effect = lambda name: {"engine": "postgresql"}

        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.object(
                service, "_test_target", return_value=(True, "Connected", {})
            ):
                with patch.object(service, "check_llm", return_value={"success": True}):
                    result = service.validate_all(target_names=["prod"])

        assert len(result.target_results) == 1
        assert result.target_results[0]["name"] == "prod"

    def test_updates_target_verification_status(self, service, mock_config):
        """Test validate_all updates target verification status."""
        verification = {"attempted": True, "success": True}

        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.object(
                service,
                "_test_target",
                return_value=(True, "Connected", verification),
            ):
                with patch.object(service, "check_llm", return_value={"success": True}):
                    service.validate_all()

        # Should have called upsert to update target config
        mock_config.upsert.assert_called()
        mock_config.save.assert_called()

    def test_includes_llm_result(self, service, mock_config):
        """Test validate_all includes LLM validation result."""
        llm_result = {"success": True, "model": "claude-sonnet-4-20250514"}

        with patch.object(service, "_load_config", return_value=mock_config):
            with patch.object(
                service, "_test_target", return_value=(True, "Connected", {})
            ):
                with patch.object(service, "check_llm", return_value=llm_result):
                    result = service.validate_all()

        assert result.llm_result == llm_result


class TestInitServiceCheckLLM:
    """Tests for check_llm() method."""

    @pytest.fixture
    def service(self):
        """Create InitService instance."""
        return InitService()

    @pytest.fixture
    def mock_config(self):
        """Create mock TargetsConfig."""
        cfg = Mock()
        cfg.get_llm_config.return_value = {"provider": "claude"}
        return cfg

    def test_llm_not_configured(self, service, mock_config):
        """Test check_llm when LLM provider is not claude."""
        mock_config.get_llm_config.return_value = {"provider": "openai"}

        with patch.object(service, "_load_config", return_value=mock_config):
            result = service.check_llm()

        assert result["success"] is False
        assert "not configured" in result["error"]

    def test_anthropic_key_missing(self, service, mock_config):
        """Test check_llm when ANTHROPIC_API_KEY is not set."""
        with patch.dict(os.environ, {}, clear=True):
            with patch("lib.services.anthropic_env._has_active_trial", return_value=False):
                if "ANTHROPIC_API_KEY" in os.environ:
                    del os.environ["ANTHROPIC_API_KEY"]

                result = service.check_llm(mock_config)

        assert result["success"] is False
        assert "ANTHROPIC_API_KEY" in result["error"]

    def test_llm_api_success(self, service, mock_config):
        """Test check_llm with successful API call."""
        mock_llm = Mock()
        mock_llm.query.return_value = {"text": "pong"}

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("lib.llm_manager.llm_manager.LLMManager", return_value=mock_llm):
                result = service.check_llm(mock_config)

        assert result["success"] is True

    def test_llm_api_success_with_trial_token(self, service, mock_config):
        """Test check_llm succeeds when only RDST_TRIAL_TOKEN is set."""
        mock_llm = Mock()
        mock_llm.query.return_value = {"text": "pong"}

        with patch.dict(os.environ, {"RDST_TRIAL_TOKEN": "test-token"}, clear=True):
            with patch("lib.llm_manager.llm_manager.LLMManager", return_value=mock_llm):
                result = service.check_llm(mock_config)

        assert result["success"] is True

    def test_llm_api_failure(self, service, mock_config):
        """Test check_llm when API call fails."""
        mock_llm = Mock()
        mock_llm.query.side_effect = Exception("API Error")

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("lib.llm_manager.llm_manager.LLMManager", return_value=mock_llm):
                result = service.check_llm(mock_config)

        assert result["success"] is False
        assert "API Error" in result["error"]

    def test_check_llm_auto_configures_when_provider_missing(self, service):
        """Test check_llm auto-configures Claude when key exists but provider is unset."""
        llm_config: Dict[str, Any] = {}
        cfg = Mock()
        cfg.get_llm_config.side_effect = lambda: dict(llm_config)
        cfg.set_llm_config.side_effect = lambda update: llm_config.update(update)
        cfg.save = Mock()

        mock_llm = Mock()
        mock_llm.query.return_value = {"text": "pong"}

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            with patch("lib.llm_manager.llm_manager.LLMManager", return_value=mock_llm):
                result = service.check_llm(cfg)

        assert result["success"] is True
        assert llm_config.get("provider") == "claude"
        cfg.set_llm_config.assert_called_once()
        cfg.save.assert_called_once()


class TestInitServiceMarkComplete:
    """Tests for mark_complete() method."""

    @pytest.fixture
    def service(self):
        """Create InitService instance."""
        return InitService()

    @pytest.fixture
    def mock_config(self):
        """Create mock TargetsConfig."""
        cfg = Mock()
        cfg.mark_init_completed = Mock()
        cfg.save = Mock()
        return cfg

    def test_marks_init_completed(self, service, mock_config):
        """Test mark_complete calls config method."""
        with patch.object(service, "_load_config", return_value=mock_config):
            result = service.mark_complete()

        assert result is True
        mock_config.mark_init_completed.assert_called_once()

    def test_saves_config(self, service, mock_config):
        """Test mark_complete saves config."""
        with patch.object(service, "_load_config", return_value=mock_config):
            service.mark_complete()

        mock_config.save.assert_called_once()


class TestInitServiceTestTarget:
    """Tests for _test_target() method."""

    @pytest.fixture
    def service(self):
        """Create InitService instance."""
        return InitService()

    def test_unsupported_engine(self, service):
        """Test _test_target with unsupported engine."""
        target = {"engine": "oracle", "host": "localhost"}

        ok, msg, verification = service._test_target(target)

        assert ok is False
        assert "Unsupported engine" in msg
        assert verification["success"] is False

    def test_postgresql_connection_success(self, service):
        """Test _test_target with successful PostgreSQL connection."""
        target = {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "testdb",
            "user": "testuser",
            "password_env": "DB_PASS",
        }

        mock_dm = Mock()
        mock_dm.connect.return_value = True
        mock_dm.get_connection_state.return_value = {
            "attempted": True,
            "success": True,
        }
        mock_dm.disconnect = Mock()

        with patch.dict(os.environ, {"DB_PASS": "secret"}):
            with patch(
                "lib.data_manager.data_manager.DataManager", return_value=mock_dm
            ):
                with patch("lib.data_manager.data_manager.ConnectionConfig"):
                    ok, msg, verification = service._test_target(target)

        assert ok is True
        assert "Connected" in msg

    def test_mysql_connection_success(self, service):
        """Test _test_target with successful MySQL connection."""
        target = {
            "engine": "mysql",
            "host": "localhost",
            "port": 3306,
            "database": "testdb",
            "user": "testuser",
            "password": "secret",
        }

        mock_dm = Mock()
        mock_dm.connect.return_value = True
        mock_dm.get_connection_state.return_value = {
            "attempted": True,
            "success": True,
        }
        mock_dm.disconnect = Mock()

        with patch("lib.data_manager.data_manager.DataManager", return_value=mock_dm):
            with patch("lib.data_manager.data_manager.ConnectionConfig"):
                ok, msg, verification = service._test_target(target)

        assert ok is True
        assert "Connected" in msg


class TestInitServiceCleanErrorMessage:
    """Tests for _clean_error_message() method."""

    @pytest.fixture
    def service(self):
        """Create InitService instance."""
        return InitService()

    def test_connection_refused(self, service):
        """Test cleaning connection refused error."""
        err = "Could not connect: Connection refused by host"
        result = service._clean_error_message(err)

        assert "Connection refused" in result
        assert "is the server running" in result

    def test_password_auth_failed(self, service):
        """Test cleaning password authentication error."""
        err = "FATAL: password authentication failed for user 'testuser'"
        result = service._clean_error_message(err)

        assert "Authentication failed" in result

    def test_host_not_found(self, service):
        """Test cleaning host not found error."""
        err = "could not translate host name 'unknown.host' to address"
        result = service._clean_error_message(err)

        assert "Host not found" in result

    def test_timeout(self, service):
        """Test cleaning timeout error."""
        err = "connection timeout after 30 seconds"
        result = service._clean_error_message(err)

        assert "timeout" in result.lower()

    def test_database_not_found(self, service):
        """Test cleaning database not found error."""
        err = "FATAL: database 'nonexistent' does not exist"
        result = service._clean_error_message(err)

        assert "Database not found" in result

    def test_ssl_error(self, service):
        """Test cleaning SSL error."""
        err = "SSL connection error: certificate verify failed"
        result = service._clean_error_message(err)

        assert "SSL" in result

    def test_truncates_long_error(self, service):
        """Test truncating long error messages."""
        err = "A" * 100
        result = service._clean_error_message(err)

        assert len(result) <= 80

    def test_takes_first_line_of_multiline(self, service):
        """Test taking first line of multiline error."""
        err = "First line error\nSecond line detail\nThird line"
        result = service._clean_error_message(err)

        assert "First line" in result
        assert "Second line" not in result


class TestInitServicePasswordResolution:
    """Verify InitService delegates to resolve_password."""

    @pytest.fixture
    def service(self):
        return InitService()

    def test_get_status_uses_resolve_password(self, service):
        """get_status should delegate to resolve_password for has_password."""
        cfg = Mock()
        cfg.list_targets.return_value = ["prod"]
        cfg.get.return_value = {"password": "direct", "engine": "postgresql"}
        cfg.get_default.return_value = "prod"
        cfg.is_init_completed.return_value = False
        cfg.get_llm_config.return_value = {}

        with patch.object(service, "_load_config", return_value=cfg):
            status = service.get_status()

        assert status.targets[0]["has_password"] is True
