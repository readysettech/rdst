"""One logical query, as every entry point presents it, has one identity.

Queries reach RDST from pg_stat_statements, MySQL statement digests, pasted SQL (Analyze,
the Database page's "Analyze a query", `rdst analyze -q`, `rdst query add`, MCP tools,
which run the CLI), Ask, `rdst scan` and `rdst top`. Each row below is the spelling one of
them hands to its identity call; all of them must hash alike and name the same slots.
"""

from __future__ import annotations

import re
import pytest

from features.analyze.cli.command import AnalyzeCommand
from features.monitor.api import routes
from features.query_registry.discovery import _usable_statement_text
from features.top.service import TopService
from shared.monitor.monitor_store import MonitorStore
from shared.query_registry.query_registry import (
    QueryRegistry,
    canonicalize_sql,
    extract_observed_params,
    hash_sql,
)
from shared.query_registry.sql_normalizer import normalize_and_extract, reconstruct_sql

LITERAL = "SELECT id, name FROM users WHERE email = 'a@b.c' AND status IN (1, 2, 3) LIMIT 10"

# (entry point, text as that entry point presents it)
SPELLINGS = [
    ("pasted literal SQL", LITERAL),
    ("pasted, case/whitespace/comment/semicolon variant",
     "/* app:web */ select ID,\n    name\nfrom users\nwhere email='x@y.z'  and status in (7)\n"
     "limit 5;"),
    ("pasted, shorter IN list", LITERAL.replace("(1, 2, 3)", "(4, 5)")),
    ("pg_stat_statements", "SELECT id, name FROM users WHERE email = $1 AND status IN "
                           "($2, $3, $4) LIMIT $5"),
    ("pg_stat_statements, squashed list (PG 18)",
     "SELECT id, name FROM users WHERE email = $1 AND status IN ($2 /*, ... */) LIMIT $3"),
    ("rdst scan ($N placeholders)",
     "SELECT id, name FROM users WHERE email = $1 AND status IN ($2) LIMIT $3"),
    ("registry text (:pN)",
     "SELECT id, name FROM users WHERE email = :p1 AND status IN (:p2, :p3) LIMIT :p4"),
    ("MySQL digest", "SELECT `id` , `name` FROM `users` WHERE `email` = ? AND `status` "
                     "IN (...) LIMIT ?"),
    ("MySQL pasted with backticks",
     "SELECT `id`, `name` FROM `users` WHERE `email` = 'q' AND `status` IN (8, 9) LIMIT 20"),
    ("MySQL anonymous ? list", "SELECT id, name FROM users WHERE email = ? AND status IN "
                               "(?, ?, ?) LIMIT ?"),
]
IDENTITY = hash_sql(LITERAL)


@pytest.mark.parametrize("source,text", SPELLINGS, ids=[s for s, _ in SPELLINGS])
def test_every_spelling_hashes_to_one_identity(source, text):
    assert hash_sql(text) == IDENTITY, source


@pytest.mark.parametrize("source,text", SPELLINGS, ids=[s for s, _ in SPELLINGS])
def test_entry_points_agree(source, text, tmp_path, monkeypatch):
    # Collector lanes (pg_stat_statements, MySQL digest) alias engine keys this way.
    assert hash_sql(_usable_statement_text(text)) == IDENTITY
    # rdst top and the realtime saver.
    assert TopService._compute_registry_hash(None, text) == IDENTITY
    # rdst analyze -q (and MCP rdst_analyze with a query).
    assert AnalyzeCommand._resolve_inline_query(None, text, "").hash == IDENTITY
    # rdst query add checks for a collision with this hash before saving.
    assert hash_sql(canonicalize_sql(text)) == IDENTITY
    # The Database page's "Analyze a query".
    store = MonitorStore(tmp_path / "monitor.db")
    monkeypatch.setattr(routes, "_store", lambda: store)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.load", lambda self: None)
    monkeypatch.setattr("shared.config.targets.TargetsConfig.get", lambda self, target: {})
    added = routes.add_query("db", routes.AddQueryRequest(sql=text))
    assert added["key"] == IDENTITY


def test_registry_saves_converge_on_one_entry(tmp_path):
    """Analyze, Ask, query add, top and scan all store through add_query."""
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    saves = [
        dict(sql=LITERAL, source="analyze", analyzed=True),
        dict(sql=LITERAL.lower(), source="ask", question="users by email"),
        dict(sql=LITERAL.replace("(1, 2, 3)", "(4)"), source="manual", save_intent=True),
        dict(sql=SPELLINGS[3][1], source="top", observed=True),
        dict(sql=SPELLINGS[5][1], source="scan", skip_param_extraction=True),
        dict(sql=SPELLINGS[7][1], source="top-historical", dialect="mysql",
             observed_params_sql="SELECT id, name FROM users WHERE email = 'm@n.o' "
                                 "AND status IN (6) LIMIT 3"),
    ]
    hashes = {registry.add_query(**save)[0] for save in saves}
    assert hashes == {IDENTITY}
    assert len(registry.list_queries()) == 1


def _textual_values(sql: str, dialect: str | None) -> list:
    normalized, params = normalize_and_extract(canonicalize_sql(sql), dialect)
    return [params[f"p{n}"]["value"] for n in re.findall(r":p(\d+)\b", normalized)]


SCALAR = "SELECT id FROM orders WHERE customer_id = 42 AND state = 'paid' LIMIT 10"
SCALAR_SPELLINGS = [
    ("postgres", SCALAR),
    ("postgres", "select id from ORDERS where customer_id=42 and state='paid' limit 10;"),
    ("mysql", SCALAR),
    ("mysql", "SELECT `id` FROM `orders` WHERE `customer_id` = 42 AND `state` = 'paid' "
              "LIMIT 10"),
]


@pytest.mark.parametrize("dialect,text", SCALAR_SPELLINGS)
def test_literal_spellings_extract_the_same_values_in_slot_order(dialect, text):
    assert hash_sql(text, dialect) == hash_sql(SCALAR, dialect)
    assert _textual_values(text, dialect) == ["42", "paid", "10"]


@pytest.mark.parametrize("dialect,engine_text", [
    ("postgres", "SELECT id FROM orders WHERE customer_id = $1 AND state = $2 LIMIT $3"),
    ("mysql", "SELECT `id` FROM `orders` WHERE `customer_id` = ? AND `state` = ? LIMIT ?"),
])
def test_engine_texts_take_live_values_in_the_same_slots(dialect, engine_text):
    assert hash_sql(engine_text) == hash_sql(SCALAR)
    observed = extract_observed_params(SCALAR, engine_text, dialect)
    assert {name: value["value"] for name, value in observed.items()} == {
        "p1": "42", "p2": "paid", "p3": "10"}
    # The stored engine text and these values rebuild the live statement.
    rebuilt = reconstruct_sql(engine_text, observed, dialect)
    assert hash_sql(rebuilt) == hash_sql(SCALAR)
    assert "42" in rebuilt and "'paid'" in rebuilt


def test_stored_text_and_values_stay_a_pair_across_spellings(tmp_path):
    """A later spelling's values replace the entry's text too, so they never misalign."""
    registry = QueryRegistry(registry_path=str(tmp_path / "queries.toml"))
    digest = "SELECT `id` FROM `orders` WHERE `customer_id` = ? AND `state` = ? LIMIT ?"
    registry.add_query(sql=digest, dialect="mysql", source="top-historical",
                       observed_params_sql=SCALAR)
    key, _ = registry.add_query(
        sql="SELECT id FROM orders WHERE customer_id = 7 AND state = 'new' LIMIT 3",
        dialect="mysql", source="analyze")
    entry = registry.get_query(key)
    rebuilt = reconstruct_sql(entry.sql, entry.parameters, "mysql")
    assert "customer_id = 7" in rebuilt and "state = 'new'" in rebuilt
    assert "LIMIT 3" in rebuilt


@pytest.mark.parametrize("text", [
    # Postgres texts whose identity must not move: no value list, no MySQL spelling.
    "SELECT id FROM orders WHERE customer_id = $1",
    'SELECT "User"."id" FROM "User" WHERE "User"."email" = $1',
    "SELECT count(*) FROM orders WHERE placed_at > now() - interval '1 day'",
    "UPDATE orders SET state = $1 WHERE id = $2",
    "SELECT data->>'name' FROM docs WHERE data @> $1::jsonb",
])
def test_postgres_identity_is_unchanged_without_value_lists(text):
    from shared.query_registry.query_registry import _sql_digest, normalize_sql
    from shared.query_registry.sql_normalizer import canonicalize_placeholder_style

    assert hash_sql(text) == _sql_digest(canonicalize_placeholder_style(normalize_sql(text)))


def test_quoted_postgres_identifiers_keep_their_case():
    assert hash_sql('SELECT id FROM "Users"', "postgres") != hash_sql(
        "SELECT id FROM users", "postgres")
    assert hash_sql("SELECT id FROM Users", "postgres") == hash_sql(
        "SELECT id FROM users", "postgres")


def test_schema_qualified_names_stay_distinct():
    assert hash_sql("SELECT id FROM shop.users") != hash_sql("SELECT id FROM users")
    assert hash_sql("SELECT `id` FROM `shop` . `users`") == hash_sql(
        "SELECT id FROM shop.users")



def test_library_upgrade_rekeys_identities_onto_the_identity_hash(tmp_path):
    import sqlite3

    from shared.query_registry.library_store import LibraryStore, library_db_path_for
    from shared.query_registry.query_registry import _sql_digest, normalize_sql
    from shared.query_registry.sql_normalizer import canonicalize_placeholder_style

    toml_path = tmp_path / "queries.toml"
    registry = QueryRegistry(registry_path=str(toml_path))
    text = SPELLINGS[3][1]
    key, _ = registry.add_query(sql=text, source="top", observed=True)
    previous = _sql_digest(canonicalize_placeholder_style(normalize_sql(text)))
    assert previous != key
    db_path = library_db_path_for(toml_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE query_identity SET hash=?", (previous,))
        conn.execute("UPDATE target_query SET rm_hash=?", (previous,))
        conn.execute("PRAGMA user_version=15")
    upgraded = LibraryStore(db_path, toml_path).load_all()
    assert set(upgraded) == {IDENTITY}
