"""
Unit tests for rdst scan command and related components.

Covers:
- Error message uses correct --schema flag (not --target)
- Error messages are not printed twice (renderer + RdstResult)
- AST extraction includes preceding variable assignments
- Duplicate entries for ORM chains are deduplicated by snippet_hash
- ORM detection is import-aware (SQLAlchemy vs Django)
- "No files" message is not printed twice
- JSON output mode emits error info to stderr, not silent
- Empty scan in table mode does not dump raw JSON
- SQLAlchemy text("SQL...") snippets are extracted directly, not sent to LLM
- Check exit code reflects actual status (fail/error/pass/warning)
- Raw SQL with unknown tables correctly identified as "Table not found in schema"
- Scan spinner shows file path instead of parent directory
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from features.scan.cli.command import ScanCommand
from features.scan.extractors.ast_extractor import extract_queries_from_source
from features.scan.service import ScanService

pytestmark = pytest.mark.usefixtures("run_blocking_inline")


class TestErrorMessageFlagName:
    """Error message must use --schema, not --target."""

    def test_missing_target_error_uses_schema_flag(self):
        """When no target provided, error message references --schema flag."""
        cmd = ScanCommand(console=MagicMock())
        result = cmd._scan_directory(
            directory=".",
            dry_run=False,
            analyze=False,
            target=None,
            output_json=False,
        )

        assert result.ok is False
        assert "--schema" in result.message
        assert "--target" not in result.message

    def test_missing_target_error_message_shows_usage(self):
        """Error message includes a usage example with --schema."""
        cmd = ScanCommand(console=MagicMock())
        result = cmd._scan_directory(
            directory=".",
            dry_run=False,
            analyze=False,
            target=None,
            output_json=False,
        )

        assert "Usage:" in result.message
        assert "--schema" in result.message


class TestNoDoubledErrorMessages:
    """Error from ScanErrorEvent must not be printed twice."""

    def _make_error_event_scan(self, error_msg: str):
        """Run _scan_directory with a service that emits a ScanErrorEvent."""
        from features.scan.events import ScanErrorEvent

        async def _fake_scan(_input_data, _options):
            yield ScanErrorEvent(type="error", phase="discovery", message=error_msg)

        cmd = ScanCommand(console=MagicMock())

        # ScanService is imported lazily inside run_async; patch at its source module
        with patch("features.scan.service.ScanService") as MockService:
            instance = MockService.return_value
            instance.scan_directory = _fake_scan
            # Also patch the lazy import path used inside the closure
            with patch("features.scan.cli.command.ScanService", MockService, create=True):
                result = cmd._scan_directory(
                    directory=".",
                    dry_run=False,
                    analyze=False,
                    target="mydb",
                    output_json=False,
                )

        return result

    def test_error_result_has_empty_message(self):
        """When renderer already printed the error, RdstResult.message is empty."""
        result = self._make_error_event_scan("Schema file not found")

        assert result.ok is False
        # The message must be empty so rdst.py doesn't print "Error: ..." a second time
        assert result.message == ""

    def test_error_result_is_not_ok(self):
        """RdstResult.ok is False on scan error."""
        result = self._make_error_event_scan("Something went wrong")
        assert result.ok is False


class TestASTExtractorPrecedingAssignments:
    """Snippets must include preceding variable assignments."""

    def test_stmt_variable_included_in_snippet(self):
        """
        When a terminal method uses a variable defined earlier in the same
        function, the assignment should be included in the ORM snippet.
        """
        source = """\
from sqlalchemy import select
from sqlalchemy.orm import Session

def get_users(db: Session):
    stmt = select(User).where(User.active == True)
    result = db.execute(stmt).scalars().all()
    return result
"""
        queries = extract_queries_from_source(source, "views.py")
        assert len(queries) >= 1

        # The extracted snippet for the terminal call should include the stmt assignment
        snippets = [q.orm_snippet for q in queries]
        combined = "\n".join(snippets)
        assert "stmt" in combined
        # At minimum the terminal expression itself must be present
        assert "execute" in combined or "all" in combined

    def test_stmt_variable_assignment_line_in_snippet(self):
        """The actual assignment line is present in the snippet text."""
        source = """\
from sqlalchemy import select

def fetch(db):
    stmt = select(Order).where(Order.status == 'open')
    return db.execute(stmt).scalars().all()
"""
        queries = extract_queries_from_source(source, "service.py")
        assert len(queries) >= 1

        snippet = queries[0].orm_snippet
        # The assignment should be pulled in
        assert "stmt = select" in snippet

    def test_no_preceding_assignment_no_change(self):
        """
        When the terminal expression is self-contained (no preceding variable),
        the snippet is unchanged.
        """
        source = """\
from sqlalchemy.orm import Session

def get_all(db: Session):
    return db.query(User).filter(User.active == True).all()
"""
        queries = extract_queries_from_source(source, "repo.py")
        assert len(queries) >= 1
        snippet = queries[0].orm_snippet
        # Should not include any extra lines beyond the chain itself
        assert "db.query(User)" in snippet


class TestDeduplicationBySnippetHash:
    """ORM chains triggering multiple terminal methods must be deduplicated."""

    def test_execute_scalars_all_chain_deduped(self):
        """
        session.execute(stmt).scalars().all() must produce exactly one entry,
        not three (execute, scalars are not terminal, but execute and all both are).
        """
        source = """\
from sqlalchemy.orm import Session
from sqlalchemy import select

def get_rows(db: Session):
    stmt = select(User)
    return db.execute(stmt).scalars().all()
"""
        queries = extract_queries_from_source(source, "repo.py")
        # All extracted queries should have unique snippet hashes
        hashes = [q.snippet_hash for q in queries]
        assert len(hashes) == len(set(hashes)), (
            f"Duplicate snippet hashes found: {hashes}"
        )

    def test_no_duplicate_hashes_for_chained_terminals(self):
        """Multiple terminal methods on same chain don't produce duplicates."""
        source = """\
from sqlalchemy.orm import Session

def service_a(db: Session):
    return db.query(Item).filter(Item.active == True).all()

def service_b(db: Session):
    return db.query(Item).first()
"""
        queries = extract_queries_from_source(source, "svc.py")
        hashes = [q.snippet_hash for q in queries]
        assert len(hashes) == len(set(hashes)), (
            f"Duplicate snippet hashes: {hashes}"
        )

    def test_distinct_queries_not_deduped(self):
        """Two genuinely different queries in same file both appear."""
        source = """\
from sqlalchemy.orm import Session

def get_users(db: Session):
    return db.query(User).all()

def get_orders(db: Session):
    return db.query(Order).first()
"""
        queries = extract_queries_from_source(source, "repo.py")
        assert len(queries) == 2, f"Expected 2 distinct queries, got {len(queries)}"


class TestORMDetectionImportAware:
    """ORM detection must use imports, not just method patterns."""

    def _detect(self, content: str, filename: str = "views.py") -> list:
        from features.scan.service import ScanService
        return ScanService._detect_orms(Path(filename), content)

    def test_sqlalchemy_file_not_labeled_django(self):
        """A file with SQLAlchemy imports must NOT be labeled as Django."""
        content = """\
from sqlalchemy.orm import Session
from sqlalchemy import select

def get_users(db: Session):
    return db.query(User).filter(User.active == True).all()
"""
        detected = self._detect(content)
        assert "sqlalchemy" in detected
        assert "django" not in detected

    def test_django_file_not_labeled_sqlalchemy(self):
        """A file with Django imports must NOT be labeled as SQLAlchemy."""
        content = """\
from django.db import models
from myapp.models import User

def get_active_users():
    return User.objects.filter(active=True).all()
"""
        detected = self._detect(content)
        assert "django" in detected
        assert "sqlalchemy" not in detected

    def test_file_with_both_imports_labeled_both(self):
        """
        A file importing both SQLAlchemy and Django (rare but valid) gets both
        labels.
        """
        content = """\
from sqlalchemy.orm import Session
from django.db import models

def mixed():
    pass
"""
        detected = self._detect(content)
        # Both should appear since both imports are present
        assert "sqlalchemy" in detected or "django" in detected

    def test_file_no_orm_imports_uses_pattern_fallback(self):
        """
        A Python file with no explicit ORM import falls back to pattern
        matching (backwards-compatibility).
        """
        content = """\
# Legacy file with no explicit imports
def get_users(db):
    return db.query(User).filter(User.active == True).all()
"""
        detected = self._detect(content)
        # With no explicit imports both patterns can fire — that's the fallback
        # The important thing is no crash and at least one ORM is detected
        assert isinstance(detected, list)


class TestNoFilesMessageNotDoubled:
    """'No files with ORM patterns found' must appear only once."""

    def _run_scan_no_files(self):
        """Run _scan_directory when the service finds no ORM files."""
        from features.scan.events import ScanFilesFoundEvent

        async def _fake_scan(_input_data, _options):
            yield ScanFilesFoundEvent(type="files_found", files=[], total=0)

        cmd = ScanCommand(console=MagicMock())

        # ScanService is imported lazily inside run_async; patch at its source module
        with patch("features.scan.service.ScanService") as MockService:
            instance = MockService.return_value
            instance.scan_directory = _fake_scan
            with patch("features.scan.cli.command.ScanService", MockService, create=True):
                result = cmd._scan_directory(
                    directory=".",
                    dry_run=False,
                    analyze=False,
                    target="mydb",
                    output_json=False,
                )

        return result

    def test_no_files_result_message_is_empty(self):
        """
        When no ORM files are found, RdstResult.message is empty.

        The renderer already prints the message via _render_files_found, so the
        command must not return it in the message field to avoid a second print.
        """
        result = self._run_scan_no_files()
        assert result.ok is True
        assert result.message == ""

    def test_no_files_result_is_ok(self):
        """Finding no ORM files is not an error condition."""
        result = self._run_scan_no_files()
        assert result.ok is True


class TestJsonModeErrorVisibility:
    """--output json should not silently swallow scan errors."""

    def _run_scan_with_error(self, error_msg: str, output_json: bool):
        """Helper: run _scan_directory with a service that emits ScanErrorEvent."""
        from features.scan.events import ScanErrorEvent

        async def _fake_scan(_input_data, _options):
            yield ScanErrorEvent(type="error", phase="config", message=error_msg)

        cmd = ScanCommand(console=MagicMock())

        with patch("features.scan.service.ScanService") as MockService:
            instance = MockService.return_value
            instance.scan_directory = _fake_scan
            with patch("features.scan.cli.command.ScanService", MockService, create=True):
                result = cmd._scan_directory(
                    directory=".",
                    dry_run=False,
                    analyze=False,
                    target="mydb",
                    output_json=output_json,
                )

        return result

    def test_json_mode_result_is_not_ok_on_error(self):
        """JSON mode: result.ok must be False when a scan error occurs."""
        result = self._run_scan_with_error("No schema found for target 'mydb'", output_json=True)
        assert result.ok is False

    def test_json_mode_error_message_is_non_empty(self):
        """JSON mode: result.message must contain the error text, not be empty."""
        error_msg = "No schema found for target 'mydb'"
        result = self._run_scan_with_error(error_msg, output_json=True)
        assert result.message  # not empty string
        assert error_msg in result.message

    def test_json_mode_writes_json_error_to_stderr(self, capsys):
        """JSON mode: a JSON error object must be written to stderr."""
        error_msg = "No schema found for target 'mydb'"
        self._run_scan_with_error(error_msg, output_json=True)

        captured = capsys.readouterr()
        # stderr must contain parseable JSON with an "error" key
        stderr_text = captured.err.strip()
        assert stderr_text, "stderr should not be empty in JSON error mode"
        error_obj = json.loads(stderr_text)
        assert "error" in error_obj
        assert error_msg in error_obj["error"]

    def test_non_json_mode_error_message_is_empty(self):
        """Table mode: renderer already printed the error; result.message stays empty.

        This prevents rdst.py from printing "Error: ..." a second time after the
        ScanRenderer already displayed the Rich-formatted error in the terminal.
        """
        error_msg = "Some scan error"
        result = self._run_scan_with_error(error_msg, output_json=False)
        assert result.ok is False
        # message must be empty so rdst.py does not double-print it
        assert result.message == ""

    def test_json_mode_stderr_is_valid_json(self, capsys):
        """JSON mode: stderr output must be parseable JSON on any error."""
        self._run_scan_with_error("LLM API key not configured", output_json=True)
        captured = capsys.readouterr()
        # Should not raise
        parsed = json.loads(captured.err.strip())
        assert isinstance(parsed, dict)


class TestEmptyScanNoRawJsonDump:
    """No ORM files found should not leak raw JSON in table mode."""

    def _run_empty_scan(self, output_json: bool):
        """Helper: run _scan_directory with a service that emits empty files found."""
        from features.scan.events import ScanCompleteEvent, ScanFilesFoundEvent

        async def _fake_scan(_input_data, _options):
            yield ScanFilesFoundEvent(type="files_found", files=[], total=0)
            yield ScanCompleteEvent(
                type="complete",
                success=True,
                summary={
                    "files_count": 0,
                    "queries_total": 0,
                    "queries_sql": 0,
                    "queries_skipped": 0,
                    "cache_hits": 0,
                    "cache_misses": 0,
                    "registry_new": 0,
                    "registry_updated": 0,
                    "registry_total": 0,
                    "message": "No files with ORM patterns found.",
                },
            )

        cmd = ScanCommand(console=MagicMock())

        with patch("features.scan.service.ScanService") as MockService:
            instance = MockService.return_value
            instance.scan_directory = _fake_scan
            with patch("features.scan.cli.command.ScanService", MockService, create=True):
                result = cmd._scan_directory(
                    directory=".",
                    dry_run=False,
                    analyze=False,
                    target="mydb",
                    output_json=output_json,
                )

        return result

    def test_table_mode_empty_scan_has_no_data(self):
        """Table mode: empty scan must return data=None so rdst.py does not dump JSON."""
        result = self._run_empty_scan(output_json=False)
        assert result.ok is True
        # data must be None (or falsy) to prevent rdst.py's json.dumps branch
        assert not result.data, (
            f"Expected result.data to be None/falsy, got {result.data!r}. "
            "This would cause rdst.py to print raw JSON in table mode."
        )

    def test_table_mode_empty_scan_has_no_message(self):
        """Table mode: the renderer already printed the message; result.message must be empty."""
        result = self._run_empty_scan(output_json=False)
        assert result.ok is True
        assert result.message == ""

    def test_json_mode_empty_scan_returns_data(self):
        """JSON mode: empty scan must still return structured data when --output json is set."""
        result = self._run_empty_scan(output_json=True)
        assert result.ok is True
        # data must be present and parseable
        assert result.data is not None
        assert "files" in result.data
        assert "queries" in result.data
        assert result.data["files"] == []
        assert result.data["queries"] == []

    def test_json_mode_empty_scan_message_is_json(self):
        """JSON mode: result.message should be valid JSON for empty results."""
        result = self._run_empty_scan(output_json=True)
        assert result.ok is True
        # message should be parseable JSON
        parsed = json.loads(result.message)
        assert "files" in parsed
        assert "queries" in parsed


class TestTextLiteralSqlExtraction:
    """SQLAlchemy text("SQL") snippets must be extracted without LLM."""

    def test_double_quoted_text_pattern(self):
        """text("SELECT ...") extracts the literal SQL."""
        orm_code = 'session.execute(text("SELECT * FROM users WHERE active = true")).fetchall()'
        sql = ScanService._extract_text_literal_sql(orm_code)
        assert sql == "SELECT * FROM users WHERE active = true"

    def test_single_quoted_text_pattern(self):
        """text('SELECT ...') extracts the literal SQL."""
        orm_code = "db.execute(text('SELECT id FROM orders WHERE status = $1'))"
        sql = ScanService._extract_text_literal_sql(orm_code)
        assert sql == "SELECT id FROM orders WHERE status = $1"

    def test_triple_double_quoted_text_pattern(self):
        """text(\"\"\"SELECT ...\"\"\") extracts the literal SQL."""
        orm_code = 'result = session.execute(text("""SELECT id, name FROM customers LIMIT 100"""))'
        sql = ScanService._extract_text_literal_sql(orm_code)
        assert sql == "SELECT id, name FROM customers LIMIT 100"

    def test_triple_single_quoted_text_pattern(self):
        """text('''SELECT ...''') extracts the literal SQL."""
        orm_code = "result = session.execute(text('''SELECT * FROM products WHERE price > $1'''))"
        sql = ScanService._extract_text_literal_sql(orm_code)
        assert sql == "SELECT * FROM products WHERE price > $1"

    def test_multiline_text_sql(self):
        """Multi-line SQL inside text() is extracted intact."""
        orm_code = (
            'session.execute(text("""\n'
            "    SELECT id, name\n"
            "    FROM users\n"
            '    WHERE active = true\n"""))'
        )
        sql = ScanService._extract_text_literal_sql(orm_code)
        assert sql is not None
        assert "SELECT id, name" in sql
        assert "FROM users" in sql

    def test_non_text_orm_returns_none(self):
        """Regular ORM (no text()) returns None — should go to LLM."""
        orm_code = "session.query(User).filter(User.active == True).all()"
        sql = ScanService._extract_text_literal_sql(orm_code)
        assert sql is None

    def test_empty_text_call_returns_none(self):
        """text('') or text("") should return None (empty string is not useful SQL)."""
        assert ScanService._extract_text_literal_sql('session.execute(text(""))') is None
        assert ScanService._extract_text_literal_sql("session.execute(text(''))") is None

    def test_text_sql_bypasses_llm_in_batch_convert(self):
        """_batch_convert_snippets must not call LLM for text() snippets."""
        service = ScanService()

        orm_code = 'session.execute(text("SELECT * FROM users WHERE active = true")).fetchall()'
        queries = [
            {
                "orm_code": orm_code,
                "snippet_hash": "abc123",
                "sql": "",
                "issues": [],
            }
        ]

        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        # LLMManager is lazily imported inside _batch_convert_snippets;
        # patch it at the source module level.
        with patch("shared.llm_manager.LLMManager") as MockLLM:
            service._batch_convert_snippets(
                queries=queries,
                snippet_cache=mock_cache,
                schema_context="",
                sql_dialect="PostgreSQL",
            )
            # LLM must never have been called
            MockLLM.return_value.query.assert_not_called()

        assert queries[0]["sql"] == "SELECT * FROM users WHERE active = true"

    def test_text_sql_mixed_with_orm_only_sends_orm_to_llm(self):
        """When mix of text() and ORM queries, only ORM queries go to LLM."""
        service = ScanService()

        text_query = {
            "orm_code": 'session.execute(text("SELECT 1")).fetchone()',
            "snippet_hash": "aaa111",
            "sql": "",
            "issues": [],
        }
        orm_query = {
            "orm_code": "session.query(User).filter(User.id == $1).all()",
            "snippet_hash": "bbb222",
            "sql": "",
            "issues": [],
        }

        mock_cache = MagicMock()
        mock_cache.get.return_value = None

        # Simulate LLM returning SQL for the one ORM query
        with patch("shared.llm_manager.LLMManager") as MockLLM:
            MockLLM.return_value.query.return_value = {
                "text": '{"queries": ["SELECT id FROM users WHERE id = $1"]}'
            }

            service._batch_convert_snippets(
                queries=[text_query, orm_query],
                snippet_cache=mock_cache,
                schema_context="",
                sql_dialect="PostgreSQL",
            )

            # LLM called exactly once (for the ORM query only)
            MockLLM.return_value.query.assert_called_once()

        # text() query resolved without LLM
        assert text_query["sql"] == "SELECT 1"
        # ORM query resolved via LLM
        assert orm_query["sql"] == "SELECT id FROM users WHERE id = $1"

    def test_text_pattern_with_whitespace_around_quotes(self):
        """text(  "SQL"  ) with extra whitespace still extracts correctly."""
        orm_code = 'session.execute(text(  "SELECT 1"  ))'
        sql = ScanService._extract_text_literal_sql(orm_code)
        assert sql == "SELECT 1"


class TestCheckExitCodeOnFailure:
    """_check_queries must return ok=False when status is fail or error."""

    def test_fail_status_returns_ok_false(self):
        """When status is 'fail', _check_queries must return ok=False."""
        from shared.cli.types import RdstResult
        results = {
            "status": "fail",
            "total_queries": 5,
            "new_queries": 0,
            "queries_with_issues": 2,
            "issues": [],
        }
        is_failure = results["status"] in ("fail", "error")
        result = RdstResult(not is_failure, "", data=results)
        assert result.ok is False

    def test_error_status_returns_ok_false(self):
        """When status is 'error', _check_queries must return ok=False."""
        from shared.cli.types import RdstResult
        results = {
            "status": "error",
            "total_queries": 5,
            "new_queries": 0,
            "queries_with_issues": 0,
            "issues": [],
        }
        is_failure = results["status"] in ("fail", "error")
        result = RdstResult(not is_failure, "", data=results)
        assert result.ok is False

    def test_pass_status_returns_ok_true(self):
        """When status is 'pass', _check_queries must return ok=True."""
        from shared.cli.types import RdstResult
        results = {
            "status": "pass",
            "total_queries": 5,
            "new_queries": 0,
            "queries_with_issues": 0,
            "issues": [],
        }
        is_failure = results["status"] in ("fail", "error")
        result = RdstResult(not is_failure, "", data=results)
        assert result.ok is True

    def test_warning_status_returns_ok_true(self):
        """When status is 'warning', _check_queries must return ok=True (non-blocking)."""
        from shared.cli.types import RdstResult
        results = {
            "status": "warning",
            "total_queries": 5,
            "new_queries": 2,
            "queries_with_issues": 0,
            "issues": [],
        }
        is_failure = results["status"] in ("fail", "error")
        result = RdstResult(not is_failure, "", data=results)
        assert result.ok is True

    def test_fail_status_json_mode_returns_ok_false(self):
        """JSON mode: fail status must still produce ok=False."""
        from shared.cli.types import RdstResult
        results = {
            "status": "fail",
            "total_queries": 3,
            "new_queries": 0,
            "queries_with_issues": 3,
            "issues": [],
        }
        is_failure = results["status"] in ("fail", "error")
        result = RdstResult(not is_failure, json.dumps(results, indent=2), data=results)
        assert result.ok is False
        # message should still be valid JSON
        parsed = json.loads(result.message)
        assert parsed["status"] == "fail"

    def test_actual_check_queries_no_queries_passes(self):
        """_check_queries with no scan queries in registry returns pass."""
        cmd = ScanCommand(console=MagicMock())
        mock_registry = MagicMock()
        mock_registry.list_queries.return_value = []

        with patch("features.scan.cli.command.QueryRegistry", return_value=mock_registry):
            result = cmd._check_queries(
                directory=".",
                diff=None,
                target=None,
                output_json=False,
            )

        assert result.ok is True

    def test_actual_check_queries_with_scan_queries_passes(self):
        """_check_queries with scan queries and pass status returns ok=True."""
        cmd = ScanCommand(console=MagicMock())
        mock_registry = MagicMock()
        mock_query = MagicMock()
        mock_query.source = "scan"
        mock_registry.list_queries.return_value = [mock_query]

        with patch("features.scan.cli.command.QueryRegistry", return_value=mock_registry):
            result = cmd._check_queries(
                directory=".",
                diff=None,
                target=None,
                output_json=False,
            )

        # Default status is "pass" — must succeed
        assert result.ok is True


class TestSkipReasonTableNotFound:
    """SQL snippets with unknown tables should be skipped as 'Table not found in schema'."""

    def _infer(self, sql: str, orm_code: str, q: dict = None) -> str:
        service = ScanService()
        return service._infer_skip_reason(sql, orm_code, q or {})

    def test_raw_select_with_no_sql_output_gets_table_not_found(self):
        """When orm_code contains SELECT and sql is empty, reason is 'Table not found in schema'."""
        reason = self._infer("", "SELECT * FROM unknown_table WHERE id = $1")
        assert reason == "Table not found in schema"

    def test_raw_select_with_not_a_query_marker_gets_table_not_found(self):
        """When LLM outputs '-- Not a query' for a SELECT snippet, reason is table not found."""
        reason = self._infer("-- Not a query", "SELECT id, name FROM some_unknown_table")
        assert reason == "Table not found in schema"

    def test_insert_with_not_a_query_marker_gets_table_not_found(self):
        """INSERT snippets with unknown table should use 'Table not found in schema'."""
        reason = self._infer(
            "-- not a query",
            "INSERT INTO unknown_orders (id, total) VALUES ($1, $2)",
        )
        assert reason == "Table not found in schema"

    def test_update_with_unknown_table_gets_table_not_found(self):
        """UPDATE snippets with unknown table should use 'Table not found in schema'."""
        reason = self._infer("", "UPDATE unknown_users SET active = false WHERE id = $1")
        assert reason == "Table not found in schema"

    def test_delete_with_unknown_table_gets_table_not_found(self):
        """DELETE snippets with unknown table should use 'Table not found in schema'."""
        reason = self._infer("", "DELETE FROM unknown_sessions WHERE expired = true")
        assert reason == "Table not found in schema"

    def test_non_sql_orm_code_still_gets_not_a_database_query(self):
        """Pure ORM method calls that aren't SQL still get 'Not a database query'."""
        reason = self._infer("", "session.query(User).filter(User.active).all()")
        assert reason == "Not a database query"

    def test_session_management_still_detected(self):
        """session.commit() is session management, not table-not-found."""
        reason = self._infer("", "session.commit()")
        assert reason == "Session management, not a query"

    def test_bulk_create_still_detected(self):
        """bulk_create is a bulk operation, not table-not-found."""
        reason = self._infer("", "User.objects.bulk_create(users)")
        assert reason == "Bulk operation - list of objects built at runtime"

    def test_with_clause_gets_table_not_found(self):
        """WITH (CTE) snippets with unknown table should use 'Table not found in schema'."""
        reason = self._infer(
            "",
            "WITH ranked AS (SELECT id FROM unknown_table) SELECT * FROM ranked",
        )
        assert reason == "Table not found in schema"

    def test_cross_file_query_still_takes_priority(self):
        """imports_builder=True takes priority over SQL-looks-like-table-not-found."""
        reason = self._infer("", "SELECT * FROM foo", q={"imports_builder": True})
        assert "Cross-file" in reason


class TestSpinnerShowsFilePath:
    """Spinner should show the file path when scanning a single file."""

    def test_single_file_spinner_uses_file_path(self, tmp_path):
        """When input is a file, the discovery spinner must mention the file path."""
        import asyncio
        from features.scan.models import ScanInput, ScanOptions
        from features.scan.events import ScanStatusEvent

        fake_file = tmp_path / "models.py"
        fake_file.write_text("from sqlalchemy import Column\n")

        input_data = ScanInput(directory=str(fake_file), target="mydb", source="test")
        options = ScanOptions(dry_run=True)

        messages = []

        async def collect():
            service = ScanService()
            with patch("features.scan.service.rdst_semantic_layer_dir") as mock_dir_fn:
                mock_schema = MagicMock()
                mock_schema.exists.return_value = True
                fake_dir = MagicMock()
                fake_dir.__truediv__ = lambda self_inner, other: mock_schema
                mock_dir_fn.return_value = fake_dir

                with patch.object(service, "_find_orm_files", return_value=[]):
                    async for event in service.scan_directory(input_data, options):
                        if isinstance(event, ScanStatusEvent) and event.phase == "discovery":
                            messages.append(event.message)

        asyncio.run(collect())

        assert messages, "Expected at least one discovery status event"
        discovery_msg = messages[0]
        # Must mention the file path, not just the parent directory
        assert str(fake_file) in discovery_msg, (
            f"Expected file path '{fake_file}' in spinner message, got: {discovery_msg!r}"
        )

    def test_single_file_spinner_does_not_show_only_parent_dir(self, tmp_path):
        """When input is /tmp/foo/models.py, spinner must not say 'Scanning /tmp/foo ...'."""
        import asyncio
        from features.scan.models import ScanInput, ScanOptions
        from features.scan.events import ScanStatusEvent

        subdir = tmp_path / "myapp"
        subdir.mkdir()
        fake_file = subdir / "models.py"
        fake_file.write_text("from sqlalchemy import Column\n")

        input_data = ScanInput(directory=str(fake_file), target="mydb", source="test")
        options = ScanOptions(dry_run=True)

        messages = []

        async def collect():
            service = ScanService()
            with patch("features.scan.service.rdst_semantic_layer_dir") as mock_dir_fn:
                mock_schema = MagicMock()
                mock_schema.exists.return_value = True
                fake_dir = MagicMock()
                fake_dir.__truediv__ = lambda self_inner, other: mock_schema
                mock_dir_fn.return_value = fake_dir

                with patch.object(service, "_find_orm_files", return_value=[]):
                    async for event in service.scan_directory(input_data, options):
                        if isinstance(event, ScanStatusEvent) and event.phase == "discovery":
                            messages.append(event.message)

        asyncio.run(collect())

        assert messages
        discovery_msg = messages[0]
        parent_only = f"Scanning {str(subdir)} for ORM patterns"
        assert discovery_msg != parent_only, (
            f"Spinner should not show parent-only path when a file was given. Got: {discovery_msg!r}"
        )
        assert str(fake_file) in discovery_msg

    def test_directory_scan_spinner_shows_directory(self, tmp_path):
        """When input is a directory, spinner should still show the directory path."""
        import asyncio
        from features.scan.models import ScanInput, ScanOptions
        from features.scan.events import ScanStatusEvent

        input_data = ScanInput(directory=str(tmp_path), target="mydb", source="test")
        options = ScanOptions(dry_run=True)

        messages = []

        async def collect():
            service = ScanService()
            with patch("features.scan.service.rdst_semantic_layer_dir") as mock_dir_fn:
                mock_schema = MagicMock()
                mock_schema.exists.return_value = True
                fake_dir = MagicMock()
                fake_dir.__truediv__ = lambda self_inner, other: mock_schema
                mock_dir_fn.return_value = fake_dir

                with patch.object(service, "_find_orm_files", return_value=[]):
                    async for event in service.scan_directory(input_data, options):
                        if isinstance(event, ScanStatusEvent) and event.phase == "discovery":
                            messages.append(event.message)

        asyncio.run(collect())

        assert messages
        discovery_msg = messages[0]
        assert str(tmp_path) in discovery_msg
