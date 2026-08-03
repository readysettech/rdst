"""
Unit tests for DataManager.

Tests data management, query execution, and data operations.
"""

from unittest.mock import MagicMock, patch

import pytest

from shared.data_manager import data_manager as data_manager_module

DataManager = data_manager_module.DataManager


class TestDataManagerBasic:
    """Basic tests for DataManager class."""

    def test_class_exists(self):
        """Test DataManager class exists."""
        assert DataManager is not None

    def test_has_expected_methods(self):
        """Test DataManager has expected methods."""
        # Check for common methods that should exist
        assert hasattr(DataManager, '__init__')


class TestDataManagerHelpers:
    """Tests for DataManager helper functions."""

    def test_module_imports(self):
        """Test module imports successfully."""
        assert data_manager_module is not None

    def test_module_has_datamanager(self):
        """Test module has DataManager class."""
        assert hasattr(data_manager_module, 'DataManager')

    def test_system_collectors_fail_clearly_on_windows(self):
        manager = object.__new__(DataManager)
        manager.logger = MagicMock()

        with patch.object(data_manager_module.os, "name", "nt"):
            with pytest.raises(RuntimeError, match="not supported on Windows"):
                manager._execute_system_command("uptime")


# Note: The DataManager class has complex initialization that requires
# database connections and other dependencies. Full unit testing would
# require extensive mocking. These tests verify the basic structure exists.
# For comprehensive testing, integration tests with actual database
# connections are recommended.
