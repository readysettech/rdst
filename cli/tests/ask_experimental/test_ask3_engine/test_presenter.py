"""Tests for Ask3Presenter."""

import sys
sys.path.insert(0, '.')

import pytest
from io import StringIO
from lib.engines.ask3.presenter import Ask3Presenter, QuietPresenter
from lib.engines.ask3.types import Interpretation, ValidationError


class TestAsk3Presenter:
    """Tests for Ask3Presenter class."""

    def test_create_presenter(self):
        """Test basic presenter creation."""
        presenter = Ask3Presenter(verbose=True, use_rich=False)

        assert presenter.verbose is True
        assert presenter.use_rich is False

    def test_format_value_none(self):
        """Test formatting None values."""
        presenter = Ask3Presenter()

        assert presenter._format_value(None) == "NULL"

    def test_format_value_bytes(self):
        """Test formatting binary values."""
        presenter = Ask3Presenter()

        result = presenter._format_value(b"hello")
        assert "binary" in result
        assert "5 bytes" in result

    def test_format_value_long_string(self):
        """Test truncation of long strings."""
        presenter = Ask3Presenter()

        long_str = "x" * 100
        result = presenter._format_value(long_str)

        assert len(result) < len(long_str)
        assert result.endswith("...")


class TestQuietPresenter:
    """Tests for QuietPresenter class."""

    def test_suppresses_output(self, capsys):
        """Test that quiet presenter suppresses normal output."""
        presenter = QuietPresenter()

        presenter.info("This should not appear")
        presenter.warning("Neither should this")

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_shows_errors(self, capsys):
        """Test that quiet presenter still shows errors."""
        presenter = QuietPresenter()

        presenter.error("This error should appear")

        captured = capsys.readouterr()
        assert "This error should appear" in captured.out


class TestPresenterOutput:
    """Test presenter output methods."""

    def test_interpretations_output(self, capsys):
        """Test interpretations are displayed."""
        presenter = Ask3Presenter(use_rich=False)

        interpretations = [
            Interpretation(
                id=1,
                description="Find active users",
                assumptions=["Active = status 'A'"],
                sql_approach="Filter by status"
            ),
            Interpretation(
                id=2,
                description="Find logged in users",
                assumptions=["Logged in = has session"],
                sql_approach="Check sessions"
            )
        ]

        presenter.interpretations(interpretations)

        captured = capsys.readouterr()
        assert "Find active users" in captured.out
        assert "Find logged in users" in captured.out

    def test_validation_error_output(self, capsys):
        """Test validation errors are displayed."""
        presenter = Ask3Presenter(use_rich=False)

        errors = [
            ValidationError(
                column="user_id",
                table_alias="u",
                message="Column not found",
                suggestions=["userid", "id"]
            )
        ]

        presenter.validation_error(errors)

        captured = capsys.readouterr()
        assert "user_id" in captured.out
        assert "Column not found" in captured.out
        assert "userid" in captured.out or "id" in captured.out

    def test_execution_result_empty(self, capsys):
        """Test empty result display."""
        presenter = Ask3Presenter(use_rich=False)

        presenter.execution_result(
            columns=["id", "name"],
            rows=[],
            time_ms=10.5
        )

        captured = capsys.readouterr()
        assert "No results" in captured.out or "0 rows" in captured.out

    def test_execution_result_with_data(self, capsys):
        """Test result display with data."""
        presenter = Ask3Presenter(use_rich=False)

        presenter.execution_result(
            columns=["id", "name"],
            rows=[[1, "John"], [2, "Jane"]],
            time_ms=15.0
        )

        captured = capsys.readouterr()
        assert "John" in captured.out
        assert "Jane" in captured.out
        assert "2 rows" in captured.out


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
