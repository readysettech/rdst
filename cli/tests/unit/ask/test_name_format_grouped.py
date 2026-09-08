"""Grouped output splitting preserves group keys and full result multiplicity."""

import pytest
import sqlglot
from features.ask.name_format import plan_name_format, accepts_name_format

BASE = "SELECT CONCAT(c.given,' ',c.family) AS full_name,c.phone FROM contacts c JOIN invoices i ON i.contact_id=c.id WHERE i.amount>(SELECT AVG(amount) FROM invoices) GROUP BY c.id,c.given,c.family,c.phone"


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize(
    "suffix", ["", " ORDER BY c.phone", " ORDER BY c.phone LIMIT 2"]
)
def test_grouped_projection_preserves_every_row_clause(dialect, suffix):
    sql = BASE + suffix
    plan = plan_name_format(sql, dialect)
    assert plan is not None
    before, after = [
        sqlglot.parse_one(s, read=dialect) for s in (sql, plan.candidate_sql)
    ]
    assert all(
        after.args.get(k) == v for k, v in before.args.items() if k != "expressions"
    )
    assert [x.sql() for x in after.expressions] == ["c.given", "c.family", "c.phone"]
    assert accepts_name_format(
        [["Ari Vale", "123"], ["Ari Vale", "123"]],
        [["Ari", "Vale", "123"], ["Ari", "Vale", "123"]],
        plan,
    )
    assert not accepts_name_format(
        [["Ari Vale", "123"], ["Ari Vale", "123"]], [["Ari", "Vale", "123"]], plan
    )


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CONCAT(given,' ',family) FROM contacts GROUP BY given",
        "SELECT CONCAT(given,' ',family),phone FROM contacts GROUP BY given,family",
        "SELECT CONCAT(given,' ',family) AS name FROM contacts GROUP BY name",
        "SELECT CONCAT(given,' ',family) FROM contacts GROUP BY 1",
        "SELECT CONCAT(given,' ',family) FROM contacts GROUP BY CONCAT(given,' ',family)",
        "SELECT CONCAT(given,' ',family) FROM contacts GROUP BY given,family WITH ROLLUP",
        "SELECT CONCAT(given,' ',family) FROM contacts GROUP BY CUBE(given,family)",
        "SELECT CONCAT(given,' ',family) FROM contacts GROUP BY GROUPING SETS ((given),(family))",
        "SELECT CONCAT(given,' ',family),COUNT(*) FROM contacts GROUP BY given,family",
        "SELECT CONCAT(given,' ',family) FROM contacts GROUP BY given,family HAVING COUNT(*)>1",
        "SELECT CONCAT(c.given,' ',c.family) FROM contacts c GROUP BY given,family",
        "SELECT CONCAT(given,' ',family) AS name FROM contacts GROUP BY given,family ORDER BY name",
        "SELECT CONCAT(given,' ',family) FROM contacts GROUP BY given,family ORDER BY 1",
    ],
)
def test_unproven_group_keys_and_extended_grouping_abstain(dialect, sql):
    assert plan_name_format(sql, dialect) is None


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ([["Ari Vale", "123"]], [["Ari", "Vale", "123"]], True),
        ([["Ari Vale", "123"]], [["Ari", "Vale", "999"]], False),
        ([["Ari Vale", "123"]], [["ARI", "Vale", "123"]], False),
        ([[None, "123"]], [[None, "Vale", "123"]], False),
        ([[" Vale", "123"]], [["", "Vale", "123"]], True),
        (
            [["Ari Vale", "123"]],
            [["Ari", "Vale", "123"], ["Ari", "Vale", "123"]],
            False,
        ),
    ],
)
def test_grouped_complete_reconstruction(before, after, expected):
    assert (
        accepts_name_format(before, after, plan_name_format(BASE, "mysql")) is expected
    )
