"""
Tests for SQLGlot-based SQL normalization.

Tests the sql_normalizer module which provides robust AST-based
SQL parameterization using SQLGlot.
"""

import pytest
from lib.query_registry.sql_normalizer import (
    normalize_and_extract,
    reconstruct_sql,
    get_placeholder_names,
)


class TestNormalizeAndExtract:
    """Tests for normalize_and_extract function."""

    def test_empty_query(self):
        """Empty query returns empty result."""
        normalized, params = normalize_and_extract("")
        assert normalized == ""
        assert params == {}

    def test_whitespace_only(self):
        """Whitespace-only query returns as-is."""
        normalized, params = normalize_and_extract("   ")
        assert normalized == "   "
        assert params == {}

    def test_basic_string_literal(self):
        """String literals are extracted with type info."""
        sql = "SELECT * FROM users WHERE name = 'John'"
        normalized, params = normalize_and_extract(sql)

        assert "'John'" not in normalized
        assert ":p" in normalized
        assert len(params) == 1
        # Find the param with value 'John'
        john_param = [p for p in params.values() if p['value'] == 'John'][0]
        assert john_param['type'] == 'string'

    def test_basic_numeric_literal(self):
        """Numeric literals are extracted with type info."""
        sql = "SELECT * FROM users WHERE id = 123"
        normalized, params = normalize_and_extract(sql)

        assert "123" not in normalized
        assert ":p" in normalized
        assert len(params) == 1
        # Find the param with value '123'
        num_param = [p for p in params.values() if p['value'] == '123'][0]
        assert num_param['type'] == 'number'

    def test_multiple_literals(self):
        """Multiple literals are extracted in order."""
        sql = "SELECT * FROM users WHERE status = 'active' AND age > 25 LIMIT 10"
        normalized, params = normalize_and_extract(sql)

        assert "'active'" not in normalized
        assert "25" not in normalized
        assert "10" not in normalized
        assert len(params) == 3

        # Check types
        types = {p['type'] for p in params.values()}
        assert 'string' in types
        assert 'number' in types

    def test_comment_with_literal_not_extracted(self):
        """Literals in comments should NOT be extracted (the key bug fix)."""
        sql = """-- Get 'active' customers
SELECT * FROM customers WHERE status = 'active' LIMIT 10"""
        normalized, params = normalize_and_extract(sql)

        # Should only extract 2 params (status value and LIMIT), not the comment
        assert len(params) == 2

        # The comment should be preserved (possibly converted to /* */ format)
        assert 'active' in normalized  # Comment content preserved

    def test_multiline_comment_with_literal(self):
        """Literals in multiline comments should NOT be extracted."""
        sql = """/* Filter for status = 'pending' orders */
SELECT * FROM orders WHERE status = 'shipped'"""
        normalized, params = normalize_and_extract(sql)

        # Should only extract 1 param (the actual WHERE value)
        assert len(params) == 1
        param = list(params.values())[0]
        assert param['value'] == 'shipped'

    def test_nested_subquery(self):
        """Literals in subqueries are extracted."""
        sql = """SELECT * FROM users
WHERE id IN (SELECT user_id FROM orders WHERE total > 100)
AND status = 'active'"""
        normalized, params = normalize_and_extract(sql)

        # Should extract both 100 and 'active'
        assert len(params) == 2
        values = {p['value'] for p in params.values()}
        assert '100' in values
        assert 'active' in values

    def test_decimal_numbers(self):
        """Decimal numbers are extracted correctly."""
        sql = "SELECT * FROM products WHERE price = 19.99"
        normalized, params = normalize_and_extract(sql)

        assert "19.99" not in normalized
        assert len(params) == 1

    def test_negative_numbers(self):
        """Negative numbers in expressions."""
        sql = "SELECT * FROM accounts WHERE balance > -100"
        normalized, params = normalize_and_extract(sql)

        # Note: negative sign may be part of expression, not literal
        assert len(params) >= 1

    def test_placeholder_naming(self):
        """Placeholders use :p1, :p2 naming convention."""
        sql = "SELECT * FROM t WHERE a = 1 AND b = 2 AND c = 3"
        normalized, params = normalize_and_extract(sql)

        # All params should have pN naming
        for name in params.keys():
            assert name.startswith('p')
            assert name[1:].isdigit()

    def test_preserves_query_structure(self):
        """SQL structure is preserved after normalization."""
        sql = "SELECT name, email FROM users WHERE status = 'active' ORDER BY name"
        normalized, params = normalize_and_extract(sql)

        assert "SELECT" in normalized.upper()
        assert "FROM" in normalized.upper()
        assert "WHERE" in normalized.upper()
        assert "ORDER BY" in normalized.upper()


class TestReconstructSql:
    """Tests for reconstruct_sql function."""

    def test_basic_reconstruction(self):
        """Basic reconstruction replaces placeholders with values."""
        normalized = "SELECT * FROM users WHERE id = :p1"
        params = {'p1': {'value': 123, 'type': 'number'}}

        result = reconstruct_sql(normalized, params)
        assert "123" in result
        assert ":p1" not in result

    def test_string_reconstruction(self):
        """String values are properly quoted."""
        normalized = "SELECT * FROM users WHERE name = :p1"
        params = {'p1': {'value': 'John', 'type': 'string'}}

        result = reconstruct_sql(normalized, params)
        assert "'John'" in result

    def test_multiple_params(self):
        """Multiple parameters are reconstructed."""
        normalized = "SELECT * FROM users WHERE status = :p1 AND age > :p2 LIMIT :p3"
        params = {
            'p1': {'value': 'active', 'type': 'string'},
            'p2': {'value': 25, 'type': 'number'},
            'p3': {'value': 10, 'type': 'number'}
        }

        result = reconstruct_sql(normalized, params)
        assert "'active'" in result
        assert "25" in result
        assert "10" in result

    def test_empty_params(self):
        """Empty params returns SQL as-is."""
        sql = "SELECT * FROM users"
        result = reconstruct_sql(sql, {})
        assert result == sql

    def test_roundtrip(self):
        """Normalize then reconstruct produces equivalent SQL."""
        original = "SELECT * FROM users WHERE status = 'active' AND age > 25 LIMIT 10"
        normalized, params = normalize_and_extract(original)
        reconstructed = reconstruct_sql(normalized, params)

        # Should be semantically equivalent (may differ in formatting)
        assert "'active'" in reconstructed
        assert "25" in reconstructed
        assert "10" in reconstructed

    def test_missing_param_not_replaced(self):
        """Missing parameters are not replaced."""
        normalized = "SELECT * FROM users WHERE id = :p1 AND name = :p2"
        params = {'p1': {'value': 123, 'type': 'number'}}

        result = reconstruct_sql(normalized, params)
        assert "123" in result
        assert ":p2" in result  # Not replaced


class TestGetPlaceholderNames:
    """Tests for get_placeholder_names function."""

    def test_single_placeholder(self):
        """Single placeholder is detected."""
        sql = "SELECT * FROM users WHERE id = :p1"
        names = get_placeholder_names(sql)
        assert names == {'p1'}

    def test_multiple_placeholders(self):
        """Multiple placeholders are detected."""
        sql = "SELECT * FROM users WHERE status = :p1 AND age > :p2 LIMIT :p3"
        names = get_placeholder_names(sql)
        assert names == {'p1', 'p2', 'p3'}

    def test_no_placeholders(self):
        """No placeholders returns empty set."""
        sql = "SELECT * FROM users"
        names = get_placeholder_names(sql)
        assert names == set()

    def test_empty_query(self):
        """Empty query returns empty set."""
        names = get_placeholder_names("")
        assert names == set()

    def test_duplicate_placeholder(self):
        """Same placeholder used twice is counted once."""
        sql = "SELECT * FROM users WHERE id = :p1 OR parent_id = :p1"
        names = get_placeholder_names(sql)
        assert names == {'p1'}


class TestEdgeCases:
    """Edge case tests."""

    def test_escaped_quotes_in_string(self):
        """Escaped quotes in strings are handled."""
        sql = "SELECT * FROM users WHERE name = 'O''Brien'"
        normalized, params = normalize_and_extract(sql)

        assert len(params) == 1
        # Value should include the escaped quote
        param = list(params.values())[0]
        assert "Brien" in param['value']

    def test_unicode_in_strings(self):
        """Unicode characters in strings are preserved."""
        sql = "SELECT * FROM users WHERE name = 'José'"
        normalized, params = normalize_and_extract(sql)

        assert len(params) == 1
        param = list(params.values())[0]
        assert param['value'] == 'José'

    def test_very_long_string(self):
        """Very long strings are handled."""
        long_value = 'x' * 1000
        sql = f"SELECT * FROM logs WHERE message = '{long_value}'"
        normalized, params = normalize_and_extract(sql)

        assert len(params) == 1
        param = list(params.values())[0]
        assert param['value'] == long_value

    def test_null_handling(self):
        """NULL is not treated as a literal."""
        sql = "SELECT * FROM users WHERE deleted_at IS NULL"
        normalized, params = normalize_and_extract(sql)

        # NULL should not be extracted as a parameter
        assert "NULL" in normalized.upper()

    def test_boolean_values(self):
        """Boolean values in SQL."""
        sql = "SELECT * FROM users WHERE active = true"
        normalized, params = normalize_and_extract(sql)

        # Behavior depends on SQLGlot - just ensure no crash
        assert "SELECT" in normalized.upper()

    def test_date_literal(self):
        """Date literals are extracted."""
        sql = "SELECT * FROM orders WHERE created_at > '2024-01-01'"
        normalized, params = normalize_and_extract(sql)

        assert len(params) == 1
        param = list(params.values())[0]
        assert param['value'] == '2024-01-01'
        assert param['type'] == 'string'

    def test_complex_expression(self):
        """Complex expressions with multiple literals."""
        sql = """
        SELECT u.name, COUNT(*) as order_count
        FROM users u
        JOIN orders o ON u.id = o.user_id
        WHERE o.status = 'completed'
          AND o.total > 100
          AND o.created_at > '2024-01-01'
        GROUP BY u.name
        HAVING COUNT(*) > 5
        ORDER BY order_count DESC
        LIMIT 10
        """
        normalized, params = normalize_and_extract(sql)

        # Should extract: 'completed', 100, '2024-01-01', 5, 10
        assert len(params) == 5


class TestFallbackBehavior:
    """Tests for fallback behavior when SQLGlot fails."""

    def test_invalid_sql_uses_fallback(self):
        """Invalid SQL falls back to regex normalization."""
        # This is intentionally malformed SQL
        sql = "SELECTT * FROMM users WHERE"
        normalized, params = normalize_and_extract(sql)

        # Should not crash, should return something
        assert normalized is not None

    def test_dialect_hint(self):
        """Dialect hint is used for parsing."""
        sql = "SELECT * FROM users WHERE id = 1"
        # Test with explicit dialect
        normalized, params = normalize_and_extract(sql, dialect='postgres')
        assert len(params) == 1
