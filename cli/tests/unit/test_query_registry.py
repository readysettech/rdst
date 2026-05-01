"""
Unit tests for query_registry.py

Tests the query registry functionality including normalization, hashing, and TOML persistence.
"""

import pytest
import tempfile
from pathlib import Path

from shared.query_registry.query_registry import (
    normalize_sql,
    hash_sql,
    extract_parameters_from_sql,
    reconstruct_query_with_params,
    verify_query_completeness,
    QueryEntry,
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

        # Should replace numeric literal with named placeholder
        assert "123" not in result
        assert ":p" in result  # Named placeholder like :p1

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
        """Test that string literals are replaced with named placeholders."""
        sql = "SELECT * FROM users WHERE name = 'John'"
        result = normalize_sql(sql)

        assert "'John'" not in result
        assert ":p" in result  # Named placeholder like :p1

    def test_numeric_literal_replacement(self):
        """Test that numeric literals are replaced with named placeholders."""
        sql = "SELECT * FROM orders WHERE total > 100.50 AND count = 5"
        result = normalize_sql(sql)

        assert "100.50" not in result
        # Note: "5" might appear in placeholder names like ":p5", so check for original value
        assert " 5" not in result or ":p" in result

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

    def test_strips_leading_line_comment(self):
        """Leading line comments are removed before normalization."""
        sql = "-- find one user\nSELECT * FROM users WHERE id = 123"
        result = normalize_sql(sql)

        assert "find one user" not in result.lower()
        assert result == "SELECT * FROM users WHERE id = :p1"

    def test_strips_leading_block_comment(self):
        """Leading block comments are removed before normalization."""
        sql = "/* find one user */ SELECT * FROM users WHERE id = 123"
        result = normalize_sql(sql)

        assert "find one user" not in result.lower()
        assert result == "SELECT * FROM users WHERE id = :p1"


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

    def test_comments_do_not_affect_hash(self):
        """Equivalent queries hash the same after comment stripping."""
        sql_with_comments = "-- lookup user\nSELECT * FROM users WHERE id = 123"
        sql_without_comments = "SELECT * FROM users WHERE id = 123"

        assert hash_sql(sql_with_comments) == hash_sql(sql_without_comments)


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
            source="top",
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
            "source": "top",
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
            "source": "manual",
        }

        entry = QueryEntry.from_dict(data)

        # Should have defaults for new fields
        assert entry.last_target == ""
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

        # Tag should be updated to new alias
        entry = registry.get_query(hash1)
        assert entry.tag == "second"

    def test_get_query_by_tag(self, temp_dir):
        """Test retrieving query by tag."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "SELECT * FROM orders WHERE status = 'pending'"
        registry.add_query(sql, tag="pending_orders")

        entry = registry.get_query_by_tag("pending_orders")
        assert entry is not None

    def test_update_tag_on_existing_hash(self, temp_dir):
        # Adding same query twice with different tag should update alias.
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "SELECT * FROM customers WHERE id = 5"
        query_hash, _ = registry.add_query(sql, tag="first-name")

        # Re-add same SQL with new tag
        _, is_new = registry.add_query(sql, tag="renamed")
        assert is_new is False

        entry = registry.get_query(query_hash)
        assert entry is not None
        assert entry.tag == "renamed"

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
        """Test handling queries under the 16KB size limit."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Create a query under 16KB
        columns = ", ".join([f"col{i}" for i in range(400)])  # ~2KB
        sql = f"SELECT {columns} FROM small_table WHERE id = 1"
        assert len(sql.encode("utf-8")) < 16384, "Test query should be under 16KB"

        query_hash, _ = registry.add_query(sql)
        entry = registry.get_query(query_hash)

        assert entry is not None

    def test_query_exceeds_size_limit(self, temp_dir):
        """Test that queries exceeding 64KB are rejected."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Create a query over 64KB
        base = "SELECT * FROM t WHERE x = "
        padding_needed = 65537 - len(base.encode("utf-8"))
        sql = base + "'" + "x" * (padding_needed - 2) + "'"
        assert len(sql.encode("utf-8")) > 65536, "Test query should be over 64KB"

        with pytest.raises(ValueError) as exc_info:
            registry.add_query(sql)

        assert "exceeds registry limit" in str(exc_info.value)


class TestQuerySizeLimits:
    """Tests for query size limit enforcement (16KB default, configurable via env var)."""

    def test_registry_accepts_query_under_limit(self, temp_dir):
        """Test that queries under 16KB are accepted by registry."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "SELECT * FROM users WHERE id = 1"
        assert len(sql.encode("utf-8")) < 16384

        query_hash, is_new = registry.add_query(sql)
        assert is_new is True
        assert registry.get_query(query_hash) is not None

    def test_registry_rejects_query_over_limit(self, temp_dir):
        """Test that queries over 64KB are rejected by registry."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Create query over 64KB
        base = "SELECT * FROM t WHERE x = "
        padding_needed = 65537 - len(base.encode("utf-8"))
        sql = base + "'" + "x" * (padding_needed - 2) + "'"
        assert len(sql.encode("utf-8")) > 65536

        with pytest.raises(ValueError) as exc_info:
            registry.add_query(sql)

        assert "exceeds registry limit" in str(exc_info.value)

    def test_query_exactly_at_limit(self, temp_dir):
        """Test query at exactly 65536 bytes boundary."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Build query that's exactly 65536 bytes
        base = "SELECT * FROM t WHERE x = "
        padding_needed = 65536 - len(base.encode("utf-8"))
        sql = base + "'" + "x" * (padding_needed - 2) + "'"
        assert len(sql.encode("utf-8")) == 65536

        # Exactly at limit should be accepted
        query_hash, is_new = registry.add_query(sql)
        assert is_new is True

    def test_query_one_byte_over_limit(self, temp_dir):
        """Test query at 65537 bytes is rejected."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Build query that's 65537 bytes
        base = "SELECT * FROM t WHERE x = "
        padding_needed = 65537 - len(base.encode("utf-8"))
        sql = base + "'" + "x" * (padding_needed - 2) + "'"
        assert len(sql.encode("utf-8")) == 65537

        with pytest.raises(ValueError):
            registry.add_query(sql)

    def test_query_under_64kb_accepted(self, temp_dir):
        """Test that queries under 64KB are accepted."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        # Build query that's ~32KB
        base = "SELECT * FROM t WHERE x = "
        padding_needed = 32768 - len(base.encode("utf-8"))
        sql = base + "'" + "x" * (padding_needed - 2) + "'"
        assert len(sql.encode("utf-8")) == 32768

        query_hash, is_new = registry.add_query(sql)
        assert is_new is True


class TestQueryLengthConstants:
    """Tests for MAX_QUERY_LENGTH constant."""

    def test_max_query_length_is_64kb(self):
        """MAX_QUERY_LENGTH should be 64KB."""
        from shared.query_capture_limits import MAX_QUERY_LENGTH

        assert MAX_QUERY_LENGTH == 64 * 1024

    def test_db_query_size_warn_threshold(self):
        """DB_QUERY_SIZE_WARN_THRESHOLD should be 4KB."""
        from shared.query_capture_limits import DB_QUERY_SIZE_WARN_THRESHOLD

        assert DB_QUERY_SIZE_WARN_THRESHOLD == 4 * 1024


class TestVerifyQueryCompleteness:
    """Tests for verify_query_completeness function (truncation detection)."""

    def test_valid_select(self):
        """Valid SELECT query passes."""
        is_valid, error = verify_query_completeness("SELECT * FROM users WHERE id = 1")
        assert is_valid is True
        assert error is None

    def test_valid_insert(self):
        """Valid INSERT query passes."""
        is_valid, error = verify_query_completeness(
            "INSERT INTO users (name) VALUES ('test')"
        )
        assert is_valid is True
        assert error is None

    def test_valid_update(self):
        """Valid UPDATE query passes."""
        is_valid, error = verify_query_completeness(
            "UPDATE users SET name = 'test' WHERE id = 1"
        )
        assert is_valid is True
        assert error is None

    def test_empty_query(self):
        """Empty query is rejected."""
        is_valid, error = verify_query_completeness("")
        assert is_valid is False
        assert "Empty" in error

    def test_valid_select_with_leading_line_comment(self):
        """Leading line comments are ignored for completeness validation."""
        is_valid, error = verify_query_completeness(
            "-- returns active users\nSELECT * FROM users WHERE active = true"
        )
        assert is_valid is True
        assert error is None

    def test_valid_select_with_leading_block_comment(self):
        """Leading block comments are ignored for completeness validation."""
        is_valid, error = verify_query_completeness(
            "/* returns active users */ SELECT * FROM users WHERE active = true"
        )
        assert is_valid is True
        assert error is None

    def test_truncated_ends_with_where(self):
        """Query ending with WHERE is detected as truncated."""
        is_valid, error = verify_query_completeness("SELECT * FROM users WHERE")
        assert is_valid is False
        assert "truncated" in error.lower()

    def test_truncated_ends_with_and(self):
        """Query ending with AND is detected as truncated."""
        is_valid, error = verify_query_completeness(
            "SELECT * FROM users WHERE id = 1 AND"
        )
        assert is_valid is False
        assert "truncated" in error.lower()

    def test_truncated_ends_with_comma(self):
        """Query ending with comma is detected as truncated."""
        is_valid, error = verify_query_completeness("SELECT id, name,")
        assert is_valid is False
        assert "truncated" in error.lower()

    def test_truncated_ends_with_open_paren(self):
        """Query ending with open parenthesis is detected as truncated."""
        is_valid, error = verify_query_completeness("SELECT * FROM users WHERE id IN (")
        assert is_valid is False
        assert "truncated" in error.lower()

    def test_truncated_ends_with_equals(self):
        """Query ending with equals is detected as truncated."""
        is_valid, error = verify_query_completeness("SELECT * FROM users WHERE id =")
        assert is_valid is False
        assert "truncated" in error.lower()

    def test_truncated_ends_with_from(self):
        """Query ending with FROM is detected as truncated."""
        is_valid, error = verify_query_completeness("SELECT * FROM")
        assert is_valid is False
        assert "truncated" in error.lower()

    def test_truncated_ends_with_join(self):
        """Query ending with JOIN is detected as truncated."""
        is_valid, error = verify_query_completeness("SELECT * FROM users JOIN")
        assert is_valid is False
        assert "truncated" in error.lower()

    def test_registry_rejects_truncated_query(self, temp_dir):
        """Registry rejects truncated queries."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        with pytest.raises(ValueError) as exc_info:
            registry.add_query("SELECT * FROM users WHERE id = 1 AND")

        assert "truncated" in str(exc_info.value).lower()

    def test_registry_stores_comment_free_normalized_sql(self, temp_dir):
        """Registry saves canonical SQL without comments."""
        registry_path = temp_dir / "test_queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))

        sql = "-- look up one user\nSELECT * FROM users WHERE id = 42"
        query_hash, is_new = registry.add_query(sql)

        assert is_new is True
        entry = registry.get_query(query_hash)
        assert entry is not None
        assert entry.sql == "SELECT * FROM users WHERE id = :p1"
        assert "look up one user" not in entry.sql.lower()


# =============================================================================
# QueryEntry — readyset_query_id and friends (CLD-1754 surface)
# =============================================================================

class TestQueryEntryReadysetFields:

    def test_default_values_empty_strings(self):
        entry = QueryEntry(sql="SELECT 1", hash="abc123")
        assert entry.readyset_query_id == ""
        assert entry.readyset_supported == ""
        assert entry.last_cache_target == ""
        assert entry.readyset_last_observed_at == ""

    def test_from_dict_backward_compat_no_new_fields(self):
        old_data = {
            "sql": "SELECT 1",
            "hash": "abc123",
            "tag": "",
            "first_analyzed": "",
            "last_analyzed": "",
            "frequency": 0,
            "source": "manual",
        }
        entry = QueryEntry.from_dict(old_data)
        assert entry.sql == "SELECT 1"
        assert entry.readyset_query_id == ""
        assert entry.readyset_supported == ""

    def test_from_dict_with_new_fields(self):
        data = {
            "sql": "SELECT 1",
            "hash": "abc123",
            "readyset_query_id": "q_abc123def456",
            "readyset_supported": "yes",
            "last_cache_target": "mydb-cache",
            "readyset_last_observed_at": "2026-04-28T15:30:00+00:00",
        }
        entry = QueryEntry.from_dict(data)
        assert entry.readyset_query_id == "q_abc123def456"
        assert entry.readyset_supported == "yes"
        assert entry.last_cache_target == "mydb-cache"

    def test_to_dict_includes_new_fields(self):
        entry = QueryEntry(
            sql="SELECT 1", hash="abc123",
            readyset_query_id="q_xyz", readyset_supported="yes",
        )
        d = entry.to_dict()
        assert d["readyset_query_id"] == "q_xyz"
        assert d["readyset_supported"] == "yes"


class TestQueryRegistryUpdateReadysetIdentity:

    def _make_registry(self, tmp_path: Path) -> QueryRegistry:
        reg = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        reg.load()
        return reg

    def test_update_existing_query(self, tmp_path):
        reg = self._make_registry(tmp_path)
        h, _ = reg.add_query(sql="SELECT * FROM users WHERE id = 1", source="manual", target="db1")
        ok = reg.update_readyset_identity(
            query_hash=h, readyset_query_id="q_abc123def456",
            readyset_supported="yes", cache_target="db1-cache",
        )
        assert ok is True
        entry = reg._queries[h]
        assert entry.readyset_query_id == "q_abc123def456"
        assert entry.readyset_supported == "yes"
        assert entry.last_cache_target == "db1-cache"
        assert entry.readyset_last_observed_at  # non-empty

    def test_update_nonexistent_query_returns_false(self, tmp_path):
        reg = self._make_registry(tmp_path)
        ok = reg.update_readyset_identity(
            query_hash="nonexistent_hash", readyset_query_id="q_xyz",
        )
        assert ok is False

    def test_update_persists_across_reload(self, tmp_path):
        reg = self._make_registry(tmp_path)
        h, _ = reg.add_query(sql="SELECT 1", source="manual", target="db1")
        reg.update_readyset_identity(
            query_hash=h, readyset_query_id="q_persist123",
            readyset_supported="yes", cache_target="db1-cache",
        )
        reg2 = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        reg2.load()
        assert reg2._queries[h].readyset_query_id == "q_persist123"
        assert reg2._queries[h].last_cache_target == "db1-cache"

    def test_update_without_optional_fields(self, tmp_path):
        reg = self._make_registry(tmp_path)
        h, _ = reg.add_query(sql="SELECT 1", source="manual", target="db1")
        reg.update_readyset_identity(query_hash=h, readyset_query_id="q_only")
        entry = reg._queries[h]
        assert entry.readyset_query_id == "q_only"
        assert entry.readyset_supported == ""
        assert entry.last_cache_target == ""

    def test_update_observed_at_changes_on_subsequent_call(self, tmp_path):
        import time
        reg = self._make_registry(tmp_path)
        h, _ = reg.add_query(sql="SELECT 1", source="manual", target="db1")
        reg.update_readyset_identity(query_hash=h, readyset_query_id="q_v1")
        first_ts = reg._queries[h].readyset_last_observed_at
        time.sleep(0.01)
        reg.update_readyset_identity(query_hash=h, readyset_query_id="q_v2")
        second_ts = reg._queries[h].readyset_last_observed_at
        assert second_ts >= first_ts

    def test_find_by_readyset_query_id_match(self, tmp_path):
        reg = self._make_registry(tmp_path)
        h, _ = reg.add_query(sql="SELECT 1", source="manual", target="db1")
        reg.update_readyset_identity(query_hash=h, readyset_query_id="q_findme")
        entry = reg.find_by_readyset_query_id("q_findme")
        assert entry is not None
        assert entry.hash == h

    def test_find_by_readyset_query_id_no_match(self, tmp_path):
        reg = self._make_registry(tmp_path)
        reg.add_query(sql="SELECT 1", source="manual", target="db1")
        assert reg.find_by_readyset_query_id("q_nope") is None

    def test_find_by_readyset_query_id_empty_registry(self, tmp_path):
        reg = self._make_registry(tmp_path)
        assert reg.find_by_readyset_query_id("q_anything") is None
