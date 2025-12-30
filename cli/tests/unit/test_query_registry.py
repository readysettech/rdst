"""
Unit tests for query_registry.py

Tests the query registry functionality including normalization, hashing, and TOML persistence.
"""

import pytest
import tempfile
from pathlib import Path

from query_registry.query_registry import (
    normalize_sql,
    hash_sql,
    extract_parameters_from_sql,
    reconstruct_query_with_params,
    QueryEntry,
    ParameterSet,
    QueryRegistry,
)


class TestNormalizeSql:
    """Tests for the normalize_sql function."""

    def test_empty_query(self):
        """Test normalization of empty query."""
        assert normalize_sql("") == ""

    def test_basic_normalization(self):
        """Test basic query normalization."""
        sql = "SELECT * FROM users WHERE id = 123"
        result = normalize_sql(sql)

        # Should replace numeric literal
        assert "123" not in result
        assert "?" in result

    def test_whitespace_collapse(self):
        """Test that whitespace is collapsed to single spaces."""
        sql = "SELECT   *   FROM    users\n\tWHERE   id = 1"
        result = normalize_sql(sql)

        # No multiple spaces, tabs, or newlines
        assert "  " not in result
        assert "\n" not in result
        assert "\t" not in result

    def test_trailing_semicolon_removal(self):
        """Test that trailing semicolons are removed."""
        sql = "SELECT * FROM users;"
        result = normalize_sql(sql)

        assert not result.endswith(";")

    def test_string_literal_replacement(self):
        """Test that string literals are replaced with ?."""
        sql = "SELECT * FROM users WHERE name = 'John'"
        result = normalize_sql(sql)

        assert "'John'" not in result
        assert "?" in result

    def test_numeric_literal_replacement(self):
        """Test that numeric literals are replaced with ?."""
        sql = "SELECT * FROM orders WHERE total > 100.50 AND count = 5"
        result = normalize_sql(sql)

        assert "100.50" not in result
        assert "5" not in result

    def test_decimal_numbers(self):
        """Test handling of decimal numbers."""
        sql = "SELECT * FROM products WHERE price = 19.99"
        result = normalize_sql(sql)

        assert "19.99" not in result

    def test_consistent_output(self):
        """Test that same query produces same normalized output."""
        sql = "SELECT * FROM users WHERE id = 123"
        result1 = normalize_sql(sql)
        result2 = normalize_sql(sql)

        assert result1 == result2


class TestHashSql:
    """Tests for the hash_sql function."""

    def test_hash_length(self):
        """Test that hash is 12 characters."""
        sql = "SELECT * FROM users"
        hash_value = hash_sql(sql)

        assert len(hash_value) == 12

    def test_hash_is_hexadecimal(self):
        """Test that hash contains only hex characters."""
        sql = "SELECT * FROM users"
        hash_value = hash_sql(sql)

        assert all(c in "0123456789abcdef" for c in hash_value)

    def test_consistent_hashing(self):
        """Test that same query always produces same hash."""
        sql = "SELECT * FROM users WHERE id = 123"
        hash1 = hash_sql(sql)
        hash2 = hash_sql(sql)

        assert hash1 == hash2

    def test_different_values_same_hash(self):
        """Test that queries with different literal values have same hash."""
        sql1 = "SELECT * FROM users WHERE id = 123"
        sql2 = "SELECT * FROM users WHERE id = 456"

        assert hash_sql(sql1) == hash_sql(sql2)

    def test_different_structure_different_hash(self):
        """Test that different query structures produce different hashes."""
        sql1 = "SELECT * FROM users WHERE id = 1"
        sql2 = "SELECT * FROM orders WHERE id = 1"

        assert hash_sql(sql1) != hash_sql(sql2)


class TestExtractParametersFromSql:
    """Tests for the extract_parameters_from_sql function."""

    def test_extract_string_parameter(self):
        """Test extraction of string parameters."""
        original = "SELECT * FROM users WHERE name = 'John'"
        parameterized = "SELECT * FROM users WHERE name = ?"

        params = extract_parameters_from_sql(original, parameterized)

        assert len(params) == 1
        assert params["param_0"] == "John"

    def test_extract_numeric_parameters(self):
        """Test extraction of numeric parameters."""
        original = "SELECT * FROM orders WHERE id = 123"
        parameterized = "SELECT * FROM orders WHERE id = ?"

        params = extract_parameters_from_sql(original, parameterized)

        assert params["param_0"] == 123

    def test_extract_float_parameters(self):
        """Test extraction of float parameters."""
        original = "SELECT * FROM products WHERE price > 19.99"
        parameterized = "SELECT * FROM products WHERE price > ?"

        params = extract_parameters_from_sql(original, parameterized)

        assert params["param_0"] == 19.99

    def test_extract_multiple_parameters(self):
        """Test extraction of multiple parameters."""
        original = "SELECT * FROM users WHERE name = 'John' AND age = 30"
        parameterized = "SELECT * FROM users WHERE name = ? AND age = ?"

        params = extract_parameters_from_sql(original, parameterized)

        assert len(params) == 2

    def test_empty_query(self):
        """Test with empty query."""
        params = extract_parameters_from_sql("", "")

        assert params == {}


class TestReconstructQueryWithParams:
    """Tests for the reconstruct_query_with_params function."""

    def test_reconstruct_with_string(self):
        """Test reconstruction with string parameter."""
        parameterized = "SELECT * FROM users WHERE name = ?"
        params = {"param_0": "John"}

        result = reconstruct_query_with_params(parameterized, params)

        assert "John" in result
        assert "?" not in result

    def test_reconstruct_with_number(self):
        """Test reconstruction with numeric parameter."""
        parameterized = "SELECT * FROM users WHERE id = ?"
        params = {"param_0": 123}

        result = reconstruct_query_with_params(parameterized, params)

        assert "123" in result
        assert "?" not in result

    def test_reconstruct_multiple_params(self):
        """Test reconstruction with multiple parameters."""
        parameterized = "SELECT * FROM users WHERE name = ? AND id = ?"
        params = {"param_0": "John", "param_1": 123}

        result = reconstruct_query_with_params(parameterized, params)

        assert "'John'" in result
        assert "123" in result

    def test_string_params_get_quoted(self):
        """Test that string parameters are properly quoted."""
        parameterized = "SELECT * FROM users WHERE name = ?"
        params = {"param_0": "Test"}

        result = reconstruct_query_with_params(parameterized, params)

        assert "'Test'" in result


class TestParameterSet:
    """Tests for the ParameterSet dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        param_set = ParameterSet(
            values={"param_0": "test"},
            analyzed_at="2024-01-15T10:00:00Z",
            target="production"
        )

        result = param_set.to_dict()

        assert result["values"] == {"param_0": "test"}
        assert result["analyzed_at"] == "2024-01-15T10:00:00Z"
        assert result["target"] == "production"

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "values": {"param_0": "test"},
            "analyzed_at": "2024-01-15T10:00:00Z",
            "target": "production"
        }

        param_set = ParameterSet.from_dict(data)

        assert param_set.values == {"param_0": "test"}
        assert param_set.analyzed_at == "2024-01-15T10:00:00Z"

    def test_from_dict_with_defaults(self):
        """Test from_dict handles missing fields gracefully."""
        data = {}

        param_set = ParameterSet.from_dict(data)

        assert param_set.values == {}
        assert param_set.analyzed_at == ""
        assert param_set.target == ""


class TestQueryEntry:
    """Tests for the QueryEntry dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        entry = QueryEntry(
            sql="SELECT * FROM users WHERE id = ?",
            hash="abc123def456",
            tag="user_lookup",
            first_analyzed="2024-01-15T10:00:00Z",
            last_analyzed="2024-01-15T10:00:00Z",
            frequency=100,
            source="top"
        )

        result = entry.to_dict()

        assert result["sql"] == "SELECT * FROM users WHERE id = ?"
        assert result["hash"] == "abc123def456"
        assert result["tag"] == "user_lookup"

    def test_from_dict(self):
        """Test creation from dictionary."""
        data = {
            "sql": "SELECT * FROM users WHERE id = ?",
            "hash": "abc123def456",
            "tag": "user_lookup",
            "first_analyzed": "2024-01-15T10:00:00Z",
            "last_analyzed": "2024-01-15T10:00:00Z",
            "frequency": 100,
            "source": "top"
        }

        entry = QueryEntry.from_dict(data)

        assert entry.sql == "SELECT * FROM users WHERE id = ?"
        assert entry.hash == "abc123def456"

    def test_from_dict_backward_compatibility(self):
        """Test that from_dict handles old format without new fields."""
        data = {
            "sql": "SELECT * FROM users",
            "hash": "abc123",
            "tag": "",
            "first_analyzed": "",
            "last_analyzed": "",
            "frequency": 0,
            "source": "manual"
        }

        entry = QueryEntry.from_dict(data)

        # Should have defaults for new fields
        assert entry.last_target == ""
        assert entry.parameter_history == []
        assert entry.most_recent_params == {}


class TestQueryRegistry:
    """Tests for the QueryRegistry class."""

    def test_init_with_custom_path(self, temp_dir):
        """Test initialization with custom registry path."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        assert registry.registry_path == registry_path

    def test_init_default_path(self):
        """Test initialization with default path."""
        registry = QueryRegistry()

        expected_path = Path.home() / ".rdst" / "queries.toml"
        assert registry.registry_path == expected_path

    def test_load_empty_registry(self, temp_dir):
        """Test loading an empty/non-existent registry."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        registry.load()

        assert registry._queries == {}
        assert registry._loaded is True

    def test_add_and_get_query(self, temp_dir):
        """Test adding and retrieving a query."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "SELECT * FROM users WHERE id = 123"
        query_hash, is_new = registry.add_query(sql, tag="test")

        assert len(query_hash) == 12
        assert is_new is True

        # Retrieve the query
        entry = registry.get_query(query_hash)
        assert entry is not None
        assert entry.tag == "test"

    def test_add_duplicate_query(self, temp_dir):
        """Test adding the same query twice updates existing entry."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "SELECT * FROM users WHERE id = 123"
        hash1, is_new1 = registry.add_query(sql, tag="first")
        hash2, is_new2 = registry.add_query(sql, tag="second")

        # Same hash, second is not new
        assert hash1 == hash2
        assert is_new1 is True
        assert is_new2 is False

        # Tag should not be overwritten
        entry = registry.get_query(hash1)
        assert entry.tag == "first"

    def test_get_query_by_tag(self, temp_dir):
        """Test retrieving query by tag."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "SELECT * FROM orders WHERE status = 'pending'"
        registry.add_query(sql, tag="pending_orders")

        entry = registry.get_query_by_tag("pending_orders")
        assert entry is not None

    def test_get_nonexistent_query(self, temp_dir):
        """Test getting a query that doesn't exist."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))
        registry.load()

        entry = registry.get_query("nonexistent")
        assert entry is None

    def test_list_queries(self, temp_dir):
        """Test listing all queries."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        registry.add_query("SELECT * FROM users")
        registry.add_query("SELECT * FROM orders")
        registry.add_query("SELECT * FROM products")

        queries = registry.list_queries()
        assert len(queries) == 3

    def test_list_queries_with_limit(self, temp_dir):
        """Test listing queries with limit."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        for i in range(10):
            registry.add_query(f"SELECT * FROM table{i}")

        queries = registry.list_queries(limit=5)
        assert len(queries) == 5

    def test_remove_query(self, temp_dir):
        """Test removing a query."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "SELECT * FROM users"
        query_hash, _ = registry.add_query(sql)

        # Remove the query
        result = registry.remove_query(query_hash)
        assert result is True

        # Query should be gone
        entry = registry.get_query(query_hash)
        assert entry is None

    def test_remove_nonexistent_query(self, temp_dir):
        """Test removing a query that doesn't exist."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))
        registry.load()

        result = registry.remove_query("nonexistent")
        assert result is False

    def test_query_exists(self, temp_dir):
        """Test checking if query exists."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "SELECT * FROM users WHERE id = 1"
        registry.add_query(sql)

        assert registry.query_exists(sql) is True
        assert registry.query_exists("SELECT * FROM nonexistent") is False

    def test_get_or_create_hash(self, temp_dir):
        """Test getting hash without adding query."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "SELECT * FROM users WHERE id = 123"
        hash_value = registry.get_or_create_hash(sql)

        assert len(hash_value) == 12
        # Query should not be added
        assert registry.get_query(hash_value) is None

    def test_persistence(self, temp_dir):
        """Test that queries persist across registry instances."""
        registry_path = temp_dir / "test_queries.toml"

        # First instance - add query
        registry1 = QueryRegistry(registry_path=str(registry_path))
        sql = "SELECT * FROM persistent_test"
        query_hash, _ = registry1.add_query(sql, tag="persistent")

        # Second instance - should find the query
        registry2 = QueryRegistry(registry_path=str(registry_path))
        entry = registry2.get_query(query_hash)

        assert entry is not None
        assert entry.tag == "persistent"

    def test_parameter_history(self, temp_dir):
        """Test that parameter history is maintained."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Add same query pattern with different values
        registry.add_query("SELECT * FROM users WHERE id = 1", target="db1")
        registry.add_query("SELECT * FROM users WHERE id = 2", target="db2")
        registry.add_query("SELECT * FROM users WHERE id = 3", target="db3")

        queries = registry.list_queries()
        assert len(queries) == 1  # Same pattern

        entry = queries[0]
        assert len(entry.parameter_history) == 3

    def test_parameter_history_limit(self, temp_dir):
        """Test that parameter history is limited to 10 entries."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Add same query pattern 15 times with different values
        for i in range(15):
            registry.add_query(f"SELECT * FROM users WHERE id = {i}")

        entry = registry.list_queries()[0]
        assert len(entry.parameter_history) <= 10


class TestEdgeCases:
    """Tests for edge cases and unusual inputs."""

    def test_query_with_special_characters(self, temp_dir):
        """Test handling queries with special characters."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "SELECT * FROM logs WHERE message LIKE '%error%'"
        query_hash, _ = registry.add_query(sql)

        entry = registry.get_query(query_hash)
        assert entry is not None

    def test_unicode_in_query(self, temp_dir):
        """Test handling unicode characters in queries."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "SELECT * FROM users WHERE name = 'José'"
        query_hash, _ = registry.add_query(sql)

        entry = registry.get_query(query_hash)
        assert entry is not None

    def test_query_at_size_limit(self, temp_dir):
        """Test handling queries at the 1KB size limit."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Create a query just under 1KB
        # "SELECT col0, col1, ... FROM table WHERE id = 1" pattern
        columns = ", ".join([f"col{i}" for i in range(80)])  # ~400 bytes
        sql = f"SELECT {columns} FROM small_table WHERE id = 1"
        assert len(sql.encode('utf-8')) < 1024, "Test query should be under 1KB"

        query_hash, _ = registry.add_query(sql)
        entry = registry.get_query(query_hash)

        assert entry is not None

    def test_query_exceeds_size_limit(self, temp_dir):
        """Test that queries exceeding 1KB are rejected."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Create a query over 1KB
        columns = ", ".join([f"col{i}" for i in range(200)])  # >1KB
        sql = f"SELECT {columns} FROM large_table WHERE id = 1"
        assert len(sql.encode('utf-8')) > 1024, "Test query should be over 1KB"

        with pytest.raises(ValueError) as exc_info:
            registry.add_query(sql)

        assert "exceeds registry limit" in str(exc_info.value)
        assert "1KB" in str(exc_info.value)


class TestQuerySizeLimits:
    """Tests for query size limit enforcement (1KB default, 10KB bypass max)."""

    def test_registry_accepts_query_under_1kb(self, temp_dir):
        """Test that queries under 1KB are accepted by registry."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Query well under 1KB
        sql = "SELECT * FROM users WHERE id = 1"
        assert len(sql.encode('utf-8')) < 1024

        query_hash, is_new = registry.add_query(sql)
        assert is_new is True
        assert registry.get_query(query_hash) is not None

    def test_registry_rejects_query_over_1kb(self, temp_dir):
        """Test that queries over 1KB are rejected by registry."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Create query over 1KB
        columns = ", ".join([f"column_{i}" for i in range(150)])
        sql = f"SELECT {columns} FROM large_table WHERE id = 1"
        assert len(sql.encode('utf-8')) > 1024

        with pytest.raises(ValueError) as exc_info:
            registry.add_query(sql)

        assert "exceeds registry limit" in str(exc_info.value)

    def test_query_exactly_at_1kb_limit(self, temp_dir):
        """Test query at exactly 1024 bytes boundary."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Build query that's exactly 1024 bytes
        base = "SELECT * FROM t WHERE x = "
        padding_needed = 1024 - len(base.encode('utf-8'))
        sql = base + "'" + "x" * (padding_needed - 2) + "'"
        assert len(sql.encode('utf-8')) == 1024

        # Exactly at limit should be accepted
        query_hash, is_new = registry.add_query(sql)
        assert is_new is True

    def test_query_one_byte_over_limit(self, temp_dir):
        """Test query at 1025 bytes is rejected."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Build query that's 1025 bytes
        base = "SELECT * FROM t WHERE x = "
        padding_needed = 1025 - len(base.encode('utf-8'))
        sql = base + "'" + "x" * (padding_needed - 2) + "'"
        assert len(sql.encode('utf-8')) == 1025

        with pytest.raises(ValueError):
            registry.add_query(sql)

    def test_error_message_includes_bypass_hint(self, temp_dir):
        """Test that error message mentions --large-query-bypass."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        columns = ", ".join([f"col{i}" for i in range(200)])
        sql = f"SELECT {columns} FROM table WHERE id = 1"

        with pytest.raises(ValueError) as exc_info:
            registry.add_query(sql)

        assert "--large-query-bypass" in str(exc_info.value)
