from features.ask import name_format as m
import pytest, sqlglot
from sqlglot import exp


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize(
    "suffix",
    [
        "",
        " WHERE score > (SELECT AVG(score) FROM contacts)",
        " ORDER BY phone",
        " GROUP BY given,family,phone",
        " LIMIT 5",
    ],
)
def test_preserves_every_nonprojection_clause(dialect, suffix):
    sql = (
        "SELECT DISTINCT CONCAT(given,' ',family) AS full_name,phone FROM contacts"
        + suffix
    )
    p = m.plan_name_format(sql, dialect)
    assert p
    a = sqlglot.parse_one(sql, read=dialect)
    b = sqlglot.parse_one(p.candidate_sql, read=dialect)
    b.set("expressions", [e.copy() for e in a.expressions])
    assert a == b


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_distinct_on_not_supported(dialect):
    assert (
        m.plan_name_format(
            "SELECT DISTINCT ON (given) CONCAT(given,' ',family) FROM contacts", dialect
        )
        is None
    )


@pytest.mark.parametrize(
    "original,candidate,expected",
    [
        ([["Ari Vale", "1"]], [["Ari", "Vale", "1"]], True),
        (
            [["Ari Bo Vale", "1"]],
            [["Ari Bo", "Vale", "1"], ["Ari", "Bo Vale", "1"]],
            False,
        ),
        ([["Ari Vale", "1"]], [["Ari", "Vale", "1"], ["Ari", "Vale", "1"]], False),
        ([[None, "1"]], [[None, "Vale", "1"]], False),
        ([["Ari Vale", "1"]], [["Ari", "Vale", "2"]], False),
        ([["Ari Vale", None]], [["Ari", "Vale", None]], True),
    ],
)
def test_complete_distinct_reconstruction(original, candidate, expected):
    p = m.plan_name_format(
        "SELECT DISTINCT CONCAT(given,' ',family),phone FROM contacts", "mysql"
    )
    assert m.accepts_name_format(original, candidate, p) == expected
