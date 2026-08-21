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


def _identity_rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in conn.execute("SELECT * FROM query_identity ORDER BY hash")
        ]
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
        # v12 takes the saved catalog row too: saving is not the investment
        # that makes a relation-free statement worth keeping.
        assert "dddddddddddd" not in entries
        assert set(entries) == {
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


class TestPlaceholderArtifactRepair:
    """Schema v9: repair identities written by the digit-lifting normalizer.

    That normalizer extracted the digit of `$N` parameters as a literal
    value, storing fused `$:pN` text, the lifted digits as parameter
    "observations", and hashes derived from the damaged text (which the v8
    guard could not recognize).
    """

    RAW_TEXT = "SELECT id, name FROM users WHERE org_id = $1 LIMIT $2"
    CORRUPTED_SQL = "SELECT id, name FROM users WHERE org_id = $:p1 LIMIT $:p2"
    ORPHAN_CORRUPTED_SQL = "SELECT sku FROM items WHERE vendor_id = $:p1"
    ORPHAN_FOLDED_SQL = "SELECT sku FROM items WHERE vendor_id = :p1"
    ZOMBIE_RAW = "SELECT status, total FROM orders WHERE customer_id = $1 LIMIT $2"
    ZOMBIE_CORRUPTED_SQL = (
        "SELECT status, total FROM orders WHERE customer_id = $:p1 LIMIT $:p2"
    )
    POISONED_PARAMS = {
        "p1": {"value": "1", "type": "number"},
        "p2": {"value": "2", "type": "number"},
    }

    def _seed_v8(self, tmp_path, entries):
        """Write entries into a current-schema store, then stamp it v8."""
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        store = library_store.LibraryStore(db_path, toml_path)
        store.apply_changes({}, entries)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 8")
            conn.commit()
        finally:
            conn.close()
        return db_path, toml_path

    @staticmethod
    def _identity_row(db_path, query_hash):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM query_identity WHERE hash = ?", (query_hash,)
            ).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def _damaged_fixture(self):
        from shared.query_registry.query_registry import (
            _sql_digest,
            hash_sql,
            normalize_sql,
        )

        corrupted_hash = _sql_digest(self.CORRUPTED_SQL)
        canonical = hash_sql(self.RAW_TEXT)
        assert canonical != corrupted_hash
        clean_text = "SELECT email FROM customers WHERE id = 5"
        clean_hash = hash_sql(clean_text)
        legit_text = "SELECT a FROM accounts WHERE owner_id = $1"
        legit_hash = hash_sql(legit_text)
        entries = {
            corrupted_hash: {
                "hash": corrupted_hash,
                "sql": self.CORRUPTED_SQL,
                "original_sql": self.RAW_TEXT,
                "source": "top-realtime",
                "frequency": 9,
                "observation_count": 9,
                "parameters": dict(self.POISONED_PARAMS),
                "most_recent_params": {"p1": "1", "p2": "2"},
                "target_lifecycle": {
                    "demo": {
                        "first_observed_at": "2026-08-15T10:00:00Z",
                        "last_observed_at": "2026-08-16T10:00:00Z",
                        "sources": ["top-realtime"],
                    },
                },
            },
            # Artifact entry with no original text: the fused $ can only be
            # folded out of the stored normalized text itself.
            _sql_digest(self.ORPHAN_CORRUPTED_SQL): {
                "hash": _sql_digest(self.ORPHAN_CORRUPTED_SQL),
                "sql": self.ORPHAN_CORRUPTED_SQL,
                "original_sql": "",
                "source": "top-realtime",
                "parameters": {"p1": {"value": "1", "type": "number"}},
                "most_recent_params": {"p1": "1"},
                "target_lifecycle": {
                    "demo": {
                        "first_observed_at": "2026-08-15T11:00:00Z",
                        "sources": ["top-realtime"],
                    },
                },
            },
            # Clean entry minted by the current pipeline: a repair candidate
            # (it spells :pN) whose hash already matches, kept byte-for-byte.
            clean_hash: {
                "hash": clean_hash,
                "sql": normalize_sql(clean_text),
                "original_sql": clean_text,
                "source": "manual",
                "parameters": {"p1": {"value": "5", "type": "number"}},
                "most_recent_params": {"p1": "5"},
                "target_lifecycle": {
                    "demo": {
                        "saved_at": "2026-08-01T10:00:00Z",
                        "sources": ["manual"],
                    },
                },
            },
            # Genuinely-original $N spelling under its canonical hash: the
            # fold targets only the fused $:pN artifact, never plain $N.
            legit_hash: {
                "hash": legit_hash,
                "sql": normalize_sql(legit_text),
                "original_sql": legit_text,
                "source": "top-historical",
                "target_lifecycle": {
                    "demo": {
                        "first_observed_at": "2026-08-10T10:00:00Z",
                        "sources": ["top-historical"],
                    },
                },
            },
        }
        return entries, corrupted_hash, canonical, clean_hash, legit_hash

    def _zombie_fixture(self):
        from shared.query_registry.query_registry import (
            _sql_digest,
            hash_sql,
            normalize_sql,
        )

        canonical = hash_sql(self.ZOMBIE_RAW)
        corrupted_hash = _sql_digest(self.ZOMBIE_CORRUPTED_SQL)
        assert corrupted_hash != canonical
        entries = {
            # Reviewed identity already keyed under the canonical hash.
            canonical: {
                "hash": canonical,
                "sql": normalize_sql(self.ZOMBIE_RAW),
                "original_sql": self.ZOMBIE_RAW,
                "source": "top-historical",
                "tag": "order_totals",
                "frequency": 12,
                "observation_count": 12,
                "avg_duration_ms": 8.0,
                "target_lifecycle": {
                    "demo": {
                        "first_observed_at": "2026-08-01T10:00:00Z",
                        "last_observed_at": "2026-08-05T10:00:00Z",
                        "reviewed_at": "2026-08-02T09:00:00Z",
                        "sources": ["top-historical"],
                    },
                },
            },
            # The same logical query re-admitted under a damaged hash after
            # the digit-lifting build observed it again.
            corrupted_hash: {
                "hash": corrupted_hash,
                "sql": self.ZOMBIE_CORRUPTED_SQL,
                "original_sql": self.ZOMBIE_RAW,
                "source": "top-realtime",
                "frequency": 30,
                "observation_count": 30,
                "avg_duration_ms": 11.0,
                "parameters": dict(self.POISONED_PARAMS),
                "most_recent_params": {"p1": "1", "p2": "2"},
                "target_lifecycle": {
                    "demo": {
                        "first_observed_at": "2026-08-10T10:00:00Z",
                        "last_observed_at": "2026-08-16T10:00:00Z",
                        "sources": ["top-realtime"],
                    },
                },
            },
        }
        return entries, corrupted_hash, canonical

    def test_damaged_entry_rekeys_folds_text_and_drops_params(
        self, tmp_path, caplog
    ):
        from shared.query_registry.query_registry import hash_sql, normalize_sql

        entries, corrupted_hash, canonical, clean_hash, legit_hash = (
            self._damaged_fixture()
        )
        orphan_canonical = hash_sql(self.ORPHAN_CORRUPTED_SQL)
        db_path, toml_path = self._seed_v8(tmp_path, entries)
        clean_before = self._identity_row(db_path, clean_hash)
        legit_before = self._identity_row(db_path, legit_hash)

        with caplog.at_level(logging.INFO, logger=library_store.logger.name):
            migrated = library_store.LibraryStore(db_path, toml_path).load_all()

        assert _user_version(db_path) == SCHEMA_VERSION
        assert set(migrated) == {
            canonical,
            orphan_canonical,
            clean_hash,
            legit_hash,
        }
        repaired = migrated[canonical]
        # The stored normalized text regenerates from the original through
        # the current pipeline, shedding the fused artifact.
        assert repaired["sql"] == normalize_sql(self.RAW_TEXT)
        assert "$:p" not in repaired["sql"]
        assert repaired["original_sql"] == self.RAW_TEXT
        assert repaired["parameters"] == {}
        assert repaired["most_recent_params"] == {}
        assert repaired["frequency"] == 9
        lifecycle = repaired["target_lifecycle"]["demo"]
        assert lifecycle["first_observed_at"] == "2026-08-15T10:00:00Z"
        assert lifecycle["last_observed_at"] == "2026-08-16T10:00:00Z"

        # Without an original text the fused $ folds out of the stored
        # normalized text itself.
        orphan = migrated[orphan_canonical]
        assert orphan["sql"] == self.ORPHAN_FOLDED_SQL
        assert orphan["parameters"] == {}
        assert orphan["most_recent_params"] == {}

        # Untouched rows keep their bytes: the clean entry (its observed
        # parameter values included) and the genuinely-$N entry.
        assert self._identity_row(db_path, clean_hash) == clean_before
        assert self._identity_row(db_path, legit_hash) == legit_before
        assert "$1" in migrated[legit_hash]["sql"]

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT rm_hash FROM target_query WHERE rm_hash = ?",
                (canonical,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert any(
            "Re-keyed query identity" in record.getMessage()
            and corrupted_hash in record.getMessage()
            for record in caplog.records
        )

    def test_zombie_pair_merges_with_lifecycle_union(self, tmp_path, caplog):
        from shared.query_registry.query_registry import normalize_sql

        entries, corrupted_hash, canonical = self._zombie_fixture()
        db_path, toml_path = self._seed_v8(tmp_path, entries)

        with caplog.at_level(logging.INFO, logger=library_store.logger.name):
            migrated = library_store.LibraryStore(db_path, toml_path).load_all()

        assert set(migrated) == {canonical}
        entry = migrated[canonical]
        # The reviewed identity survives (tag curation outranks the damaged
        # row once its lifted-digit parameters are scrubbed before ranking).
        assert entry["tag"] == "order_totals"
        assert entry["source"] == "top-historical"
        assert entry["sql"] == normalize_sql(self.ZOMBIE_RAW)
        assert entry["parameters"] == {}
        assert entry["most_recent_params"] == {}
        assert entry["frequency"] == 30
        assert entry["observation_count"] == 30
        assert entry["avg_duration_ms"] == 11.0
        lifecycle = entry["target_lifecycle"]["demo"]
        assert lifecycle["reviewed_at"] == "2026-08-02T09:00:00Z"
        assert lifecycle["first_observed_at"] == "2026-08-01T10:00:00Z"
        assert lifecycle["last_observed_at"] == "2026-08-16T10:00:00Z"
        assert sorted(lifecycle["sources"]) == ["top-historical", "top-realtime"]
        # The merge log names every folded-in hash, the canonical hash, and
        # which member's hash the survivor carried.
        assert any(
            "Merged query identities" in record.getMessage()
            and corrupted_hash in record.getMessage()
            and canonical in record.getMessage()
            for record in caplog.records
        )

    def test_foreign_hash_row_stays_untouched(self, tmp_path):
        # Hand-imported data whose hash matches no pipeline derivation is
        # precious and keeps its key, mirroring the v8 guard.
        text = "SELECT a FROM foreign_rows WHERE b = $1"
        entries = {
            "abcdefabcdef": {
                "hash": "abcdefabcdef",
                "sql": text,
                "original_sql": text,
                "source": "manual",
            },
        }
        db_path, toml_path = self._seed_v8(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()
        assert set(migrated) == {"abcdefabcdef"}
        assert migrated["abcdefabcdef"]["sql"] == text

    def test_ordinal_lift_row_rekeys_on_proven_provenance(self, tmp_path):
        from shared.query_registry.query_registry import _sql_digest, hash_sql
        from shared.query_registry.sql_normalizer import (
            canonicalize_placeholder_style,
        )

        # A build that lifted ORDER BY/GROUP BY ordinals stored this text
        # for "... GROUP BY 1" and hashed its textual canonicalization.
        lifted_sql = "SELECT region, COUNT(*) FROM shipments GROUP BY :p1"
        raw_text = "SELECT region, COUNT(*) FROM shipments GROUP BY 1"
        lifted_hash = _sql_digest(canonicalize_placeholder_style(lifted_sql))
        canonical = hash_sql(raw_text)
        assert canonical != lifted_hash
        entries = {
            lifted_hash: {
                "hash": lifted_hash,
                "sql": lifted_sql,
                "original_sql": raw_text,
                "source": "top-historical",
                "parameters": {"p1": {"value": "1", "type": "number"}},
                "most_recent_params": {"p1": "1"},
            },
        }
        db_path, toml_path = self._seed_v8(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()
        assert set(migrated) == {canonical}
        entry = migrated[canonical]
        assert entry["original_sql"] == raw_text
        # The regenerated text keeps the ordinal, so the lifted digit's
        # parameter observation names no placeholder and is dropped.
        assert entry["sql"] == raw_text
        assert entry["parameters"] == {}
        assert entry["most_recent_params"] == {}

    def test_repair_is_idempotent(self, tmp_path):
        damaged, _, _, _, _ = self._damaged_fixture()
        zombie, _, _ = self._zombie_fixture()
        db_path, toml_path = self._seed_v8(tmp_path, {**damaged, **zombie})
        first = library_store.LibraryStore(db_path, toml_path).load_all()

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 8")
            conn.commit()
        finally:
            conn.close()
        second = library_store.LibraryStore(db_path, toml_path).load_all()
        assert second == first
        assert _user_version(db_path) == SCHEMA_VERSION

    def test_fresh_store_creates_at_current_version(self, tmp_path):
        assert SCHEMA_VERSION == 12
        toml_path = tmp_path / "queries.toml"
        registry = QueryRegistry(registry_path=str(toml_path))
        registry.add_query("SELECT 1", source="manual")
        assert _user_version(library_db_path_for(toml_path)) == 12

    def test_toml_import_repairs_damaged_entries(self, tmp_path):
        from shared.query_registry.query_registry import hash_sql, normalize_sql

        entries, corrupted_hash, canonical, clean_hash, legit_hash = (
            self._damaged_fixture()
        )
        toml_path = _write_legacy_toml(tmp_path, {"queries": entries})
        store = library_store.LibraryStore(
            library_db_path_for(toml_path), toml_path
        )

        imported = store.load_all()
        assert corrupted_hash not in imported
        assert set(imported) == {
            canonical,
            hash_sql(self.ORPHAN_CORRUPTED_SQL),
            clean_hash,
            legit_hash,
        }
        assert imported[canonical]["sql"] == normalize_sql(self.RAW_TEXT)
        assert imported[canonical]["parameters"] == {}
        assert _user_version(store.path) == SCHEMA_VERSION


class TestPlaceholderResidueRepair:
    """Schema v10: finish the v9 repair on databases v9 already migrated.

    v9 left three residues behind: lifted slot indices kept as parameter
    "observations" on rows whose hash was already canonical, stored text
    regenerated without the row's dialect (turning `$N` into `'$N'`), and
    ordinal-lifted rows that still hash apart from live observations.
    """

    def _seed_v9(self, tmp_path, entries):
        """Write entries into a current-schema store, then stamp it v9."""
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        store = library_store.LibraryStore(db_path, toml_path)
        store.apply_changes({}, entries)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 9")
            conn.commit()
        finally:
            conn.close()
        return db_path, toml_path

    @staticmethod
    def _entry(query_hash, **fields):
        entry = {
            "hash": query_hash,
            "source": "top-historical",
            "target_lifecycle": {
                "demo": {
                    "first_observed_at": "2026-08-15T10:00:00Z",
                    "last_observed_at": "2026-08-16T10:00:00Z",
                    "sources": ["top-historical"],
                },
            },
        }
        entry.update(fields)
        return entry

    ENGINE_SQL = "SELECT id FROM orders WHERE customer_id = $1 LIMIT $2"

    def test_lifted_slot_indices_are_dropped(self, tmp_path):
        from shared.query_registry.query_registry import hash_sql

        engine_hash = hash_sql(self.ENGINE_SQL)
        entries = {
            engine_hash: self._entry(
                engine_hash,
                sql=self.ENGINE_SQL,
                original_sql=self.ENGINE_SQL,
                parameters={
                    "p1": {"value": "2", "type": "number"},
                    "p2": {"value": "1", "type": "number"},
                },
                most_recent_params={"p1": "2", "p2": "1"},
            ),
        }
        db_path, toml_path = self._seed_v9(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()

        assert _user_version(db_path) == SCHEMA_VERSION
        assert set(migrated) == {engine_hash}
        assert migrated[engine_hash]["sql"] == self.ENGINE_SQL
        assert migrated[engine_hash]["parameters"] == {}
        assert migrated[engine_hash]["most_recent_params"] == {}

    def test_sampled_slot_values_survive(self, tmp_path):
        from shared.query_registry.query_registry import hash_sql

        engine_hash = hash_sql(self.ENGINE_SQL)
        sampled = {"p1": "4711", "p2": "25"}
        entries = {
            engine_hash: self._entry(
                engine_hash,
                sql=self.ENGINE_SQL,
                original_sql=self.ENGINE_SQL,
                most_recent_params=dict(sampled),
            ),
        }
        db_path, toml_path = self._seed_v9(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()

        assert migrated[engine_hash]["most_recent_params"] == sampled

    def test_literal_bearing_original_restores_real_values(self, tmp_path):
        from shared.query_registry.query_registry import (
            canonicalize_sql,
            hash_sql,
        )
        from shared.query_registry.sql_normalizer import normalize_and_extract

        raw = "SELECT name FROM users WHERE org_id = 7 LIMIT 10"
        normalized, extracted = normalize_and_extract(canonicalize_sql(raw))
        raw_hash = hash_sql(raw)
        entries = {
            raw_hash: self._entry(
                raw_hash,
                sql=normalized,
                original_sql=raw,
                parameters={
                    name: {"value": str(index), "type": "number"}
                    for index, name in enumerate(sorted(extracted), 1)
                },
                most_recent_params={
                    name: str(index)
                    for index, name in enumerate(sorted(extracted), 1)
                },
            ),
        }
        db_path, toml_path = self._seed_v9(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()

        entry = migrated[raw_hash]
        assert entry["parameters"] == extracted
        assert entry["most_recent_params"] == {
            name: info["value"] for name, info in extracted.items()
        }

    def test_values_proving_their_own_identity_survive(self, tmp_path):
        from shared.query_registry.query_registry import (
            canonicalize_sql,
            hash_sql,
        )
        from shared.query_registry.sql_normalizer import normalize_and_extract

        # Values that happen to read as slot indices, on an entry whose
        # only stored text is already normalized: substituting them back
        # reproduces the entry's own hash, which proves they are real.
        raw = "SELECT title FROM posts WHERE score > 1 LIMIT 2"
        normalized, extracted = normalize_and_extract(canonicalize_sql(raw))
        raw_hash = hash_sql(raw)
        assert library_store._lifted_slot_indices(extracted)
        entries = {
            raw_hash: self._entry(
                raw_hash,
                sql=normalized,
                original_sql=normalized,
                parameters=dict(extracted),
            ),
        }
        db_path, toml_path = self._seed_v9(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()

        assert migrated[raw_hash]["parameters"] == extracted

    def test_quoted_placeholder_text_is_regenerated(self, tmp_path, monkeypatch):
        from shared.query_registry import query_registry
        from shared.query_registry.query_registry import hash_sql, normalize_sql

        monkeypatch.setattr(
            query_registry, "dialect_for_target", lambda target: "postgres"
        )
        raw = "SELECT DATE_TRUNC($1, created_at) AS bucket FROM events"
        degraded = normalize_sql(raw)
        assert "'$1'" in degraded
        raw_hash = hash_sql(raw)
        entries = {
            raw_hash: self._entry(
                raw_hash, sql=degraded, original_sql=raw, last_target="demo"
            ),
        }
        db_path, toml_path = self._seed_v9(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()

        # The hash is derived dialect-lessly and stays put; only the text
        # the user reads and RDST re-executes is regenerated.
        assert set(migrated) == {raw_hash}
        assert migrated[raw_hash]["sql"] == normalize_sql(raw, "postgres")
        assert "'$1'" not in migrated[raw_hash]["sql"]

    def test_ordinal_lift_without_original_rekeys(self, tmp_path):
        from shared.query_registry.query_registry import _sql_digest, hash_sql
        from shared.query_registry.sql_normalizer import (
            canonicalize_placeholder_style,
        )

        lifted_sql = "SELECT region, COUNT(*) FROM shipments GROUP BY :p1 ORDER BY :p2"
        raw = "SELECT region, COUNT(*) FROM shipments GROUP BY 1 ORDER BY 2"
        lifted_hash = _sql_digest(canonicalize_placeholder_style(lifted_sql))
        canonical = hash_sql(raw)
        assert canonical != lifted_hash
        entries = {
            lifted_hash: self._entry(
                lifted_hash,
                sql=lifted_sql,
                original_sql="",
                parameters={
                    "p1": {"value": "1", "type": "number"},
                    "p2": {"value": "2", "type": "number"},
                },
                most_recent_params={"p1": "1", "p2": "2"},
            ),
        }
        db_path, toml_path = self._seed_v9(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()

        assert set(migrated) == {canonical}
        entry = migrated[canonical]
        assert entry["sql"] == raw
        assert entry["original_sql"] == ""
        assert entry["parameters"] == {}
        assert entry["most_recent_params"] == {}

    def test_ordinal_rekey_merges_with_the_live_identity(self, tmp_path):
        from shared.query_registry.query_registry import _sql_digest, hash_sql
        from shared.query_registry.sql_normalizer import (
            canonicalize_placeholder_style,
        )

        lifted_sql = "SELECT region, COUNT(*) FROM shipments GROUP BY :p1 ORDER BY :p2"
        raw = "SELECT region, COUNT(*) FROM shipments GROUP BY 1 ORDER BY 2"
        lifted_hash = _sql_digest(canonicalize_placeholder_style(lifted_sql))
        canonical = hash_sql(raw)
        entries = {
            lifted_hash: self._entry(
                lifted_hash,
                sql=lifted_sql,
                original_sql="",
                frequency=40,
                parameters={
                    "p1": {"value": "1", "type": "number"},
                    "p2": {"value": "2", "type": "number"},
                },
            ),
            canonical: self._entry(
                canonical,
                sql=raw,
                original_sql=raw,
                tag="shipments_by_region",
                frequency=3,
                target_lifecycle={
                    "demo": {
                        "first_observed_at": "2026-08-10T10:00:00Z",
                        "last_observed_at": "2026-08-14T10:00:00Z",
                        "sources": ["top-realtime"],
                    },
                },
            ),
        }
        db_path, toml_path = self._seed_v9(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()

        assert set(migrated) == {canonical}
        entry = migrated[canonical]
        # Curation survives; the lifecycle unions across both members.
        assert entry["tag"] == "shipments_by_region"
        assert entry["frequency"] == 40
        lifecycle = entry["target_lifecycle"]["demo"]
        assert lifecycle["first_observed_at"] == "2026-08-10T10:00:00Z"
        assert lifecycle["last_observed_at"] == "2026-08-16T10:00:00Z"
        assert sorted(lifecycle["sources"]) == ["top-historical", "top-realtime"]

    def test_foreign_hash_row_stays_untouched(self, tmp_path):
        entries = {
            "abcdefabcdef": self._entry(
                "abcdefabcdef",
                sql=self.ENGINE_SQL,
                original_sql=self.ENGINE_SQL,
                parameters={
                    "p1": {"value": "2", "type": "number"},
                    "p2": {"value": "1", "type": "number"},
                },
            ),
        }
        db_path, toml_path = self._seed_v9(tmp_path, entries)

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()

        assert set(migrated) == {"abcdefabcdef"}
        assert migrated["abcdefabcdef"]["parameters"] == {
            "p1": {"value": "2", "type": "number"},
            "p2": {"value": "1", "type": "number"},
        }

    def test_repair_is_idempotent(self, tmp_path):
        from shared.query_registry.query_registry import _sql_digest, hash_sql
        from shared.query_registry.sql_normalizer import (
            canonicalize_placeholder_style,
        )

        engine_hash = hash_sql(self.ENGINE_SQL)
        lifted_sql = "SELECT region, COUNT(*) FROM shipments GROUP BY :p1"
        lifted_hash = _sql_digest(canonicalize_placeholder_style(lifted_sql))
        entries = {
            engine_hash: self._entry(
                engine_hash,
                sql=self.ENGINE_SQL,
                original_sql=self.ENGINE_SQL,
                parameters={
                    "p1": {"value": "2", "type": "number"},
                    "p2": {"value": "1", "type": "number"},
                },
            ),
            lifted_hash: self._entry(
                lifted_hash,
                sql=lifted_sql,
                original_sql="",
                parameters={"p1": {"value": "1", "type": "number"}},
            ),
        }
        db_path, toml_path = self._seed_v9(tmp_path, entries)
        first = library_store.LibraryStore(db_path, toml_path).load_all()

        # Re-running the migration over its own output changes nothing.
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 9")
            conn.commit()
        finally:
            conn.close()
        second = library_store.LibraryStore(db_path, toml_path).load_all()
        assert second == first
        assert library_store.LibraryStore(db_path, toml_path).load_all() == first
        assert _user_version(db_path) == SCHEMA_VERSION

    def test_clean_store_is_left_alone(self, tmp_path):
        from shared.query_registry.query_registry import hash_sql

        raw = "SELECT name FROM users WHERE org_id = 7"
        raw_hash = hash_sql(raw)
        entries = {
            raw_hash: self._entry(
                raw_hash,
                sql="SELECT name FROM users WHERE org_id = :p1",
                original_sql=raw,
                parameters={"p1": {"value": "7", "type": "number"}},
                most_recent_params={"p1": "7"},
            ),
        }
        db_path, toml_path = self._seed_v9(tmp_path, entries)
        before = _identity_rows(db_path)

        library_store.LibraryStore(db_path, toml_path).load_all()

        assert _identity_rows(db_path) == before


class TestObservationHistoryRekey:
    """Observation history follows the identities library.db re-keys."""

    LIFTED_SQL = "SELECT region, COUNT(*) FROM shipments GROUP BY :p1"
    RAW = "SELECT region, COUNT(*) FROM shipments GROUP BY 1"

    def _window(self, query_hash, window_end, calls):
        return {
            "normalized_hash": query_hash,
            "window_start": window_end - 60.0,
            "window_end": window_end,
            "calls_delta": calls,
            "exec_time_delta": float(calls),
            "approximate_qps": calls / 60.0,
            "completeness": "complete",
        }

    def test_rekey_identities_moves_and_folds_rows(self, tmp_path):
        store = observation_store.ObservationStore(tmp_path / "cache.db")
        try:
            store.record_identity_aliases("demo", "epoch-1", {"key-1": "aaaa"})
            store.record_recent_observations(
                "demo",
                [self._window("aaaa", 100.0, 3), self._window("bbbb", 100.0, 4)],
            )
            moved = store.rekey_identities({"aaaa": "bbbb", "cccc": "cccc"})
            assert moved
            assert store.get_identity_aliases("demo", "epoch-1") == {
                "key-1": "bbbb"
            }
            with store._read() as conn:
                rows = conn.execute(
                    "SELECT normalized_hash, calls_delta, exec_time_delta"
                    " FROM recent_observation"
                ).fetchall()
        finally:
            store.close()
        assert [tuple(row) for row in rows] == [("bbbb", 7, 7.0)]

    def test_migration_rekeys_observation_history(self, tmp_path):
        from shared.query_registry.query_registry import _sql_digest, hash_sql
        from shared.query_registry.sql_normalizer import (
            canonicalize_placeholder_style,
        )

        lifted_hash = _sql_digest(canonicalize_placeholder_style(self.LIFTED_SQL))
        canonical = hash_sql(self.RAW)
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        seeder = TestPlaceholderResidueRepair()
        entries = {
            lifted_hash: seeder._entry(
                lifted_hash,
                sql=self.LIFTED_SQL,
                original_sql="",
                parameters={"p1": {"value": "1", "type": "number"}},
            ),
        }
        db_path, toml_path = seeder._seed_v9(tmp_path, entries)
        store = observation_store.ObservationStore(tmp_path / "cache.db")
        try:
            store.record_identity_aliases(
                "demo", "epoch-1", {"key-1": lifted_hash}
            )
            store.record_recent_observations(
                "demo", [self._window(lifted_hash, 100.0, 5)]
            )
        finally:
            store.close()

        migrated = library_store.LibraryStore(db_path, toml_path).load_all()
        assert set(migrated) == {canonical}

        store = observation_store.ObservationStore(tmp_path / "cache.db")
        try:
            assert store.get_identity_aliases("demo", "epoch-1") == {
                "key-1": canonical
            }
            with store._read() as conn:
                hashes = [
                    row[0]
                    for row in conn.execute(
                        "SELECT normalized_hash FROM recent_observation"
                    )
                ]
        finally:
            store.close()
        assert hashes == [canonical]

    def test_missing_cache_file_is_not_created(self, tmp_path):
        from shared.query_registry.query_registry import _sql_digest

        lifted_hash = _sql_digest(
            "SELECT region, COUNT(*) FROM shipments GROUP BY :p1"
        )
        seeder = TestPlaceholderResidueRepair()
        db_path, toml_path = seeder._seed_v9(
            tmp_path,
            {
                lifted_hash: seeder._entry(
                    lifted_hash,
                    sql=self.LIFTED_SQL,
                    original_sql="",
                    parameters={"p1": {"value": "1", "type": "number"}},
                ),
            },
        )

        library_store.LibraryStore(db_path, toml_path).load_all()

        assert not (tmp_path / "cache.db").exists()


class TestSelfTrafficPrune:
    """Schema v11: drop the RDST diagnostic statements markers cannot reach.

    Schema profiling reads user relations, so its statements were admitted
    as workload before the ``/*rdst:...*/`` self markers existed. They are
    recognized by matching the stored text against the template that would
    have emitted it, for the table and columns the text itself names.
    """

    PG_COLUMN_STATS = (
        'SELECT COUNT(*) AS __total, COUNT("id") AS "id__cnt", '
        'COUNT(DISTINCT "id") AS "id__dist", '
        'SUM(CASE WHEN "id" IS NULL THEN $1 ELSE $2 END) AS "id__nulls", '
        'COUNT("title") AS "title__cnt", COUNT(DISTINCT "title") AS "title__dist", '
        'SUM(CASE WHEN "title" IS NULL THEN $3 ELSE $4 END) AS "title__nulls" '
        'FROM "posts" TABLESAMPLE SYSTEM($5)'
    )
    PG_TOP_VALUES = (
        'SELECT "body"::text, COUNT(*) AS cnt FROM "posts" TABLESAMPLE SYSTEM($1) '
        'WHERE "body" IS NOT NULL GROUP BY "body" ORDER BY cnt DESC LIMIT $2'
    )
    PG_TOP_VALUES_SMALL = (
        'SELECT "tagname"::text, COUNT(*) AS cnt FROM "tags" '
        'WHERE "tagname" IS NOT NULL GROUP BY "tagname" ORDER BY cnt DESC LIMIT $1'
    )
    PG_SAMPLE_ROWS = 'SELECT * FROM "votes" TABLESAMPLE SYSTEM($1) LIMIT $2'
    PG_ENUM_SAMPLE = (
        'WITH sampled AS (\n'
        '    SELECT "class"\n'
        '    FROM "badges" TABLESAMPLE SYSTEM($1)\n'
        '    LIMIT $2\n'
        ')\n'
        'SELECT DISTINCT "class"\n'
        'FROM sampled\n'
        'WHERE "class" IS NOT NULL\n'
        'LIMIT $3'
    )
    MYSQL_COLUMN_STATS = (
        "SELECT COUNT(*) AS __total, COUNT(`id`) AS `id__cnt`, "
        "COUNT(DISTINCT `id`) AS `id__dist`, "
        "SUM(CASE WHEN `id` IS NULL THEN ? ELSE ? END) AS `id__nulls` "
        "FROM (SELECT `id` FROM `posts` LIMIT ?) sampled"
    )
    MYSQL_TOP_VALUES = (
        "SELECT CAST(`name` AS CHAR) AS val, COUNT(*) AS cnt FROM `badges` "
        "WHERE `name` IS NOT NULL GROUP BY val ORDER BY cnt DESC LIMIT ?"
    )
    MYSQL_ENUM_SAMPLE = (
        "SELECT DISTINCT `class`\n"
        "    FROM (\n"
        "        SELECT `class`\n"
        "        FROM `badges`\n"
        "        LIMIT 10000\n"
        "    ) subq\n"
        "    WHERE `class` IS NOT NULL\n"
        "    LIMIT 21"
    )
    # A user query that uses TABLESAMPLE without being a template instance.
    USER_TABLESAMPLE = (
        'SELECT id, title FROM "posts" TABLESAMPLE SYSTEM($1) WHERE score > $2'
    )
    # The small-table sample-rows form, indistinguishable from user SQL.
    USER_STAR_LIMIT = 'SELECT * FROM "tags" LIMIT $1'

    def _seed_v10(self, tmp_path, entries):
        """Write entries into a current-schema store, then stamp it v10."""
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        library_store.LibraryStore(db_path, toml_path).apply_changes({}, entries)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 10")
            conn.commit()
        finally:
            conn.close()
        return db_path, toml_path

    @staticmethod
    def _entry(sql, **lifecycle):
        from shared.query_registry.query_registry import hash_sql

        query_hash = hash_sql(sql)
        target = {
            "first_observed_at": "2026-08-19T10:00:00Z",
            "last_observed_at": "2026-08-19T11:00:00Z",
            "sources": ["top-historical"],
        }
        target.update(lifecycle)
        return query_hash, {
            "hash": query_hash,
            "sql": sql,
            "original_sql": sql,
            "source": "top-historical",
            "target_lifecycle": {"demo": target},
        }

    def _migrate(self, tmp_path, texts, lifecycles=None):
        lifecycles = lifecycles or {}
        entries = {}
        hashes = {}
        for text in texts:
            query_hash, entry = self._entry(text, **lifecycles.get(text, {}))
            entries[query_hash] = entry
            hashes[text] = query_hash
        db_path, toml_path = self._seed_v10(tmp_path, entries)
        surviving = set(library_store.LibraryStore(db_path, toml_path).load_all())
        return db_path, hashes, surviving

    def test_postgres_template_rows_are_pruned(self, tmp_path):
        from features.schema.semantic_layer.pattern_detector import (
            detect_delimiter_columns_sql_postgres,
        )

        delimiter = detect_delimiter_columns_sql_postgres(
            ["text"], "comments", 100_000
        ).split("*/ ", 1)[1]
        texts = [
            self.PG_COLUMN_STATS,
            self.PG_TOP_VALUES,
            self.PG_TOP_VALUES_SMALL,
            self.PG_SAMPLE_ROWS,
            self.PG_ENUM_SAMPLE,
            delimiter,
        ]
        db_path, _, surviving = self._migrate(tmp_path, texts)
        assert surviving == set()
        assert _user_version(db_path) == SCHEMA_VERSION

    def test_mysql_template_rows_are_pruned(self, tmp_path):
        from features.schema.semantic_layer.pattern_detector import (
            detect_delimiter_columns_sql_mysql,
        )

        delimiter = detect_delimiter_columns_sql_mysql(
            ["text"], "comments", 100_000
        ).split("*/ ", 1)[1]
        texts = [
            self.MYSQL_COLUMN_STATS,
            self.MYSQL_TOP_VALUES,
            self.MYSQL_ENUM_SAMPLE,
            delimiter,
        ]
        _, _, surviving = self._migrate(tmp_path, texts)
        assert surviving == set()

    def test_measured_template_rows_survive(self, tmp_path):
        """v11 spares a saved template row; v12 keeps only measured ones."""
        texts = [self.PG_TOP_VALUES, self.PG_SAMPLE_ROWS, self.PG_ENUM_SAMPLE]
        lifecycles = {
            self.PG_TOP_VALUES: {"saved_at": "2026-08-19T12:00:00Z"},
            self.PG_SAMPLE_ROWS: {
                "last_analyzed_at": "2026-08-19T12:00:00Z",
                "analysis_count": 1,
            },
            self.PG_ENUM_SAMPLE: {
                "last_compared_at": "2026-08-19T12:00:00Z",
                "comparison_count": 2,
            },
        }
        _, hashes, surviving = self._migrate(tmp_path, texts, lifecycles)
        assert surviving == {
            hashes[self.PG_SAMPLE_ROWS],
            hashes[self.PG_ENUM_SAMPLE],
        }

    def test_reviewed_only_template_row_is_pruned(self, tmp_path):
        texts = [self.PG_TOP_VALUES]
        lifecycles = {self.PG_TOP_VALUES: {"reviewed_at": "2026-08-19T12:00:00Z"}}
        _, _, surviving = self._migrate(tmp_path, texts, lifecycles)
        assert surviving == set()

    def test_user_queries_survive(self, tmp_path):
        texts = [self.USER_TABLESAMPLE, self.USER_STAR_LIMIT]
        _, hashes, surviving = self._migrate(tmp_path, texts)
        assert surviving == set(hashes.values())

    def test_prune_is_idempotent(self, tmp_path):
        texts = [self.PG_TOP_VALUES, self.USER_TABLESAMPLE]
        db_path, hashes, surviving = self._migrate(tmp_path, texts)
        assert surviving == {hashes[self.USER_TABLESAMPLE]}

        toml_path = db_path.with_name("queries.toml")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 10")
            conn.commit()
        finally:
            conn.close()
        again = library_store.LibraryStore(db_path, toml_path).load_all()
        assert set(again) == surviving
        assert _user_version(db_path) == SCHEMA_VERSION

    def test_clean_store_is_untouched(self, tmp_path, caplog):
        texts = [self.USER_TABLESAMPLE, self.USER_STAR_LIMIT]
        with caplog.at_level(logging.INFO, logger=library_store.__name__):
            _, hashes, surviving = self._migrate(tmp_path, texts)
        assert surviving == set(hashes.values())
        assert "self-traffic" not in caplog.text

    def test_ladder_from_v8_prunes_template_rows(self, tmp_path):
        seeder = TestPlaceholderArtifactRepair()
        template_hash, template = self._entry(self.PG_TOP_VALUES)
        user_hash, user = self._entry(self.USER_TABLESAMPLE)
        db_path, toml_path = seeder._seed_v8(
            tmp_path, {template_hash: template, user_hash: user}
        )

        surviving = library_store.LibraryStore(db_path, toml_path).load_all()

        assert set(surviving) == {user_hash}
        assert _user_version(db_path) == SCHEMA_VERSION

    def test_prune_logs_shape_and_skip_reason(self, tmp_path, caplog):
        texts = [self.PG_TOP_VALUES, self.PG_SAMPLE_ROWS]
        lifecycles = {self.PG_SAMPLE_ROWS: {"saved_at": "2026-08-19T12:00:00Z"}}
        with caplog.at_level(logging.INFO, logger=library_store.__name__):
            self._migrate(tmp_path, texts, lifecycles)
        assert "Pruned RDST top_values statement" in caplog.text
        assert "Keeping RDST sample_rows statement" in caplog.text
        assert "removed 1 RDST self-traffic entries (top_values 1)" in caplog.text


class TestSelfTemplateRecognizer:
    """The recognizer agrees with the builders it mirrors."""

    def test_delimiter_probes_match_live_builders(self):
        from features.schema.semantic_layer.pattern_detector import (
            detect_delimiter_columns_sql_mysql,
            detect_delimiter_columns_sql_postgres,
        )
        from shared.query_registry.self_traffic import match_self_template

        for builder in (
            detect_delimiter_columns_sql_postgres,
            detect_delimiter_columns_sql_mysql,
        ):
            for row_estimate in (100, 100_000):
                sql = builder(["text", "body"], "comments", row_estimate)
                assert match_self_template(sql) == "delimiter_probe"
                assert match_self_template(sql.split("*/ ", 1)[1]) == (
                    "delimiter_probe"
                )

    def test_setting_probe_matches_the_statement_stats_reader(self):
        from features.query_registry.statement_stats import PG_VERSION_SQL
        from shared.query_registry.self_traffic import match_self_template

        assert match_self_template(PG_VERSION_SQL) == "setting_probe"
        # pg_stat_statements stores it with the GUC name already replaced.
        assert match_self_template("SELECT current_setting($1)::int") == (
            "setting_probe"
        )

    def test_non_template_statements_are_not_matched(self):
        from shared.query_registry.self_traffic import match_self_template

        for sql in (
            "",
            "SELECT 1",
            "SELECT current_setting($1)",
            'SELECT current_setting($1)::int FROM "settings"',
            'SELECT * FROM "tags" LIMIT $1',
            'SELECT DISTINCT "class" FROM "badges" WHERE "class" IS NOT NULL LIMIT $1',
            'SELECT "name"::text, COUNT(*) AS cnt FROM "badges" '
            'WHERE "name" IS NOT NULL GROUP BY "name" ORDER BY cnt DESC',
            'SELECT COUNT(*) AS __total, COUNT("id") AS "id__cnt", '
            'COUNT(DISTINCT "id") AS "id__dist", '
            'SUM(CASE WHEN "id" IS NULL THEN $1 ELSE $2 END) AS "id__nulls" '
            'FROM "tags" WHERE "id" > $3',
            'SELECT * FROM "votes" TABLESAMPLE SYSTEM($1) WHERE id > $2 LIMIT $3',
        ):
            assert match_self_template(sql) is None


class TestSystemStatementPrune:
    """Schema v12: statements that are not user workload leave the library.

    Saving and reviewing are sweeps over whatever the list showed, so they
    no longer hold a catalog statement or an RDST probe in place; analysis
    and comparison do, because their measurements need their subject.
    """

    CATALOG = "SELECT relname FROM pg_stat_user_indexes"
    VERSION = "SELECT version()"
    SETTING_PROBE = "SELECT current_setting($1)::int"
    USER_QUERY = "SELECT title FROM posts WHERE score > $1"

    def _seed_v11(self, tmp_path, entries):
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        library_store.LibraryStore(db_path, toml_path).apply_changes({}, entries)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 11")
            conn.commit()
        finally:
            conn.close()
        return db_path, toml_path

    @staticmethod
    def _entry(sql, source="top-historical", **lifecycle):
        from shared.query_registry.query_registry import hash_sql

        query_hash = hash_sql(sql)
        target = {
            "first_observed_at": "2026-08-19T10:00:00Z",
            "last_observed_at": "2026-08-19T11:00:00Z",
            "sources": [source],
        }
        target.update(lifecycle)
        return query_hash, {
            "hash": query_hash,
            "sql": sql,
            "original_sql": sql,
            "source": source,
            "target_lifecycle": {"demo": target},
        }

    def _migrate(self, tmp_path, specs):
        entries = {}
        hashes = {}
        for sql, kwargs in specs:
            query_hash, entry = self._entry(sql, **kwargs)
            entries[query_hash] = entry
            hashes[sql] = query_hash
        db_path, toml_path = self._seed_v11(tmp_path, entries)
        surviving = set(library_store.LibraryStore(db_path, toml_path).load_all())
        return db_path, hashes, surviving

    def test_relation_free_statements_are_pruned(self, tmp_path):
        specs = [
            (self.CATALOG, {}),
            (self.VERSION, {"source": "cache"}),
            (self.SETTING_PROBE, {"source": "cache"}),
            (self.USER_QUERY, {}),
        ]
        db_path, hashes, surviving = self._migrate(tmp_path, specs)

        assert surviving == {hashes[self.USER_QUERY]}
        assert _user_version(db_path) == SCHEMA_VERSION

    def test_saved_and_reviewed_statements_are_pruned(self, tmp_path):
        specs = [
            (
                self.VERSION,
                {
                    "source": "cache",
                    "saved_at": "2026-08-19T12:00:00Z",
                    "reviewed_at": "2026-08-19T12:30:00Z",
                },
            ),
        ]
        _, _, surviving = self._migrate(tmp_path, specs)

        assert surviving == set()

    def test_a_cached_statement_is_still_pruned(self, tmp_path):
        """A relation-free statement cannot be a useful cache."""
        query_hash, entry = self._entry(self.VERSION, source="cache")
        entry["readyset_query_id"] = "q_491622c7e943fcc7"
        entry["readyset_supported"] = "yes"
        db_path, toml_path = self._seed_v11(tmp_path, {query_hash: entry})

        surviving = library_store.LibraryStore(db_path, toml_path).load_all()

        assert surviving == {}

    @pytest.mark.parametrize(
        "lifecycle",
        [
            {"last_analyzed_at": "2026-08-19T12:00:00Z"},
            {"analysis_count": 1},
            {"last_compared_at": "2026-08-19T12:00:00Z"},
            {"comparison_count": 2},
        ],
    )
    def test_measured_statements_survive(self, tmp_path, lifecycle):
        specs = [(self.CATALOG, lifecycle)]
        _, hashes, surviving = self._migrate(tmp_path, specs)

        assert surviving == {hashes[self.CATALOG]}

    def test_user_authored_statements_survive(self, tmp_path):
        """A person who typed, imported, or asked for it keeps it."""
        specs = [
            (self.CATALOG, {"source": "manual"}),
            (self.VERSION, {"source": "web"}),
            (self.SETTING_PROBE, {"source": "file"}),
        ]
        _, hashes, surviving = self._migrate(tmp_path, specs)

        assert surviving == set(hashes.values())

    def test_an_asked_statement_survives(self, tmp_path):
        query_hash, entry = self._entry(self.CATALOG)
        entry["question"] = "which indexes are unused?"
        db_path, toml_path = self._seed_v11(tmp_path, {query_hash: entry})

        surviving = library_store.LibraryStore(db_path, toml_path).load_all()

        assert set(surviving) == {query_hash}

    def test_prune_is_idempotent(self, tmp_path):
        specs = [(self.CATALOG, {}), (self.USER_QUERY, {})]
        db_path, hashes, surviving = self._migrate(tmp_path, specs)
        assert surviving == {hashes[self.USER_QUERY]}

        toml_path = db_path.with_name("queries.toml")
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 11")
            conn.commit()
        finally:
            conn.close()
        again = library_store.LibraryStore(db_path, toml_path).load_all()

        assert set(again) == surviving
        assert _user_version(db_path) == SCHEMA_VERSION

    def test_clean_store_is_untouched(self, tmp_path, caplog):
        specs = [(self.USER_QUERY, {})]
        with caplog.at_level(logging.INFO, logger=library_store.__name__):
            _, hashes, surviving = self._migrate(tmp_path, specs)

        assert surviving == set(hashes.values())
        assert "system statements" not in caplog.text


class TestMigrationSafetyNet:
    """A migration never leaves a user without their queries."""

    def _seed_v11(self, tmp_path):
        from shared.query_registry.query_registry import hash_sql

        sql = "SELECT title FROM posts WHERE score > $1"
        query_hash = hash_sql(sql)
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        library_store.LibraryStore(db_path, toml_path).apply_changes(
            {},
            {
                query_hash: {
                    "hash": query_hash,
                    "sql": sql,
                    "original_sql": sql,
                    "source": "top-historical",
                    "target_lifecycle": {"demo": {"sources": ["top-historical"]}},
                }
            },
        )
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 11")
            conn.commit()
        finally:
            conn.close()
        return db_path, toml_path, query_hash

    def test_upgrade_backs_the_file_up_first(self, tmp_path):
        db_path, toml_path, query_hash = self._seed_v11(tmp_path)

        library_store.LibraryStore(db_path, toml_path).load_all()

        backup = db_path.with_name(f"{db_path.name}.pre-v11.bak")
        assert backup.exists()
        assert _user_version(backup) == 11
        conn = sqlite3.connect(backup)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            stored = conn.execute("SELECT hash FROM query_identity").fetchall()
        finally:
            conn.close()
        assert stored == [(query_hash,)]

    def test_backup_is_kept_once_per_source_version(self, tmp_path):
        db_path, toml_path, _ = self._seed_v11(tmp_path)
        library_store.LibraryStore(db_path, toml_path).load_all()
        backup = db_path.with_name(f"{db_path.name}.pre-v11.bak")
        original = backup.read_bytes()

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA user_version = 11")
            conn.commit()
        finally:
            conn.close()
        library_store.LibraryStore(db_path, toml_path).load_all()

        assert backup.read_bytes() == original
        assert sorted(path.name for path in tmp_path.glob("*.bak")) == [
            backup.name
        ]

    def test_a_current_store_is_not_backed_up(self, tmp_path):
        toml_path = tmp_path / "queries.toml"
        db_path = library_db_path_for(toml_path)
        library_store.LibraryStore(db_path, toml_path).apply_changes({}, {})

        library_store.LibraryStore(db_path, toml_path).load_all()

        assert list(tmp_path.glob("*.bak")) == []

    def test_a_failed_migration_keeps_the_prior_version(self, tmp_path, monkeypatch):
        db_path, toml_path, query_hash = self._seed_v11(tmp_path)

        def explode(self, conn):
            raise sqlite3.OperationalError("no such column: nope")

        monkeypatch.setattr(
            library_store.LibraryStore, "_prune_system_statements", explode
        )
        with pytest.raises(library_store.LibraryMigrationError) as raised:
            library_store.LibraryStore(db_path, toml_path).load_all()

        message = str(raised.value)
        assert "schema 12" in message
        assert "no such column: nope" in message
        assert f"{db_path.name}.pre-v11.bak" in message
        assert "still holds every query at schema 11" in message
        # The failure names its own cause, so it needs no chained traceback.
        assert raised.value.__cause__ is None
        assert _user_version(db_path) == 11
        conn = sqlite3.connect(db_path)
        try:
            assert conn.execute("SELECT hash FROM query_identity").fetchall() == [
                (query_hash,)
            ]
        finally:
            conn.close()

    def test_the_registry_passes_the_explanation_through(self, tmp_path, monkeypatch):
        """A command that reads the library reports the message, not a wrapper."""
        _, toml_path, _ = self._seed_v11(tmp_path)

        def explode(self, conn):
            raise sqlite3.OperationalError("no such column: nope")

        monkeypatch.setattr(
            library_store.LibraryStore, "_prune_system_statements", explode
        )
        registry = QueryRegistry(registry_path=str(toml_path))
        with pytest.raises(library_store.LibraryMigrationError) as raised:
            registry.load()

        assert str(raised.value).startswith("library.db at ")

    def test_a_command_that_never_reads_the_library_is_unaffected(
        self, tmp_path, monkeypatch
    ):
        _, toml_path, _ = self._seed_v11(tmp_path)

        def explode(self, conn):
            raise sqlite3.OperationalError("no such column: nope")

        monkeypatch.setattr(
            library_store.LibraryStore, "_prune_system_statements", explode
        )

        # Construction opens nothing; the store is reached by the first read
        # or write, so commands that touch no query keep working.
        QueryRegistry(registry_path=str(toml_path))


class TestParameterProvenanceGuard:
    """Values someone chose are never mistaken for digit-lift residue.

    The repair recognizes the digit-lifting normalizer's residue by its
    shape: values that are exactly 1..N. A user is free to ask a query
    about those same numbers, and says so by storing them with a source.
    """

    SQL = "SELECT * FROM orders WHERE id = :p1 AND status = :p2"
    OBSERVED = {"p1": "1", "p2": "2"}

    @staticmethod
    def _typed(source=""):
        from shared.query_registry.query_registry import typed_parameter

        return {
            "p1": typed_parameter("1", source),
            "p2": typed_parameter("2", source),
        }

    def test_slot_indices_without_provenance_are_residue(self):
        assert library_store._lifted_slot_indices(self._typed()) is True
        assert library_store._lifted_slot_indices(self.OBSERVED) is True

    def test_slot_indices_with_provenance_are_values(self):
        for source in ("user", "suggested"):
            assert library_store._lifted_slot_indices(self._typed(source)) is False

    def test_repair_scrubs_unsourced_slot_indices(self):
        params, observed = library_store._repaired_parameters(
            self.SQL, "", self._typed(), dict(self.OBSERVED), None, "000000000000"
        )

        assert (params, observed) == ({}, {})

    def test_repair_keeps_values_a_user_stored(self):
        typed = self._typed("user")

        params, observed = library_store._repaired_parameters(
            self.SQL, "", typed, dict(self.OBSERVED), None, "000000000000"
        )

        assert params == typed
        assert observed == self.OBSERVED

    def test_repair_keeps_accepted_suggestions(self):
        typed = self._typed("suggested")

        params, observed = library_store._repaired_parameters(
            self.SQL, "", typed, dict(self.OBSERVED), None, "000000000000"
        )

        assert params == typed
        assert observed == self.OBSERVED
