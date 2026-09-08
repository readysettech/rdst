import pytest
from types import SimpleNamespace as NS
from features.ask.identifier_quoting import (
    mysql_syntax_error,
    plan_identifier_quoting,
    candidate_from_keywords,
)

ERROR = "(1064, 'syntax error near reserved word')"
S = NS(
    tables={x: NS(columns={}) for x in ["groups", "rank", "safe_table", "Condition"]}
)


@pytest.mark.parametrize(
    "sql,expected",
    [
        ("SELECT amount FROM groups", "SELECT amount FROM `groups`"),
        (
            "SELECT x.amount\nFROM groups AS x WHERE x.label='groups' /* groups */;",
            "SELECT x.amount\nFROM `groups` AS x WHERE x.label='groups' /* groups */;",
        ),
        (
            "SELECT g.amount FROM groups g JOIN rank r ON r.id=g.id",
            "SELECT g.amount FROM `groups` g JOIN `rank` r ON r.id=g.id",
        ),
        (
            "SELECT g.amount FROM groups g JOIN safe_table s ON s.id=g.id",
            "SELECT g.amount FROM `groups` g JOIN safe_table s ON s.id=g.id",
        ),
        (
            "SELECT a.amount FROM groups a WHERE EXISTS(SELECT 1 FROM groups b WHERE b.id=a.id)",
            "SELECT a.amount FROM `groups` a WHERE EXISTS(SELECT 1 FROM `groups` b WHERE b.id=a.id)",
        ),
        (
            "SELECT g.amount FROM groups g JOIN `rank` r ON r.id=g.id",
            "SELECT g.amount FROM `groups` g JOIN `rank` r ON r.id=g.id",
        ),
        (
            "SELECT 'Türkçe Ω' AS note, amount FROM groups",
            "SELECT 'Türkçe Ω' AS note, amount FROM `groups`",
        ),
        ("SELECT amount FROM Condition", "SELECT amount FROM `Condition`"),
        (
            "SELECT amount FROM groups WHERE label='a\\\\b'",
            "SELECT amount FROM `groups` WHERE label='a\\\\b'",
        ),
        ('SELECT "label" FROM groups', 'SELECT "label" FROM `groups`'),
        (
            "SELECT amount FROM groups WHERE id=1||id=2",
            "SELECT amount FROM `groups` WHERE id=1||id=2",
        ),
        (
            "SELECT t.amount FROM (SELECT amount FROM groups) t",
            "SELECT t.amount FROM (SELECT amount FROM `groups`) t",
        ),
    ],
)
def test_exact_token_changes(sql, expected):
    p = plan_identifier_quoting(sql, "mysql", S, ERROR)
    assert p
    words = {t.name.upper() for t in p.tokens}
    rows = [[w, 0 if w == "SAFE_TABLE" else 1] for w in sorted(words)]
    assert candidate_from_keywords(p, rows) == expected


@pytest.mark.parametrize(
    "error",
    [
        None,
        "",
        "(1146, 'missing table')",
        "(1054, 'unknown column')",
        "1064",
        "('1064', 'syntax')",
        "(1064, None)",
        "(1064, '')",
        "[1064, 'syntax']",
        "(1064, 'syntax', 'extra')",
        "Syntax error, try quoting",
        '(True, "syntax")',
    ],
)
def test_unsupported_error(error):
    assert not mysql_syntax_error(error)
    assert (
        plan_identifier_quoting("SELECT amount FROM groups", "mysql", S, error) is None
    )


@pytest.mark.parametrize(
    "sql,dialect,schema",
    [
        ("SELECT amount FROM groups", "postgres", S),
        ("SELECT amount FROM groups", "postgresql", S),
        ("SELECT amount FROM groups", "sqlite", S),
        ("SELECT amount FROM groups", "mysql", None),
        ("SELECT amount FROM unknown_table", "mysql", S),
        ("SELECT amount FROM `groups`", "mysql", S),
        ("SELECT 1", "mysql", S),
        ("SELECT * FROM otherdb.groups", "mysql", S),
        ("SELECT amount FROM groups; SELECT 2", "mysql", S),
        ("DELETE FROM groups", "mysql", S),
        ("UPDATE groups SET amount=2", "mysql", S),
        ('SELECT amount FROM groups INTO OUTFILE "/tmp/x"', "mysql", S),
        ("SELECT amount FROM groups FOR UPDATE", "mysql", S),
        ("WITH groups AS (SELECT 1 AS amount) SELECT amount FROM groups", "mysql", S),
        ("SELECT amount FROM groups UNION SELECT amount FROM rank", "mysql", S),
        ("SELECT amount FROM groups USE INDEX (idx)", "mysql", S),
        ("SELECT amount FROM GROUPS", "mysql", S),
        ("SELECT FROM", "mysql", S),
    ],
)
def test_unsupported_shape(sql, dialect, schema):
    assert plan_identifier_quoting(sql, dialect, schema, ERROR) is None


@pytest.mark.parametrize(
    "rows",
    [
        None,
        [],
        [["GROUPS", 0]],
        [["GROUPS", True]],
        [["GROUPS", "1"]],
        [["groups", 1]],
        [["RANK", 1]],
        [["GROUPS", 1], ["GROUPS", 1]],
        [["GROUPS", 2]],
        [["GROUPS", 1, "x"]],
        ["GROUPS"],
    ],
)
def test_ambiguous_keyword_proof(rows):
    p = plan_identifier_quoting("SELECT amount FROM groups", "mysql", S, ERROR)
    assert p
    assert candidate_from_keywords(p, rows) is None
