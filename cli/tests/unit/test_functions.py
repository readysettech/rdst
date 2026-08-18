"""
Unit tests for functions modules.

Tests explain_analysis, parallel_merge, query_metrics and other function utilities.
"""

import importlib.util
import sys
import threading
from pathlib import Path

# Import module directly to avoid package __init__.py issues
def _import_module_directly(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

_rdst_path = Path(__file__).parent.parent.parent
parallel_merge = _import_module_directly(
    "parallel_merge",
    _rdst_path / "features" / "analyze" / "functions" / "parallel_merge.py",
)
query_metrics = _import_module_directly(
    "query_metrics",
    _rdst_path / "features" / "analyze" / "functions" / "query_metrics.py",
)

merge_parallel_analysis_results = parallel_merge.merge_parallel_analysis_results
_extract_table_names_from_sql = query_metrics._extract_table_names_from_sql


class TestMergeParallelAnalysisResults:
    """Tests for merge_parallel_analysis_results function."""

    def test_missing_analysis_branch(self):
        """Test error when analysis branch is missing."""
        result = merge_parallel_analysis_results(
            analysis_branch=None,
            readyset_branch={"some": "data"}
        )

        assert result["success"] is False
        assert "Missing analysis branch" in result["error"]

    def test_missing_readyset_branch(self):
        """Test error when Readyset branch is missing."""
        result = merge_parallel_analysis_results(
            analysis_branch={"some": "data"},
            readyset_branch=None
        )

        assert result["success"] is False
        assert "Missing Readyset branch" in result["error"]

    def test_successful_merge(self):
        """Test successful merging of both branches."""
        analysis_branch = {
            "registry_normalization": {"normalized": True},
            "llm_parameterization": {"params": []},
            "explain_results": {"success": True, "execution_time_ms": 50},
            "query_metrics": {"metrics": {}},
            "schema_collection": {"tables": []},
            "llm_analysis": {"success": True, "analysis_results": {}},
            "rewrite_test_results": {"rewrites": []},
            "readyset_cacheability": {"cacheable": True, "confidence": "high"}
        }

        readyset_branch = {
            "readyset_explain_cache": {
                "success": True,
                "cacheable": True,
                "confidence": "high"
            },
            "readyset_container": {
                "success": True,
                "container_name": "rdst-test",
                "port": 5433
            },
            "readyset_ready": {"ready": True}
        }

        result = merge_parallel_analysis_results(
            analysis_branch=analysis_branch,
            readyset_branch=readyset_branch,
            query="SELECT * FROM users",
            target="test-db"
        )

        assert result["success"] is True
        assert result["query"] == "SELECT * FROM users"
        assert result["target"] == "test-db"
        assert "explain_results" in result
        assert "llm_analysis" in result
        assert "readyset_cacheability" in result

    def test_readyset_container_verdict(self):
        """Test final verdict uses Readyset container results."""
        analysis_branch = {
            "explain_results": {},
            "readyset_cacheability": {"cacheable": False, "confidence": "low"}
        }

        readyset_branch = {
            "readyset_explain_cache": {
                "success": True,
                "cacheable": True,
                "confidence": "high"
            },
            "readyset_container": {"success": True},
            "readyset_ready": {"ready": True}
        }

        result = merge_parallel_analysis_results(
            analysis_branch=analysis_branch,
            readyset_branch=readyset_branch
        )

        final_verdict = result["readyset_cacheability"]["final_verdict"]
        assert final_verdict["cacheable"] is True
        assert final_verdict["confidence"] == "high"
        assert final_verdict["method"] == "readyset_container"

    def test_fallback_to_static_analysis(self):
        """Test fallback to static analysis when Readyset fails."""
        analysis_branch = {
            "explain_results": {},
            "readyset_cacheability": {"cacheable": True, "confidence": "medium"}
        }

        readyset_branch = {
            "readyset_explain_cache": {"success": False},
            "readyset_container": {"success": False},
            "readyset_ready": {"ready": False}
        }

        result = merge_parallel_analysis_results(
            analysis_branch=analysis_branch,
            readyset_branch=readyset_branch
        )

        final_verdict = result["readyset_cacheability"]["final_verdict"]
        assert final_verdict["cacheable"] is True
        assert final_verdict["confidence"] == "medium"
        assert final_verdict["method"] == "static_analysis"


class TestAnalyzeWorkerLifecycle:
    """Cancellation cannot close evidence ahead of a background execution."""

    def test_delayed_worker_cancelled_before_start_never_executes(self):
        from features.analyze.functions import explain_analysis

        state = explain_analysis._AnalyzeWorkerState("backend_pid")
        identifier_ready = threading.Event()
        allow_start = threading.Event()
        executed = threading.Event()

        def worker():
            try:
                state.set_identifier(42)
                identifier_ready.set()
                allow_start.wait()
                if not state.begin_execution():
                    return
                executed.set()
                state.mark_database_completion()
            finally:
                state.finish()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        assert identifier_ready.wait(timeout=1.0)

        state.request_cancel()
        allow_start.set()
        thread.join(timeout=1.0)

        assert not thread.is_alive()
        assert not executed.is_set()
        assert state.snapshot()["started"] is False

    def test_failed_cancel_has_bounded_settle_wait(self):
        from features.analyze.functions import explain_analysis

        release = threading.Event()
        thread = threading.Thread(target=release.wait, daemon=True)
        thread.start()

        assert explain_analysis._settle_analyze_worker(thread, timeout=0.01) is False
        assert thread.is_alive()

        release.set()
        thread.join(timeout=1.0)
        assert not thread.is_alive()

    def test_open_evidence_closes_only_after_worker_exit(self, monkeypatch):
        from features.analyze.functions import explain_analysis

        state = explain_analysis._AnalyzeWorkerState("backend_pid")
        state.set_identifier(42)
        assert state.begin_execution() is True

        release = threading.Event()

        def worker():
            release.wait()
            state.mark_database_completion()
            state.finish()

        worker_thread = threading.Thread(target=worker, daemon=True)
        worker_thread.start()

        recorded = []
        recorded_lock = threading.Lock()
        closed = threading.Event()

        def capture(*args, **kwargs):
            with recorded_lock:
                recorded.append((args, kwargs))
                if len(recorded) == 2:
                    closed.set()

        monkeypatch.setattr(explain_analysis, "record_execution_evidence", capture)
        snapshot = state.snapshot()
        monkeypatch.setattr(
            explain_analysis,
            "_execute_postgres_explain_analyze",
            lambda sql, config, **kwargs: {
                "success": True,
                "plan_format": "json",
                "explain_analyze_started": True,
                "explain_analyze_started_at": snapshot["started_at"],
                "explain_analyze_ended_at": None,
                "_analyze_worker_thread": worker_thread,
                "_analyze_worker_state": state,
            },
        )

        result = explain_analysis.execute_explain_analyze(
            "SELECT * FROM users",
            target="t1",
            target_config={"engine": "postgresql"},
        )

        assert len(recorded) == 1
        assert recorded[0][1]["ended_at"] is None
        assert "_analyze_worker_thread" not in result
        assert "_analyze_worker_state" not in result

        release.set()
        assert closed.wait(timeout=1.0)
        worker_thread.join(timeout=1.0)

        first_kwargs = recorded[0][1]
        second_kwargs = recorded[1][1]
        assert first_kwargs["run_id"] == second_kwargs["run_id"]
        assert first_kwargs["started_at"] == second_kwargs["started_at"]
        assert second_kwargs["ended_at"] >= second_kwargs["started_at"]


class TestExtractTableNamesFromSql:
    """Tests for _extract_table_names_from_sql function."""

    def test_simple_select(self):
        """Test extracting table from simple SELECT."""
        tables = _extract_table_names_from_sql("SELECT * FROM users")
        assert "users" in tables

    def test_join_query(self):
        """Test extracting tables from JOIN query."""
        sql = "SELECT u.name, o.total FROM users u JOIN orders o ON u.id = o.user_id"
        tables = _extract_table_names_from_sql(sql)
        assert "users" in tables
        assert "orders" in tables

    def test_multiple_joins(self):
        """Test extracting tables from multiple JOINs."""
        sql = """
            SELECT *
            FROM users u
            JOIN orders o ON u.id = o.user_id
            JOIN products p ON o.product_id = p.id
            LEFT JOIN categories c ON p.category_id = c.id
        """
        tables = _extract_table_names_from_sql(sql)
        assert "users" in tables
        assert "orders" in tables
        assert "products" in tables
        assert "categories" in tables

    def test_insert_query(self):
        """Test extracting table from INSERT."""
        sql = "INSERT INTO users (name, email) VALUES ('John', 'john@example.com')"
        tables = _extract_table_names_from_sql(sql)
        assert "users" in tables

    def test_update_query(self):
        """Test extracting table from UPDATE."""
        sql = "UPDATE users SET active = true WHERE id = 1"
        tables = _extract_table_names_from_sql(sql)
        assert "users" in tables

    def test_quoted_table_names(self):
        """Test extracting quoted table names."""
        sql = 'SELECT * FROM "users"'
        tables = _extract_table_names_from_sql(sql)
        assert "users" in tables

    def test_backtick_quoted_names(self):
        """Test extracting backtick-quoted table names."""
        sql = "SELECT * FROM `users`"
        tables = _extract_table_names_from_sql(sql)
        assert "users" in tables

    def test_empty_query(self):
        """Test empty query returns empty list."""
        tables = _extract_table_names_from_sql("")
        assert tables == []

    def test_no_tables(self):
        """Test query without tables returns empty list."""
        tables = _extract_table_names_from_sql("SELECT 1 + 1")
        assert len(tables) == 0

    def test_max_tables_limit(self):
        """Test that max 10 tables are returned."""
        # This is an artificial test - we'll verify the limit behavior
        sql = " ".join([f"JOIN table{i} ON a = b" for i in range(15)])
        tables = _extract_table_names_from_sql(sql)
        assert len(tables) <= 10


class TestExplainAnalysisHelpers:
    """Tests for explain_analysis helper functions."""

    def test_postgres_rows_examined(self):
        """Test PostgreSQL rows examined extraction."""
        # Import the module
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )

        plan_data = {
            "Plan": {
                "Actual Rows": 100,
                "Plans": [
                    {"Actual Rows": 50},
                    {"Actual Rows": 25, "Plans": [{"Actual Rows": 10}]}
                ]
            }
        }

        rows = explain_analysis._extract_postgres_rows_examined(plan_data)
        assert rows == 185  # 100 + 50 + 25 + 10

    def test_postgres_rows_returned(self):
        """Test PostgreSQL rows returned extraction."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )

        plan_data = {
            "Plan": {"Actual Rows": 42}
        }

        rows = explain_analysis._extract_postgres_rows_returned(plan_data)
        assert rows == 42

    def test_postgres_cost(self):
        """Test PostgreSQL cost extraction."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )

        plan_data = {
            "Plan": {"Total Cost": 123.45}
        }

        cost = explain_analysis._extract_postgres_cost(plan_data)
        assert cost == 123.45

    def test_postgres_actual_time(self):
        """Test PostgreSQL actual time extraction."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )

        plan_data = {
            "Plan": {"Actual Total Time": 55.5}
        }

        time_ms = explain_analysis._extract_postgres_actual_time(plan_data)
        assert time_ms == 55.5

    def test_empty_plan_data(self):
        """Test handling of empty plan data."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )

        assert explain_analysis._extract_postgres_rows_examined({}) == 0
        assert explain_analysis._extract_postgres_rows_returned({}) == 0
        assert explain_analysis._extract_postgres_cost({}) == 0.0
        assert explain_analysis._extract_postgres_actual_time({}) == 0.0

    def test_normalize_plan_data_dict(self):
        """_normalize_plan_data passes through a plain dict."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )
        d = {"Plan": {"Node Type": "Seq Scan"}}
        assert explain_analysis._normalize_plan_data(d) == d

    def test_normalize_plan_data_list(self):
        """_normalize_plan_data unwraps a list with one dict element."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )
        inner = {"Plan": {"Node Type": "Seq Scan"}}
        assert explain_analysis._normalize_plan_data([inner]) == inner

    def test_normalize_plan_data_json_string(self):
        """_normalize_plan_data parses a JSON string (psycopg2 edge case)."""
        import json
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )
        inner = {"Plan": {"Node Type": "Index Scan"}}
        raw = json.dumps([inner])
        assert explain_analysis._normalize_plan_data(raw) == inner

    def test_normalize_plan_data_invalid(self):
        """_normalize_plan_data returns empty dict for unrecognized input."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )
        assert explain_analysis._normalize_plan_data(42) == {}
        assert explain_analysis._normalize_plan_data("not json") == {}
        assert explain_analysis._normalize_plan_data([]) == {}

    def test_rows_examined_falls_back_to_plan_rows(self):
        """Rows examined uses Plan Rows when Actual Rows is absent."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )
        plan_data = {
            "Plan": {
                "Plan Rows": 200,
                "Plans": [{"Plan Rows": 50}]
            }
        }
        assert explain_analysis._extract_postgres_rows_examined(plan_data) == 250

    def test_rows_returned_falls_back_to_plan_rows(self):
        """Rows returned uses Plan Rows when Actual Rows is absent."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )
        plan_data = {"Plan": {"Plan Rows": 75}}
        assert explain_analysis._extract_postgres_rows_returned(plan_data) == 75

    def test_cost_handles_string_value(self):
        """Cost extraction handles string Total Cost without crashing."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )
        plan_data = {"Plan": {"Total Cost": "99.5"}}
        assert explain_analysis._extract_postgres_cost(plan_data) == 99.5


class TestMySQLJsonHelpers:
    """Tests for MySQL JSON format helper functions."""

    def test_mysql_json_cost_extraction(self):
        """Test MySQL JSON cost extraction."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )

        plan_data = {
            "query_block": {
                "read_cost": 10.0,
                "eval_cost": 5.0,
                "sort_cost": 2.0
            }
        }

        cost = explain_analysis._extract_mysql_json_cost(plan_data)
        assert cost == 17.0

    def test_mysql_json_rows_returned(self):
        """Test MySQL JSON rows returned extraction."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _rdst_path / "features" / "analyze" / "functions" / "explain_analysis.py"
        )

        plan_data = {
            "query_block": {
                "rows_produced_per_join": 100
            }
        }

        rows = explain_analysis._extract_mysql_json_rows_returned(plan_data)
        assert rows == 100


class TestExecuteExplainAnalyze:
    """Tests for execute_explain_analyze function."""

    def test_missing_target_config(self):
        """Test error when target config is missing."""
        from features.analyze.functions.explain_analysis import execute_explain_analyze

        result = execute_explain_analyze(
            sql="SELECT * FROM users",
            target=None
        )

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_unsupported_engine(self):
        """Test error for unsupported database engine."""
        from features.analyze.functions.explain_analysis import execute_explain_analyze

        result = execute_explain_analyze(
            sql="SELECT * FROM users",
            target="test",
            target_config={"engine": "oracle", "host": "localhost"}
        )

        assert result["success"] is False
        assert "Unsupported" in result["error"]

    def test_mysql_parameterized_query(self):
        """Test MySQL rejects parameterized queries."""
        from features.analyze.functions.explain_analysis import execute_explain_analyze

        result = execute_explain_analyze(
            sql="SELECT * FROM users WHERE id = ?",
            target="test",
            target_config={
                "engine": "mysql",
                "host": "localhost",
                "port": 3306,
                "database": "test",
                "user": "test"
            }
        )

        assert result["success"] is False
        # Either pymysql is not available or the query has parameterized syntax
        assert "?" in result["error"] or "pymysql not available" in result["error"]


class TestCollectQueryMetrics:
    """Tests for collect_query_metrics function."""

    def test_missing_target_config(self):
        """Test error when target config is missing."""
        result = query_metrics.collect_query_metrics(
            sql="SELECT * FROM users",
            target=None
        )

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_unsupported_engine(self):
        """Test error for unsupported database engine."""
        result = query_metrics.collect_query_metrics(
            sql="SELECT * FROM users",
            target="test",
            target_config={"engine": "oracle"}
        )

        assert result["success"] is False
        assert "Unsupported" in result["error"]


class TestAnalyzeExecutionEvidence:
    """Q11 attribution: the analyze lane records what it ran on the target."""

    @staticmethod
    def _capture(monkeypatch, module):
        recorded = []
        monkeypatch.setattr(
            module,
            "record_execution_evidence",
            lambda *args, **kwargs: recorded.append((args, kwargs)),
        )
        return recorded

    def test_full_explain_analyze_records_one_user_execution(self, monkeypatch):
        from features.analyze.functions import explain_analysis

        recorded = self._capture(monkeypatch, explain_analysis)
        monkeypatch.setattr(
            explain_analysis,
            "_execute_postgres_explain_analyze",
            lambda sql, config, **kwargs: {
                "success": True,
                "plan_format": "analyze",
                "explain_analyze_ended_at": 123.456,
            },
        )

        result = explain_analysis.execute_explain_analyze(
            "SELECT * FROM users",
            target="t1",
            target_config={"engine": "postgresql"},
        )

        assert result["success"] is True
        assert len(recorded) == 1
        args, kwargs = recorded[0]
        assert args == ("t1", [{"sql": "SELECT * FROM users", "exec_count": 1}])
        assert kwargs["lane"] == "rdst/analyze"
        assert kwargs["started_at"] == kwargs["ended_at"] == 123.456
        assert isinstance(kwargs["started_at"], float)

    def test_fast_mode_records_zero_user_executions(self, monkeypatch):
        from features.analyze.functions import explain_analysis

        recorded = self._capture(monkeypatch, explain_analysis)
        monkeypatch.setattr(
            explain_analysis,
            "_execute_postgres_explain_analyze",
            lambda sql, config, **kwargs: {
                "success": True,
                "plan_format": "json",
                "explain_analyze_skipped": True,
            },
        )

        explain_analysis.execute_explain_analyze(
            "SELECT * FROM users",
            target="t1",
            target_config={"engine": "postgresql"},
            fast_mode=True,
        )

        assert recorded == []

    def test_fast_mode_that_completes_analyze_records_one(self, monkeypatch):
        from features.analyze.functions import explain_analysis

        recorded = self._capture(monkeypatch, explain_analysis)
        monkeypatch.setattr(
            explain_analysis,
            "_execute_mysql_explain_analyze",
            lambda sql, config, **kwargs: {
                "success": True,
                "plan_format": "analyze",
                "explain_analyze_started": True,
            },
        )

        explain_analysis.execute_explain_analyze(
            "SELECT * FROM users",
            target="t1",
            target_config={"engine": "mysql"},
            fast_mode=True,
        )

        args, _kwargs = recorded[0]
        assert args == ("t1", [{"sql": "SELECT * FROM users", "exec_count": 1}])

    def test_cancelled_analyze_records_unknown_count(self, monkeypatch):
        from features.analyze.functions import explain_analysis

        recorded = self._capture(monkeypatch, explain_analysis)
        monkeypatch.setattr(
            explain_analysis,
            "_execute_postgres_explain_analyze",
            lambda sql, config, **kwargs: {
                "success": True,
                "plan_format": "json",
                "explain_analyze_started": True,
                "explain_analyze_started_at": 200.125,
                "explain_analyze_ended_at": 201.875,
                "explain_analyze_timeout": True,
            },
        )

        explain_analysis.execute_explain_analyze(
            "SELECT * FROM users",
            target="t1",
            target_config={"engine": "postgresql"},
        )

        args, _kwargs = recorded[0]
        assert args == ("t1", [{"sql": "SELECT * FROM users", "exec_count": None}])
        assert _kwargs["started_at"] == 200.125
        assert _kwargs["ended_at"] == 201.875

    def test_cancelled_before_analyze_starts_records_zero(self, monkeypatch):
        from features.analyze.functions import explain_analysis

        recorded = self._capture(monkeypatch, explain_analysis)
        monkeypatch.setattr(
            explain_analysis,
            "_execute_postgres_explain_analyze",
            lambda sql, config, **kwargs: {
                "success": True,
                "plan_format": "json",
                "explain_analyze_started": False,
                "explain_analyze_skipped": True,
            },
        )

        explain_analysis.execute_explain_analyze(
            "SELECT * FROM users",
            target="t1",
            target_config={"engine": "postgresql"},
        )

        assert recorded == []

    def test_failed_explain_records_nothing(self, monkeypatch):
        from features.analyze.functions import explain_analysis

        recorded = self._capture(monkeypatch, explain_analysis)
        monkeypatch.setattr(
            explain_analysis,
            "_execute_postgres_explain_analyze",
            lambda sql, config, **kwargs: {"success": False, "error": "boom"},
        )

        explain_analysis.execute_explain_analyze(
            "SELECT * FROM users",
            target="t1",
            target_config={"engine": "postgresql"},
        )

        assert recorded == []

    def test_failed_analyze_after_possible_start_records_unknown(self, monkeypatch):
        from features.analyze.functions import explain_analysis

        recorded = self._capture(monkeypatch, explain_analysis)
        monkeypatch.setattr(
            explain_analysis,
            "_execute_postgres_explain_analyze",
            lambda sql, config, **kwargs: {
                "success": False,
                "error": "no fallback plan",
                "explain_analyze_started": True,
            },
        )

        result = explain_analysis.execute_explain_analyze(
            "SELECT * FROM users",
            target="t1",
            target_config={"engine": "postgresql"},
        )

        assert result["success"] is False
        args, _kwargs = recorded[0]
        assert args == (
            "t1",
            [{"sql": "SELECT * FROM users", "exec_count": None}],
        )

    def test_metrics_collection_does_not_record_user_execution(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            query_metrics,
            "record_execution_evidence",
            lambda *args, **kwargs: recorded.append((args, kwargs)),
            raising=False,
        )
        monkeypatch.setattr(
            query_metrics,
            "_collect_postgres_metrics",
            lambda sql, config, query_hash, target: {"success": True, "metrics": {}},
        )

        result = query_metrics.collect_query_metrics(
            sql="SELECT * FROM users",
            target="t1",
            target_config={"engine": "postgresql"},
        )

        assert result["success"] is True
        assert recorded == []

    def test_failed_metrics_collection_records_nothing(self, monkeypatch):
        recorded = []
        monkeypatch.setattr(
            query_metrics,
            "record_execution_evidence",
            lambda *args, **kwargs: recorded.append((args, kwargs)),
            raising=False,
        )
        monkeypatch.setattr(
            query_metrics,
            "_collect_postgres_metrics",
            lambda sql, config, query_hash, target: {"success": False},
        )

        query_metrics.collect_query_metrics(
            sql="SELECT * FROM users",
            target="t1",
            target_config={"engine": "postgresql"},
        )

        assert recorded == []

    def test_store_failure_does_not_fail_the_analysis(self, monkeypatch, tmp_path):
        # Drive the real best-effort helper into a broken store: cache.db
        # exists, but opening it raises. The analysis must still succeed.
        from features.analyze.functions import explain_analysis
        from shared.query_registry import observation_store

        cache_db = tmp_path / "cache.db"
        cache_db.touch()
        monkeypatch.setattr(
            observation_store, "default_cache_db_path", lambda: cache_db
        )

        def broken_store(*args, **kwargs):
            raise RuntimeError("store exploded")

        monkeypatch.setattr(observation_store, "ObservationStore", broken_store)
        monkeypatch.setattr(
            explain_analysis,
            "_execute_postgres_explain_analyze",
            lambda sql, config, **kwargs: {
                "success": True,
                "plan_format": "analyze",
            },
        )

        result = explain_analysis.execute_explain_analyze(
            "SELECT * FROM users",
            target="t1",
            target_config={"engine": "postgresql"},
        )

        assert result["success"] is True
