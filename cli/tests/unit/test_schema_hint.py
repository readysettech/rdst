"""Tests for build_schema_hint in schema_collector."""

from features.schema.schema_collector import build_schema_hint


def test_no_semantic_layer_returns_init_and_profile_hint():
    hint = build_schema_hint("mydb", has_semantic_layer=False, has_column_stats=False)
    assert hint is not None
    assert "schema init" in hint
    assert "schema profile" in hint
    assert "--target mydb" in hint


def test_no_column_stats_returns_profile_only_hint():
    hint = build_schema_hint("mydb", has_semantic_layer=True, has_column_stats=False)
    assert hint is not None
    assert "schema profile" in hint
    assert "schema init" not in hint
    assert "--target mydb" in hint


def test_all_present_returns_none():
    assert build_schema_hint("mydb", has_semantic_layer=True, has_column_stats=True) is None


def test_target_name_interpolated():
    hint = build_schema_hint("prod-db", has_semantic_layer=False, has_column_stats=False)
    assert "--target prod-db" in hint
