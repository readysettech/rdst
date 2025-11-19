"""
Unit tests for query_parameterization.py

Tests the dual parameterization strategy:
1. Registry normalization - for consistent hashing
2. LLM parameterization - for PII protection
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
query_parameterization = _import_module_directly("query_parameterization", _lib_path / "functions" / "query_parameterization.py")

normalize_for_registry = query_parameterization.normalize_for_registry
parameterize_for_llm = query_parameterization.parameterize_for_llm
_normalize_sql_for_registry = query_parameterization._normalize_sql_for_registry
_parameterize_for_llm_safety = query_parameterization._parameterize_for_llm_safety
_detect_parameter_types = query_parameterization._detect_parameter_types
_calculate_sensitivity_score = query_parameterization._calculate_sensitivity_score


class TestNormalizeForRegistry:
    """Tests for normalize_for_registry function."""

    def test_basic_select_query(self):
        """Test normalization of a basic SELECT query."""
        sql = "SELECT * FROM users WHERE id = 123"
        result = normalize_for_registry(sql)

        assert result["original_sql"] == sql
        assert "normalized_sql" in result
        assert "hash" in result
        assert len(result["hash"]) == 12  # MD5 truncated to 12 chars
        assert result["normalization_type"] == "registry"

    def test_string_literal_normalization(self):
        """Test that string literals are normalized to '?'."""
        sql = "SELECT * FROM users WHERE name = 'John'"
        result = normalize_for_registry(sql)

        assert "'?'" in result["normalized_sql"]
        assert "'John'" not in result["normalized_sql"]
        assert "string_literals" in result["parameters_detected"]

    def test_numeric_literal_normalization(self):
        """Test that numeric literals are normalized to ?."""
        sql = "SELECT * FROM orders WHERE total > 100.50 AND quantity = 5"
        result = normalize_for_registry(sql)

        # Numerics become ?
        assert "100.50" not in result["normalized_sql"]
        assert "numeric_literals" in result["parameters_detected"]

    def test_date_literal_normalization(self):
        """Test that date literals are normalized."""
        sql = "SELECT * FROM events WHERE created_at > '2024-01-15'"
        result = normalize_for_registry(sql)

        assert "2024-01-15" not in result["normalized_sql"]
        assert "date_literals" in result["parameters_detected"]

    def test_keyword_case_normalization(self):
        """Test that SQL keywords are normalized to uppercase."""
        sql = "select * from users where id = 1"
        result = normalize_for_registry(sql)

        assert "SELECT" in result["normalized_sql"]
        assert "FROM" in result["normalized_sql"]
        assert "WHERE" in result["normalized_sql"]

    def test_whitespace_normalization(self):
        """Test that multiple whitespace is collapsed."""
        sql = "SELECT   *   FROM    users\n\tWHERE   id = 1"
        result = normalize_for_registry(sql)

        # Should have single spaces
        assert "  " not in result["normalized_sql"]
        assert "\n" not in result["normalized_sql"]
        assert "\t" not in result["normalized_sql"]

    def test_comment_removal(self):
        """Test that SQL comments are removed."""
        sql = "SELECT * FROM users -- this is a comment\nWHERE id = 1"
        result = normalize_for_registry(sql)

        assert "comment" not in result["normalized_sql"]

    def test_multiline_comment_removal(self):
        """Test that multiline comments are removed."""
        sql = "SELECT * /* get all columns */ FROM users WHERE id = 1"
        result = normalize_for_registry(sql)

        assert "get all columns" not in result["normalized_sql"]

    def test_trailing_semicolon_removal(self):
        """Test that trailing semicolons are removed."""
        sql = "SELECT * FROM users WHERE id = 1;"
        result = normalize_for_registry(sql)

        assert not result["normalized_sql"].endswith(";")

    def test_consistent_hashing(self):
        """Test that logically equivalent queries produce same hash."""
        sql1 = "SELECT * FROM users WHERE id = 123"
        sql2 = "SELECT * FROM users WHERE id = 456"

        result1 = normalize_for_registry(sql1)
        result2 = normalize_for_registry(sql2)

        # Same structure = same hash
        assert result1["hash"] == result2["hash"]

    def test_different_structures_different_hash(self):
        """Test that different query structures produce different hashes."""
        sql1 = "SELECT * FROM users WHERE id = 123"
        sql2 = "SELECT * FROM orders WHERE id = 123"

        result1 = normalize_for_registry(sql1)
        result2 = normalize_for_registry(sql2)

        assert result1["hash"] != result2["hash"]

    def test_error_handling(self):
        """Test that errors are handled gracefully."""
        # Even with unusual input, should not raise exception
        sql = ""
        result = normalize_for_registry(sql)

        assert "normalized_sql" in result
        assert "hash" in result


class TestParameterizeForLLM:
    """Tests for parameterize_for_llm function."""

    def test_basic_parameterization(self):
        """Test basic LLM parameterization."""
        sql = "SELECT * FROM users WHERE id = 123"
        result = parameterize_for_llm(sql)

        assert result["original_sql"] == sql
        assert "parameterized_sql" in result
        assert "sensitivity_score" in result
        assert result["parameterization_type"] == "llm_safe"

    def test_string_literal_parameterization(self):
        """Test that string literals are replaced with <STRING_VALUE>."""
        sql = "SELECT * FROM users WHERE name = 'John Doe'"
        result = parameterize_for_llm(sql)

        assert "'John Doe'" not in result["parameterized_sql"]
        assert "<STRING_VALUE>" in result["parameterized_sql"]

    def test_numeric_parameterization(self):
        """Test that numeric values are replaced with <NUMBER>."""
        sql = "SELECT * FROM orders WHERE total > 1000"
        result = parameterize_for_llm(sql)

        assert "1000" not in result["parameterized_sql"]
        assert "<NUMBER>" in result["parameterized_sql"]

    def test_email_parameterization(self):
        """Test that email addresses are detected and parameterized."""
        sql = "SELECT * FROM users WHERE email = 'john@example.com'"
        result = parameterize_for_llm(sql)

        # Email should be in a string literal, which becomes <STRING_VALUE>
        assert "john@example.com" not in result["parameterized_sql"]

    def test_sensitivity_score_calculation(self):
        """Test that sensitivity score is calculated appropriately."""
        # Query with potentially sensitive data should have higher score
        sql_sensitive = "SELECT * FROM users WHERE email = 'john@example.com' AND phone = '555-123-4567'"
        sql_simple = "SELECT * FROM logs WHERE id = 1"

        result_sensitive = parameterize_for_llm(sql_sensitive)
        result_simple = parameterize_for_llm(sql_simple)

        # Sensitive query should have higher score
        assert result_sensitive["sensitivity_score"] >= result_simple["sensitivity_score"]

    def test_safe_for_llm_flag(self):
        """Test that safe_for_llm flag is set based on sensitivity."""
        sql = "SELECT * FROM logs WHERE id = 1"
        result = parameterize_for_llm(sql)

        # Low sensitivity should be safe
        assert result["safe_for_llm"] is True
        assert result["sensitivity_score"] <= 7

    def test_comment_removal_for_llm(self):
        """Test that comments are removed for LLM safety."""
        sql = "SELECT * FROM users -- Contains SSN: 123-45-6789"
        result = parameterize_for_llm(sql)

        assert "SSN" not in result["parameterized_sql"]
        assert "123-45-6789" not in result["parameterized_sql"]

    def test_replacements_tracking(self):
        """Test that replacements are tracked for debugging."""
        sql = "SELECT * FROM users WHERE name = 'John' AND id = 123"
        result = parameterize_for_llm(sql)

        assert "replacements_made" in result
        assert len(result["replacements_made"]) > 0

    def test_error_handling(self):
        """Test that errors are handled gracefully."""
        sql = ""
        result = parameterize_for_llm(sql)

        assert "parameterized_sql" in result
        assert "sensitivity_score" in result


class TestInternalFunctions:
    """Tests for internal helper functions."""

    def test_normalize_sql_for_registry_empty_input(self):
        """Test normalization with empty input."""
        result = _normalize_sql_for_registry("")
        assert result == ""

    def test_normalize_sql_for_registry_complex_query(self):
        """Test normalization of a complex query."""
        sql = """
        SELECT u.name, COUNT(o.id) as order_count
        FROM users u
        JOIN orders o ON u.id = o.user_id
        WHERE o.status = 'completed'
        GROUP BY u.name
        HAVING COUNT(o.id) > 5
        ORDER BY order_count DESC
        """
        result = _normalize_sql_for_registry(sql)

        assert "SELECT" in result
        assert "GROUP BY" in result
        assert "HAVING" in result
        assert "ORDER BY" in result

    def test_parameterize_for_llm_safety_returns_tuple(self):
        """Test that _parameterize_for_llm_safety returns correct tuple."""
        sql = "SELECT * FROM users WHERE id = 123"
        parameterized, replacements = _parameterize_for_llm_safety(sql)

        assert isinstance(parameterized, str)
        assert isinstance(replacements, list)

    def test_detect_parameter_types(self):
        """Test parameter type detection."""
        original = "SELECT * FROM users WHERE name = 'John' AND id = 123"
        normalized = "SELECT * FROM users WHERE name = '?' AND id = ?"

        types = _detect_parameter_types(original, normalized)

        assert "string_literals" in types

    def test_calculate_sensitivity_score_empty_replacements(self):
        """Test sensitivity calculation with no replacements."""
        score = _calculate_sensitivity_score([])
        assert score == 1

    def test_calculate_sensitivity_score_email(self):
        """Test that email replacements increase sensitivity."""
        replacements = [{"type": "email_addresses", "count": 1}]
        score = _calculate_sensitivity_score(replacements)

        assert score > 1

    def test_calculate_sensitivity_score_multiple_types(self):
        """Test sensitivity with multiple replacement types."""
        replacements = [
            {"type": "string_literals", "count": 3},
            {"type": "numeric_literals", "count": 5},
        ]
        score = _calculate_sensitivity_score(replacements)

        assert 1 < score <= 10

    def test_calculate_sensitivity_score_capped_at_10(self):
        """Test that sensitivity score is capped at 10."""
        replacements = [
            {"type": "email_addresses", "count": 100},
            {"type": "phone_numbers", "count": 100},
        ]
        score = _calculate_sensitivity_score(replacements)

        assert score == 10


class TestEdgeCases:
    """Tests for edge cases and unusual inputs."""

    def test_query_with_quoted_identifiers(self):
        """Test handling of quoted identifiers."""
        sql = 'SELECT * FROM "Users" WHERE "ID" = 123'
        result = normalize_for_registry(sql)

        assert "normalized_sql" in result
        assert "hash" in result

    def test_query_with_special_characters(self):
        """Test handling of special characters in strings."""
        sql = "SELECT * FROM logs WHERE message LIKE '%error: O''Brien%'"
        result = parameterize_for_llm(sql)

        assert "O''Brien" not in result["parameterized_sql"]

    def test_very_long_query(self):
        """Test handling of very long queries."""
        # Create a long query with many columns
        columns = ", ".join([f"col{i}" for i in range(100)])
        sql = f"SELECT {columns} FROM large_table WHERE id = 1"

        result = normalize_for_registry(sql)
        assert result["hash"] is not None

    def test_nested_subquery(self):
        """Test handling of nested subqueries."""
        sql = """
        SELECT * FROM users
        WHERE id IN (
            SELECT user_id FROM orders
            WHERE total > (
                SELECT AVG(total) FROM orders WHERE status = 'completed'
            )
        )
        """
        result = normalize_for_registry(sql)

        assert "normalized_sql" in result
        assert "hash" in result

    def test_unicode_in_strings(self):
        """Test handling of unicode characters."""
        sql = "SELECT * FROM users WHERE name = 'José García'"
        result = parameterize_for_llm(sql)

        assert "José" not in result["parameterized_sql"]
        assert "<STRING_VALUE>" in result["parameterized_sql"]
