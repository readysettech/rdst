"""JSON path literals stay intact when queries are normalized or parameterized.

Regression for the MySQL report where `name->>'$.address.city' = 'xxx'`
came out as `NAME ->> ? = ?`: the path names a key in the document and must
survive normalization, only the compared value is data.
"""

import pytest

from shared.query_parameterization import normalize_for_registry, parameterize_for_llm
from shared.query_registry.sql_normalizer import normalize_and_extract, reconstruct_sql
from shared.sql_json_paths import is_json_path_literal, mask_json_paths

MYSQL_ARROW = "SELECT * FROM place WHERE name->>'$.address.city' = 'xxx' AND id = 12"


def test_registry_normalizer_keeps_mysql_json_path():
    normalized = normalize_for_registry(MYSQL_ARROW)["normalized_sql"]
    assert normalized == "SELECT * FROM place WHERE name->>'$.address.city' = '?' AND id = ?"


def test_llm_parameterizer_keeps_mysql_json_path():
    parameterized = parameterize_for_llm(MYSQL_ARROW)["parameterized_sql"]
    assert "name->>'$.address.city' = '<STRING_VALUE>'" in parameterized


@pytest.mark.parametrize(
    "sql, kept, replaced",
    [
        (
            "SELECT * FROM place WHERE JSON_EXTRACT(name, '$.items[0].id') = 'b'",
            ["'$.items[0].id'"],
            ["'b'"],
        ),
        (
            "SELECT * FROM place WHERE JSON_CONTAINS_PATH(name, 'one', '$.a') AND x = 'v'",
            ["'one'", "'$.a'"],
            ["'v'"],
        ),
        (
            "SELECT * FROM t WHERE data->>'city' = 'Boston' AND data #>> '{a,b}' = 'y'",
            ["'city'", "'{a,b}'"],
            ["'Boston'", "'y'"],
        ),
        (
            "SELECT * FROM t WHERE jsonb_path_exists(data, '$.a ? (@ > 5)') AND x = 'v'",
            ["'$.a ? (@ > 5)'"],
            ["'v'"],
        ),
    ],
)
def test_regex_normalizers_distinguish_paths_from_values(sql, kept, replaced):
    for text in (
        normalize_for_registry(sql)["normalized_sql"],
        parameterize_for_llm(sql)["parameterized_sql"],
    ):
        for literal in kept:
            assert literal in text, (literal, text)
        for literal in replaced:
            assert literal not in text, (literal, text)


def test_plain_string_values_still_parameterized():
    sql = "SELECT * FROM t WHERE created_at > '2026-01-01' AND email = 'a@b.com'"
    assert normalize_for_registry(sql)["normalized_sql"] == (
        "SELECT * FROM t WHERE created_at > '?' AND email = '?'"
    )


def test_is_json_path_literal_rules():
    sql = "SELECT a->'k', JSON_VALUE(doc, '$.x'), lower('$notapath'), f(doc, 'v') FROM t"
    literals = {sql[m.start() : m.end()]: (m.start(), m.end()) for m in __import__("re").finditer(r"'[^']*'", sql)}
    assert is_json_path_literal(sql, *literals["'k'"])
    assert is_json_path_literal(sql, *literals["'$.x'"])
    assert is_json_path_literal(sql, *literals["'$notapath'"])  # `$` prefix is treated as a path
    assert not is_json_path_literal(sql, *literals["'v'"])


def test_mask_and_restore_round_trip():
    sql = "SELECT * FROM t WHERE d->>'$.a[1]' = '1' AND n = 2"
    masked, restore = mask_json_paths(sql)
    assert "'$.a[1]'" not in masked
    assert restore(masked) == sql


def test_sqlglot_normalizer_generates_in_dialect_and_skips_paths():
    normalized, params = normalize_and_extract(
        "SELECT * FROM place p WHERE p.name->>'$.address.city' = 'xxx' LIMIT 5", dialect="mysql"
    )
    assert normalized == "SELECT * FROM place p WHERE p.name ->> '$.address.city' = :p2 LIMIT :p1"
    assert [p["value"] for p in params.values()] == ["5", "xxx"]
    assert reconstruct_sql(normalized, params, dialect="mysql") == (
        "SELECT * FROM place p WHERE p.name ->> '$.address.city' = 'xxx' LIMIT 5"
    )


def test_sqlglot_normalizer_postgres_paths_and_placeholders():
    normalized, params = normalize_and_extract(
        "SELECT * FROM t WHERE data->>'city' = 'x' AND data #>> '{a,b}' = 'y'", dialect="postgres"
    )
    assert normalized == "SELECT * FROM t WHERE data ->> 'city' = :p1 AND data #>> '{a,b}' = :p2"
    assert reconstruct_sql(normalized, params, dialect="postgres") == (
        "SELECT * FROM t WHERE data ->> 'city' = 'x' AND data #>> '{a,b}' = 'y'"
    )
