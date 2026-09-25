"""Database page Library reads release SQLite readers before decoding entries."""
import sqlite3

import pytest

from shared.query_registry import library_store, observation_store
from shared.query_registry.query_registry import QueryRegistry


@pytest.mark.parametrize('method', ['load_keys', 'load_all'])
@pytest.mark.parametrize('decoder', ['_decode_identity', '_decode_lifecycle'])
def test_page_decode_allows_a_rollback_journal_writer(tmp_path, monkeypatch, decoder, method):
    monkeypatch.setattr(observation_store, '_journal_mode_for_runtime', lambda: 'delete')
    registry = QueryRegistry(registry_path=str(tmp_path / 'queries.toml'), use_sqlite=True)
    hashes = [registry.add_query(f'SELECT * FROM table_{index}', target='scale')[0]
              for index in range(2)]
    store = registry.library_store
    decode = getattr(library_store, decoder)
    commits = []

    def decode_with_writer(row):
        with sqlite3.connect(store.path, timeout=0) as writer:
            writer.execute("UPDATE query_identity SET tag='updated' WHERE hash=?", (hashes[0],))
        commits.append(row)
        return decode(row)

    monkeypatch.setattr(library_store, decoder, decode_with_writer)
    entries = store.load_keys(hashes) if method == 'load_keys' else store.load_all()
    assert set(entries) == set(hashes)
    assert len(commits) == 2
    assert all('scale' in entry['target_lifecycle'] for entry in entries.values())


@pytest.mark.parametrize('value', [0.12345678901234566, 1.0000000000000002, 1.0e-250, 1.0e250])
def test_library_materialization_preserves_float_values(tmp_path, value):
    registry = QueryRegistry(registry_path=str(tmp_path / 'queries.toml'), use_sqlite=True)
    key, _ = registry.add_query('SELECT * FROM floats', avg_duration_ms=value, max_duration_ms=value)
    store = registry.library_store
    assert store.load_keys([key])[key]['avg_duration_ms'] == value
    assert store.load_all()[key]['max_duration_ms'] == value
