"""
Unit tests for lib/agent/runtime.py safety functions.

Tests SQL validation for read-only enforcement, LIMIT injection,
column denial, and table whitelisting.
"""

import pytest

from lib.agent.runtime import (
    validate_read_only,
    inject_limit,
    validate_columns,
    validate_tables,
    AgentResponse,
)


class TestValidateReadOnly:
    """Tests for read-only SQL validation."""

    def test_select_is_allowed(self):
        """Test SELECT statements are allowed."""
        ok, msg = validate_read_only("SELECT * FROM users")
        assert ok is True
        assert msg == ""

    def test_select_with_where(self):
        """Test SELECT with WHERE clause."""
        ok, msg = validate_read_only("SELECT id, name FROM users WHERE active = true")
        assert ok is True

    def test_select_with_join(self):
        """Test SELECT with JOIN."""
        sql = """
        SELECT u.name, o.total
        FROM users u
        JOIN orders o ON u.id = o.user_id
        WHERE o.created_at > '2025-01-01'
        """
        ok, msg = validate_read_only(sql)
        assert ok is True

    def test_select_with_subquery(self):
        """Test SELECT with subquery."""
        sql = """
        SELECT * FROM users
        WHERE id IN (SELECT user_id FROM orders WHERE total > 100)
        """
        ok, msg = validate_read_only(sql)
        assert ok is True

    def test_with_cte_is_allowed(self):
        """Test WITH (CTE) statements are allowed."""
        sql = """
        WITH active_users AS (
            SELECT * FROM users WHERE active = true
        )
        SELECT * FROM active_users
        """
        ok, msg = validate_read_only(sql)
        assert ok is True

    def test_insert_is_rejected(self):
        """Test INSERT statements are rejected."""
        ok, msg = validate_read_only("INSERT INTO users (name) VALUES ('test')")
        assert ok is False
        assert "INSERT" in msg.upper()

    def test_update_is_rejected(self):
        """Test UPDATE statements are rejected."""
        ok, msg = validate_read_only("UPDATE users SET name = 'test' WHERE id = 1")
        assert ok is False
        assert "UPDATE" in msg.upper()

    def test_delete_is_rejected(self):
        """Test DELETE statements are rejected."""
        ok, msg = validate_read_only("DELETE FROM users WHERE id = 1")
        assert ok is False
        assert "DELETE" in msg.upper()

    def test_drop_is_rejected(self):
        """Test DROP statements are rejected."""
        ok, msg = validate_read_only("DROP TABLE users")
        assert ok is False
        assert "DROP" in msg.upper()

    def test_truncate_is_rejected(self):
        """Test TRUNCATE statements are rejected."""
        ok, msg = validate_read_only("TRUNCATE TABLE users")
        assert ok is False

    def test_create_is_rejected(self):
        """Test CREATE statements are rejected."""
        ok, msg = validate_read_only("CREATE TABLE test (id INT)")
        assert ok is False
        assert "CREATE" in msg.upper()

    def test_alter_is_rejected(self):
        """Test ALTER statements are rejected."""
        ok, msg = validate_read_only("ALTER TABLE users ADD COLUMN email VARCHAR(255)")
        assert ok is False
        assert "ALTER" in msg.upper()


class TestInjectLimit:
    """Tests for LIMIT injection."""

    def test_adds_limit_to_simple_query(self):
        """Test LIMIT is added to query without one."""
        sql = "SELECT * FROM users"
        result = inject_limit(sql, 100)

        assert "LIMIT" in result.upper()
        assert "100" in result

    def test_preserves_existing_limit(self):
        """Test existing LIMIT is preserved."""
        sql = "SELECT * FROM users LIMIT 50"
        result = inject_limit(sql, 100)

        # Should keep existing LIMIT 50, not add 100
        assert "50" in result

    def test_handles_complex_query(self):
        """Test LIMIT injection on complex query."""
        sql = """
        SELECT u.name, COUNT(o.id) as order_count
        FROM users u
        LEFT JOIN orders o ON u.id = o.user_id
        GROUP BY u.name
        ORDER BY order_count DESC
        """
        result = inject_limit(sql, 1000)

        assert "LIMIT" in result.upper()

    def test_returns_original_on_parse_error(self):
        """Test returns original SQL if parsing fails."""
        sql = "INVALID SQL SYNTAX HERE"
        result = inject_limit(sql, 100)

        # Should return original on parse failure
        assert result == sql


class TestValidateColumns:
    """Tests for column denial validation."""

    def test_no_denied_columns_passes(self):
        """Test query passes with no denied columns."""
        ok, msg = validate_columns("SELECT * FROM users", None)
        assert ok is True

    def test_empty_denied_list_passes(self):
        """Test query passes with empty denied list."""
        ok, msg = validate_columns("SELECT * FROM users", [])
        assert ok is True

    def test_exact_column_match_rejected(self):
        """Test exact column reference is rejected."""
        sql = "SELECT ssn FROM users"
        ok, msg = validate_columns(sql, ["ssn"])

        assert ok is False
        assert "ssn" in msg.lower()

    def test_table_qualified_column_rejected(self):
        """Test table.column reference is rejected."""
        sql = "SELECT users.ssn FROM users"
        ok, msg = validate_columns(sql, ["users.ssn"])

        assert ok is False
        assert "ssn" in msg.lower()

    def test_wildcard_pattern_rejected(self):
        """Test wildcard pattern denial works."""
        sql = "SELECT password_hash FROM users"
        ok, msg = validate_columns(sql, ["password*"])

        assert ok is False
        assert "password" in msg.lower()

    def test_allowed_columns_pass(self):
        """Test non-denied columns pass."""
        sql = "SELECT id, name, email FROM users"
        ok, msg = validate_columns(sql, ["ssn", "password"])

        assert ok is True

    def test_case_insensitive_match(self):
        """Test column denial is case-insensitive."""
        sql = "SELECT SSN FROM users"
        ok, msg = validate_columns(sql, ["ssn"])

        assert ok is False

    def test_multiple_denied_columns(self):
        """Test multiple denied columns."""
        sql = "SELECT id, ssn, password FROM users"
        denied = ["ssn", "password"]
        ok, msg = validate_columns(sql, denied)

        assert ok is False


class TestValidateTables:
    """Tests for table whitelist validation."""

    def test_no_whitelist_passes(self):
        """Test query passes with no whitelist."""
        ok, msg = validate_tables("SELECT * FROM users", None)
        assert ok is True

    def test_empty_whitelist_passes(self):
        """Test query passes with empty whitelist (means all allowed)."""
        ok, msg = validate_tables("SELECT * FROM users", [])
        assert ok is True

    def test_allowed_table_passes(self):
        """Test whitelisted table passes."""
        sql = "SELECT * FROM users"
        ok, msg = validate_tables(sql, ["users"])

        assert ok is True

    def test_not_allowed_table_rejected(self):
        """Test non-whitelisted table is rejected."""
        sql = "SELECT * FROM sensitive_data"
        ok, msg = validate_tables(sql, ["users", "orders"])

        assert ok is False
        assert "sensitive_data" in msg

    def test_join_with_allowed_tables(self):
        """Test JOIN with all allowed tables passes."""
        sql = "SELECT * FROM users u JOIN orders o ON u.id = o.user_id"
        ok, msg = validate_tables(sql, ["users", "orders"])

        assert ok is True

    def test_join_with_disallowed_table(self):
        """Test JOIN with one disallowed table is rejected."""
        sql = "SELECT * FROM users u JOIN admin_logs a ON u.id = a.user_id"
        ok, msg = validate_tables(sql, ["users", "orders"])

        assert ok is False
        assert "admin_logs" in msg

    def test_case_insensitive_match(self):
        """Test table whitelist is case-insensitive."""
        sql = "SELECT * FROM USERS"
        ok, msg = validate_tables(sql, ["users"])

        assert ok is True


class TestAgentResponse:
    """Tests for AgentResponse dataclass."""

    def test_success_response(self):
        """Test successful response creation."""
        response = AgentResponse(
            success=True,
            sql="SELECT * FROM users",
            columns=["id", "name"],
            rows=[[1, "Alice"], [2, "Bob"]],
            row_count=2,
            execution_time_ms=50.5,
        )

        assert response.success is True
        assert response.sql == "SELECT * FROM users"
        assert response.row_count == 2

    def test_error_response(self):
        """Test error response creation."""
        response = AgentResponse(
            success=False,
            error="Access denied",
        )

        assert response.success is False
        assert response.error == "Access denied"

    def test_to_dict_success(self):
        """Test successful response serialization."""
        response = AgentResponse(
            success=True,
            sql="SELECT 1",
            columns=["col"],
            rows=[[1]],
            row_count=1,
        )
        result = response.to_dict()

        assert result["success"] is True
        assert result["sql"] == "SELECT 1"
        assert result["columns"] == ["col"]
        assert result["rows"] == [[1]]
        assert result["row_count"] == 1

    def test_to_dict_error(self):
        """Test error response serialization."""
        response = AgentResponse(
            success=False,
            error="Something went wrong",
        )
        result = response.to_dict()

        assert result["success"] is False
        assert result["error"] == "Something went wrong"
        assert "sql" not in result
        assert "rows" not in result

    def test_to_dict_omits_empty_fields(self):
        """Test serialization omits empty optional fields."""
        response = AgentResponse(success=True)
        result = response.to_dict()

        assert "sql" not in result
        assert "columns" not in result
        assert "rows" not in result
        assert "error" not in result
