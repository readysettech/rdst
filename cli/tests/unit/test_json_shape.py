"""Tests for JSON column shape detection."""

from features.schema.semantic_layer.json_shape import (
    MAX_JSON_KEYS,
    json_sample_sql_mysql,
    json_sample_sql_postgres,
    summarize_json_documents,
)


def test_nested_paths_with_types_and_examples():
    documents = [
        '{"power": {"now_w": 412.5, "unit": "W"}, "ok": true}',
        {"power": {"now_w": None, "unit": "W"}, "ok": False},
    ]
    assert summarize_json_documents(documents) == {
        "ok": "boolean, e.g. true",
        "power.now_w": "null|number, e.g. 412.5",
        "power.unit": 'string, e.g. "W"',
    }


def test_array_elements_use_bracket_paths():
    summary = summarize_json_documents(['{"readings": [{"v": 1}, {"v": 2}], "tags": ["a"]}'])
    assert summary == {"readings[].v": "number, e.g. 1", "tags[]": 'string, e.g. "a"'}


def test_empty_containers_and_invalid_documents():
    assert summarize_json_documents(['{"extra": {}}', "not json", None]) == {"extra": "object"}


def test_most_common_paths_are_kept_and_examples_truncated():
    rare = [{f"k{index}": 1} for index in range(MAX_JSON_KEYS)]
    common = [{"note": "x" * 100}] * 2
    summary = summarize_json_documents(common + rare)
    assert len(summary) == MAX_JSON_KEYS
    assert next(iter(summary)) == "note"
    assert len(summary["note"].partition("e.g. ")[2]) == 40


def test_sample_queries_are_bounded():
    assert json_sample_sql_postgres('"t"', '"c"', 1_000, "") == (
        'SELECT "c"::text FROM "t" WHERE "c" IS NOT NULL LIMIT 100'
    )
    assert json_sample_sql_mysql("`t`", "`c`", 1_000, "") == (
        "SELECT CAST(`c` AS CHAR) FROM `t` WHERE `c` IS NOT NULL LIMIT 100"
    )


def test_large_tables_are_sampled():
    assert 'FROM "t" TABLESAMPLE SYSTEM(1.0) WHERE' in json_sample_sql_postgres(
        '"t"', '"c"', 1_000_000, ""
    )
    assert "FROM (SELECT `c` FROM `t` LIMIT 10000) AS sampled WHERE" in json_sample_sql_mysql(
        "`t`", "`c`", 1_000_000, ""
    )
