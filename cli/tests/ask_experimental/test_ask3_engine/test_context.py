"""Tests for Ask3Context."""

import sys
sys.path.insert(0, '.')

import pytest
from lib.engines.ask3.context import Ask3Context
from lib.engines.ask3.types import (
    Status,
    DbType,
    SchemaSource,
    Interpretation,
    ValidationError,
    ExecutionResult,
)


class TestAsk3Context:
    """Tests for Ask3Context dataclass."""

    def test_create_context(self):
        """Test basic context creation."""
        ctx = Ask3Context(
            question="Find users named John",
            target="mydb",
            db_type=DbType.POSTGRESQL
        )

        assert ctx.question == "Find users named John"
        assert ctx.target == "mydb"
        assert ctx.db_type == DbType.POSTGRESQL
        assert ctx.status == Status.PENDING

    def test_default_values(self):
        """Test default values are set correctly."""
        ctx = Ask3Context(question="test", target="test")

        assert ctx.max_retries == 2
        assert ctx.timeout_seconds == 30
        assert ctx.max_rows == 100
        assert ctx.verbose is False
        assert ctx.no_interactive is False
        assert ctx.schema_source == SchemaSource.SEMANTIC
        assert ctx.retry_count == 0
        assert ctx.total_tokens == 0

    def test_mark_error(self):
        """Test marking context as errored."""
        ctx = Ask3Context(question="test", target="test")
        ctx.mark_error("Something went wrong")

        assert ctx.status == Status.ERROR
        assert ctx.error_message == "Something went wrong"

    def test_mark_cancelled(self):
        """Test marking context as cancelled."""
        ctx = Ask3Context(question="test", target="test")
        ctx.mark_cancelled()

        assert ctx.status == Status.CANCELLED

    def test_mark_success(self):
        """Test marking context as successful."""
        ctx = Ask3Context(question="test", target="test")
        ctx.mark_success()

        assert ctx.status == Status.SUCCESS

    def test_add_llm_call(self):
        """Test tracking LLM calls."""
        ctx = Ask3Context(question="test", target="test")

        ctx.add_llm_call(
            prompt="Hello",
            response="World",
            tokens=10,
            latency_ms=100.0,
            model="claude-3",
            phase="generate"
        )

        assert len(ctx.llm_calls) == 1
        assert ctx.total_tokens == 10
        assert ctx.total_llm_time_ms == 100.0

    def test_validation_error_management(self):
        """Test validation error tracking."""
        ctx = Ask3Context(question="test", target="test")

        assert not ctx.has_validation_errors()

        ctx.validation_errors.append(ValidationError(
            column="test_col",
            table_alias="t",
            message="Column not found"
        ))

        assert ctx.has_validation_errors()

        ctx.clear_validation_errors()
        assert not ctx.has_validation_errors()

    def test_retry_management(self):
        """Test retry counter management."""
        ctx = Ask3Context(question="test", target="test", max_retries=2)

        assert ctx.can_retry()
        assert ctx.retry_count == 0

        ctx.increment_retry()
        assert ctx.retry_count == 1
        assert ctx.can_retry()

        ctx.increment_retry()
        assert ctx.retry_count == 2
        assert not ctx.can_retry()

    def test_serialization(self):
        """Test to_dict serialization."""
        ctx = Ask3Context(
            question="Find users",
            target="mydb",
            db_type=DbType.POSTGRESQL
        )
        ctx.sql = "SELECT * FROM users"
        ctx.mark_success()

        data = ctx.to_dict()

        assert data['question'] == "Find users"
        assert data['target'] == "mydb"
        assert data['db_type'] == DbType.POSTGRESQL
        assert data['sql'] == "SELECT * FROM users"
        assert data['status'] == Status.SUCCESS

    def test_deserialization(self):
        """Test from_dict deserialization."""
        data = {
            'question': 'Find users',
            'target': 'mydb',
            'db_type': 'postgresql',
            'sql': 'SELECT * FROM users',
            'status': 'success',
            'max_retries': 3,
        }

        ctx = Ask3Context.from_dict(data)

        assert ctx.question == 'Find users'
        assert ctx.target == 'mydb'
        assert ctx.sql == 'SELECT * FROM users'
        assert ctx.status == 'success'
        assert ctx.max_retries == 3


class TestInterpretation:
    """Tests for Interpretation dataclass."""

    def test_create_interpretation(self):
        """Test creating an interpretation."""
        interp = Interpretation(
            id=1,
            description="Find active users",
            assumptions=["Active means status = 'A'"],
            sql_approach="Filter by status column"
        )

        assert interp.id == 1
        assert interp.description == "Find active users"
        assert len(interp.assumptions) == 1

    def test_serialization(self):
        """Test to_dict/from_dict."""
        interp = Interpretation(
            id=1,
            description="Test",
            assumptions=["a", "b"],
            sql_approach="approach"
        )

        data = interp.to_dict()
        restored = Interpretation.from_dict(data)

        assert restored.id == interp.id
        assert restored.description == interp.description
        assert restored.assumptions == interp.assumptions


class TestValidationError:
    """Tests for ValidationError dataclass."""

    def test_create_error(self):
        """Test creating a validation error."""
        err = ValidationError(
            column="user_id",
            table_alias="u",
            message="Column not found",
            suggestions=["userid", "id"]
        )

        assert err.column == "user_id"
        assert err.table_alias == "u"
        assert len(err.suggestions) == 2


class TestExecutionResult:
    """Tests for ExecutionResult dataclass."""

    def test_create_result(self):
        """Test creating an execution result."""
        result = ExecutionResult(
            columns=["id", "name"],
            rows=[[1, "John"], [2, "Jane"]],
            row_count=2,
            execution_time_ms=10.5
        )

        assert len(result.columns) == 2
        assert len(result.rows) == 2
        assert result.row_count == 2
        assert result.error is None

    def test_error_result(self):
        """Test creating an error result."""
        result = ExecutionResult(
            error="Connection failed",
            execution_time_ms=5.0
        )

        assert result.error == "Connection failed"
        assert result.row_count == 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
