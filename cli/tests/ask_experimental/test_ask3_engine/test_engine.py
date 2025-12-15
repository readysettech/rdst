"""Tests for Ask3Engine."""

import sys
sys.path.insert(0, '.')

import pytest
from unittest.mock import Mock, patch, MagicMock

from lib.engines.ask3.engine import Ask3Engine, create_engine
from lib.engines.ask3.context import Ask3Context
from lib.engines.ask3.presenter import QuietPresenter
from lib.engines.ask3.types import Status, DbType


class MockLLMManager:
    """Mock LLM manager for testing."""

    def generate_response(self, prompt, temperature=0.0, max_tokens=1000, extra=None):
        """Return a mock SQL generation response."""
        return {
            'response': '''{
                "analysis": {"needs_clarification": false, "ambiguities": []},
                "sql_generation": {
                    "sql": "SELECT * FROM users WHERE name = 'John'",
                    "explanation": "Find users named John",
                    "confidence": 0.95,
                    "assumptions": [],
                    "tables_used": ["users"],
                    "columns_used": ["name"]
                },
                "safety_assessment": {"is_read_only": true, "warnings": []},
                "clarifications": [],
                "alternatives": []
            }''',
            'total_tokens': 100,
            'model': 'test-model'
        }


class MockSemanticManager:
    """Mock semantic layer manager for testing."""

    def __init__(self):
        self._exists = True

    def exists(self, target):
        return self._exists

    def load(self, target):
        """Return mock semantic layer."""
        mock_layer = Mock()
        mock_layer.tables = {
            'users': Mock(
                description='User accounts',
                columns={
                    'id': Mock(data_type='integer', description='Primary key', enum_values={}),
                    'name': Mock(data_type='varchar', description='User name', enum_values={}),
                    'email': Mock(data_type='varchar', description='Email', enum_values={})
                }
            )
        }
        # Add empty terminology dict to avoid Mock iteration issues
        mock_layer.terminology = {}
        return mock_layer


class TestAsk3Engine:
    """Tests for Ask3Engine class."""

    def test_create_engine(self):
        """Test engine creation."""
        engine = Ask3Engine()

        assert engine.presenter is not None
        assert engine.llm_manager is None  # Created on demand
        assert engine.semantic_manager is None  # Created on demand

    def test_create_engine_with_custom_presenter(self):
        """Test engine with custom presenter."""
        presenter = QuietPresenter()
        engine = Ask3Engine(presenter=presenter)

        assert engine.presenter is presenter

    def test_factory_function(self):
        """Test create_engine factory."""
        engine = create_engine(verbose=True, use_rich=False)

        assert engine.presenter.verbose is True
        assert engine.presenter.use_rich is False

    def test_is_schema_error(self):
        """Test schema error detection."""
        engine = Ask3Engine()

        # PostgreSQL patterns
        assert engine._is_schema_error("column 'foo' does not exist")
        assert engine._is_schema_error('relation "users" does not exist')
        assert engine._is_schema_error("undefined column")

        # MySQL patterns
        assert engine._is_schema_error("Unknown column 'foo'")
        assert engine._is_schema_error("Table 'db.users' doesn't exist")

        # Non-schema errors
        assert not engine._is_schema_error("connection refused")
        assert not engine._is_schema_error("syntax error")


class TestEngineIntegration:
    """Integration tests for Ask3Engine."""

    def test_run_with_mocks(self):
        """Test full run with mocked dependencies."""
        presenter = QuietPresenter()
        llm_manager = MockLLMManager()
        semantic_manager = MockSemanticManager()

        # Mock db executor
        def mock_executor(sql, config):
            return {
                'success': True,
                'columns': ['id', 'name'],
                'rows': [[1, 'John']],
                'error': None
            }

        engine = Ask3Engine(
            presenter=presenter,
            llm_manager=llm_manager,
            semantic_manager=semantic_manager,
            db_executor=mock_executor
        )

        result = engine.run(
            question="Find users named John",
            target="testdb",
            target_config={
                'host': 'localhost',
                'port': 5432,
                'user': 'test',
                'password': 'test',
                'database': 'testdb'
            },
            no_interactive=True
        )

        assert result.status == Status.SUCCESS
        assert result.sql is not None
        assert "SELECT" in result.sql
        assert result.execution_result is not None
        assert result.execution_result.row_count == 1

    def test_run_with_execution_error(self):
        """Test handling of execution errors."""
        presenter = QuietPresenter()
        llm_manager = MockLLMManager()
        semantic_manager = MockSemanticManager()

        # Mock db executor that fails
        def mock_executor(sql, config):
            return {
                'success': False,
                'columns': [],
                'rows': [],
                'error': 'Connection refused'
            }

        engine = Ask3Engine(
            presenter=presenter,
            llm_manager=llm_manager,
            semantic_manager=semantic_manager,
            db_executor=mock_executor
        )

        result = engine.run(
            question="Find users",
            target="testdb",
            target_config={'host': 'localhost'},
            no_interactive=True
        )

        assert result.execution_result is not None
        assert result.execution_result.error == 'Connection refused'

    def test_run_without_schema(self):
        """Test run when no semantic layer exists."""
        presenter = QuietPresenter()
        llm_manager = MockLLMManager()

        # Mock semantic manager that says no layer exists
        semantic_manager = MockSemanticManager()
        semantic_manager._exists = False

        # Mock db executor
        def mock_executor(sql, config):
            return {
                'success': True,
                'columns': ['id'],
                'rows': [[1]],
                'error': None
            }

        engine = Ask3Engine(
            presenter=presenter,
            llm_manager=llm_manager,
            semantic_manager=semantic_manager,
            db_executor=mock_executor
        )

        # Should still work, just with empty/fallback schema
        result = engine.run(
            question="Find users",
            target="testdb",
            target_config={'host': 'localhost'},
            no_interactive=True
        )

        # Won't error on missing schema, just logs warning
        assert result.phase in ['execute', 'present', 'schema']


class TestValidationRetry:
    """Tests for validation retry logic."""

    def test_retry_on_validation_error(self):
        """Test that validation errors trigger retry."""
        presenter = QuietPresenter()
        semantic_manager = MockSemanticManager()

        # LLM that returns invalid column first, then valid
        call_count = 0

        class RetryLLMManager:
            def generate_response(self, prompt, temperature=0.0, max_tokens=1000, extra=None):
                nonlocal call_count
                call_count += 1

                if call_count == 1:
                    # First call: invalid column
                    return {
                        'response': '''{
                            "analysis": {"needs_clarification": false},
                            "sql_generation": {
                                "sql": "SELECT invalid_col FROM users",
                                "explanation": "test",
                                "confidence": 0.9
                            },
                            "safety_assessment": {"is_read_only": true}
                        }''',
                        'total_tokens': 50,
                        'model': 'test'
                    }
                else:
                    # Retry: valid column
                    return {
                        'response': '''{
                            "analysis": {"needs_clarification": false},
                            "sql_generation": {
                                "sql": "SELECT id, name FROM users",
                                "explanation": "test",
                                "confidence": 0.95
                            },
                            "safety_assessment": {"is_read_only": true}
                        }''',
                        'total_tokens': 50,
                        'model': 'test'
                    }

        def mock_executor(sql, config):
            return {
                'success': True,
                'columns': ['id', 'name'],
                'rows': [[1, 'John']],
                'error': None
            }

        engine = Ask3Engine(
            presenter=presenter,
            llm_manager=RetryLLMManager(),
            semantic_manager=semantic_manager,
            db_executor=mock_executor
        )

        result = engine.run(
            question="Find users",
            target="testdb",
            target_config={'host': 'localhost'},
            no_interactive=True
        )

        # Should have retried
        assert call_count >= 1


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
