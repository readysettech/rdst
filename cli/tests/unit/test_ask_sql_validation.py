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
from features.ask.engine.ask3.phases.validate import validate_sql
from features.ask.engine.ask3.types import ColumnInfo, SchemaInfo, Status, TableInfo
from features.ask.sql_generation import generate_sql_from_nl
from features.ask.sql_validation import (
    check_read_only,
    validate_filter_literal_provenance,
    validate_sql_for_ask,
    validate_tables_against_schema,
)

Q72_SCHEMA = """Table: frpm
  School Name (text) -- The official name of the school.
  County Name (text) -- The county containing the school.
  School Type (enum) -- School classification. [enum: State Special Schools]
  Educational Option Type (enum) -- Program model. [enum: State Special School]
  Academic Year (enum) -- Reporting year. [enum: 2014-2015]
  Enrollment (Ages 5-17) (double) -- Enrollment count.

Table: schools
  CDSCode (varchar)
  City (text)
  EdOpsName (enum) -- Program model. [enum: State Special School]
"""


class TestFilterLiteralProvenance:
    def test_blocks_free_text_when_the_same_value_is_a_listed_enum(self):
        result = validate_filter_literal_provenance(
            "SELECT f.`Enrollment (Ages 5-17)` FROM frpm f "
            "WHERE f.`School Name` = 'State Special School' "
            "AND f.`Academic Year` = '2014-2015'",
            question=(
                "How many students are enrolled at the State Special School school "
                "for the 2014-2015 academic year?"
            ),
            schema_formatted=Q72_SCHEMA,
            dialect="mysql",
        )

        assert result["is_valid"] is False
        assert [(issue["kind"], issue["literal"]) for issue in result["issues"]] == [
            ("enum_shadowed_free_text", "State Special School")
        ]
        assert result["issues"][0]["suggestions"] == [
            "frpm.educational option type",
            "schools.edopsname",
        ]

    def test_accepts_the_value_on_an_enum_column(self):
        result = validate_filter_literal_provenance(
            "SELECT `Enrollment (Ages 5-17)` FROM frpm "
            "WHERE `Educational Option Type` = 'State Special School'",
            question="Show State Special School enrollment",
            schema_formatted=Q72_SCHEMA,
            dialect="mysql",
        )

        assert result == {"is_valid": True, "issues": [], "warnings": []}

    @pytest.mark.parametrize("wording", ["named", "called", "titled", "known as"])
    def test_explicit_name_wording_allows_a_free_text_name(self, wording):
        result = validate_filter_literal_provenance(
            "SELECT language FROM sets WHERE name = 'Archenemy'",
            question=f"Show languages for the set {wording} Archenemy",
            schema_formatted=(
                "Table: sets\n  name (text)\n  type (enum) [enum: Archenemy]\n"
            ),
            dialect="mysql",
        )

        assert result["is_valid"] is True
        assert result["issues"] == []

    def test_unsupported_literal_is_advisory_without_a_blocking_issue(self):
        result = validate_filter_literal_provenance(
            "SELECT language FROM cards WHERE name = 'Fellwar Stone'",
            question='Which foreign language is used by "A Pedra Fellwar"?',
            schema_formatted="Table: cards\n  name (text)\n",
            dialect="mysql",
        )

        assert result["is_valid"] is True
        assert result["issues"] == []
        assert [(item["kind"], item["literal"]) for item in result["warnings"]] == [
            ("unsupported_literal", "Fellwar Stone")
        ]

    def test_matched_database_value_supports_a_storage_literal(self):
        result = validate_filter_literal_provenance(
            "SELECT language FROM foreign_data WHERE name = 'A Pedra Fellwar'",
            question="Which foreign language is used by the card?",
            schema_formatted="Table: foreign_data\n  name (text)\n  language (text)\n",
            matched_database_values="- 'A Pedra Fellwar': foreign_data.name",
            dialect="mysql",
        )

        assert result == {"is_valid": True, "issues": [], "warnings": []}

    @pytest.mark.parametrize("question_date", ["2012/1/1", "2012.01.01"])
    def test_accepts_an_equivalent_complete_calendar_date(self, question_date):
        result = validate_filter_literal_provenance(
            "SELECT COUNT(*) FROM transactions WHERE Date > '2012-01-01'",
            question=f"How many transactions happened after {question_date}?",
            schema_formatted="Table: transactions\n  Date (date)\n",
            dialect="mysql",
        )

        assert result == {"is_valid": True, "issues": [], "warnings": []}

    @pytest.mark.parametrize(
        ("question_date", "sql_date"),
        [
            ("2012/1/2", "2012-01-01"),
            ("2012/1", "2012-01-01"),
            ("2012/2/30", "2012-02-30"),
        ],
    )
    def test_does_not_equate_different_partial_or_invalid_dates(
        self, question_date, sql_date
    ):
        result = validate_filter_literal_provenance(
            f"SELECT COUNT(*) FROM transactions WHERE Date > '{sql_date}'",
            question=f"How many transactions happened after {question_date}?",
            schema_formatted="Table: transactions\n  Date (date)\n",
            dialect="mysql",
        )

        assert result["is_valid"] is True
        assert [(item["kind"], item["literal"]) for item in result["warnings"]] == [
            ("unsupported_literal", sql_date)
        ]

    def test_phase_includes_advisory_literals_when_a_blocker_triggers_repair(self):
        ctx = Ask3Context(
            question=(
                "How many students are enrolled at the State Special School school "
                "in Fremont for the 2014-2015 academic year?"
            ),
            target="california_schools",
            db_type="mysql",
            enforce_result_limit=False,
        )
        ctx.schema_formatted = Q72_SCHEMA
        ctx.schema_info = SchemaInfo(
            target=ctx.target,
            db_type=ctx.db_type,
            tables={
                "frpm": TableInfo(
                    name="frpm",
                    columns={
                        name: ColumnInfo(name=name, data_type=data_type)
                        for name, data_type in {
                            "School Name": "text",
                            "County Name": "text",
                            "School Type": "enum",
                            "Educational Option Type": "enum",
                            "Academic Year": "enum",
                            "Enrollment (Ages 5-17)": "double",
                        }.items()
                    },
                )
            },
        )
        ctx.sql = (
            "SELECT `Enrollment (Ages 5-17)` FROM frpm "
            "WHERE `School Name` = 'State Special School' "
            "AND `County Name` = 'Alameda' "
            "AND `Academic Year` = '2014-2015'"
        )

        validate_sql(ctx, MagicMock())

        assert len(ctx.validation_errors) == 2
        assert "free-text column" in ctx.validation_errors[0].message
        assert "Alameda" in ctx.validation_errors[1].message


def test_table_validation_rejects_unknown_physical_table() -> None:
    result = validate_tables_against_schema(
        "SELECT id FROM invented_users",
        {"users": ["id"]},
        "postgresql",
    )

    assert result["is_valid"] is False
    assert result["invalid_tables"] == ["invented_users"]


def test_table_validation_does_not_treat_cte_alias_as_physical_table() -> None:
    result = validate_tables_against_schema(
        "WITH selected AS (SELECT id FROM users) SELECT id FROM selected",
        {"users": ["id"]},
        "postgresql",
    )

    assert result["is_valid"] is True


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
        assert (
            validate_sql_for_ask(sql, max_limit=1000, default_limit=100)["is_valid"]
            is True
        )

    def test_real_write_is_still_rejected(self):
        result = check_read_only("DELETE FROM users WHERE id = 1")

        assert result["is_read_only"] is False
        assert "DELETE" in result["dangerous_keywords"]

    def test_mysql_replace_function_is_read_only_but_replace_statement_is_not(self):
        query = "SELECT REPLACE(name, 'old', 'new') FROM users"

        assert check_read_only(query)["is_read_only"] is True
        validated = validate_sql_for_ask(query, enforce_result_limit=False)
        assert validated["is_valid"] is True

        write = check_read_only("REPLACE INTO users (id) VALUES (1)")
        assert write["is_read_only"] is False
        assert "REPLACE" in write["dangerous_keywords"]


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
            "sql": sql,
            "explanation": "",
            "confidence": 0.9,
            "assumptions": [],
            "cannot_answer": False,
            "cannot_answer_reason": "",
            "missing_schema": [],
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
        ctx = Ask3Context(
            question="q", target="testdb", target_config={"host": "localhost"}
        )
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

        ctx = execute_query(
            self._ctx("SELECT * INTO exfil FROM users"), MagicMock(), executor
        )

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

    def test_evaluation_select_executes_without_limit_mutation(self):
        executed = []
        ctx = self._ctx("SELECT id FROM users")
        ctx.enforce_result_limit = False

        def executor(sql, config):
            executed.append(sql)
            return {"success": True, "rows": [[1]], "columns": ["id"]}

        ctx = execute_query(ctx, MagicMock(), executor)

        assert executed == ["SELECT id FROM users"]
        assert ctx.status == Status.SUCCESS

    def test_executor_preserves_structured_failure_kind(self):
        def executor(sql, config):
            return {
                "success": False,
                "rows": [],
                "columns": [],
                "error": "query timed out",
                "error_kind": "timeout",
            }

        ctx = execute_query(self._ctx("SELECT id FROM users"), MagicMock(), executor)

        assert ctx.execution_result.error == "query timed out"
        assert ctx.execution_result.error_kind == "timeout"

    def test_read_only_cte_allows_keyword_like_alias(self):
        sql = """WITH summary AS (
            SELECT COUNT(*) AS count FROM users
        )
        SELECT count FROM summary"""

        result = validate_sql_for_ask(sql, enforce_result_limit=False)

        assert result["is_valid"] is True
        assert result["is_safe"] is True

    def test_parenthesized_set_operation_is_read_only(self):
        result = validate_sql_for_ask(
            "(SELECT 1) UNION (SELECT 2)", enforce_result_limit=False
        )

        assert result["is_valid"] is True
        assert result["is_safe"] is True

    def test_evaluation_policy_still_blocks_writes(self):
        executed = []
        ctx = self._ctx("DELETE FROM users")
        ctx.enforce_result_limit = False

        def executor(sql, config):
            executed.append(sql)
            return {"success": True, "rows": [], "columns": []}

        ctx = execute_query(ctx, MagicMock(), executor)

        assert executed == []
        assert ctx.status == Status.ERROR


class TestAskExecutionEvidence:
    """Q11 attribution: a successful ask execution records exactly one run."""

    def _ctx(self, sql: str) -> Ask3Context:
        ctx = Ask3Context(
            question="q", target="testdb", target_config={"host": "localhost"}
        )
        ctx.sql = sql
        return ctx

    def _executor(self, executed):
        def executor(sql, config):
            executed.append(sql)
            return {"success": True, "rows": [[1]], "columns": ["id"]}

        return executor

    def test_successful_execution_records_one_run(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            "features.ask.engine.ask3.phases.execute.record_execution_evidence",
            lambda *args, **kwargs: recorded.append((args, kwargs)),
        )
        timestamps = iter((100.125, 100.875))
        monkeypatch.setattr(
            "features.ask.engine.ask3.phases.execute.time.time",
            lambda: next(timestamps),
        )
        executed = []

        ctx = execute_query(
            self._ctx("SELECT id FROM users"), MagicMock(), self._executor(executed)
        )

        assert ctx.status == Status.SUCCESS
        assert len(recorded) == 1
        args, kwargs = recorded[0]
        assert args == (
            "testdb",
            [{"sql": "SELECT id FROM users LIMIT 100", "exec_count": 1}],
        )
        assert kwargs["lane"] == "rdst/ask"
        assert kwargs["started_at"] == kwargs["ended_at"] == 100.875
        assert isinstance(kwargs["started_at"], float)

    def test_failed_execution_records_nothing(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            "features.ask.engine.ask3.phases.execute.record_execution_evidence",
            lambda *args, **kwargs: recorded.append((args, kwargs)),
        )

        def executor(sql, config):
            return {"success": False, "error": "boom"}

        ctx = execute_query(self._ctx("SELECT id FROM users"), MagicMock(), executor)

        assert ctx.execution_result.error == "boom"
        assert recorded == []

    def test_store_failure_does_not_fail_the_execution(self, monkeypatch, tmp_path):
        # Drive the real best-effort helper into a broken store: cache.db
        # exists, but opening it raises. The ask answer must still succeed.
        from shared.query_registry import observation_store

        cache_db = tmp_path / "cache.db"
        cache_db.touch()
        monkeypatch.setattr(
            observation_store, "default_cache_db_path", lambda: cache_db
        )

        def broken_store(*args, **kwargs):
            raise RuntimeError("store exploded")

        monkeypatch.setattr(observation_store, "ObservationStore", broken_store)
        executed = []

        ctx = execute_query(
            self._ctx("SELECT id FROM users"), MagicMock(), self._executor(executed)
        )

        assert ctx.status == Status.SUCCESS
        assert executed == ["SELECT id FROM users LIMIT 100"]
