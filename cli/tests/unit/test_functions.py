"""
Unit tests for functions modules.

Tests explain_analysis, parallel_merge, query_metrics and other function utilities.
"""

import importlib.util
import sys
from pathlib import Path

# Import module directly to avoid package __init__.py issues
def _import_module_directly(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

_lib_path = Path(__file__).parent.parent.parent / "lib"
parallel_merge = _import_module_directly("parallel_merge", _lib_path / "functions" / "parallel_merge.py")
query_metrics = _import_module_directly("query_metrics", _lib_path / "functions" / "query_metrics.py")

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
        """Test error when ReadySet branch is missing."""
        result = merge_parallel_analysis_results(
            analysis_branch={"some": "data"},
            readyset_branch=None
        )

        assert result["success"] is False
        assert "Missing ReadySet branch" in result["error"]

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
        """Test final verdict uses ReadySet container results."""
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
        """Test fallback to static analysis when ReadySet fails."""
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
            _lib_path / "functions" / "explain_analysis.py"
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
            _lib_path / "functions" / "explain_analysis.py"
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
            _lib_path / "functions" / "explain_analysis.py"
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
            _lib_path / "functions" / "explain_analysis.py"
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
            _lib_path / "functions" / "explain_analysis.py"
        )

        assert explain_analysis._extract_postgres_rows_examined({}) == 0
        assert explain_analysis._extract_postgres_rows_returned({}) == 0
        assert explain_analysis._extract_postgres_cost({}) == 0.0
        assert explain_analysis._extract_postgres_actual_time({}) == 0.0


class TestMySQLJsonHelpers:
    """Tests for MySQL JSON format helper functions."""

    def test_mysql_json_cost_extraction(self):
        """Test MySQL JSON cost extraction."""
        explain_analysis = _import_module_directly(
            "explain_analysis",
            _lib_path / "functions" / "explain_analysis.py"
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
            _lib_path / "functions" / "explain_analysis.py"
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
        from lib.functions.explain_analysis import execute_explain_analyze

        result = execute_explain_analyze(
            sql="SELECT * FROM users",
            target=None
        )

        assert result["success"] is False
        assert "not found" in result["error"]

    def test_unsupported_engine(self):
        """Test error for unsupported database engine."""
        from lib.functions.explain_analysis import execute_explain_analyze

        result = execute_explain_analyze(
            sql="SELECT * FROM users",
            target="test",
            target_config={"engine": "oracle", "host": "localhost"}
        )

        assert result["success"] is False
        assert "Unsupported" in result["error"]

    def test_mysql_parameterized_query(self):
        """Test MySQL rejects parameterized queries."""
        from lib.functions.explain_analysis import execute_explain_analyze

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
