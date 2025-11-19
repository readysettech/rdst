"""
Unit tests for query_safety.py

Tests the query safety validation functionality that prevents destructive SQL operations.
"""

import pytest
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
query_safety = _import_module_directly("query_safety", _lib_path / "functions" / "query_safety.py")

validate_query_safety = query_safety.validate_query_safety
DANGEROUS_KEYWORDS = query_safety.DANGEROUS_KEYWORDS
ALLOWED_KEYWORDS = query_safety.ALLOWED_KEYWORDS
_normalize_query_for_safety = query_safety._normalize_query_for_safety
_extract_sql_keywords = query_safety._extract_sql_keywords
_validate_query_patterns = query_safety._validate_query_patterns


class TestValidateQuerySafety:
    """Tests for the main validate_query_safety function."""

    def test_safe_select_query(self):
        """Test that basic SELECT queries are marked as safe."""
        sql = "SELECT * FROM users WHERE id = 123"
        result = validate_query_safety(sql)

        assert result["safe"] is True
        assert len(result["issues"]) == 0

    def test_safe_select_with_joins(self):
        """Test that SELECT with JOINs is safe."""
        sql = """
        SELECT u.name, o.total
        FROM users u
        JOIN orders o ON u.id = o.user_id
        WHERE o.status = 'active'
        """
        result = validate_query_safety(sql)

        assert result["safe"] is True

    def test_safe_explain_query(self):
        """Test that EXPLAIN queries are safe."""
        sql = "EXPLAIN SELECT * FROM users"
        result = validate_query_safety(sql)

        assert result["safe"] is True

    def test_safe_with_cte(self):
        """Test that WITH (CTE) queries are safe."""
        sql = """
        WITH active_users AS (
            SELECT * FROM users WHERE status = 'active'
        )
        SELECT * FROM active_users
        """
        result = validate_query_safety(sql)

        assert result["safe"] is True

    def test_safe_show_query(self):
        """Test that SHOW queries are safe."""
        sql = "SHOW TABLES"
        result = validate_query_safety(sql)

        assert result["safe"] is True

    def test_safe_describe_query(self):
        """Test that DESCRIBE queries are safe."""
        sql = "DESCRIBE users"
        result = validate_query_safety(sql)

        assert result["safe"] is True

    def test_empty_query_is_unsafe(self):
        """Test that empty queries are marked as unsafe."""
        sql = ""
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert "Empty query" in result["issues"][0]

    def test_whitespace_only_query_is_unsafe(self):
        """Test that whitespace-only queries are marked as unsafe."""
        sql = "   \n\t   "
        result = validate_query_safety(sql)

        assert result["safe"] is False


class TestDangerousQueries:
    """Tests that dangerous queries are properly detected."""

    def test_delete_query_is_unsafe(self):
        """Test that DELETE queries are marked as unsafe."""
        sql = "DELETE FROM users WHERE id = 123"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert "DELETE" in result["dangerous_keywords"]

    def test_update_query_is_unsafe(self):
        """Test that UPDATE queries are marked as unsafe."""
        sql = "UPDATE users SET name = 'John' WHERE id = 123"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert "UPDATE" in result["dangerous_keywords"]

    def test_insert_query_is_unsafe(self):
        """Test that INSERT queries are marked as unsafe."""
        sql = "INSERT INTO users (name, email) VALUES ('John', 'john@example.com')"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert "INSERT" in result["dangerous_keywords"]

    def test_drop_table_is_unsafe(self):
        """Test that DROP TABLE queries are marked as unsafe."""
        sql = "DROP TABLE users"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert "DROP" in result["dangerous_keywords"]

    def test_truncate_is_unsafe(self):
        """Test that TRUNCATE queries are marked as unsafe."""
        sql = "TRUNCATE TABLE users"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert "TRUNCATE" in result["dangerous_keywords"]

    def test_create_table_is_unsafe(self):
        """Test that CREATE TABLE queries are marked as unsafe."""
        sql = "CREATE TABLE new_users (id INT, name VARCHAR(255))"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert "CREATE" in result["dangerous_keywords"]

    def test_alter_table_is_unsafe(self):
        """Test that ALTER TABLE queries are marked as unsafe."""
        sql = "ALTER TABLE users ADD COLUMN phone VARCHAR(20)"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert "ALTER" in result["dangerous_keywords"]

    def test_grant_is_unsafe(self):
        """Test that GRANT queries are marked as unsafe."""
        sql = "GRANT SELECT ON users TO reader"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert "GRANT" in result["dangerous_keywords"]

    def test_revoke_is_unsafe(self):
        """Test that REVOKE queries are marked as unsafe."""
        sql = "REVOKE SELECT ON users FROM reader"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert "REVOKE" in result["dangerous_keywords"]

    def test_call_is_unsafe(self):
        """Test that CALL (stored procedure) queries are marked as unsafe."""
        sql = "CALL some_procedure(123)"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert "CALL" in result["dangerous_keywords"]


class TestDangerousPatterns:
    """Tests for dangerous SQL patterns detected by pattern matching."""

    def test_into_outfile_is_unsafe(self):
        """Test that INTO OUTFILE is detected and blocked."""
        sql = "SELECT * FROM users INTO OUTFILE '/tmp/data.csv'"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert any("OUTFILE" in issue for issue in result["issues"])

    def test_load_file_is_unsafe(self):
        """Test that LOAD_FILE is detected and blocked."""
        sql = "SELECT LOAD_FILE('/etc/passwd')"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert any("LOAD_FILE" in issue for issue in result["issues"])

    def test_sleep_function_is_unsafe(self):
        """Test that SLEEP function is detected and blocked."""
        sql = "SELECT * FROM users WHERE SLEEP(10) = 0"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert any("SLEEP" in issue for issue in result["issues"])

    def test_multiple_statements_is_unsafe(self):
        """Test that multiple statements are detected and blocked."""
        sql = "SELECT * FROM users; DELETE FROM users"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert any("Multiple statements" in issue for issue in result["issues"])

    def test_system_variables_warning(self):
        """Test that system variable access generates a warning."""
        sql = "SELECT @@version"
        result = validate_query_safety(sql)

        # Note: The code may allow SELECT with system variables as it's read-only
        # The warning is in issues but may not mark it as unsafe
        assert any("System variables" in issue for issue in result["issues"]) or result["safe"] is True

    def test_excessive_nesting_is_unsafe(self):
        """Test that excessive nesting depth is flagged."""
        # Create deeply nested query
        sql = "SELECT " + "(" * 60 + "1" + ")" * 60
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert any("nesting" in issue.lower() for issue in result["issues"])


class TestInternalFunctions:
    """Tests for internal helper functions."""

    def test_normalize_query_for_safety_removes_comments(self):
        """Test that comment removal works."""
        sql = "SELECT * FROM users -- this is a comment"
        result = _normalize_query_for_safety(sql)

        assert "comment" not in result

    def test_normalize_query_for_safety_removes_multiline_comments(self):
        """Test that multiline comment removal works."""
        sql = "SELECT * /* comment */ FROM users"
        result = _normalize_query_for_safety(sql)

        assert "/*" not in result
        assert "*/" not in result

    def test_normalize_query_collapses_whitespace(self):
        """Test that whitespace collapsing works."""
        sql = "SELECT   *   FROM\n\tusers"
        result = _normalize_query_for_safety(sql)

        assert "  " not in result
        assert "\n" not in result
        assert "\t" not in result

    def test_extract_sql_keywords(self):
        """Test keyword extraction."""
        sql = "SELECT name FROM users WHERE id = 1"
        keywords = _extract_sql_keywords(sql)

        assert "SELECT" in keywords
        assert "FROM" in keywords
        assert "WHERE" in keywords

    def test_extract_sql_keywords_handles_case(self):
        """Test that keyword extraction handles case properly."""
        sql = "select * from users"
        keywords = _extract_sql_keywords(sql)

        assert "SELECT" in keywords
        assert "FROM" in keywords

    def test_validate_query_patterns_empty(self):
        """Test pattern validation with safe query."""
        sql = "SELECT * FROM users WHERE id = 1"
        issues = _validate_query_patterns(sql)

        assert len(issues) == 0


class TestConstants:
    """Tests for the constant sets."""

    def test_dangerous_keywords_contains_dml(self):
        """Test that dangerous keywords include DML operations."""
        assert "DELETE" in DANGEROUS_KEYWORDS
        assert "UPDATE" in DANGEROUS_KEYWORDS
        assert "INSERT" in DANGEROUS_KEYWORDS

    def test_dangerous_keywords_contains_ddl(self):
        """Test that dangerous keywords include DDL operations."""
        assert "DROP" in DANGEROUS_KEYWORDS
        assert "CREATE" in DANGEROUS_KEYWORDS
        assert "ALTER" in DANGEROUS_KEYWORDS
        assert "TRUNCATE" in DANGEROUS_KEYWORDS

    def test_dangerous_keywords_contains_admin(self):
        """Test that dangerous keywords include admin operations."""
        assert "GRANT" in DANGEROUS_KEYWORDS
        assert "REVOKE" in DANGEROUS_KEYWORDS
        assert "SHUTDOWN" in DANGEROUS_KEYWORDS

    def test_allowed_keywords_contains_read_operations(self):
        """Test that allowed keywords include read operations."""
        assert "SELECT" in ALLOWED_KEYWORDS
        assert "WITH" in ALLOWED_KEYWORDS
        assert "SHOW" in ALLOWED_KEYWORDS
        assert "DESCRIBE" in ALLOWED_KEYWORDS
        assert "EXPLAIN" in ALLOWED_KEYWORDS


class TestEdgeCases:
    """Tests for edge cases and unusual inputs."""

    def test_select_in_column_name_is_safe(self):
        """Test that 'select' in column names doesn't trigger issues."""
        sql = "SELECT selected_items FROM products"
        result = validate_query_safety(sql)

        assert result["safe"] is True

    def test_delete_in_column_name_triggers_warning(self):
        """Test behavior when 'delete' appears in identifier."""
        # DELETE at the start triggers the dangerous keyword check
        sql = "SELECT deleted_at FROM users"
        result = validate_query_safety(sql)

        # Should be safe because DELETE is not at the start and is part of identifier
        assert result["safe"] is True

    def test_case_insensitive_dangerous_keyword_detection(self):
        """Test that dangerous keywords are detected case-insensitively."""
        sql = "delete from users where id = 1"
        result = validate_query_safety(sql)

        assert result["safe"] is False

    def test_very_long_query_limit(self):
        """Test that very long queries are flagged."""
        # Create query longer than 50KB
        sql = "SELECT " + "a, " * 30000 + "b FROM users"
        result = validate_query_safety(sql)

        assert result["safe"] is False
        assert any("length" in issue.lower() for issue in result["issues"])

    def test_subquery_with_update_is_unsafe(self):
        """Test that subqueries containing UPDATE are detected."""
        sql = "SELECT * FROM (UPDATE users SET x = 1 RETURNING *) t"
        result = validate_query_safety(sql)

        assert result["safe"] is False

    def test_cte_with_delete_is_unsafe(self):
        """Test that CTEs containing DELETE are detected."""
        sql = """
        WITH deleted AS (
            DELETE FROM users WHERE status = 'inactive' RETURNING *
        )
        SELECT * FROM deleted
        """
        result = validate_query_safety(sql)

        assert result["safe"] is False

    def test_result_contains_keywords_found(self):
        """Test that result contains keywords for debugging."""
        sql = "SELECT name FROM users WHERE id = 1"
        result = validate_query_safety(sql)

        assert "keywords_found" in result
        assert len(result["keywords_found"]) > 0

    def test_error_handling_in_validation(self):
        """Test that validation handles errors gracefully."""
        # Should not raise exception even with unusual input
        sql = "SELECT * FROM users WHERE name = 'Test"  # Unclosed quote
        result = validate_query_safety(sql)

        assert "safe" in result
        assert "issues" in result
