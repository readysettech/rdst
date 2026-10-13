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
    describe_validation_issues,
    validate_filter_literal_provenance,
    validate_resolved_columns_against_schema,
    validate_sql_for_ask,
    validate_tables_against_schema,
)
from shared.llm_manager.base import LLMError

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


def test_literal_provenance_parses_postgresql_targets() -> None:
    # Targets name the dialect "postgresql"; sqlglot only knows "postgres".
    result = validate_filter_literal_provenance(
        "SELECT primarytitle FROM title_basics WHERE titletype = 'documentary'",
        question="list some titles",
        schema_formatted=(
            "Table: title_basics\n  primarytitle (text)\n"
            "  titletype (text) [enum: movie, short, tvSeries]\n"
        ),
        dialect="postgresql",
    )

    assert [warning["kind"] for warning in result["warnings"]] == ["unsupported_literal"]


def test_table_validation_rejects_unknown_physical_table() -> None:
    result = validate_tables_against_schema(
        "SELECT id FROM invented_users",
        {"users": ["id"]},
        "postgresql",
    )

    assert result["is_valid"] is False
    assert result["invalid_tables"] == ["invented_users"]


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
@pytest.mark.parametrize("validator", [
    validate_tables_against_schema,
    validate_resolved_columns_against_schema,
])
def test_schema_validation_rejects_unescaped_apostrophe_without_raising(validator, dialect):
    result = validator(
        "SELECT event_name FROM events WHERE event_name = 'Women's Soccer'",
        {"events": ["event_name"]},
        dialect,
    )
    assert result["is_valid"] is False
    assert "tokenizing" in result["error_message"]


def test_validation_phase_records_tokenizer_failure_for_repair():
    ctx = Ask3Context(question="Show women's soccer events", target="fixture")
    ctx.db_type = "mysql"
    ctx.sql = "SELECT event_name FROM events WHERE event_name = 'Women's Soccer'"
    ctx.schema_info = SchemaInfo(target="fixture", db_type="mysql", tables={
        "events": TableInfo(name="events", columns={
            "event_name": ColumnInfo(name="event_name", data_type="text")
        })
    })
    validate_sql(ctx, MagicMock())
    assert ctx.validation_errors
    assert "tokenizing" in ctx.validation_errors[0].message


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
        assert result["error_code"] == "ask_unsafe_sql"
        assert result["error_category"] == "read_only"

    def test_read_only_statement_passes(self):
        result = self._generate("SELECT id FROM users")

        assert result["success"] is True
        assert result["sql"] == "SELECT id FROM users"

    def test_provider_error_code_is_preserved(self):
        llm = MagicMock()
        llm.propagate_query_errors = False
        llm.generate_response.side_effect = LLMError(
            "Monthly limit reached", code="HOSTED_CAP_REACHED", status=403
        )

        result = generate_sql_from_nl(
            nl_question="anything",
            filtered_schema="",
            database_engine="postgresql",
            target_database="testdb",
            llm_manager=llm,
        )

        assert result["success"] is False
        assert result["error_code"] == "HOSTED_CAP_REACHED"


class TestDescribeValidationIssues:
    def test_issues_read_as_one_sentence_from_their_messages(self):
        from features.ask.engine.ask3.types import ValidationError

        issues = [
            {"column": "status", "message": "Filter literal 'x' has no source.", "kind": "k"},
            ValidationError(column="", table_alias=None, message="Table 'users' does not exist"),
            "Only a single statement is allowed",
            {"column": "id", "message": {"nested": True}},
            {"column": "id"},
        ]

        assert describe_validation_issues(issues) == (
            "The generated SQL failed validation: Filter literal 'x' has no source; "
            "Table 'users' does not exist; Only a single statement is allowed."
        )

    def test_issues_without_messages_still_read_as_a_sentence(self):
        assert describe_validation_issues([{"column": "id"}]) == (
            "The generated SQL failed validation."
        )
        assert describe_validation_issues(None) == "The generated SQL failed validation."


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
        assert ctx.error_message.startswith("The generated SQL failed validation: ")
        assert ctx.execution_result.error == ctx.error_message

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

        # One row past the added cap tells whether the cap cut the result.
        assert executed == ["SELECT id FROM users LIMIT 101"]
        assert ctx.sql == "SELECT id FROM users LIMIT 100"
        assert ctx.status == Status.SUCCESS

    @pytest.mark.parametrize(
        ("sql", "returned", "row_limit", "truncated", "rows"),
        [
            ("SELECT id FROM users", 101, 100, True, 100),
            ("SELECT id FROM users", 100, 100, False, 100),
            ("SELECT id FROM users LIMIT 5000", 1001, 1000, True, 1000),
        ],
    )
    def test_an_ask_cap_reports_exact_truncation(
        self, sql, returned, row_limit, truncated, rows
    ):
        executed = []

        def executor(run_sql, config):
            executed.append(run_sql)
            return {"success": True, "rows": [[1]] * returned, "columns": ["id"]}

        ctx = execute_query(self._ctx(sql), MagicMock(), executor)

        assert executed == [f"SELECT id FROM users LIMIT {row_limit + 1}"]
        assert ctx.sql == f"SELECT id FROM users LIMIT {row_limit}"
        assert ctx.row_limit == row_limit
        assert ctx.execution_result.truncated is truncated
        assert ctx.execution_result.row_count == rows

    def test_the_query_own_limit_runs_unchanged_and_is_not_truncation(self):
        executed = []

        def executor(sql, config):
            executed.append(sql)
            return {"success": True, "rows": [[1]] * 10, "columns": ["id"]}

        ctx = execute_query(
            self._ctx("SELECT id FROM users LIMIT 10"), MagicMock(), executor
        )

        assert executed == ["SELECT id FROM users LIMIT 10"]
        assert ctx.row_limit == 10
        assert not (ctx.limit_added or ctx.limit_reduced)
        assert ctx.execution_result.truncated is False

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

    @pytest.mark.parametrize(
        ("sql", "executed_sql", "reduced"),
        [
            ("SELECT id FROM users ORDER BY score FETCH FIRST 1000 ROWS WITH TIES",
             "SELECT id FROM users ORDER BY score FETCH FIRST 1000 ROWS WITH TIES", False),
            ("SELECT id FROM users ORDER BY score FETCH FIRST 5000 ROWS WITH TIES",
             "SELECT id FROM users ORDER BY score FETCH FIRST 1000 ROWS WITH TIES", True),
        ],
    )
    def test_ties_past_the_cap_are_cut_to_the_cap(self, sql, executed_sql, reduced):
        executed = []

        def executor(run_sql, config):
            executed.append(run_sql)
            return {"success": True, "rows": [[1]] * 1500, "columns": ["id"]}

        ctx = execute_query(self._ctx(sql), MagicMock(), executor)

        assert executed == [executed_sql]
        assert ctx.execution_result.row_count == 1000
        assert ctx.execution_result.truncated is True
        assert ctx.row_limit == 1000
        assert ctx.rows_capped is True
        assert ctx.limit_reduced is reduced

    def test_ties_within_the_cap_are_the_query_own_result(self):
        def executor(run_sql, config):
            return {"success": True, "rows": [[1]] * 12, "columns": ["id"]}

        ctx = execute_query(
            self._ctx("SELECT id FROM users ORDER BY score FETCH FIRST 10 ROWS WITH TIES"),
            MagicMock(),
            executor,
        )

        assert ctx.execution_result.row_count == 12
        assert ctx.execution_result.truncated is False
        assert ctx.rows_capped is False

    def test_no_cap_applies_without_result_limit_enforcement(self):
        ctx = self._ctx("SELECT id FROM users ORDER BY score FETCH FIRST 1000 ROWS WITH TIES")
        ctx.enforce_result_limit = False

        def executor(run_sql, config):
            return {"success": True, "rows": [[1]] * 1500, "columns": ["id"]}

        ctx = execute_query(ctx, MagicMock(), executor)

        assert ctx.execution_result.row_count == 1500
        assert ctx.rows_capped is False

    @pytest.mark.parametrize(
        ("db_type", "runner"), [("postgresql", "_execute_postgres"), ("mysql", "_execute_mysql")]
    )
    def test_database_fetch_stops_one_row_past_the_cap(self, monkeypatch, db_type, runner):
        from features.ask.engine.ask3.phases import execute as execute_phase

        calls = []

        def run(sql, config, timeout, target, *, statement_cancel=None, max_rows=None):
            calls.append(max_rows)
            return {"success": True, "rows": [[1]] * max_rows, "columns": ["id"]}

        monkeypatch.setattr(execute_phase, runner, run)
        ctx = self._ctx("SELECT id FROM users ORDER BY score FETCH FIRST 1000 ROWS WITH TIES")
        ctx.db_type = db_type

        ctx = execute_query(ctx, MagicMock())

        assert calls == [1001]
        assert ctx.execution_result.row_count == 1000
        assert ctx.execution_result.truncated is True

    @pytest.mark.parametrize("db_type", ["postgresql", "mysql"])
    def test_driver_fetches_a_bounded_batch(self, monkeypatch, db_type):
        import psycopg2

        from features.ask.engine.ask3.phases import execute as execute_phase

        fetched = []

        class Cursor:
            description = [("id",)]

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql):
                pass

            def fetchmany(self, size):
                fetched.append(size)
                return [(1,)] * size

            def fetchall(self):
                raise AssertionError("an unbounded fetch can pull every tied row")

        class Conn:
            def cursor(self):
                return Cursor()

            def close(self):
                pass

        monkeypatch.setattr(
            "shared.db_connection.resolve_connection_params",
            lambda **_kwargs: {"host": "h", "user": "u", "database": "d"},
        )
        monkeypatch.setattr(psycopg2, "connect", lambda **_kwargs: Conn())
        monkeypatch.setattr(
            "shared.db_connection.postgres_connection_kwargs", lambda params, **kw: {}
        )
        monkeypatch.setattr(
            "shared.db_connection.create_mysql_connection_from_params",
            lambda params, **kw: Conn(),
        )
        run = execute_phase._execute_mysql if db_type == "mysql" else execute_phase._execute_postgres

        result = run("SELECT id FROM users", {}, 0, "db", max_rows=3)

        assert fetched == [3]
        assert result["rows"] == [[1], [1], [1]]

    def test_dialect_reaches_the_limit_check(self):
        executed = []

        def executor(run_sql, config):
            executed.append(run_sql)
            return {"success": True, "rows": [[1]], "columns": ["s"]}

        ctx = self._ctx("SELECT 'it\\'s (x' AS s FROM users LIMIT 5")
        ctx.db_type = "mysql"
        ctx = execute_query(ctx, MagicMock(), executor)

        assert executed == ["SELECT 'it\\'s (x' AS s FROM users LIMIT 5"]


class TestLimitFlagsFollowTheExecutedSql:
    """The LIMIT notes describe the statement that ran, never one it replaced."""

    def _validated(self, sql: str) -> Ask3Context:
        ctx = Ask3Context(
            question="q", target="testdb", target_config={"host": "localhost"}
        )
        ctx.sql = sql
        return validate_sql(ctx, MagicMock())

    def _executor(self, executed, returned):
        def executor(run_sql, config):
            executed.append(run_sql)
            return {"success": True, "rows": [[1]] * returned, "columns": ["id"]}

        return executor

    def test_the_linear_cap_is_probed_and_reported(self):
        ctx = self._validated("SELECT id FROM users")
        executed = []

        ctx = execute_query(ctx, MagicMock(), self._executor(executed, 101))

        assert executed == ["SELECT id FROM users LIMIT 101"]
        assert ctx.limit_added is True
        assert ctx.execution_result.truncated is True
        assert ctx.execution_result.row_count == 100

    def test_agent_sql_with_its_own_limit_is_not_reported_as_capped(self):
        from features.ask.engine.ask3.agent_context import AgentExplorationContext

        ctx = self._validated("SELECT id FROM users")
        assert ctx.limit_added and ctx.row_limit == 100
        agent_ctx = AgentExplorationContext.from_ask3_context(ctx, "zero_rows")
        agent_ctx.final_sql = "SELECT id FROM users WHERE active LIMIT 100"
        ctx = agent_ctx.to_ask3_context()
        executed = []

        ctx = execute_query(ctx, MagicMock(), self._executor(executed, 100))

        assert executed == ["SELECT id FROM users WHERE active LIMIT 100"]
        assert not (ctx.limit_added or ctx.limit_reduced)
        assert ctx.row_limit == 100
        assert ctx.execution_result.row_count == 100

    def test_agent_sql_without_a_limit_gets_its_own_cap(self):
        ctx = self._validated("SELECT id FROM users LIMIT 5000")
        assert ctx.limit_reduced
        ctx.sql = "SELECT id FROM users WHERE active"
        executed = []

        ctx = execute_query(ctx, MagicMock(), self._executor(executed, 3))

        assert executed == ["SELECT id FROM users WHERE active LIMIT 101"]
        assert ctx.limit_added is True and ctx.limit_reduced is False
        assert ctx.row_limit == 100


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
        assert executed == ["SELECT id FROM users LIMIT 101"]


@pytest.mark.parametrize(
    ("sql", "validated", "limit_added", "limit_reduced"),
    [
        # FETCH FIRST is the query's own limit; appending LIMIT would break it.
        (
            "SELECT title FROM posts ORDER BY score DESC FETCH FIRST 9 ROWS WITH TIES",
            "SELECT title FROM posts ORDER BY score DESC FETCH FIRST 9 ROWS WITH TIES",
            False,
            False,
        ),
        (
            "SELECT title FROM posts ORDER BY score DESC FETCH NEXT 5000 ROWS ONLY",
            "SELECT title FROM posts ORDER BY score DESC FETCH NEXT 1000 ROWS ONLY",
            False,
            True,
        ),
        # A LIMIT inside a subquery or a string doesn't bound the outer query.
        (
            "SELECT * FROM (SELECT id FROM posts LIMIT 5) recent JOIN votes v ON v.postid = recent.id",
            "SELECT * FROM (SELECT id FROM posts LIMIT 5) recent JOIN votes v ON v.postid = recent.id LIMIT 100",
            True,
            False,
        ),
        (
            "SELECT 'LIMIT 3' AS note FROM posts",
            "SELECT 'LIMIT 3' AS note FROM posts LIMIT 100",
            True,
            False,
        ),
        (
            "SELECT id FROM posts LIMIT 20, 5000",
            "SELECT id FROM posts LIMIT 20, 1000",
            False,
            True,
        ),
    ],
)
def test_limit_handling_reads_only_the_outermost_query(
    sql, validated, limit_added, limit_reduced
):
    result = validate_sql_for_ask(sql, default_limit=100, max_limit=1000)

    assert result["validated_sql"] == validated
    assert bool(result.get("limit_added")) is limit_added
    assert bool(result.get("limit_reduced")) is limit_reduced


@pytest.mark.parametrize(
    ("sql", "dialect", "validated"),
    [
        # MySQL strings take backslash escapes, in either quote.
        ("SELECT 'it\\'s (x' AS s FROM t LIMIT 5", "mysql",
         "SELECT 'it\\'s (x' AS s FROM t LIMIT 5"),
        ('SELECT "a\\" (" AS s FROM t LIMIT 5', "mysql", 'SELECT "a\\" (" AS s FROM t LIMIT 5'),
        ("SELECT id FROM t # note (\nLIMIT 5", "mysql", "SELECT id FROM t # note (\nLIMIT 5"),
        # MySQL's "--" opens a comment only before whitespace; "2--1" subtracts.
        ("SELECT 2--1 AS d FROM t LIMIT 5", "mysql", "SELECT 2--1 AS d FROM t LIMIT 5"),
        ("SELECT id FROM t -- note (\nLIMIT 5", "mysql", "SELECT id FROM t -- note (\nLIMIT 5"),
        ("SELECT id FROM t --\x01(\nLIMIT 5", "mysql", "SELECT id FROM t --\x01(\nLIMIT 5"),
        # MySQL runs what an executable comment holds.
        ("SELECT id FROM t /*! LIMIT 5 */", "mysql", "SELECT id FROM t /*! LIMIT 5 */"),
        ("SELECT id FROM t /*!80000 LIMIT 5 */", "mysql", "SELECT id FROM t /*!80000 LIMIT 5 */"),
        ("SELECT /*+ NO_INDEX(t) ( */ id FROM t LIMIT 5", "mysql",
         "SELECT /*+ NO_INDEX(t) ( */ id FROM t LIMIT 5"),
        # PostgreSQL takes them only in E'' strings; a standard string ends at its quote.
        ("SELECT E'it\\'s (' AS s FROM t LIMIT 5", "postgresql",
         "SELECT E'it\\'s (' AS s FROM t LIMIT 5"),
        ("SELECT 'C:\\' AS p, '(' AS q FROM t LIMIT 5", "postgresql",
         "SELECT 'C:\\' AS p, '(' AS q FROM t LIMIT 5"),
        ("SELECT $$ it's ( $$ AS s FROM t LIMIT 5", "postgresql",
         "SELECT $$ it's ( $$ AS s FROM t LIMIT 5"),
        ("SELECT $q$ ($$ $q$ AS s FROM t LIMIT 5", "postgresql",
         "SELECT $q$ ($$ $q$ AS s FROM t LIMIT 5"),
        ("SELECT $$LIMIT 3$$ AS s FROM t", "postgresql", "SELECT $$LIMIT 3$$ AS s FROM t LIMIT 100"),
        ("SELECT id FROM t /* a /* b */ ( */ LIMIT 5", "postgresql",
         "SELECT id FROM t /* a /* b */ ( */ LIMIT 5"),
        # A parameter is not a dollar quote.
        ("SELECT id FROM t WHERE a = $1 AND b = $2 LIMIT 5", "postgresql",
         "SELECT id FROM t WHERE a = $1 AND b = $2 LIMIT 5"),
    ],
)
def test_outer_limit_survives_dialect_quoting(sql, dialect, validated):
    result = validate_sql_for_ask(sql, default_limit=100, max_limit=1000, dialect=dialect)

    assert result["is_valid"], result["issues"]
    assert result["validated_sql"] == validated
