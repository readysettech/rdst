"""
Unit tests for analysis functions.

Tests schema_collector, readyset_cacheability, and performance_comparison.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Import module directly to avoid package __init__.py issues
def _import_module_directly(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

_lib_path = Path(__file__).parent.parent.parent / "lib"

# Import modules
schema_collector = _import_module_directly("schema_collector", _lib_path / "functions" / "schema_collector.py")
readyset_cacheability = _import_module_directly("readyset_cacheability", _lib_path / "functions" / "readyset_cacheability.py")
performance_comparison = _import_module_directly("performance_comparison", _lib_path / "functions" / "performance_comparison.py")

# Functions to test
collect_target_schema = schema_collector.collect_target_schema
collect_schema_for_query = schema_collector.collect_schema_for_query
_extract_table_names_from_sql = schema_collector._extract_table_names_from_sql

check_readyset_cacheability = readyset_cacheability.check_readyset_cacheability
_normalize_for_analysis = readyset_cacheability._normalize_for_analysis
_is_select_query = readyset_cacheability._is_select_query
_find_blocking_issues = readyset_cacheability._find_blocking_issues
_find_warnings = readyset_cacheability._find_warnings
_has_parameters = readyset_cacheability._has_parameters
_generate_create_cache_command = readyset_cacheability._generate_create_cache_command
generate_explain_create_cache = readyset_cacheability.generate_explain_create_cache

_calculate_statistics = performance_comparison._calculate_statistics
_percentile = performance_comparison._percentile
format_performance_comparison = performance_comparison.format_performance_comparison
compare_query_performance = performance_comparison.compare_query_performance


class TestSchemaCollectorExtractTables:
    """Tests for table name extraction from SQL."""

    def test_simple_select(self):
        """Test extracting from simple SELECT."""
        tables = _extract_table_names_from_sql("SELECT * FROM users")
        assert "users" in tables

    def test_join_query(self):
        """Test extracting from JOIN query."""
        sql = "SELECT * FROM users JOIN orders ON users.id = orders.user_id"
        tables = _extract_table_names_from_sql(sql)
        assert "users" in tables
        assert "orders" in tables

    def test_multiple_joins(self):
        """Test extracting from multiple JOINs."""
        sql = """
            SELECT * FROM users u
            INNER JOIN orders o ON u.id = o.user_id
            LEFT JOIN products p ON o.product_id = p.id
        """
        tables = _extract_table_names_from_sql(sql)
        assert "users" in tables
        assert "orders" in tables
        assert "products" in tables

    def test_insert_query(self):
        """Test extracting from INSERT."""
        sql = "INSERT INTO users (name) VALUES ('test')"
        tables = _extract_table_names_from_sql(sql)
        assert "users" in tables

    def test_update_query(self):
        """Test extracting from UPDATE."""
        sql = "UPDATE users SET name = 'test'"
        tables = _extract_table_names_from_sql(sql)
        assert "users" in tables

    def test_with_aliases(self):
        """Test extracting with table aliases."""
        sql = "SELECT * FROM users AS u JOIN orders AS o ON u.id = o.user_id"
        tables = _extract_table_names_from_sql(sql)
        assert "users" in tables
        assert "orders" in tables

    def test_sql_keywords_excluded(self):
        """Test SQL keywords are not treated as table names."""
        sql = "SELECT * FROM users WHERE id = 1"
        tables = _extract_table_names_from_sql(sql)
        assert "where" not in tables
        assert "select" not in tables


class TestCollectTargetSchema:
    """Tests for collect_target_schema workflow step."""

    def test_missing_target_config(self):
        """Test error when target_config is missing."""
        result = collect_target_schema("SELECT * FROM users", target="test")
        assert result["success"] is False
        assert "not available" in result["schema_info"].lower() or "no target_config" in result.get("error", "").lower()

    def test_invalid_string_target_config(self):
        """Test error when target_config is invalid string."""
        result = collect_target_schema(
            "SELECT * FROM users",
            target="test",
            target_config="invalid json"
        )
        assert result["success"] is False

    def test_unsupported_engine(self):
        """Test unsupported database engine."""
        result = collect_schema_for_query(
            "SELECT * FROM users",
            {"engine": "oracle", "host": "localhost"}
        )
        assert "Unsupported" in result


class TestReadySetCacheability:
    """Tests for ReadySet cacheability checking."""

    def test_empty_query(self):
        """Test empty query is not cacheable."""
        result = check_readyset_cacheability("")
        assert result["cacheable"] is False
        assert "empty" in result["explanation"]

    def test_simple_select_cacheable(self):
        """Test simple SELECT is cacheable."""
        result = check_readyset_cacheability("SELECT * FROM users WHERE id = 1")
        assert result["cacheable"] is True
        assert result["confidence"] == "high"

    def test_insert_not_cacheable(self):
        """Test INSERT is not cacheable."""
        result = check_readyset_cacheability("INSERT INTO users (name) VALUES ('test')")
        assert result["cacheable"] is False
        assert "SELECT" in result["issues"][0]

    def test_update_not_cacheable(self):
        """Test UPDATE is not cacheable."""
        result = check_readyset_cacheability("UPDATE users SET name = 'test'")
        assert result["cacheable"] is False

    def test_delete_not_cacheable(self):
        """Test DELETE is not cacheable."""
        result = check_readyset_cacheability("DELETE FROM users WHERE id = 1")
        assert result["cacheable"] is False

    def test_now_function_not_cacheable(self):
        """Test NOW() function makes query uncacheable."""
        result = check_readyset_cacheability("SELECT NOW()")
        assert result["cacheable"] is False
        assert any("NOW" in issue for issue in result["issues"])

    def test_current_timestamp_not_cacheable(self):
        """Test CURRENT_TIMESTAMP makes query uncacheable."""
        result = check_readyset_cacheability("SELECT CURRENT_TIMESTAMP")
        assert result["cacheable"] is False

    def test_rand_not_cacheable(self):
        """Test RAND() makes query uncacheable."""
        result = check_readyset_cacheability("SELECT RAND()")
        assert result["cacheable"] is False

    def test_for_update_not_cacheable(self):
        """Test FOR UPDATE makes query uncacheable."""
        result = check_readyset_cacheability("SELECT * FROM users FOR UPDATE")
        assert result["cacheable"] is False
        assert any("FOR UPDATE" in issue for issue in result["issues"])

    def test_cte_cacheable(self):
        """Test CTE (WITH clause) is cacheable."""
        sql = """
            WITH active_users AS (
                SELECT * FROM users WHERE active = true
            )
            SELECT * FROM active_users
        """
        result = check_readyset_cacheability(sql)
        assert result["cacheable"] is True

    def test_union_warning(self):
        """Test UNION generates warning."""
        sql = "SELECT * FROM users UNION SELECT * FROM admins"
        result = check_readyset_cacheability(sql)
        assert len(result["warnings"]) > 0
        assert any("UNION" in w for w in result["warnings"])

    def test_json_function_warning(self):
        """Test JSON functions generate warning."""
        sql = "SELECT JSON_EXTRACT(data, '$.name') FROM users"
        result = check_readyset_cacheability(sql)
        assert len(result["warnings"]) > 0


class TestNormalizeForAnalysis:
    """Tests for query normalization."""

    def test_removes_comments(self):
        """Test comments are removed."""
        sql = "SELECT * FROM users -- this is a comment"
        normalized = _normalize_for_analysis(sql)
        assert "comment" not in normalized.lower()

    def test_removes_multiline_comments(self):
        """Test multiline comments are removed."""
        sql = "SELECT /* multi\nline */ * FROM users"
        normalized = _normalize_for_analysis(sql)
        assert "multi" not in normalized.lower()

    def test_normalizes_whitespace(self):
        """Test whitespace is normalized."""
        sql = "SELECT   *   FROM    users"
        normalized = _normalize_for_analysis(sql)
        assert "   " not in normalized

    def test_uppercase(self):
        """Test result is uppercase."""
        sql = "select * from users"
        normalized = _normalize_for_analysis(sql)
        assert "SELECT" in normalized
        assert "FROM" in normalized


class TestIsSelectQuery:
    """Tests for SELECT query detection."""

    def test_select_detected(self):
        """Test SELECT is detected."""
        assert _is_select_query("SELECT * FROM USERS") is True

    def test_with_detected(self):
        """Test WITH (CTE) is detected."""
        assert _is_select_query("WITH X AS (SELECT 1) SELECT * FROM X") is True

    def test_insert_not_select(self):
        """Test INSERT is not SELECT."""
        assert _is_select_query("INSERT INTO USERS VALUES (1)") is False

    def test_update_not_select(self):
        """Test UPDATE is not SELECT."""
        assert _is_select_query("UPDATE USERS SET A = 1") is False


class TestHasParameters:
    """Tests for parameter detection."""

    def test_postgresql_style(self):
        """Test PostgreSQL-style parameters ($1, $2)."""
        assert _has_parameters("SELECT * FROM users WHERE id = $1") is True

    def test_mysql_style(self):
        """Test MySQL-style parameters (?)."""
        assert _has_parameters("SELECT * FROM users WHERE id = ?") is True

    def test_named_parameters(self):
        """Test named parameters (:param)."""
        assert _has_parameters("SELECT * FROM users WHERE id = :user_id") is True

    def test_no_parameters(self):
        """Test query without parameters."""
        assert _has_parameters("SELECT * FROM users WHERE id = 1") is False


class TestGenerateCreateCacheCommand:
    """Tests for CREATE CACHE command generation."""

    def test_basic_command(self):
        """Test basic command generation."""
        command = _generate_create_cache_command("SELECT * FROM users", "high")
        assert "CREATE CACHE" in command
        assert "SELECT * FROM users" in command

    def test_removes_semicolon(self):
        """Test trailing semicolon is removed from query."""
        command = _generate_create_cache_command("SELECT * FROM users;", "high")
        assert ";;;" not in command  # Should not have double semicolons

    def test_medium_confidence_comment(self):
        """Test medium confidence adds comment."""
        command = _generate_create_cache_command("SELECT * FROM users", "medium")
        assert "Medium confidence" in command

    def test_low_confidence_comment(self):
        """Test low confidence adds comment."""
        command = _generate_create_cache_command("SELECT * FROM users", "low")
        assert "Low confidence" in command


class TestGenerateExplainCreateCache:
    """Tests for EXPLAIN CREATE CACHE generation."""

    def test_generates_explain_command(self):
        """Test EXPLAIN command is generated."""
        result = generate_explain_create_cache("SELECT * FROM users")
        assert "EXPLAIN CREATE CACHE" in result["explain_command"]
        assert "SELECT * FROM users" in result["explain_command"]

    def test_empty_query_error(self):
        """Test empty query returns error."""
        result = generate_explain_create_cache("")
        assert result["explain_command"] is None
        assert "error" in result


class TestCalculateStatistics:
    """Tests for performance statistics calculation."""

    def test_empty_list(self):
        """Test empty list returns zeros."""
        stats = _calculate_statistics([])
        assert stats["mean"] == 0
        assert stats["median"] == 0
        assert stats["min"] == 0
        assert stats["max"] == 0

    def test_single_value(self):
        """Test single value statistics."""
        stats = _calculate_statistics([100.0])
        assert stats["mean"] == 100.0
        assert stats["median"] == 100.0
        assert stats["min"] == 100.0
        assert stats["max"] == 100.0
        assert stats["stddev"] == 0

    def test_multiple_values(self):
        """Test multiple values statistics."""
        times = [10.0, 20.0, 30.0, 40.0, 50.0]
        stats = _calculate_statistics(times)

        assert stats["mean"] == 30.0
        assert stats["median"] == 30.0
        assert stats["min"] == 10.0
        assert stats["max"] == 50.0
        assert stats["stddev"] > 0

    def test_percentiles(self):
        """Test percentile calculation."""
        times = list(range(1, 101))  # 1 to 100
        stats = _calculate_statistics([float(t) for t in times])

        assert 45 <= stats["p50"] <= 55
        assert 90 <= stats["p95"] <= 100
        assert 95 <= stats["p99"] <= 100


class TestPercentile:
    """Tests for percentile calculation."""

    def test_empty_list(self):
        """Test empty list returns 0."""
        assert _percentile([], 50) == 0

    def test_p50(self):
        """Test 50th percentile."""
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = _percentile(data, 50)
        assert 2.5 <= result <= 3.5

    def test_p95(self):
        """Test 95th percentile."""
        data = [float(i) for i in range(1, 101)]
        result = _percentile(data, 95)
        assert 94 <= result <= 96

    def test_p99(self):
        """Test 99th percentile."""
        data = [float(i) for i in range(1, 101)]
        result = _percentile(data, 99)
        assert 98 <= result <= 100


class TestCompareQueryPerformance:
    """Tests for query performance comparison."""

    def test_missing_query(self):
        """Test error when query is missing."""
        result = compare_query_performance(
            query=None,
            original_db_config={"host": "localhost"}
        )
        assert result["success"] is False
        assert "No query" in result["error"]

    def test_missing_db_config(self):
        """Test error when db config is missing."""
        result = compare_query_performance(
            query="SELECT * FROM users",
            original_db_config=None
        )
        assert result["success"] is False
        assert "configuration" in result["error"].lower()


class TestFormatPerformanceComparison:
    """Tests for performance comparison formatting."""

    def test_format_failure(self):
        """Test formatting failure result."""
        result = {"success": False, "error": "Test error"}
        formatted = format_performance_comparison(result)
        assert "Test error" in formatted

    def test_format_success_readyset_faster(self):
        """Test formatting when ReadySet is faster."""
        result = {
            "success": True,
            "original": {
                "host": "localhost",
                "port": 5432,
                "stats": {
                    "mean": 100.0,
                    "median": 95.0,
                    "min": 80.0,
                    "max": 120.0,
                    "stddev": 10.0,
                    "p95": 115.0,
                    "p99": 118.0
                }
            },
            "readyset": {
                "host": "localhost",
                "port": 5433,
                "stats": {
                    "mean": 10.0,
                    "median": 9.5,
                    "min": 8.0,
                    "max": 12.0,
                    "stddev": 1.0,
                    "p95": 11.5,
                    "p99": 11.8
                }
            },
            "speedup": {
                "mean": 10.0,
                "median": 10.0,
                "improvement_pct": 900.0
            },
            "winner": "readyset"
        }

        formatted = format_performance_comparison(result)

        assert "Performance Comparison" in formatted
        assert "ReadySet" in formatted
        assert "10.0x faster" in formatted or "10.00x faster" in formatted

    def test_format_success_original_faster(self):
        """Test formatting when original is faster."""
        result = {
            "success": True,
            "original": {
                "host": "localhost",
                "port": 5432,
                "stats": {
                    "mean": 10.0,
                    "median": 9.5,
                    "min": 8.0,
                    "max": 12.0,
                    "stddev": 1.0,
                    "p95": 11.5,
                    "p99": 11.8
                }
            },
            "readyset": {
                "host": "localhost",
                "port": 5433,
                "stats": {
                    "mean": 100.0,
                    "median": 95.0,
                    "min": 80.0,
                    "max": 120.0,
                    "stddev": 10.0,
                    "p95": 115.0,
                    "p99": 118.0
                }
            },
            "speedup": {
                "mean": 0.1,
                "median": 0.1,
                "improvement_pct": -900.0
            },
            "winner": "original"
        }

        formatted = format_performance_comparison(result)

        assert "Original database is faster" in formatted or "slower" in formatted
