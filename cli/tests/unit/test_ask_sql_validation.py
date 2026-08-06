"""Safety tests for the ask-path SQL validator.

Covers the constructs that stay inside a single read-only-looking SELECT
(SELECT ... INTO), the literal false-positives that used to reject harmless
queries, and the two gates that keep unvalidated SQL from reaching a database:
generation and execution.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.phases.execute import execute_query
from features.ask.engine.ask3.types import Status
from features.ask.sql_generation import generate_sql_from_nl
from features.ask.sql_validation import check_read_only, validate_sql_for_ask


class TestSelectInto:
    """SELECT ... INTO writes rows to a destination the caller never sees."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * INTO exfil FROM users",
            "SELECT * INTO TEMP TABLE exfil FROM users",
            "SELECT id INTO UNLOGGED exfil FROM users",
            "SELECT id INTO TEMPORARY exfil FROM users",
        ],
    )
    def test_select_into_a_table_is_blocked(self, sql):
        result = validate_sql_for_ask(sql, max_limit=1000, default_limit=100)

        assert result["is_valid"] is False
        assert any("INTO" in issue for issue in result["issues"])

    def test_into_outfile_is_still_reported_as_a_filesystem_write(self):
        sql = "SELECT * FROM posts INTO OUTFILE '/tmp/dump.txt'"
        result = validate_sql_for_ask(sql, max_limit=1000, default_limit=100)

        assert result["is_valid"] is False
        assert result["issues"] == [
            "INTO OUTFILE/DUMPFILE writes to the database server filesystem"
        ]

    def test_the_word_into_inside_a_literal_is_text(self):
        sql = "SELECT id FROM posts WHERE title = 'INTO the woods'"
        result = validate_sql_for_ask(sql, max_limit=1000, default_limit=100)

        assert result["is_valid"] is True


class TestKeywordsInsideLiterals:
    """A keyword inside a string literal cannot execute."""

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT name FROM users WHERE note = 'please DROP by the office'",
            "SELECT id FROM tickets WHERE status = 'update pending'",
            "SELECT id FROM audit WHERE action = 'DELETE'",
        ],
    )
    def test_literal_keyword_does_not_read_as_a_write(self, sql):
        assert check_read_only(sql)["is_read_only"] is True
        assert validate_sql_for_ask(sql, max_limit=1000, default_limit=100)["is_valid"] is True

    def test_real_write_is_still_rejected(self):
        result = check_read_only("DELETE FROM users WHERE id = 1")

        assert result["is_read_only"] is False
        assert "DELETE" in result["dangerous_keywords"]


class TestGenerationSafetyGate:
    """The model's own safety_assessment is not evidence about its SQL."""

    def _llm_returning(self, payload: dict) -> MagicMock:
        llm = MagicMock()
        llm.generate_response.return_value = {
            "response": json.dumps(payload),
            "tokens_used": 0,
            "model": "test",
        }
        return llm

    def _payload(self, sql: str) -> dict:
        return {
            "analysis": {"needs_clarification": False, "ambiguities": []},
            "clarifications": [],
            "sql_generation": {"sql": sql, "explanation": "", "confidence": 0.9},
            # The model grades itself as read-only regardless of what it wrote.
            "safety_assessment": {"is_read_only": True, "warnings": []},
            "alternatives": [],
        }

    def _generate(self, sql: str) -> dict:
        return generate_sql_from_nl(
            nl_question="anything",
            filtered_schema="",
            database_engine="postgresql",
            target_database="testdb",
            llm_manager=self._llm_returning(self._payload(sql)),
        )

    def test_write_statement_is_rejected_despite_a_clean_self_assessment(self):
        result = self._generate("DELETE FROM users")

        assert result["success"] is False
        assert "read-only" in result["error"]

    def test_read_only_statement_passes(self):
        result = self._generate("SELECT id FROM users")

        assert result["success"] is True
        assert result["sql"] == "SELECT id FROM users"


class TestExecutionGate:
    """Agent paths hand SQL straight to execute, so execute validates too."""

    def _ctx(self, sql: str) -> Ask3Context:
        ctx = Ask3Context(question="q", target="testdb", target_config={"host": "localhost"})
        ctx.sql = sql
        return ctx

    def test_unvalidated_write_never_reaches_the_executor(self):
        executed = []

        def executor(sql, config):
            executed.append(sql)
            return {"success": True, "rows": [], "columns": []}

        ctx = execute_query(self._ctx("DROP TABLE users"), MagicMock(), executor)

        assert executed == []
        assert ctx.status == Status.ERROR
        assert "validation failed" in ctx.error_message

    def test_select_into_never_reaches_the_executor(self):
        executed = []

        def executor(sql, config):
            executed.append(sql)
            return {"success": True, "rows": [], "columns": []}

        ctx = execute_query(self._ctx("SELECT * INTO exfil FROM users"), MagicMock(), executor)

        assert executed == []
        assert ctx.status == Status.ERROR

    def test_valid_select_executes_with_a_limit(self):
        executed = []

        def executor(sql, config):
            executed.append(sql)
            return {"success": True, "rows": [[1]], "columns": ["id"]}

        ctx = execute_query(self._ctx("SELECT id FROM users"), MagicMock(), executor)

        assert executed == ["SELECT id FROM users LIMIT 100"]
        assert ctx.status == Status.SUCCESS
