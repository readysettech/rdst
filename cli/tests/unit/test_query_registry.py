"""
Unit tests for query_registry.py

Tests the query registry functionality including normalization, hashing, and TOML persistence.
"""

import pytest
import tempfile
import toml
from datetime import datetime, timezone
from pathlib import Path

from shared.query_registry.query_registry import (
    normalize_sql,
    hash_sql,
    extract_observed_params,
    extract_parameters_from_sql,
    reconstruct_query_with_params,
    registry_sqlite_enabled,
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

    def test_placeholder_styles_share_one_hash(self):
        """$N, anonymous ?, :pN, and literal-bearing texts hash alike."""
        variants = [
            "SELECT a FROM t WHERE b = $1 LIMIT $2",
            "SELECT a FROM t WHERE b = ? LIMIT ?",
            "SELECT a FROM t WHERE b = :p1 LIMIT :p2",
            "SELECT a FROM t WHERE b = 1 LIMIT 5",
        ]
        hashes = {hash_sql(sql) for sql in variants}
        assert len(hashes) == 1

    def test_placeholder_position_still_distinguishes_structure(self):
        assert hash_sql("SELECT a FROM t WHERE b = $1") != hash_sql(
            "SELECT a FROM t WHERE c = $1"
        )

    def test_string_literal_question_mark_does_not_add_a_slot(self):
        assert hash_sql("SELECT * FROM t WHERE name = 'who?'") == hash_sql(
            "SELECT * FROM t WHERE name = 'other'"
        )

    def test_postgres_json_operator_does_not_become_a_slot(self):
        with_operator = "SELECT * FROM t WHERE tags ?| ARRAY['a'] LIMIT 5"
        without_operator = "SELECT * FROM t WHERE tags = ARRAY['a'] LIMIT 5"
        assert hash_sql(with_operator) != hash_sql(without_operator)
        # Stable across literal values, so the ?| text keeps one identity.
        assert hash_sql(with_operator) == hash_sql(
            "SELECT * FROM t WHERE tags ?| ARRAY['b'] LIMIT 9"
        )

    def test_dollar_placeholder_texts_converge_across_dialects(self):
        """Raw $N text, its dialect-less and postgres normalizations, the :pN
        spelling, and legacy fused $:pN stored texts all share one hash."""
        from shared.query_registry.sql_normalizer import normalize_and_extract

        raw = (
            "SELECT id, name FROM users WHERE score > $1 "
            "ORDER BY created_at DESC LIMIT $2"
        )
        forms = [
            raw,
            normalize_and_extract(raw, None)[0],
            normalize_and_extract(raw, "postgres")[0],
            "SELECT id, name FROM users WHERE score > :p1 "
            "ORDER BY created_at DESC LIMIT :p2",
            "SELECT id, name FROM users WHERE score > $:p2 "
            "ORDER BY created_at DESC LIMIT $:p1",
        ]
        assert len({hash_sql(form) for form in forms}) == 1

    def test_fallback_normalized_text_keeps_raw_identity(self):
        """Dialect-less sqlglot cannot parse TABLESAMPLE, so hashing routes
        through the regex fallback; its output must re-hash to the raw
        text's identity with $N slots intact."""
        from shared.query_registry.sql_normalizer import normalize_and_extract

        raw = (
            "SELECT id, name FROM users TABLESAMPLE SYSTEM($1) "
            "WHERE score > 10 LIMIT $2"
        )
        assert hash_sql(raw) == hash_sql(normalize_and_extract(raw, None)[0])

    def test_group_by_ordinal_keeps_one_identity_across_dialects(self):
        sql = (
            "SELECT DATE_TRUNC($1, created_at) AS day, COUNT(*) FROM events "
            "GROUP BY 1 ORDER BY 1 DESC"
        )
        from shared.query_registry.sql_normalizer import normalize_and_extract

        assert hash_sql(sql) == hash_sql(normalize_and_extract(sql, "postgres")[0])


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

    def test_embedded_quote_cannot_terminate_the_literal(self):
        """A quote inside a value is doubled, so the value stays one literal."""
        parameterized = "SELECT * FROM users WHERE name = ?"
        params = {"param_0": "O'Brien'; DROP TABLE users --"}

        result = reconstruct_query_with_params(parameterized, params)

        assert result == (
            "SELECT * FROM users WHERE name = "
            "'O''Brien''; DROP TABLE users --'"
        )


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

    def test_from_dict_normalizes_list_last_target(self):
        """Legacy writers stored last_target as a list; normalize to a string."""
        base = {
            "sql": "SELECT count(*) FROM users",
            "hash": "abc123",
            "tag": "",
            "first_analyzed": "",
            "last_analyzed": "",
            "frequency": 0,
            "source": "chat",
        }

        assert QueryEntry.from_dict({**base, "last_target": []}).last_target == ""
        assert QueryEntry.from_dict({**base, "last_target": ["pgtest"]}).last_target == "pgtest"


class TestQueryLifecycleMigration:
    """The unified web lifecycle stays truthful and backward-compatible."""

    def test_legacy_discovery_entry_migrates_as_new_and_saved(self):
        entry = QueryEntry.from_dict(
            {
                "sql": "SELECT * FROM users",
                "hash": "abc123",
                "source": "top-historical",
                "last_target": "demo",
                "first_analyzed": "2026-08-01T10:00:00Z",
                "last_analyzed": "2026-08-02T10:00:00Z",
            }
        )

        lifecycle = entry.lifecycle_for("demo")
        assert lifecycle is not None
        assert lifecycle.first_observed_at == "2026-08-01T10:00:00Z"
        assert lifecycle.last_observed_at == "2026-08-02T10:00:00Z"
        assert lifecycle.saved_at == "2026-08-01T10:00:00Z"
        assert lifecycle.sources == ["top-historical"]
        assert entry.is_new_for("demo") is True

    def test_legacy_manual_entry_is_saved_but_not_new(self):
        entry = QueryEntry.from_dict(
            {
                "sql": "SELECT * FROM users",
                "hash": "abc123",
                "source": "manual",
                "last_target": "demo",
                "first_analyzed": "2026-08-01T10:00:00Z",
                "last_analyzed": "2026-08-01T10:00:00Z",
            }
        )

        lifecycle = entry.lifecycle_for("demo")
        assert lifecycle is not None
        assert lifecycle.saved_at == "2026-08-01T10:00:00Z"
        assert lifecycle.first_observed_at == ""
        assert entry.is_new_for("demo") is False

    def test_from_dict_does_not_mutate_serialized_input(self):
        data = {
            "sql": "SELECT 1",
            "hash": "abc123",
            "last_target": ["demo"],
        }

        QueryEntry.from_dict(data)

        assert data["last_target"] == ["demo"]
        assert "target_lifecycle" not in data


class TestQueryRegistryLifecycle:
    def _make_registry(self, tmp_path: Path) -> QueryRegistry:
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        registry.load()
        return registry

    def test_same_sql_keeps_lifecycle_for_each_target(self, tmp_path):
        registry = self._make_registry(tmp_path)
        query_hash, _ = registry.add_query(
            "SELECT * FROM users",
            source="top-historical",
            target="demo",
            observed=True,
        )
        registry.add_query(
            "SELECT * FROM users",
            source="scan",
            target="analytics",
        )

        entry = registry.get_query(query_hash)
        assert entry is not None
        assert entry.belongs_to_target("demo") is True
        assert entry.belongs_to_target("analytics") is True
        assert entry.lifecycle_for("demo").sources == ["top-historical"]
        assert entry.lifecycle_for("analytics").sources == ["scan"]

    def test_review_is_target_scoped_and_first_observation_stays_reviewed(
        self, tmp_path
    ):
        registry = self._make_registry(tmp_path)
        query_hash, _ = registry.add_query(
            "SELECT * FROM users",
            source="top-historical",
            target="demo",
            observed=True,
            save_intent=False,
        )
        assert registry.get_query(query_hash).is_new_for("demo") is True

        assert registry.mark_reviewed(
            query_hash,
            target="demo",
            reviewed_at="2026-08-03T10:00:00Z",
        )
        registry.add_query(
            "SELECT * FROM users",
            source="top-historical",
            target="demo",
            observed=True,
            save_intent=False,
        )

        entry = registry.get_query(query_hash)
        assert entry.is_new_for("demo") is False
        assert entry.lifecycle_for("demo").reviewed_at == "2026-08-03T10:00:00Z"

    def test_analysis_activity_does_not_imply_saved_intent(self, tmp_path):
        registry = self._make_registry(tmp_path)
        query_hash, _ = registry.add_query(
            "SELECT * FROM users",
            source="web",
            target="demo",
            analyzed=True,
            save_intent=False,
        )
        registry.add_query(
            "SELECT * FROM users",
            source="web",
            target="demo",
            analyzed=True,
            save_intent=False,
        )

        lifecycle = registry.get_query(query_hash).lifecycle_for("demo")
        assert lifecycle.saved_at == ""
        assert lifecycle.last_analyzed_at
        assert lifecycle.analysis_count == 2
        assert lifecycle.sources == ["web"]

    def test_target_lifecycle_persists_across_reload(self, tmp_path):
        registry_path = tmp_path / "queries.toml"
        registry = QueryRegistry(registry_path=str(registry_path))
        query_hash, _ = registry.add_query(
            "SELECT * FROM users",
            source="top-historical",
            target="demo",
            observed=True,
        )
        registry.mark_reviewed(
            query_hash,
            target="demo",
            reviewed_at="2026-08-03T10:00:00Z",
        )

        reloaded = QueryRegistry(registry_path=str(registry_path))
        reloaded.load()
        lifecycle = reloaded.get_query(query_hash).lifecycle_for("demo")
        assert lifecycle.first_observed_at
        assert lifecycle.reviewed_at == "2026-08-03T10:00:00Z"
        assert lifecycle.sources == ["top-historical"]

    def test_add_query_stars_only_when_the_caller_asks(self, tmp_path):
        """The star is the user's; an add says nothing about it by default."""
        registry = self._make_registry(tmp_path)
        query_hash, is_new = registry.add_query(
            "SELECT * FROM users",
            source="manual",
            target="demo",
        )
        chosen_hash, _ = registry.add_query(
            "SELECT * FROM orders",
            source="manual",
            target="demo",
            save_intent=True,
        )

        entry = registry.get_query(query_hash)
        assert is_new is True
        assert entry.first_analyzed
        assert entry.last_analyzed
        assert entry.lifecycle_for("demo").saved_at == ""
        assert entry.is_new_for("demo") is False
        assert registry.get_query(chosen_hash).lifecycle_for("demo").saved_at

    def test_a_re_add_keeps_the_moment_the_user_starred_it(self, tmp_path):
        registry = self._make_registry(tmp_path)
        query_hash, _ = registry.add_query(
            "SELECT * FROM users", source="web", target="demo", save_intent=True,
        )
        first_starred_at = registry.get_query(query_hash).lifecycle_for("demo").saved_at

        registry.add_query(
            "SELECT * FROM users", source="web", target="demo", save_intent=True,
        )

        lifecycle = registry.get_query(query_hash).lifecycle_for("demo")
        assert lifecycle.saved_at == first_starred_at

    def test_review_rejects_a_target_the_query_does_not_belong_to(self, tmp_path):
        registry = self._make_registry(tmp_path)
        query_hash, _ = registry.add_query(
            "SELECT * FROM users",
            source="top-historical",
            target="demo",
            observed=True,
        )

        assert registry.mark_reviewed(query_hash, target="analytics") is False
        assert registry.get_query(query_hash).lifecycle_for("analytics") is None


class TestBatchPersistence:
    def _make_registry(self, tmp_path: Path) -> QueryRegistry:
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        registry.load()
        return registry

    def _count_saves(self, monkeypatch) -> list:
        saves: list[int] = []
        original_save = QueryRegistry.save

        def counting_save(registry):
            saves.append(1)
            original_save(registry)

        monkeypatch.setattr(QueryRegistry, "save", counting_save)
        return saves

    def test_defer_save_persists_once_for_many_adds(self, tmp_path, monkeypatch):
        registry = self._make_registry(tmp_path)
        saves = self._count_saves(monkeypatch)

        with registry.defer_save():
            for table in ("users", "orders", "items"):
                registry.add_query(
                    f"SELECT * FROM {table}",
                    source="top-historical",
                    target="demo",
                    observed=True,
                    save_intent=False,
                )

        assert saves == [1]
        reloaded = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        reloaded.load()
        assert len(reloaded.list_queries()) == 3

    def test_defer_save_without_mutations_writes_nothing(self, tmp_path, monkeypatch):
        registry = self._make_registry(tmp_path)
        saves = self._count_saves(monkeypatch)

        with registry.defer_save():
            pass

        assert saves == []
        assert not (tmp_path / "queries.toml").exists()
        assert not (tmp_path / "library.db").exists()

    def test_add_query_alone_still_saves_immediately(self, tmp_path, monkeypatch):
        registry = self._make_registry(tmp_path)
        saves = self._count_saves(monkeypatch)

        registry.add_query("SELECT * FROM users", source="manual", target="demo")

        assert saves == [1]
        assert (tmp_path / "library.db").exists()

    def test_batch_matches_sequential_adds(self, tmp_path, monkeypatch):
        class FrozenDatetime:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 8, 10, 10, 0, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(
            "shared.query_registry.query_registry.datetime", FrozenDatetime
        )
        items = [
            {
                "sql": f"SELECT * FROM t{i} WHERE id = {i}",
                "source": "top-historical",
                "target": "demo",
                "observed": True,
                "save_intent": False,
            }
            for i in range(3)
        ]

        sequential = QueryRegistry(registry_path=str(tmp_path / "sequential.toml"))
        for item in items:
            sequential.add_query(**item)

        batched = QueryRegistry(registry_path=str(tmp_path / "batched.toml"))
        results = batched.add_queries_batch(items)

        assert [is_new for _, is_new in results] == [True, True, True]
        sequential.export_toml_projection()
        batched.export_toml_projection()
        assert toml.load(tmp_path / "batched.toml") == toml.load(
            tmp_path / "sequential.toml"
        )


class TestRegistrySqliteFlag:
    """RDST_REGISTRY_SQLITE gates the SQLite backend at construction time."""

    def test_unset_env_defaults_to_enabled(self, monkeypatch):
        monkeypatch.delenv("RDST_REGISTRY_SQLITE", raising=False)
        assert registry_sqlite_enabled() is True

    def test_disabling_values_are_trimmed_and_case_insensitive(self):
        for value in ("0", "false", "FALSE", " False ", "\t0\n"):
            assert registry_sqlite_enabled(value) is False, value

    def test_other_values_keep_sqlite_enabled(self):
        for value in ("", "1", "true", "yes", "no", "off"):
            assert registry_sqlite_enabled(value) is True, value

    def test_env_flag_selects_toml_backend(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RDST_REGISTRY_SQLITE", "false")
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))

        registry.add_query("SELECT * FROM users", source="manual", target="demo")

        assert (tmp_path / "queries.toml").exists()
        assert not (tmp_path / "library.db").exists()

    def test_constructor_override_beats_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RDST_REGISTRY_SQLITE", "0")
        registry = QueryRegistry(
            registry_path=str(tmp_path / "queries.toml"), use_sqlite=True
        )

        registry.add_query("SELECT * FROM users", source="manual", target="demo")

        assert (tmp_path / "library.db").exists()


class TestTomlBackendMode:
    """With SQLite disabled, queries.toml stays the authoritative store."""

    def _registry(self, tmp_path: Path) -> QueryRegistry:
        return QueryRegistry(
            registry_path=str(tmp_path / "queries.toml"), use_sqlite=False
        )

    def _count_toml_writes(self, monkeypatch) -> list:
        import shared.persistence as persistence

        writes: list[str] = []
        original_write = persistence._write_toml

        def counting_write(path, data):
            writes.append(path.name)
            original_write(path, data)

        monkeypatch.setattr(persistence, "_write_toml", counting_write)
        return writes

    def test_round_trip_without_sqlite_side_effects(self, tmp_path):
        registry = self._registry(tmp_path)
        query_hash, is_new = registry.add_query(
            "SELECT * FROM users WHERE id = 42", tag="user_lookup"
        )
        assert is_new is True
        assert query_hash in toml.load(tmp_path / "queries.toml")["queries"]

        reloaded = self._registry(tmp_path)
        entry = reloaded.get_query(query_hash)
        assert entry is not None
        assert entry.tag == "user_lookup"
        assert len(reloaded.list_queries()) == 1

        assert reloaded.remove_query(query_hash) is True
        assert toml.load(tmp_path / "queries.toml")["queries"] == {}

        file_names = {path.name for path in tmp_path.iterdir()}
        assert "library.db" not in file_names
        assert "queries.toml" in file_names

    def test_save_goes_through_the_file_lock(self, tmp_path):
        registry = self._registry(tmp_path)

        registry.add_query("SELECT * FROM users", source="manual", target="demo")

        assert (tmp_path / "queries.toml.lock").exists()

    def test_existing_toml_loads_without_migration(self, tmp_path):
        path = tmp_path / "queries.toml"
        with open(path, "w", encoding="utf-8") as handle:
            toml.dump(
                {"queries": {"cccccccccccc": {"sql": "SELECT 1", "hash": "cccccccccccc"}}},
                handle,
            )

        registry = self._registry(tmp_path)
        registry.load()

        assert registry.get_query("cccccccccccc") is not None
        assert not (tmp_path / "library.db").exists()
        assert not list(tmp_path.glob("*.bak"))

    def test_defer_save_writes_toml_once_per_batch(self, tmp_path, monkeypatch):
        registry = self._registry(tmp_path)
        writes = self._count_toml_writes(monkeypatch)

        with registry.defer_save():
            for table in ("users", "orders", "items"):
                registry.add_query(
                    f"SELECT * FROM {table}", source="manual", target="demo"
                )

        assert writes == ["queries.toml"]
        reloaded = self._registry(tmp_path)
        assert len(reloaded.list_queries()) == 3

    def test_add_queries_batch_writes_toml_once(self, tmp_path, monkeypatch):
        registry = self._registry(tmp_path)
        writes = self._count_toml_writes(monkeypatch)

        results = registry.add_queries_batch(
            [
                {"sql": f"SELECT * FROM t{i} WHERE id = {i}", "target": "demo"}
                for i in range(3)
            ]
        )

        assert [is_new for _, is_new in results] == [True, True, True]
        assert writes == ["queries.toml"]

    def test_export_fails_with_clear_message(self, tmp_path):
        registry = self._registry(tmp_path)
        registry.add_query("SELECT * FROM users", source="manual", target="demo")

        with pytest.raises(RuntimeError, match="SQLite registry is disabled"):
            registry.export_toml_projection()


class TestProceduralStatementRejection:
    """Procedural statements get a clear error, not a parse failure."""

    def test_do_block_rejected_with_honest_error(self, temp_dir):
        registry = QueryRegistry(registry_path=str(Path(temp_dir) / "queries.toml"))
        do_block = (
            "DO $$ DECLARE i int; BEGIN FOR i IN 1..300 LOOP "
            "PERFORM count(*) FROM orders WHERE customer_id = (random()*5000)::int; "
            "END LOOP; END $$"
        )

        with pytest.raises(ValueError, match="DO statements can't be saved"):
            registry.add_query(sql=do_block, source="top", target="pgtest")

    def test_call_rejected_with_honest_error(self, temp_dir):
        registry = QueryRegistry(registry_path=str(Path(temp_dir) / "queries.toml"))

        with pytest.raises(ValueError, match="CALL statements can't be saved"):
            registry.add_query(sql="CALL refresh_stats()", source="top", target="pgtest")

    def test_stacked_statements_rejected(self, temp_dir):
        """Stacked SQL must not be stored whole and replayed at re-execution sinks."""
        registry = QueryRegistry(registry_path=str(Path(temp_dir) / "queries.toml"))

        with pytest.raises(ValueError, match="Multiple statements"):
            registry.add_query(sql="SELECT 1; DROP TABLE users", source="top")

    def test_stacked_statements_rejected_when_extraction_skipped(self, temp_dir):
        """Scan-sourced entries skip completeness checking but not this one."""
        registry = QueryRegistry(registry_path=str(Path(temp_dir) / "queries.toml"))

        with pytest.raises(ValueError, match="Multiple statements"):
            registry.add_query(
                sql="SELECT 1; DROP TABLE users",
                source="scan",
                skip_param_extraction=True,
            )

    def test_schema_changing_statement_rejected(self, temp_dir):
        registry = QueryRegistry(registry_path=str(Path(temp_dir) / "queries.toml"))

        with pytest.raises(ValueError, match="DROP statements can't be saved"):
            registry.add_query(sql="DROP TABLE users", source="top")

    def test_captured_dml_is_still_storable(self, temp_dir):
        """`rdst top` legitimately captures write traffic for reporting."""
        registry = QueryRegistry(registry_path=str(Path(temp_dir) / "queries.toml"))

        query_hash, is_new = registry.add_query(
            sql="UPDATE orders SET status = 'shipped' WHERE id = 7", source="top"
        )
        assert is_new is True
        assert query_hash


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
        assert list(temp_dir.iterdir()) == []

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

    def test_concurrent_instances_merge_independent_queries(self, temp_dir):
        registry_path = temp_dir / "test_queries.toml"
        first = QueryRegistry(registry_path=str(registry_path))
        second = QueryRegistry(registry_path=str(registry_path))
        first.load()
        second.load()

        first_hash, _ = first.add_query("SELECT * FROM first_table")
        second_hash, _ = second.add_query("SELECT * FROM second_table")

        loaded = QueryRegistry(registry_path=str(registry_path))
        loaded.load()
        assert loaded.get_query(first_hash) is not None
        assert loaded.get_query(second_hash) is not None

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


DIGEST_TEXT = "SELECT * FROM `orders` WHERE `user_id` = ? AND `status` = ? LIMIT ?"
SAMPLE_TEXT = "SELECT * FROM `orders` WHERE `user_id` = 42 AND `status` = 'open' LIMIT 10"


class TestExtractObservedParams:
    """Observed-value extraction from a literal-bearing sample of a statement
    whose identity text carries engine-normalized '?' slots."""

    def test_values_follow_textual_slot_order(self):
        # normalize_and_extract names params in AST order (the LIMIT literal
        # is visited before the WHERE literals); consumers resolve the k-th
        # '?' as key 'pk', so the mapping must be re-ordered textually.
        identity = normalize_sql(DIGEST_TEXT, "mysql")
        params = extract_observed_params(SAMPLE_TEXT, identity, "mysql")
        assert params == {
            "p1": {"value": "42", "type": "number"},
            "p2": {"value": "open", "type": "string"},
            "p3": {"value": "10", "type": "number"},
        }

    def test_slot_count_mismatch_yields_no_values(self):
        # A folded IN list or truncated sample must never misalign values.
        assert (
            extract_observed_params(
                "SELECT * FROM t WHERE a = 1",
                "SELECT * FROM t WHERE a = ? AND b = ?",
            )
            == {}
        )

    def test_sample_without_literals_yields_no_values(self):
        assert (
            extract_observed_params(
                "SELECT * FROM t", "SELECT * FROM t WHERE a = ?"
            )
            == {}
        )

    def test_pg_dollar_slots_map_positionally(self):
        # pg_stat_statements numbers $N by text position, so slot k is $k
        # and values re-order textually exactly like '?' slots do.
        assert extract_observed_params(
            "SELECT id, name FROM users WHERE email = 'a@b.c' LIMIT 10",
            "SELECT id, name FROM users WHERE email = $1 LIMIT $2",
        ) == {
            "p1": {"value": "a@b.c", "type": "string"},
            "p2": {"value": "10", "type": "number"},
        }

    def test_repeated_dollar_slot_yields_no_values(self):
        # A $N sequence that is not exactly 1..n cannot be mapped safely.
        assert (
            extract_observed_params(
                "SELECT * FROM t WHERE a = 1 AND b = 1",
                "SELECT * FROM t WHERE a = $1 AND b = $1",
            )
            == {}
        )

    def test_mixed_slot_styles_yield_no_values(self):
        assert (
            extract_observed_params(
                "SELECT * FROM t WHERE a = 1 AND b = 2",
                "SELECT * FROM t WHERE a = ? AND b = $1",
            )
            == {}
        )


class TestObservedParamsInAddQuery:
    def _make_registry(self, tmp_path: Path) -> QueryRegistry:
        return QueryRegistry(registry_path=str(tmp_path / "queries.toml"))

    def test_sample_populates_observed_values(self, tmp_path):
        registry = self._make_registry(tmp_path)
        query_hash, is_new = registry.add_query(
            sql=DIGEST_TEXT,
            source="top-historical",
            target="demo",
            dialect="mysql",
            observed=True,
            save_intent=False,
            observed_params_sql=SAMPLE_TEXT,
        )
        assert is_new
        entry = registry.get_query(query_hash)
        assert entry.most_recent_params == {"p1": "42", "p2": "open", "p3": "10"}
        assert entry.parameters["p1"] == {"value": "42", "type": "number"}

    def test_identity_ignores_the_sample(self, tmp_path):
        # The hash must derive from the identity text alone. The sample of
        # the same statement shares the identity by design (placeholder
        # canonicalization), so a structurally different sample proves the
        # identity does not follow observed_params_sql.
        registry = self._make_registry(tmp_path)
        other_sample = (
            "SELECT * FROM `orders` WHERE `user_id` = 42 AND `status` = 'open'"
            " AND `region` = 'eu' LIMIT 10"
        )
        with_sample, _ = registry.add_query(
            sql=DIGEST_TEXT,
            source="top-historical",
            target="demo",
            dialect="mysql",
            observed_params_sql=other_sample,
            save_intent=False,
        )
        assert with_sample == hash_sql(DIGEST_TEXT)
        assert with_sample == hash_sql(SAMPLE_TEXT)
        assert with_sample != hash_sql(other_sample)

    def test_sql_literals_win_over_the_sample(self, tmp_path):
        registry = self._make_registry(tmp_path)
        query_hash, _ = registry.add_query(
            sql="SELECT * FROM orders WHERE user_id = 7",
            source="web",
            target="demo",
            observed_params_sql="SELECT * FROM orders WHERE user_id = 42",
        )
        entry = registry.get_query(query_hash)
        assert entry.most_recent_params == {"p1": "7"}

    def test_refresh_without_sample_keeps_observed_values(self, tmp_path):
        registry = self._make_registry(tmp_path)
        query_hash, _ = registry.add_query(
            sql=DIGEST_TEXT,
            source="top-historical",
            target="demo",
            dialect="mysql",
            observed_params_sql=SAMPLE_TEXT,
            save_intent=False,
        )
        registry.add_query(
            sql=DIGEST_TEXT,
            source="top-historical",
            target="demo",
            dialect="mysql",
            save_intent=False,
        )
        entry = registry.get_query(query_hash)
        assert entry.most_recent_params == {"p1": "42", "p2": "open", "p3": "10"}
        assert entry.parameters["p2"] == {"value": "open", "type": "string"}

    def test_newer_sample_refreshes_observed_values(self, tmp_path):
        registry = self._make_registry(tmp_path)
        query_hash, _ = registry.add_query(
            sql=DIGEST_TEXT,
            source="top-historical",
            target="demo",
            dialect="mysql",
            observed_params_sql=SAMPLE_TEXT,
            save_intent=False,
        )
        registry.add_query(
            sql=DIGEST_TEXT,
            source="top-historical",
            target="demo",
            dialect="mysql",
            observed_params_sql=(
                "SELECT * FROM `orders` WHERE `user_id` = 7"
                " AND `status` = 'closed' LIMIT 5"
            ),
            save_intent=False,
        )
        entry = registry.get_query(query_hash)
        assert entry.most_recent_params == {"p1": "7", "p2": "closed", "p3": "5"}


class TestLiveCaptureObservedValues:
    """Raw activity-lane statements carry literals; saving them must store
    the values as observed parameters (the Parameter Assistant's first rung)."""

    def test_explicit_save_of_raw_activity_text_stores_values(self, tmp_path):
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        query_hash, _ = registry.add_query(
            sql="SELECT * FROM orders WHERE user_id = 42 AND status = 'open'",
            source="top",
            target="demo",
            observed=True,
        )
        entry = registry.get_query(query_hash)
        assert entry.most_recent_params == {"p1": "42", "p2": "open"}
        assert entry.parameters == {
            "p1": {"value": "42", "type": "number"},
            "p2": {"value": "open", "type": "string"},
        }

    def test_resave_updates_observed_values(self, tmp_path):
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        query_hash, _ = registry.add_query(
            sql="SELECT * FROM orders WHERE user_id = 42",
            source="top",
            target="demo",
        )
        resaved, _ = registry.add_query(
            sql="SELECT * FROM orders WHERE user_id = 7",
            source="top",
            target="demo",
        )
        assert resaved == query_hash
        assert registry.get_query(query_hash).most_recent_params == {"p1": "7"}


class TestPlaceholderStyleIdentity:
    """One logical query keeps one identity across placeholder styles.

    Reproduces the T7B reviewer finding: an entry minted from
    pg_stat_statements text (LIMIT $1) and a web test run of the same query
    with a value (LIMIT 123, stored as LIMIT :p1) must land on one registry
    entry with its lifecycle intact and the observed value captured.
    """

    OBSERVED_TEXT = (
        "SELECT c.country, SUM(o.total) AS revenue FROM orders o "
        "JOIN customers c ON c.id = o.customer_id "
        "GROUP BY c.country ORDER BY revenue DESC LIMIT $1"
    )
    WEB_TEXT = (
        "SELECT c.country, SUM(o.total) AS revenue FROM orders o "
        "JOIN customers c ON c.id = o.customer_id "
        "GROUP BY c.country ORDER BY revenue DESC LIMIT 123"
    )

    def test_engine_and_web_texts_share_one_hash(self):
        assert hash_sql(self.OBSERVED_TEXT) == hash_sql(self.WEB_TEXT)
        assert hash_sql(self.OBSERVED_TEXT) == hash_sql(
            self.WEB_TEXT.replace("LIMIT 123", "LIMIT :p1")
        )

    def test_web_test_run_lands_on_the_observed_entry(self, tmp_path):
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        observed_hash, is_new = registry.add_query(
            sql=self.OBSERVED_TEXT,
            source="top-historical",
            target="demo",
            observed=True,
            save_intent=False,
        )
        assert is_new is True

        web_hash, web_is_new = registry.add_query(
            sql=self.WEB_TEXT, source="web", target="demo", save_intent=True,
        )
        assert web_hash == observed_hash
        assert web_is_new is False
        assert len(registry.list_queries()) == 1

        entry = registry.get_query(observed_hash)
        assert entry.most_recent_params == {"p1": "123"}
        assert entry.parameters == {"p1": {"value": "123", "type": "number"}}
        lifecycle = entry.lifecycle_for("demo")
        assert lifecycle.first_observed_at
        assert lifecycle.last_observed_at
        assert lifecycle.saved_at
        assert sorted(lifecycle.sources) == ["top-historical", "web"]
        assert entry.is_new_for("demo") is True


class TestParameterHistoryMakesQueriesRunnable:
    """Supplied values both display as recent values and drive substitution."""

    PG_TEXT = "SELECT * FROM orders WHERE customer_id = $1 LIMIT $2"

    def _registry(self, tmp_path: Path) -> QueryRegistry:
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        registry.load()
        return registry

    def _stored(self, tmp_path: Path):
        registry = self._registry(tmp_path)
        query_hash, _ = registry.add_query(
            sql=self.PG_TEXT, source="top-historical", target="demo"
        )
        return registry, query_hash

    def test_slot_identity_is_unrunnable_until_values_arrive(self, tmp_path):
        registry, query_hash = self._stored(tmp_path)

        assert registry.get_executable_query(query_hash, interactive=False) is None

    def test_stored_values_substitute_into_slots(self, tmp_path):
        registry, query_hash = self._stored(tmp_path)

        registry.update_parameter_history(
            query_hash, {"p1": "42", "p2": "10"}, source="user"
        )
        executable = registry.get_executable_query(query_hash, interactive=False)

        assert "$1" not in executable and "$2" not in executable
        assert "42" in executable
        assert executable.endswith("LIMIT 10")

    def test_partial_values_leave_the_query_unrunnable(self, tmp_path):
        registry, query_hash = self._stored(tmp_path)

        registry.update_parameter_history(query_hash, {"p1": "42"}, source="user")

        assert registry.get_executable_query(query_hash, interactive=False) is None

    def test_both_parameter_fields_are_written(self, tmp_path):
        registry, query_hash = self._stored(tmp_path)

        registry.update_parameter_history(
            query_hash, {"p1": "42", "p2": "ana"}, source="user"
        )
        entry = registry.get_query(query_hash)

        assert entry.most_recent_params == {"p1": "42", "p2": "ana"}
        assert entry.parameters == {
            "p1": {"value": 42, "type": "number", "source": "user"},
            "p2": {"value": "ana", "type": "string", "source": "user"},
        }

    def test_provenance_is_omitted_when_unknown(self, tmp_path):
        registry, query_hash = self._stored(tmp_path)

        registry.update_parameter_history(query_hash, {"p1": 42})
        entry = registry.get_query(query_hash)

        assert entry.parameters == {"p1": {"value": 42, "type": "number"}}

    def test_keys_are_canonicalized_to_slot_names(self, tmp_path):
        registry, query_hash = self._stored(tmp_path)

        registry.update_parameter_history(
            query_hash, {"param_1": 42, "$2": 10}, source="suggested"
        )
        entry = registry.get_query(query_hash)

        assert set(entry.most_recent_params) == {"p1", "p2"}
        assert registry.get_executable_query(
            query_hash, interactive=False
        ).endswith("LIMIT 10")

    def test_values_persist_across_reload(self, tmp_path):
        registry, query_hash = self._stored(tmp_path)
        registry.update_parameter_history(
            query_hash, {"p1": "42", "p2": "10"}, source="user"
        )

        reloaded = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        reloaded.load()

        assert reloaded.get_query(query_hash).parameters["p1"]["source"] == "user"

    def test_unknown_hash_is_reported(self, tmp_path):
        registry = self._registry(tmp_path)

        assert registry.update_parameter_history("deadbeef0000", {"p1": 1}) is False
