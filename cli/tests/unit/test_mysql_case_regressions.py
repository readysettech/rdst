"""Case-sensitive MySQL identities and source-preserving table restoration."""

import sqlite3

import pytest

from features.query_registry.statement_stats import mysql_table_case
from shared.query_registry.library_store import LibraryStore, library_db_path_for
from shared.query_registry.observation_store import ObservationStore
from shared.query_registry.query_registry import QueryRegistry, _sql_digest, hash_sql, normalize_sql
from shared.query_registry.sql_normalizer import canonicalize_placeholder_style, identity_text


@pytest.mark.parametrize("upper,lower", [
    ("Orders", "orders"), ("`Orders`", "`orders`"),
    ("Shop.orders", "shop.orders"), ("`Shop`.`orders`", "`shop`.`orders`"),
])
def test_mysql_case_distinct_identities(upper, lower, tmp_path):
    first = f"SELECT * FROM {upper} WHERE id=1"
    second = f"SELECT * FROM {lower} WHERE id=2"
    assert hash_sql(first) != hash_sql(second)
    assert hash_sql(first, "mysql") != hash_sql(second, "mysql")
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    assert registry.add_query(sql=first, dialect="mysql")[0] != registry.add_query(
        sql=second, dialect="mysql")[0]
    assert len(registry.list_queries()) == 2


@pytest.mark.parametrize("version", [15, 16, 17])
@pytest.mark.parametrize("quoted", [False, True])
def test_case_sensitive_identity_upgrade_is_idempotent(version, quoted, tmp_path):
    toml_path = tmp_path / "queries.toml"
    db_path = library_db_path_for(toml_path)
    store = LibraryStore(db_path, toml_path)
    texts = ["SELECT * FROM `Orders` WHERE id=1", "SELECT * FROM `orders` WHERE id=1"]
    if not quoted:
        texts = [text.replace("`", "") for text in texts]
    entries = {}
    for text in texts:
        previous = _sql_digest(canonicalize_placeholder_style(normalize_sql(text)))
        entries[previous] = {"hash": previous, "sql": text, "original_sql": text,
                             "source": "manual"}
    store.apply_changes({}, entries)
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version={version}")
    upgraded = LibraryStore(db_path, toml_path)
    assert len(upgraded.load_all()) == 2
    expected = {hash_sql(text) for text in texts}
    assert len(expected) == 2
    assert set(upgraded.load_all()) == expected
    with upgraded._write() as conn:
        assert upgraded._rederive_identity_hashes(conn) == 0
    assert set(LibraryStore(db_path, toml_path).load_all()) == expected


@pytest.mark.parametrize("version", [16, 17])
def test_merged_v16_survivor_rekeys_without_further_case_merges(version, tmp_path):
    toml_path = tmp_path / "queries.toml"
    db_path = library_db_path_for(toml_path)
    text = "SELECT * FROM Orders WHERE id=1"
    old_hash = _sql_digest(canonicalize_placeholder_style(
        identity_text(text, preserve_table_case=False)))
    store = LibraryStore(db_path, toml_path)
    store.apply_changes({}, {old_hash: {
        "hash": old_hash, "sql": text, "original_sql": text, "source": "manual",
        "tag": "survivor", "parameters": {"p1": {"value": "1", "type": "number"}},
    }})
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"PRAGMA user_version={version}")
    upgraded = LibraryStore(db_path, toml_path)
    entries = upgraded.load_all()
    assert set(entries) == {hash_sql(text)}
    assert entries[hash_sql(text)]["tag"] == "survivor"
    assert entries[hash_sql(text)]["parameters"]["p1"]["value"] == "1"
    with upgraded._write() as conn:
        assert upgraded._rederive_identity_hashes(conn) == 0
    registry = QueryRegistry(registry_path=str(toml_path))
    lower_hash, _ = registry.add_query(sql=text.replace("Orders", "orders"), dialect="mysql")
    assert lower_hash != hash_sql(text)
    assert len(registry.list_queries()) == 2
    with ObservationStore(tmp_path / "cache.db") as cache:
        cache.record_recent_observations("db", [{
            "normalized_hash": lower_hash, "window_start": 0, "window_end": 60,
            "calls_delta": 1, "exec_time_delta": 2, "completeness": "complete",
        }])
    assert len(LibraryStore(db_path, toml_path).load_all()) == 2
    with sqlite3.connect(tmp_path / "cache.db") as conn:
        assert conn.execute("SELECT normalized_hash FROM recent_observation").fetchall() == [(lower_hash,)]


def test_postgres_target_keeps_unquoted_case_folding_on_upgrade(tmp_path, monkeypatch):
    monkeypatch.setattr("shared.query_registry.query_registry.dialect_for_target",
                        lambda target: "postgres" if target == "pg" else None)
    toml_path = tmp_path / "queries.toml"
    db_path = library_db_path_for(toml_path)
    text = "SELECT * FROM Orders WHERE id=1"
    key = hash_sql(text, "postgres")
    LibraryStore(db_path, toml_path).apply_changes({}, {key: {
        "hash": key, "sql": text, "original_sql": text, "source": "manual", "last_target": "pg",
    }})
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version=17")
    assert set(LibraryStore(db_path, toml_path).load_all()) == {key}


def test_v16_pending_move_follows_repaired_survivor(tmp_path):
    toml_path = tmp_path / "queries.toml"
    db_path = library_db_path_for(toml_path)
    text = "SELECT * FROM Orders WHERE id=1"
    current = hash_sql(text)
    folded = _sql_digest(canonicalize_placeholder_style(
        identity_text(text, preserve_table_case=False)))
    LibraryStore(db_path, toml_path).apply_changes({}, {folded: {
        "hash": folded, "sql": text, "original_sql": text, "source": "manual",
    }})
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS pending_identity_move "
                     "(old_hash TEXT PRIMARY KEY,new_hash TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO pending_identity_move VALUES (?,?)", (current, folded))
        conn.execute("PRAGMA user_version=16")
    with ObservationStore(tmp_path / "cache.db") as cache:
        cache.record_recent_observations("db", [{
            "normalized_hash": current, "window_start": 0, "window_end": 60,
            "calls_delta": 2, "exec_time_delta": 4, "completeness": "complete",
        }])
    assert set(LibraryStore(db_path, toml_path).load_all()) == {current}
    with sqlite3.connect(tmp_path / "cache.db") as conn:
        assert conn.execute("SELECT normalized_hash, calls_delta FROM recent_observation").fetchall() == [
            (current, 2)]
    assert set(LibraryStore(db_path, toml_path).load_all()) == {current}


@pytest.mark.parametrize("suffix", [
    "WHERE message = 'FROM EVENTS'",
    'WHERE message = "JOIN EVENTS"',
    r"WHERE message = 'it\'s FROM EVENTS'",
    "WHERE message = 'it''s UPDATE EVENTS'",
    "/* FROM EVENTS JOIN EVENTS */",
    "-- FROM EVENTS\nWHERE id=1",
    "# UPDATE EVENTS\nWHERE id=1",
    "WHERE message IN ('INTO EVENTS', 'TABLE EVENTS')",
])
def test_mysql_table_case_preserves_literals_and_comments(suffix):
    sql = "SELECT * FROM EVENTS " + suffix
    assert mysql_table_case(sql, ["events"]) == "SELECT * FROM events " + suffix
