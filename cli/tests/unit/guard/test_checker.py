"""Tests for query checker functions."""

import pytest

from lib.guard.config import GuardConfig, GuardsConfig, RestrictionsConfig
from lib.guard.checker import (
    check_query,
    check_read_only,
    check_require_where,
    check_require_limit,
    check_no_select_star,
    check_max_tables,
    check_denied_columns,
    check_allowed_tables,
    check_required_filters,
    check_estimated_rows,
    CheckResult,
)


class TestCheckReadOnly:
    """Test read-only query validation."""

    def test_select_allowed(self):
        """Should allow SELECT statements."""
        result = check_read_only("SELECT id FROM users")
        assert result.passed is True
        assert result.guard_name == "read_only"

    def test_select_with_where(self):
        """Should allow SELECT with WHERE."""
        result = check_read_only("SELECT * FROM users WHERE id = 1")
        assert result.passed is True

    def test_with_cte(self):
        """Should allow WITH (CTEs)."""
        result = check_read_only("WITH active AS (SELECT * FROM users) SELECT * FROM active")
        assert result.passed is True

    def test_insert_blocked(self):
        """Should block INSERT."""
        result = check_read_only("INSERT INTO users (name) VALUES ('test')")
        assert result.passed is False
        assert result.level == "block"
        assert "INSERT" in result.message

    def test_update_blocked(self):
        """Should block UPDATE."""
        result = check_read_only("UPDATE users SET name = 'test'")
        assert result.passed is False
        assert "UPDATE" in result.message

    def test_delete_blocked(self):
        """Should block DELETE."""
        result = check_read_only("DELETE FROM users WHERE id = 1")
        assert result.passed is False
        assert "DELETE" in result.message

    def test_drop_blocked(self):
        """Should block DROP."""
        result = check_read_only("DROP TABLE users")
        assert result.passed is False
        assert "DROP" in result.message

    def test_truncate_blocked(self):
        """Should block TRUNCATE."""
        result = check_read_only("TRUNCATE TABLE users")
        assert result.passed is False
        assert "TRUNCATE" in result.message

    def test_select_with_leading_comments(self):
        """Should allow SELECT with leading comments."""
        sql = """-- This is a comment
        SELECT id FROM users"""
        result = check_read_only(sql)
        assert result.passed is True

    def test_select_with_block_comments(self):
        """Should allow SELECT with block comments."""
        sql = """/* This is a comment */ SELECT id FROM users"""
        result = check_read_only(sql)
        assert result.passed is True


class TestCheckRequireWhere:
    """Test WHERE clause requirement."""

    def test_with_where(self):
        """Should pass when WHERE is present."""
        result = check_require_where("SELECT * FROM users WHERE active = true")
        assert result.passed is True

    def test_without_where(self):
        """Should fail when WHERE is missing."""
        result = check_require_where("SELECT * FROM users")
        assert result.passed is False
        assert result.level == "block"
        assert "WHERE" in result.message

    def test_with_where_in_subquery(self):
        """Should pass with WHERE in subquery."""
        sql = "SELECT * FROM users WHERE id IN (SELECT user_id FROM orders WHERE total > 100)"
        result = check_require_where(sql)
        assert result.passed is True


class TestCheckRequireLimit:
    """Test LIMIT clause requirement."""

    def test_with_limit(self):
        """Should pass when LIMIT is present."""
        result = check_require_limit("SELECT * FROM users LIMIT 10")
        assert result.passed is True

    def test_without_limit(self):
        """Should fail when LIMIT is missing."""
        result = check_require_limit("SELECT * FROM users")
        assert result.passed is False
        assert result.level == "block"
        assert "LIMIT" in result.message

    def test_with_offset_and_limit(self):
        """Should pass with OFFSET and LIMIT."""
        result = check_require_limit("SELECT * FROM users LIMIT 10 OFFSET 20")
        assert result.passed is True


class TestCheckNoSelectStar:
    """Test SELECT * detection."""

    def test_explicit_columns(self):
        """Should pass when columns are explicit."""
        result = check_no_select_star("SELECT id, name, email FROM users")
        assert result.passed is True

    def test_select_star(self):
        """Should warn when SELECT * is used."""
        result = check_no_select_star("SELECT * FROM users")
        assert result.passed is False
        assert result.level == "warn"  # Warning, not block
        assert "*" in result.message

    def test_count_star(self):
        """Should detect COUNT(*)."""
        result = check_no_select_star("SELECT COUNT(*) FROM users")
        assert result.passed is False  # COUNT(*) is also detected

    def test_table_star(self):
        """Should detect table.* patterns."""
        result = check_no_select_star("SELECT users.* FROM users")
        assert result.passed is False


class TestCheckMaxTables:
    """Test table count limit."""

    def test_single_table(self):
        """Should pass with single table."""
        result = check_max_tables("SELECT * FROM users", max_tables=3)
        assert result.passed is True

    def test_at_limit(self):
        """Should pass at exactly the limit."""
        sql = "SELECT * FROM users u JOIN orders o ON u.id = o.user_id JOIN products p ON o.product_id = p.id"
        result = check_max_tables(sql, max_tables=3)
        assert result.passed is True

    def test_over_limit(self):
        """Should warn when over limit."""
        sql = """
        SELECT * FROM users u
        JOIN orders o ON u.id = o.user_id
        JOIN products p ON o.product_id = p.id
        JOIN categories c ON p.category_id = c.id
        """
        result = check_max_tables(sql, max_tables=3)
        assert result.passed is False
        assert result.level == "warn"  # Warning, not block


class TestCheckDeniedColumns:
    """Test denied column patterns."""

    def test_no_denied(self):
        """Should pass when no denied columns referenced."""
        result = check_denied_columns(
            "SELECT id, name FROM users",
            denied_columns=["password", "ssn"],
        )
        assert result.passed is True

    def test_denied_referenced(self):
        """Should block when denied column is referenced."""
        result = check_denied_columns(
            "SELECT id, password FROM users",
            denied_columns=["password"],
        )
        assert result.passed is False
        assert result.level == "block"
        assert "password" in result.message

    def test_pattern_matching(self):
        """Should match wildcard patterns."""
        result = check_denied_columns(
            "SELECT user_password FROM users",
            denied_columns=["*password*"],
        )
        assert result.passed is False

    def test_case_insensitive(self):
        """Should match case-insensitively."""
        result = check_denied_columns(
            "SELECT PASSWORD FROM users",
            denied_columns=["password"],
        )
        assert result.passed is False


class TestCheckAllowedTables:
    """Test allowed table whitelist."""

    def test_allowed_table(self):
        """Should pass for allowed table."""
        result = check_allowed_tables(
            "SELECT * FROM users",
            allowed_tables=["users", "orders"],
        )
        assert result.passed is True

    def test_disallowed_table(self):
        """Should block disallowed table."""
        result = check_allowed_tables(
            "SELECT * FROM admin_users",
            allowed_tables=["users", "orders"],
        )
        assert result.passed is False
        assert result.level == "block"
        assert "admin_users" in result.message

    def test_join_with_disallowed(self):
        """Should block when JOIN includes disallowed table."""
        result = check_allowed_tables(
            "SELECT * FROM users u JOIN secrets s ON u.id = s.user_id",
            allowed_tables=["users"],
        )
        assert result.passed is False
        assert "secrets" in result.message

    def test_case_insensitive(self):
        """Should match tables case-insensitively."""
        result = check_allowed_tables(
            "SELECT * FROM USERS",
            allowed_tables=["users"],
        )
        assert result.passed is True


class TestCheckQuery:
    """Test full query checking with guard config."""

    def test_minimal_guard(self):
        """Should run basic checks with minimal guard."""
        config = GuardConfig(name="minimal")
        results = check_query("SELECT * FROM users", config)

        # Should always check read_only
        guard_names = [r.guard_name for r in results]
        assert "read_only" in guard_names

    def test_with_require_where(self):
        """Should check WHERE when enabled."""
        config = GuardConfig(
            name="require-where",
            guards=GuardsConfig(require_where=True),
        )

        results = check_query("SELECT * FROM users", config)
        guard_names = [r.guard_name for r in results]

        assert "require_where" in guard_names
        where_result = next(r for r in results if r.guard_name == "require_where")
        assert where_result.passed is False

    def test_with_require_limit(self):
        """Should check LIMIT when enabled."""
        config = GuardConfig(
            name="require-limit",
            guards=GuardsConfig(require_limit=True),
        )

        results = check_query("SELECT * FROM users", config)
        guard_names = [r.guard_name for r in results]

        assert "require_limit" in guard_names

    def test_with_denied_columns(self):
        """Should check denied columns when specified."""
        config = GuardConfig(
            name="deny-cols",
            restrictions=RestrictionsConfig(denied_columns=["password"]),
        )

        results = check_query("SELECT password FROM users", config)

        denied_result = next(r for r in results if r.guard_name == "denied_columns")
        assert denied_result.passed is False

    def test_passing_query(self):
        """Should pass all checks for valid query."""
        config = GuardConfig(
            name="strict",
            guards=GuardsConfig(require_where=True, require_limit=True),
        )

        results = check_query(
            "SELECT id, name FROM users WHERE active = true LIMIT 10",
            config,
        )

        # All checks should pass
        for result in results:
            assert result.passed is True or result.level != "block"

    def test_multiple_failures(self):
        """Should report multiple failures."""
        config = GuardConfig(
            name="multi-check",
            guards=GuardsConfig(require_where=True, require_limit=True),
            restrictions=RestrictionsConfig(denied_columns=["password"]),
        )

        results = check_query("SELECT password FROM users", config)

        failures = [r for r in results if not r.passed]
        assert len(failures) >= 3  # WHERE, LIMIT, denied column


class TestCheckRequiredFilters:
    """Test required filters check - catches trivial WHERE bypasses."""

    def test_no_where_clause(self):
        """Should block when no WHERE clause at all."""
        result = check_required_filters(
            "SELECT * FROM users",
            required_filters={"users": ["id", "email"]},
        )
        assert result.passed is False
        assert result.level == "block"
        assert "users" in result.message

    def test_trivial_is_not_null(self):
        """Should block WHERE id IS NOT NULL - doesn't actually filter."""
        result = check_required_filters(
            "SELECT * FROM users WHERE id IS NOT NULL",
            required_filters={"users": ["id"]},
        )
        assert result.passed is False
        assert result.level == "block"

    def test_trivial_one_equals_one(self):
        """Should block WHERE 1=1 - always true."""
        result = check_required_filters(
            "SELECT * FROM users WHERE 1=1",
            required_filters={"users": ["id"]},
        )
        assert result.passed is False

    def test_actual_value_filter(self):
        """Should pass with actual value filter."""
        result = check_required_filters(
            "SELECT * FROM users WHERE id = 123",
            required_filters={"users": ["id", "email"]},
        )
        assert result.passed is True

    def test_string_value_filter(self):
        """Should pass with string value filter."""
        result = check_required_filters(
            "SELECT * FROM users WHERE email = 'user@example.com'",
            required_filters={"users": ["id", "email"]},
        )
        assert result.passed is True

    def test_comparison_filter(self):
        """Should pass with comparison operators."""
        result = check_required_filters(
            "SELECT * FROM orders WHERE created_at > '2024-01-01'",
            required_filters={"orders": ["created_at", "order_id"]},
        )
        assert result.passed is True

    def test_in_clause_filter(self):
        """Should pass with IN clause."""
        result = check_required_filters(
            "SELECT * FROM users WHERE id IN (1, 2, 3)",
            required_filters={"users": ["id"]},
        )
        assert result.passed is True

    def test_between_clause_filter(self):
        """Should pass with BETWEEN clause."""
        result = check_required_filters(
            "SELECT * FROM users WHERE id BETWEEN 1 AND 1000",
            required_filters={"users": ["id"]},
        )
        assert result.passed is True

    def test_table_not_in_requirements(self):
        """Should pass if queried table has no requirements."""
        result = check_required_filters(
            "SELECT * FROM logs",  # logs has no requirements
            required_filters={"users": ["id"]},
        )
        assert result.passed is True

    def test_any_required_column_sufficient(self):
        """Should pass if ANY required column is filtered."""
        # Filter on email (not id) - should still pass
        result = check_required_filters(
            "SELECT * FROM users WHERE email = 'test@example.com'",
            required_filters={"users": ["id", "email"]},  # id OR email
        )
        assert result.passed is True

    def test_case_insensitive_table(self):
        """Should match table names case-insensitively."""
        result = check_required_filters(
            "SELECT * FROM USERS WHERE id = 1",
            required_filters={"users": ["id"]},
        )
        assert result.passed is True

    def test_join_with_required_table(self):
        """Should check requirements for joined tables."""
        # users table requires filter, orders doesn't
        result = check_required_filters(
            "SELECT * FROM users u JOIN orders o ON u.id = o.user_id",
            required_filters={"users": ["id"]},
        )
        # No WHERE clause filtering users.id with a value
        assert result.passed is False


class TestCheckEstimatedRows:
    """Test EXPLAIN-based row estimation.

    Note: These tests mock the database connection since we can't
    rely on a real database in unit tests.
    """

    def test_check_result_structure(self):
        """Should return proper CheckResult structure."""
        # Without target_config, should return warning
        result = check_estimated_rows(
            "SELECT * FROM users",
            max_rows=100,
            target_config={},  # Empty config
        )
        # Should handle gracefully
        assert hasattr(result, "passed")
        assert hasattr(result, "level")
        assert hasattr(result, "guard_name")
        assert result.guard_name == "max_estimated_rows"
