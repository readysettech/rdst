"""Backend tests for Ask query-spine persistence (T13 + T15 + U3).

- The registry stores the ORIGINAL submitted SQL separately from the dedupe
  hash, so hand-offs (re-analyze / re-cache / history re-ask) match what ran.
- Ask-sourced entries persist the natural-language question (feeds /ask/history
  and a readable saved-queries label).
- SQL validation flags an injected LIMIT so the UI can surface it (T15).
"""

from __future__ import annotations

from features.ask.sql_validation import validate_sql_for_ask
from shared.query_registry.query_registry import QueryRegistry, hash_sql, canonicalize_sql


def test_original_sql_is_stored_and_dedupe_hash_stays_separate(temp_dir):
    registry = QueryRegistry(registry_path=str(temp_dir / "queries.toml"))
    # Positional GROUP BY/ORDER BY is exactly what normalization rewrites to :p1.
    sql = "SELECT votetypeid, count(*) FROM votes GROUP BY 1 ORDER BY 2 DESC"
    query_hash, is_new = registry.add_query(sql, source="ask", tag="votes_by_type")
    assert is_new is True

    entry = registry.get_query(query_hash)
    # original_sql preserves the positional clauses; normalized `sql` does not.
    assert "GROUP BY 1" in entry.original_sql
    assert "ORDER BY 2" in entry.original_sql
    # The dedupe hash is computed from the canonical form, independent of the
    # stored original — it must equal the canonical hash, not depend on it.
    assert entry.hash == hash_sql(canonicalize_sql(sql))
    # The normalized SQL parameterizes the positional references.
    assert entry.original_sql != entry.sql


def test_question_persists_on_ask_entries(temp_dir):
    registry = QueryRegistry(registry_path=str(temp_dir / "queries.toml"))
    question = "Which vote type is most common?"
    query_hash, _ = registry.add_query(
        "SELECT votetypeid, count(*) FROM votes GROUP BY 1",
        source="ask",
        tag="votes_by_type",
        question=question,
    )
    entry = registry.get_query(query_hash)
    assert entry.question == question
    assert entry.source == "ask"


def test_persistence_round_trips_through_disk(temp_dir):
    path = str(temp_dir / "queries.toml")
    registry = QueryRegistry(registry_path=path)
    query_hash, _ = registry.add_query(
        "SELECT id FROM posts WHERE score > 100",
        source="ask",
        tag="high_score_posts",
        question="Which posts scored over 100?",
    )
    registry.save()

    # A fresh instance reads the persisted fields back (backward-compat safe).
    reloaded = QueryRegistry(registry_path=path)
    reloaded.load()
    entry = reloaded.get_query(query_hash)
    assert entry.question == "Which posts scored over 100?"
    assert "score > 100" in entry.original_sql


def test_backfills_original_sql_on_re_add(temp_dir):
    registry = QueryRegistry(registry_path=str(temp_dir / "queries.toml"))
    sql = "SELECT * FROM comments WHERE postid = 42"
    query_hash, _ = registry.add_query(sql, source="top")
    # Simulate a pre-persistence entry that lacks original_sql.
    registry.get_query(query_hash).original_sql = ""
    # Re-adding the same query backfills it without changing the hash.
    same_hash, is_new = registry.add_query(sql, source="ask", question="q")
    assert same_hash == query_hash
    assert is_new is False
    entry = registry.get_query(query_hash)
    assert "postid = 42" in entry.original_sql
    assert entry.question == "q"


def test_validation_flags_injected_limit():
    result = validate_sql_for_ask("SELECT * FROM votes", default_limit=100)
    assert result["is_valid"] is True
    assert result["limit_added"] is True
    assert result["limit_reduced"] is False
    assert "LIMIT 100" in result["validated_sql"]


def test_validation_does_not_flag_when_limit_present():
    result = validate_sql_for_ask("SELECT * FROM votes LIMIT 5", default_limit=100)
    assert result["limit_added"] is False
    assert result["validated_sql"] == "SELECT * FROM votes LIMIT 5"


def test_validation_flags_reduced_limit_not_added():
    result = validate_sql_for_ask(
        "SELECT * FROM votes LIMIT 999999", default_limit=100, max_limit=1000
    )
    assert result["limit_added"] is False
    assert result["limit_reduced"] is True
