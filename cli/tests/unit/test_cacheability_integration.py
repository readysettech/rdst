"""
Integration tests for ReadySet cacheability checks.

Adapted from cloud/cloud_agent/test_readyset_cacheability.py
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# Import module directly to avoid package __init__.py issues
def _import_module_directly(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

_lib_path = Path(__file__).parent.parent.parent / "lib"
readyset_cacheability = _import_module_directly(
    "readyset_cacheability",
    _lib_path / "functions" / "readyset_cacheability.py"
)

check_readyset_cacheability = readyset_cacheability.check_readyset_cacheability
generate_explain_create_cache = readyset_cacheability.generate_explain_create_cache


class TestCacheabilityIntegration:
    """Integration tests for cacheability checks with various query patterns."""

    def test_simple_parameterized_select(self):
        """Test simple parameterized SELECT query."""
        query = "SELECT * FROM users WHERE id = $1"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is True, f"Query should be cacheable: {query}"
        assert result['confidence'] == 'high'
        assert result['create_cache_command'] is not None
        assert 'CREATE CACHE CONCURRENTLY' in result['create_cache_command']

    def test_complex_join_with_group_by(self):
        """Test complex JOIN with GROUP BY query."""
        query = "SELECT u.name, COUNT(*) FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.name"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is True
        assert result['confidence'] == 'high'
        assert len(result['issues']) == 0

    def test_non_cacheable_now_function(self):
        """Test query with NOW() function (not cacheable)."""
        query = "SELECT * FROM users WHERE created_at > NOW()"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is False
        assert len(result['issues']) > 0
        assert any('NOW()' in issue for issue in result['issues'])

    def test_non_cacheable_for_update(self):
        """Test query with FOR UPDATE (not cacheable)."""
        query = "SELECT * FROM users FOR UPDATE"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is False
        assert any('FOR UPDATE' in issue for issue in result['issues'])

    def test_non_cacheable_insert(self):
        """Test INSERT statement (not cacheable)."""
        query = "INSERT INTO users (name) VALUES ('test')"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is False
        assert len(result['issues']) > 0

    def test_union_query_with_warning(self):
        """Test UNION query (cacheable with warning)."""
        query = "SELECT * FROM a UNION SELECT * FROM b"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is True
        assert result['confidence'] == 'medium'
        assert len(result['warnings']) > 0
        assert any('UNION' in warning for warning in result['warnings'])

    def test_json_functions_with_warning(self):
        """Test query with JSON functions (cacheable with warning)."""
        query = "SELECT *, JSON_EXTRACT(data, '$.name') FROM users"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is True
        assert len(result['warnings']) > 0


class TestExplainGenerationIntegration:
    """Integration tests for EXPLAIN CREATE CACHE command generation."""

    def test_explain_command_structure(self):
        """Test EXPLAIN CREATE CACHE command generation structure."""
        query = "SELECT * FROM users WHERE active = true"
        result = generate_explain_create_cache(query=query)

        assert 'explain_command' in result
        assert 'usage' in result
        assert 'note' in result

        # Check command includes all required parts
        cmd = result['explain_command']
        assert 'EXPLAIN' in cmd
        assert 'CREATE CACHE' in cmd
        assert query in cmd

    def test_explain_provides_usage_info(self):
        """Test that EXPLAIN generation provides usage information."""
        result = generate_explain_create_cache(query="SELECT * FROM products")

        assert isinstance(result['usage'], str)
        assert isinstance(result['note'], str)
        assert len(result['usage']) > 0
        assert len(result['note']) > 0


class TestConfidenceLevels:
    """Test confidence level assignment for various query patterns."""

    def test_high_confidence_simple_query(self):
        """Test high confidence for simple, well-supported query."""
        query = "SELECT * FROM users WHERE id = $1"
        result = check_readyset_cacheability(query=query)

        assert result['confidence'] == 'high'
        assert len(result['warnings']) == 0

    def test_medium_confidence_with_warnings(self):
        """Test medium confidence for query with warnings."""
        query = "SELECT * FROM a UNION SELECT * FROM b"
        result = check_readyset_cacheability(query=query)

        assert result['confidence'] == 'medium'
        assert len(result['warnings']) > 0

    def test_low_confidence_multiple_warnings(self):
        """Test low confidence for query with multiple warnings."""
        # Query with both UNION and JSON functions
        query = "SELECT JSON_EXTRACT(data, '$.x') FROM a UNION SELECT data FROM b"
        result = check_readyset_cacheability(query=query)

        # Should be cacheable but with low confidence due to multiple warnings
        assert result['cacheable'] is True
        assert result['confidence'] in ['low', 'medium']
        assert len(result['warnings']) > 1


class TestCreateCacheCommands:
    """Test CREATE CACHE command generation for various scenarios."""

    def test_create_cache_with_concurrently(self):
        """Test that CREATE CACHE includes CONCURRENTLY keyword."""
        query = "SELECT * FROM users WHERE status = 'active'"
        result = check_readyset_cacheability(query=query)

        assert 'CONCURRENTLY' in result['create_cache_command']

    def test_create_cache_preserves_query(self):
        """Test that CREATE CACHE preserves original query."""
        query = "SELECT id, name FROM users WHERE created_at > '2024-01-01'"
        result = check_readyset_cacheability(query=query)

        assert query in result['create_cache_command']

    def test_no_create_cache_for_non_cacheable(self):
        """Test that non-cacheable queries don't get CREATE CACHE command."""
        query = "SELECT NOW() FROM users"
        result = check_readyset_cacheability(query=query)

        assert result['create_cache_command'] is None


class TestEdgeCases:
    """Test edge cases and special query patterns."""

    def test_mysql_style_parameters(self):
        """Test MySQL-style parameter placeholders."""
        query = "SELECT * FROM users WHERE id = ?"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is True
        assert result['query_parameterized'] is True

    def test_named_parameters(self):
        """Test named parameter placeholders."""
        query = "SELECT * FROM users WHERE id = :user_id"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is True
        assert result['query_parameterized'] is True

    def test_multiple_tables_join(self):
        """Test query with multiple table joins."""
        query = """
            SELECT u.name, o.total, p.name
            FROM users u
            JOIN orders o ON u.id = o.user_id
            JOIN products p ON o.product_id = p.id
            WHERE u.active = true
        """
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is True

    def test_subquery(self):
        """Test query with subquery."""
        query = """
            SELECT * FROM users
            WHERE id IN (SELECT user_id FROM orders WHERE total > 100)
        """
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is True

    def test_current_date_not_cacheable(self):
        """Test CURRENT_DATE function is not cacheable."""
        query = "SELECT * FROM users WHERE created_at = CURRENT_DATE"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is False

    def test_random_not_cacheable(self):
        """Test RANDOM() function is not cacheable."""
        query = "SELECT * FROM users ORDER BY RANDOM() LIMIT 10"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is False

    def test_uuid_not_cacheable(self):
        """Test UUID() function is not cacheable."""
        query = "SELECT UUID() as id, name FROM users"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is False

    def test_stored_procedure_not_cacheable(self):
        """Test stored procedure calls are not cacheable."""
        query = "CALL process_users()"
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is False

    def test_complex_cte(self):
        """Test complex CTE query."""
        query = """
            WITH active_users AS (
                SELECT * FROM users WHERE active = true
            ),
            recent_orders AS (
                SELECT * FROM orders WHERE created_at > '2024-01-01'
            )
            SELECT u.name, COUNT(o.id)
            FROM active_users u
            JOIN recent_orders o ON u.id = o.user_id
            GROUP BY u.name
        """
        result = check_readyset_cacheability(query=query)

        assert result['cacheable'] is True
