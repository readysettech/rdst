"""Tests for the SQLite query-library store behind QueryRegistry.

Covers the one-time queries.toml import (backup, reviewed_at seeding,
atomic failure), the downgrade contract for a library.db written by a
newer build, the TOML export projection, and multi-instance visibility.
"""

import io
import logging
import math
import sqlite3
import tracemalloc
from types import SimpleNamespace

import pytest
import toml

from features.query_registry import read_model
from shared.query_registry import library_store
from shared.query_registry import observation_store
from shared.query_registry.library_store import (
    SCHEMA_VERSION,
    library_db_path_for,
)
from shared.query_registry.query_registry import QueryEntry, QueryRegistry


LEGACY_TOML_DATA = {
    "queries": {
        "aaaaaaaaaaaa": {
            "sql": "SELECT * FROM users WHERE id = :p1",
            "hash": "aaaaaaaaaaaa",
            "tag": "user_lookup",
            "original_sql": "SELECT * FROM users WHERE id = 42",
            "question": "find one user",
            "first_analyzed": "2026-08-01T10:00:00Z",
            "last_analyzed": "2026-08-02T10:00:00Z",
            "frequency": 12,
            "source": "top",
            "last_target": "demo",
            "ask_target": "demo",
            "parameters": {"p1": {"value": 42, "type": "number"}},
            "most_recent_params": {"p1": 42},
            "max_duration_ms": 15.5,
            "avg_duration_ms": 3.25,
            "observation_count": 7,
            "readyset_query_id": "q_13b0714e3f57aa57",
            "readyset_supported": "yes",
            "last_cache_target": "demo-cache",
            "readyset_last_observed_at": "2026-08-02T11:00:00+00:00",
            "future_field": "written by a newer build",
            "target_lifecycle": {
                "demo": {
                    "first_observed_at": "2026-08-01T10:00:00Z",
                    "last_observed_at": "2026-08-02T10:00:00Z",
                    "reviewed_at": "",
                    "saved_at": "2026-08-01T10:00:00Z",
                    "last_analyzed_at": "2026-08-02T10:00:00Z",
                    "analysis_count": 2,
                    "last_compared_at": "",
                    "comparison_count": 0,
                    "sources": ["top", "web"],
                },
            },
        },
        # Pre-T7A shape: no target_lifecycle; from_dict synthesizes it.
        "bbbbbbbbbbbb": {
            "sql": "SELECT count(*) FROM orders",
            "hash": "bbbbbbbbbbbb",
            "tag": "",
            "first_analyzed": "2026-07-01T09:00:00Z",
            "last_analyzed": "2026-07-05T09:00:00Z",
            "frequency": 3,
            "source": "top",
            "last_target": "demo",
        },
    }
}


def _write_legacy_toml(tmp_path, data=LEGACY_TOML_DATA):
    path = tmp_path / "queries.toml"
    with open(path, "w", encoding="utf-8") as handle:
        toml.dump(data, handle)
    return path


def _user_version(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


class TestFirstLoadImport:
    def test_import_preserves_every_field(self, tmp_path):
        toml_path = _write_legacy_toml(tmp_path)
        registry = QueryRegistry(registry_path=str(toml_path))
        registry.load()

        entry = registry.get_query("aaaaaaaaaaaa")
        source = LEGACY_TOML_DATA["queries"]["aaaaaaaaaaaa"]
        assert entry.sql == source["sql"]
        assert entry.tag == source["tag"]
        assert entry.original_sql == source["original_sql"]
        assert entry.question == source["question"]
        assert entry.first_analyzed == source["first_analyzed"]
        assert entry.last_analyzed == source["last_analyzed"]
        assert entry.frequency == source["frequency"]
        assert entry.source == source["source"]
        assert entry.last_target == source["last_target"]
        assert entry.ask_target == source["ask_target"]
        assert entry.parameters == source["parameters"]
        assert entry.most_recent_params == source["most_recent_params"]
        assert entry.max_duration_ms == source["max_duration_ms"]
        assert entry.avg_duration_ms == source["avg_duration_ms"]
        assert entry.observation_count == source["observation_count"]
        assert entry.readyset_query_id == source["readyset_query_id"]
        assert entry.readyset_supported == source["readyset_supported"]
        assert entry.last_cache_target == source["last_cache_target"]
        assert entry.readyset_last_observed_at == source["readyset_last_observed_at"]
        lifecycle = entry.lifecycle_for("demo")
        assert lifecycle.sources == ["top", "web"]
        assert lifecycle.analysis_count == 2
        assert lifecycle.saved_at == "2026-08-01T10:00:00Z"

        legacy = registry.get_query("bbbbbbbbbbbb")
        assert legacy.sql == "SELECT count(*) FROM orders"
        assert legacy.lifecycle_for("demo") is not None

    def test_import_normalizes_nonfinite_metrics_before_minting_cursors(
        self, tmp_path
    ):
        target = "nonfinite"
        entries = {}
        for index in range(2):
            query_hash = f"{index:012x}"
            entries[query_hash] = {
                "hash": query_hash,
                "sql": f"SELECT {index} FROM imported_metrics",
                "source": "manual",
                "frequency": float("inf"),
                "observation_count": float("inf"),
                "max_duration_ms": float("inf"),
                "avg_duration_ms": float("inf"),
                "target_lifecycle": {
                    target: {
                        "saved_at": "2026-08-01T00:00:00Z",
                        "sources": ["manual"],
                    }
                },
            }
        toml_path = _write_legacy_toml(tmp_path, {"queries": entries})
        store = library_store.LibraryStore(
            library_db_path_for(toml_path), toml_path
        )

        imported = store.load_all()
        for entry in imported.values():
            assert entry["frequency"] == 0
            assert entry["observation_count"] == 0
            assert entry["max_duration_ms"] == 0.0
            assert entry["avg_duration_ms"] == 0.0

        for sort in library_store._SORT_COLUMNS:
            page, _, total, next_position = store.query_library(
                target=target,
                search="",
                view="all",
                source="all",
                params="all",
                activity="all",
                impact="all",
                sort=sort,
                cursor_position=None,
                limit=1,
                now_ms=0,
            )
            assert total == 2
            assert len(page) == 1
            assert next_position is not None
            assert math.isfinite(next_position[0])
            cursor = read_model.encode_cursor(
                next_position[0], next_position[1], "nonfinite-import"
            )
            assert read_model.decode_cursor(cursor, "nonfinite-import") == next_position

    def test_import_seeds_reviewed_at_for_every_pair(self, tmp_path):
        toml_path = _write_legacy_toml(tmp_path)
        registry = QueryRegistry(registry_path=str(toml_path))
        registry.load()

        for entry in registry.list_queries():
            for lifecycle in entry.target_lifecycle.values():
                assert lifecycle.reviewed_at
            for target in entry.target_lifecycle:
                assert entry.is_new_for("" if target == "__global__" else target) is False

    def test_import_writes_backup_and_leaves_toml(self, tmp_path):
        toml_path = _write_legacy_toml(tmp_path)
        original_bytes = toml_path.read_bytes()
        registry = QueryRegistry(registry_path=str(toml_path))
        registry.load()

        backup = tmp_path / f"queries.toml.pre-sqlite-{SCHEMA_VERSION}.bak"
        assert backup.read_bytes() == original_bytes
        assert toml_path.read_bytes() == original_bytes
        db_path = library_db_path_for(toml_path)
        assert db_path.exists()
        assert _user_version(db_path) == SCHEMA_VERSION

    def test_existing_backup_is_never_overwritten(self, tmp_path):
        toml_path = _write_legacy_toml(tmp_path)
        backup = tmp_path / f"queries.toml.pre-sqlite-{SCHEMA_VERSION}.bak"
        backup.write_text("sentinel from an earlier attempt")

        registry = QueryRegistry(registry_path=str(toml_path))
        registry.load()

        assert backup.read_text() == "sentinel from an earlier attempt"
        assert registry.get_query("aaaaaaaaaaaa") is not None

    def test_second_load_does_not_reimport(self, tmp_path):
        toml_path = _write_legacy_toml(tmp_path)
        registry = QueryRegistry(registry_path=str(toml_path))
        registry.load()
        assert registry.remove_query("bbbbbbbbbbbb") is True

        # The stale TOML still lists the removed entry; the store must not
        # re-read it once user_version is set.
        assert "bbbbbbbbbbbb" in toml.load(toml_path)["queries"]
        reloaded = QueryRegistry(registry_path=str(toml_path))
        reloaded.load()
        assert reloaded.get_query("bbbbbbbbbbbb") is None
        assert reloaded.get_query("aaaaaaaaaaaa") is not None

    def test_failed_import_leaves_toml_authoritative(self, tmp_path):
        broken = {"queries": {"cccccccccccc": {"hash": "cccccccccccc"}}}
        toml_path = _write_legacy_toml(tmp_path, broken)
        original_bytes = toml_path.read_bytes()

        registry = QueryRegistry(registry_path=str(toml_path))
        with pytest.raises(RuntimeError, match="Failed to load query registry"):
            registry.load()

        assert toml_path.read_bytes() == original_bytes
        assert _user_version(library_db_path_for(toml_path)) == 0

        # A corrected TOML migrates cleanly on the next attempt.
        _write_legacy_toml(tmp_path)
        retry = QueryRegistry(registry_path=str(toml_path))
        retry.load()
        assert retry.get_query("aaaaaaaaaaaa") is not None

    def test_fresh_install_load_creates_no_files(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        toml_path = data_dir / "queries.toml"
        registry = QueryRegistry(registry_path=str(toml_path))
        registry.load()
        assert registry.list_queries() == []
        assert list(data_dir.iterdir()) == []

        # Repeated load/read cycles stay empty and still create nothing.
        registry.load()
        assert registry.list_queries() == []
        other = QueryRegistry(registry_path=str(toml_path))
        other.load()
        assert other.list_queries() == []
        assert list(data_dir.iterdir()) == []

    def test_first_write_creates_schema_and_entry(self, tmp_path):
        toml_path = tmp_path / "queries.toml"
        registry = QueryRegistry(registry_path=str(toml_path))
        registry.load()
        assert not library_db_path_for(toml_path).exists()

        query_hash, is_new = registry.add_query("SELECT 1", source="manual")
        assert is_new is True
        db_path = library_db_path_for(toml_path)
        assert db_path.exists()
        assert _user_version(db_path) == SCHEMA_VERSION
        reloaded = QueryRegistry(registry_path=str(toml_path))
        reloaded.load()
        assert reloaded.get_query(query_hash) is not None
        assert not toml_path.exists()
        assert not list(tmp_path.glob("*.bak"))

    def test_sqlite_backend_is_the_default(self, tmp_path, monkeypatch):
        """With RDST_REGISTRY_SQLITE unset, SQLite stays authoritative."""
        monkeypatch.delenv("RDST_REGISTRY_SQLITE", raising=False)
        toml_path = tmp_path / "queries.toml"
        registry = QueryRegistry(registry_path=str(toml_path))
        assert registry.sqlite_enabled is True

        registry.add_query("SELECT 1", source="manual")
        assert library_db_path_for(toml_path).exists()
        assert not toml_path.exists()


class TestDowngradeContract:
    def _bump_version(self, tmp_path):
        db_path = library_db_path_for(tmp_path / "queries.toml")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 99")
        finally:
            conn.close()

    def test_newer_store_refuses_writes_but_serves_reads(self, tmp_path):
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        query_hash, _ = registry.add_query("SELECT 1", source="manual")
        self._bump_version(tmp_path)

        downgraded = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        downgraded.load()
        assert downgraded.get_query(query_hash) is not None

        with pytest.raises(RuntimeError, match="newer"):
            downgraded.add_query("SELECT 2", source="manual")
        with pytest.raises(RuntimeError, match="newer"):
            downgraded.remove_query(query_hash)

    def test_newer_store_still_exports(self, tmp_path):
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        query_hash, _ = registry.add_query("SELECT 1", source="manual")
        self._bump_version(tmp_path)

        downgraded = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        path, count = downgraded.export_toml_projection(
            str(tmp_path / "recovered.toml")
        )
        assert count == 1
        assert query_hash in toml.load(path)["queries"]


def _journal_mode(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


class TestSqliteRuntimeCheck:
    def _fake_version(self, monkeypatch, version_info, version):
        monkeypatch.setattr(library_store, "_strict_available", None)
        monkeypatch.setattr(observation_store, "_unsafe_wal_warned", False)
        monkeypatch.setattr(observation_store, "_delete_fallback_warned", False)
        monkeypatch.delenv("RDST_UNSAFE_SQLITE_OK", raising=False)
        monkeypatch.setattr(sqlite3, "sqlite_version_info", version_info)
        monkeypatch.setattr(sqlite3, "sqlite_version", version)

    @pytest.mark.parametrize(
        ("version_info", "version"),
        [
            ((3, 44, 5), "3.44.5"),
            ((3, 47, 1), "3.47.1"),
            ((3, 50, 6), "3.50.6"),
            ((3, 51, 2), "3.51.2"),
        ],
    )
    def test_affected_wal_runtime_falls_back_to_delete(
        self, tmp_path, monkeypatch, caplog, version_info, version
    ):
        self._fake_version(monkeypatch, version_info, version)
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        with caplog.at_level(logging.WARNING, logger=observation_store.logger.name):
            query_hash, _ = registry.add_query("SELECT 1", source="manual")
        assert registry.get_query(query_hash) is not None
        db_path = library_db_path_for(tmp_path / "queries.toml")
        assert _journal_mode(db_path) == "delete"
        warnings = [
            r.getMessage()
            for r in caplog.records
            if "rollback-journal" in r.getMessage()
        ]
        assert len(warnings) == 1
        assert version in warnings[0]

    @pytest.mark.parametrize(
        ("version_info", "version"),
        [
            ((3, 44, 6), "3.44.6"),
            ((3, 50, 7), "3.50.7"),
            ((3, 51, 3), "3.51.3"),
            ((3, 52, 0), "3.52.0"),
        ],
    )
    def test_fixed_wal_runtimes_use_wal(
        self, tmp_path, monkeypatch, version_info, version
    ):
        self._fake_version(monkeypatch, version_info, version)
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        registry.add_query("SELECT 1", source="manual")
        db_path = library_db_path_for(tmp_path / "queries.toml")
        assert db_path.exists()
        assert _journal_mode(db_path) == "wal"

    def test_existing_library_switches_journal_both_directions(
        self, tmp_path, monkeypatch
    ):
        toml_path = str(tmp_path / "queries.toml")
        db_path = library_db_path_for(tmp_path / "queries.toml")
        query_hash, _ = QueryRegistry(registry_path=toml_path).add_query(
            "SELECT 1", source="manual"
        )
        assert _journal_mode(db_path) == "wal"

        self._fake_version(monkeypatch, (3, 40, 0), "3.40.0")
        downgraded = QueryRegistry(registry_path=toml_path)
        assert downgraded.get_query(query_hash) is not None
        second_hash, _ = downgraded.add_query("SELECT 2", source="manual")
        assert _journal_mode(db_path) == "delete"

        self._fake_version(monkeypatch, (3, 51, 3), "3.51.3")
        upgraded = QueryRegistry(registry_path=toml_path)
        assert upgraded.get_query(query_hash) is not None
        assert upgraded.get_query(second_hash) is not None
        upgraded.add_query("SELECT 3", source="manual")
        assert _journal_mode(db_path) == "wal"

    def test_version_floor_still_raises(self, tmp_path, monkeypatch):
        self._fake_version(monkeypatch, (3, 34, 1), "3.34.1")
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        registry.load()
        with pytest.raises(RuntimeError, match="too old"):
            registry.add_query("SELECT 1", source="manual")

    @pytest.mark.parametrize("override", ["1", "true", " TRUE "])
    def test_unsafe_override_forces_wal_with_warning(
        self, tmp_path, monkeypatch, caplog, override
    ):
        self._fake_version(monkeypatch, (3, 40, 0), "3.40.0")
        monkeypatch.setenv("RDST_UNSAFE_SQLITE_OK", override)
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        with caplog.at_level(logging.WARNING, logger=observation_store.logger.name):
            query_hash, _ = registry.add_query("SELECT 1", source="manual")
        assert registry.get_query(query_hash) is not None
        db_path = library_db_path_for(tmp_path / "queries.toml")
        assert _journal_mode(db_path) == "wal"
        warnings = [
            r.getMessage()
            for r in caplog.records
            if "WAL corruption risk accepted" in r.getMessage()
        ]
        assert len(warnings) == 1
        assert "3.40.0" in warnings[0]

    def test_unrecognized_override_value_falls_back_to_delete(
        self, tmp_path, monkeypatch
    ):
        self._fake_version(monkeypatch, (3, 40, 0), "3.40.0")
        monkeypatch.setenv("RDST_UNSAFE_SQLITE_OK", "yes")
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        registry.add_query("SELECT 1", source="manual")
        db_path = library_db_path_for(tmp_path / "queries.toml")
        assert _journal_mode(db_path) == "delete"

    def test_override_does_not_bypass_version_floor(self, tmp_path, monkeypatch):
        self._fake_version(monkeypatch, (3, 34, 1), "3.34.1")
        monkeypatch.setenv("RDST_UNSAFE_SQLITE_OK", "1")
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        registry.load()
        with pytest.raises(RuntimeError, match="too old"):
            registry.add_query("SELECT 1", source="manual")


class TestNetworkFilesystemRefusal:
    def test_linux_decodes_escaped_network_mount_path(self, monkeypatch):
        mounts = "server:/volume /mnt/team\\040share nfs rw 0 0\n"
        monkeypatch.setattr(observation_store.sys, "platform", "linux")
        monkeypatch.setattr(
            "builtins.open",
            lambda *args, **kwargs: io.StringIO(mounts),
        )

        with pytest.raises(RuntimeError, match="nfs network filesystem"):
            observation_store._refuse_network_path(
                "/mnt/team share/rdst/library.db"
            )

    def test_macos_probe_climbs_to_existing_network_mount(
        self, tmp_path, monkeypatch
    ):
        probed = []

        def fake_run(command, **kwargs):
            probed.append(command)
            return SimpleNamespace(stdout="nfs\n")

        monkeypatch.setattr(observation_store.sys, "platform", "darwin")
        monkeypatch.setattr(observation_store.subprocess, "run", fake_run)

        nested = tmp_path / "missing" / "nested" / "library.db"
        with pytest.raises(RuntimeError, match="network filesystem"):
            observation_store._refuse_network_path(str(nested))

        assert probed == [["stat", "-f", "%T", str(tmp_path)]]

    def test_macos_local_filesystem_is_accepted(self, tmp_path, monkeypatch):
        monkeypatch.setattr(observation_store.sys, "platform", "darwin")
        monkeypatch.setattr(
            observation_store.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(stdout="apfs\n"),
        )

        observation_store._refuse_network_path(str(tmp_path / "library.db"))


class TestExportProjection:
    def test_export_round_trips_with_legacy_reader(self, tmp_path):
        toml_path = _write_legacy_toml(tmp_path)
        registry = QueryRegistry(registry_path=str(toml_path))
        registry.load()

        path, count = registry.export_toml_projection(str(tmp_path / "export.toml"))
        assert count == 2
        exported = toml.load(path)["queries"]
        assert set(exported) == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}
        # Fields written only by a newer build survive the store round trip.
        assert exported["aaaaaaaaaaaa"]["future_field"] == (
            "written by a newer build"
        )
        for query_hash, data in exported.items():
            assert (
                QueryEntry.from_dict(data).to_dict()
                == registry.get_query(query_hash).to_dict()
            )

    def test_export_defaults_to_registry_path(self, tmp_path):
        registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        query_hash, _ = registry.add_query("SELECT 1", source="manual")

        path, count = registry.export_toml_projection()
        assert path == tmp_path / "queries.toml"
        assert count == 1
        assert query_hash in toml.load(path)["queries"]

    def test_export_cli_subcommand(self, tmp_path):
        from features.query_registry.cli.command import QueryCommand

        command = QueryCommand()
        command.registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
        query_hash, _ = command.registry.add_query("SELECT 1", source="manual")

        result = command.execute("export", output=str(tmp_path / "out.toml"))
        assert result.ok is True
        assert result.data["count"] == 1
        assert query_hash in toml.load(tmp_path / "out.toml")["queries"]

        rejected = command.execute("export", format="csv")
        assert rejected.ok is False


class TestMultiInstance:
    def test_two_instances_see_each_others_writes(self, tmp_path):
        path = str(tmp_path / "queries.toml")
        first = QueryRegistry(registry_path=path)
        second = QueryRegistry(registry_path=path)

        first_hash, _ = first.add_query("SELECT * FROM first_table")
        second.load()
        assert second.get_query(first_hash) is not None

        second_hash, _ = second.add_query("SELECT * FROM second_table")
        first.load()
        assert first.get_query(first_hash) is not None
        assert first.get_query(second_hash) is not None


class TestReadModelMigration:
    def test_v6_backfill_streams_large_registry_in_bounded_memory(self, tmp_path):
        row_count = 50_000
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        conn = sqlite3.connect(db_path)
        try:
            for statement in library_store._MIGRATIONS[1](True):
                conn.execute(statement)
            conn.executemany(
                "INSERT INTO query_identity "
                "(hash, sql, source, last_target, frequency) VALUES (?, ?, ?, ?, ?)",
                (
                    (
                        f"{index:012x}",
                        f"SELECT * FROM migration_table_{index}",
                        "top-historical",
                        "large-migration",
                        index,
                    )
                    for index in range(row_count)
                ),
            )
            conn.executemany(
                "INSERT INTO target_query "
                "(identity_id, target_key, first_observed_at, "
                "last_observed_at, sources) VALUES (?, ?, ?, ?, ?)",
                (
                    (
                        index + 1,
                        "large-migration",
                        "2026-08-01T00:00:00Z",
                        "2026-08-01T00:00:00Z",
                        '["top-historical"]',
                    )
                    for index in range(row_count)
                ),
            )
            conn.execute("PRAGMA user_version = 6")
            conn.commit()
        finally:
            conn.close()

        store = library_store.LibraryStore(db_path, toml_path)
        # A regression to load_all/N+1 fails immediately, while tracemalloc
        # verifies the streaming batch size rather than registry size controls
        # Python memory during the real 50k-row migration.
        store._load_all = lambda conn: (_ for _ in ()).throw(
            AssertionError("v7 migration must not load the full registry")
        )
        tracemalloc.start()
        try:
            store.ensure_open()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert _user_version(db_path) == SCHEMA_VERSION
        assert peak < 32 * 1024 * 1024
        page, facets, total, cursor = store.query_library(
            target="large-migration",
            search="",
            view="all",
            source="all",
            params="all",
            activity="all",
            impact="all",
            sort="most-frequent",
            cursor_position=None,
            limit=50,
            now_ms=0,
        )
        assert total == row_count
        assert facets["source"]["observed"] == row_count
        assert len(page) == 50
        assert cursor is not None


class TestSystemEntryPrune:
    """Schema v2 cleanup of auto-observed system-catalog entries."""

    POLLUTED = {
        "queries": {
            "cccccccccccc": {
                "sql": "SELECT * FROM pg_stat_user_indexes",
                "hash": "cccccccccccc",
                "original_sql": "SELECT * FROM pg_stat_user_indexes",
                "source": "top-historical",
                "frequency": 900,
                # Mere observation writes these legacy fields on every
                # add_query; they must not shield the entry from the prune.
                "first_analyzed": "2026-08-01T10:00:00Z",
                "last_analyzed": "2026-08-02T10:00:00Z",
                "target_lifecycle": {
                    "demo": {
                        "first_observed_at": "2026-08-01T10:00:00Z",
                        "last_observed_at": "2026-08-02T10:00:00Z",
                        "sources": ["top-historical"],
                    },
                },
            },
            # Catalog-only but genuinely analyzed (truthful lifecycle field):
            # must survive.
            "abababababab": {
                "sql": "SELECT * FROM pg_stat_activity",
                "hash": "abababababab",
                "original_sql": "SELECT * FROM pg_stat_activity",
                "source": "top-historical",
                "target_lifecycle": {
                    "demo": {
                        "first_observed_at": "2026-08-01T10:00:00Z",
                        "last_analyzed_at": "2026-08-02T10:00:00Z",
                        "analysis_count": 1,
                        "sources": ["top-historical"],
                    },
                },
            },
            # Pre-T7A synthesis artifact: saved_at stamped equal to
            # first_analyzed by the import compatibility path, catalog-only,
            # auto-sourced. Not real save intent; must be pruned.
            "acacacacacac": {
                "sql": "SELECT oid, md5(rolsuper::text) FROM pg_roles",
                "hash": "acacacacacac",
                "original_sql": "SELECT oid, md5(rolsuper::text) FROM pg_roles",
                "source": "top-historical",
                "first_analyzed": "2026-08-01T09:00:00Z",
                "last_analyzed": "2026-08-02T09:00:00Z",
                "target_lifecycle": {
                    "demo": {
                        "first_observed_at": "2026-08-01T09:00:00Z",
                        "saved_at": "2026-08-01T09:00:00Z",
                        "sources": ["top-historical"],
                    },
                },
            },
            # Catalog-only but user-curated (saved): must survive.
            "dddddddddddd": {
                "sql": "SELECT * FROM pg_tables",
                "hash": "dddddddddddd",
                "original_sql": "SELECT * FROM pg_tables",
                "source": "top-historical",
                "target_lifecycle": {
                    "demo": {
                        "first_observed_at": "2026-08-01T10:00:00Z",
                        "saved_at": "2026-08-03T10:00:00Z",
                        "sources": ["top-historical"],
                    },
                },
            },
            # Manual source, catalog-only: user intent, must survive.
            "ffffffffffff": {
                "sql": "SELECT * FROM information_schema.tables",
                "hash": "ffffffffffff",
                "original_sql": "SELECT * FROM information_schema.tables",
                "source": "manual",
                "target_lifecycle": {
                    "demo": {"sources": ["manual"]},
                },
            },
            # Observed user query: must survive.
            "eeeeeeeeeeee": {
                "sql": "SELECT * FROM users WHERE id = :p1",
                "hash": "eeeeeeeeeeee",
                "original_sql": "SELECT * FROM users WHERE id = 7",
                "source": "top-historical",
                "target_lifecycle": {
                    "demo": {"sources": ["top-historical"]},
                },
            },
        }
    }

    def test_import_prunes_uncurated_system_entries(self, tmp_path, caplog):
        toml_path = _write_legacy_toml(tmp_path, self.POLLUTED)
        store = library_store.LibraryStore(
            library_db_path_for(toml_path), toml_path
        )
        with caplog.at_level(logging.INFO, logger=library_store.logger.name):
            entries = store.load_all()
        assert "cccccccccccc" not in entries
        assert "acacacacacac" not in entries
        assert set(entries) == {
            "dddddddddddd",
            "ffffffffffff",
            "eeeeeeeeeeee",
            "abababababab",
        }
        assert _user_version(store.path) == SCHEMA_VERSION
        assert any("cccccccccccc" in record.message for record in caplog.records)

    def test_v2_store_upgrade_reprunes(self, tmp_path):
        """A store already stamped v2 by the ineffective first cleanup gets
        the corrected prune at v3."""
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        store = library_store.LibraryStore(db_path, toml_path)
        store.apply_changes({}, {})
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO query_identity "
                "(hash, sql, original_sql, source, last_analyzed) "
                "VALUES ('cccccccccccc', 'SELECT * FROM pg_tables', "
                "'SELECT * FROM pg_tables', 'top-historical', "
                "'2026-08-02T10:00:00Z')"
            )
            conn.execute("PRAGMA user_version = 2")
            conn.commit()
        finally:
            conn.close()

        entries = library_store.LibraryStore(db_path, toml_path).load_all()
        assert "cccccccccccc" not in entries
        assert _user_version(db_path) == SCHEMA_VERSION

    def test_v1_store_upgrade_prunes(self, tmp_path):
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        store = library_store.LibraryStore(db_path, toml_path)
        store.apply_changes({}, {})  # first write creates the v2 schema
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO query_identity (hash, sql, original_sql, source) "
                "VALUES ('cccccccccccc', 'SELECT * FROM pg_stat_activity', "
                "'SELECT * FROM pg_stat_activity', 'top-historical')"
            )
            identity_id = conn.execute(
                "SELECT id FROM query_identity WHERE hash = 'cccccccccccc'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO target_query (identity_id, target_key, sources) "
                "VALUES (?, 'demo', '[\"top-historical\"]')",
                (identity_id,),
            )
            conn.execute(
                "INSERT INTO query_identity (hash, sql, original_sql, source) "
                "VALUES ('eeeeeeeeeeee', 'SELECT * FROM users', "
                "'SELECT * FROM users', 'top-historical')"
            )
            conn.execute("PRAGMA user_version = 1")
            conn.commit()
        finally:
            conn.close()

        fresh = library_store.LibraryStore(db_path, toml_path)
        entries = fresh.load_all()
        assert "cccccccccccc" not in entries
        assert "eeeeeeeeeeee" in entries
        assert _user_version(db_path) == SCHEMA_VERSION

    def test_v4_store_upgrade_prunes_self_traffic_flood(self, tmp_path):
        """v5 catches RDST's own text-fetch statements and utility commands
        that the AST parser cannot classify (the self-amplifying flood)."""
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        store = library_store.LibraryStore(db_path, toml_path)
        store.apply_changes({}, {})
        flood = [
            (
                "1111111111aa",
                "SELECT userid, dbid, queryid, toplevel, query "
                "FROM pg_stat_statements WHERE queryid = ANY($1::bigint[]) AND dbid = $2",
            ),
            ("1111111111bb", "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"),
            (
                "1111111111cc",
                "SELECT t.tableoid, t.oid FROM pg_catalog.pg_index i "
                "JOIN pg_catalog.pg_class t ON (t.oid = i.indexrelid)",
            ),
        ]
        conn = sqlite3.connect(db_path)
        try:
            for query_hash, sql in flood:
                conn.execute(
                    "INSERT INTO query_identity (hash, sql, original_sql, source) "
                    "VALUES (?, ?, ?, 'top-historical')",
                    (query_hash, sql, sql),
                )
            conn.execute(
                "INSERT INTO query_identity (hash, sql, original_sql, source) "
                "VALUES ('1111111111dd', 'SELECT * FROM orders WHERE ids = ANY($1::bigint[])', "
                "'SELECT * FROM orders WHERE ids = ANY($1::bigint[])', 'top-historical')"
            )
            conn.execute("PRAGMA user_version = 4")
            conn.commit()
        finally:
            conn.close()

        entries = library_store.LibraryStore(db_path, toml_path).load_all()
        assert set(entries) == {"1111111111dd"}
        assert _user_version(db_path) == SCHEMA_VERSION


class TestPlaceholderHashMigration:
    """Schema v8: re-key and merge identities split by placeholder style."""

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

    @staticmethod
    def _previous_hash(text):
        """The stored hash a pre-v8 build minted for this text."""
        from shared.query_registry.query_registry import _sql_digest, normalize_sql

        return _sql_digest(normalize_sql(text))

    def _seed_v7(self, tmp_path, entries):
        """Write entries into a current-schema store, then stamp it v7."""
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        store = library_store.LibraryStore(db_path, toml_path)
        store.apply_changes({}, entries)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 7")
            conn.commit()
        finally:
            conn.close()
        return db_path, toml_path

    def _duplicate_pair(self):
        from shared.query_registry.query_registry import hash_sql, normalize_sql

        observed_hash = self._previous_hash(self.OBSERVED_TEXT)
        canonical = hash_sql(self.OBSERVED_TEXT)
        assert canonical == hash_sql(self.WEB_TEXT)
        assert canonical != observed_hash
        return {
            observed_hash: {
                "hash": observed_hash,
                "sql": normalize_sql(self.OBSERVED_TEXT),
                "original_sql": self.OBSERVED_TEXT,
                "source": "top-historical",
                "frequency": 40,
                "observation_count": 40,
                "avg_duration_ms": 12.0,
                "max_duration_ms": 30.0,
                "target_lifecycle": {
                    "demo": {
                        "first_observed_at": "2026-08-16T17:00:00Z",
                        "last_observed_at": "2026-08-16T17:30:00Z",
                        "sources": ["top-historical"],
                    },
                },
            },
            canonical: {
                "hash": canonical,
                "sql": normalize_sql(self.WEB_TEXT),
                "original_sql": self.WEB_TEXT,
                "source": "web",
                "parameters": {"p1": {"value": "123", "type": "number"}},
                "most_recent_params": {"p1": "123"},
                "target_lifecycle": {
                    "demo": {
                        "saved_at": "2026-08-16T17:38:05Z",
                        "sources": ["web"],
                    },
                },
            },
        }, observed_hash, canonical

    def test_duplicate_pair_merges_into_the_curated_survivor(
        self, tmp_path, caplog
    ):
        entries, observed_hash, canonical = self._duplicate_pair()
        db_path, toml_path = self._seed_v7(tmp_path, entries)

        with caplog.at_level(logging.INFO, logger=library_store.logger.name):
            merged = library_store.LibraryStore(db_path, toml_path).load_all()

        assert set(merged) == {canonical}
        assert _user_version(db_path) == SCHEMA_VERSION
        entry = merged[canonical]
        # The web row carries the curation (saved intent, captured params)
        # and survives; the observed row's evidence is unioned in.
        assert entry["source"] == "web"
        assert entry["most_recent_params"] == {"p1": "123"}
        assert entry["parameters"] == {"p1": {"value": "123", "type": "number"}}
        assert entry["frequency"] == 40
        assert entry["observation_count"] == 40
        assert entry["avg_duration_ms"] == 12.0
        lifecycle = entry["target_lifecycle"]["demo"]
        assert lifecycle["first_observed_at"] == "2026-08-16T17:00:00Z"
        assert lifecycle["last_observed_at"] == "2026-08-16T17:30:00Z"
        assert lifecycle["saved_at"] == "2026-08-16T17:38:05Z"
        assert sorted(lifecycle["sources"]) == ["top-historical", "web"]
        assert any(
            "Merged duplicate query identities" in record.getMessage()
            and observed_hash in record.getMessage()
            for record in caplog.records
        )

    def test_singleton_engine_row_is_rekeyed(self, tmp_path):
        from shared.query_registry.query_registry import hash_sql, normalize_sql

        observed_hash = self._previous_hash(self.OBSERVED_TEXT)
        canonical = hash_sql(self.OBSERVED_TEXT)
        entries = {
            observed_hash: {
                "hash": observed_hash,
                "sql": normalize_sql(self.OBSERVED_TEXT),
                "original_sql": self.OBSERVED_TEXT,
                "source": "top-historical",
                "target_lifecycle": {
                    "demo": {
                        "first_observed_at": "2026-08-16T17:00:00Z",
                        "sources": ["top-historical"],
                    },
                },
            },
        }
        db_path, toml_path = self._seed_v7(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()
        assert set(migrated) == {canonical}
        lifecycle = migrated[canonical]["target_lifecycle"]["demo"]
        assert lifecycle["first_observed_at"] == "2026-08-16T17:00:00Z"

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT hash, rm_hash FROM query_identity "
                               "JOIN target_query ON identity_id = id").fetchone()
        finally:
            conn.close()
        assert row["hash"] == canonical
        assert row["rm_hash"] == canonical

    def test_untouched_rows_keep_their_hashes(self, tmp_path):
        from shared.query_registry.query_registry import hash_sql

        placeholder_free = hash_sql("SELECT count(*) FROM orders")
        entries = {
            # Already canonical: minted by the current derivation.
            hash_sql(self.WEB_TEXT): {
                "hash": hash_sql(self.WEB_TEXT),
                "sql": "SELECT 1 FROM placeholder_rows LIMIT :p1",
                "original_sql": self.WEB_TEXT,
                "source": "web",
            },
            # Placeholder-free text: no style to canonicalize.
            placeholder_free: {
                "hash": placeholder_free,
                "sql": "SELECT count(*) FROM orders",
                "original_sql": "SELECT count(*) FROM orders",
                "source": "manual",
            },
            # Engine-style text under a hash matching neither derivation
            # (hand-imported): precious data stays untouched.
            "abcdefabcdef": {
                "hash": "abcdefabcdef",
                "sql": "SELECT a FROM foreign_rows WHERE b = $1",
                "original_sql": "SELECT a FROM foreign_rows WHERE b = $1",
                "source": "manual",
            },
        }
        db_path, toml_path = self._seed_v7(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()
        assert set(migrated) == set(entries)

    def test_migration_is_idempotent(self, tmp_path):
        entries, _, _ = self._duplicate_pair()
        db_path, toml_path = self._seed_v7(tmp_path, entries)
        first = library_store.LibraryStore(db_path, toml_path).load_all()

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 7")
            conn.commit()
        finally:
            conn.close()
        second = library_store.LibraryStore(db_path, toml_path).load_all()
        assert second == first
        assert _user_version(db_path) == SCHEMA_VERSION
