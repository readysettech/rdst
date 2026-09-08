from types import SimpleNamespace as NS
from decimal import Decimal
import pytest, sqlglot
from features.ask.count_name_completion import (
    plan_count_name,
    name_from_domain,
    candidate_sql,
    count_proof_sql,
    proved_result,
    accepts_result,
)

BASE = "SELECT (SELECT COUNT(*) FROM orders WHERE vendor='Acme') - (SELECT COUNT(*) FROM orders WHERE vendor='Beta') AS difference"
S = NS(
    tables={
        "orders": NS(
            columns={"vendor": NS(data_type="varchar"), "id": NS(data_type="int")}
        ),
        "vendors": NS(
            columns={"label": NS(data_type="varchar"), "id": NS(data_type="int")}
        ),
    }
)
ROWS = [[b"Acme Supply", 0, 0], [b"Beta", 0, 1], [None, None, None]]


@pytest.mark.parametrize(
    "sql",
    [
        BASE,
        BASE.replace(" - ", " + "),
        BASE.replace("COUNT(*)", "COUNT(id)"),
        BASE.replace("vendor='Acme'", "'Acme'=vendor").replace(
            "vendor='Beta'", "'Beta'=vendor"
        ),
        BASE + ";",
        BASE.replace("SELECT (", "SELECT /* test */ ("),
        BASE.replace("vendor='Acme'", "vendor='Acme'"),
    ],
)
def test_one_literal_only(sql):
    p = plan_count_name(sql, "mysql", S)
    assert p
    w = name_from_domain(p, ROWS)
    assert w and w.index == 0 and w.full == "Acme Supply"
    c = candidate_sql(p, w)
    assert c == sql.replace("'Acme'", "'Acme Supply'")
    assert count_proof_sql(p, w).count("COUNT(") == 3
    before = [[3]] if p.operation == "add" else [[-3]]
    expected = 5 if p.operation == "add" else -1
    assert proved_result(p, w, before, [[0, 3, 2]]) == expected
    assert accepts_result(expected, [[expected]]) and not accepts_result(
        expected, [[expected + 1]]
    )


@pytest.mark.parametrize(
    "sql",
    [
        BASE.replace(" - ", " * "),
        BASE.replace(" - ", " / "),
        BASE.replace(" - ", " UNION "),
        BASE + " LIMIT 1",
        BASE + "; DELETE FROM orders",
        BASE.replace("COUNT(*)", "SUM(id)"),
        BASE.replace("COUNT(*)", "COUNT(DISTINCT id)"),
        BASE.replace("COUNT(*)", "COUNT(1)"),
        BASE.replace("COUNT(*)", "MAX(id)"),
        BASE.replace("WHERE vendor='Acme'", "WHERE vendor LIKE 'Acme%'"),
        BASE.replace("WHERE vendor='Acme'", "WHERE vendor <> 'Acme'"),
        BASE.replace("WHERE vendor='Acme'", "WHERE vendor='Acme' AND id>1"),
        BASE.replace("WHERE vendor='Acme'", "WHERE vendor='Acme' GROUP BY id"),
        BASE.replace("WHERE vendor='Acme'", "WHERE vendor='Acme' LIMIT 1"),
        BASE.replace("WHERE vendor='Acme'", "WHERE vendor='Acme' ORDER BY id"),
        BASE.replace("vendor='Acme'", "id='Acme'"),
        BASE.replace("'Acme'", "'Beta'"),
        BASE.replace("'Acme'", '"Acme"'),
        BASE.replace("'Acme'", "'O\\'Name'"),
        BASE.replace("'Acme'", "'A'"),
        BASE.replace("'Acme'", "'Acme '"),
        BASE.replace("FROM orders", "FROM unknown"),
        BASE.replace("FROM orders", "FROM other.orders"),
        BASE.replace("FROM orders", "FROM orders FOR UPDATE"),
        BASE.replace("FROM orders", "FROM (SELECT * FROM orders) AS orders"),
        BASE.replace("WHERE vendor='Acme'", "WHERE missing='Acme'"),
        BASE.replace("'Acme'", "vendor"),
        BASE.replace("SELECT (", "SELECT 1, ("),
    ],
)
def test_unsupported_shapes(sql):
    assert plan_count_name(sql, "mysql", S) is None


@pytest.mark.parametrize("dialect", ["postgres", "postgresql", "sqlite"])
def test_other_dialects_abstain(dialect):
    assert plan_count_name(BASE, dialect, S) is None


@pytest.mark.parametrize(
    "rows",
    [
        [],
        None,
        [[b"Acme Supply", 0, 0]],
        [[b"Acme", 1, 0], [b"Beta", 0, 1]],
        [[b"Acme Supply", 0, 0], [b"Acme Systems", 0, 0], [b"Beta", 0, 1]],
        [[b"Acme Supply", 0, 0], [b"Beta", 0, 1], [b"BETA", 0, 1]],
        [[b"Acme Supply", 0, 0], [b"Beta", False, True]],
        [[b"Acme Supply", 0, 0], [b"Beta", 0, 1], [b"Beta", 0, 1]],
        [[b"Acme Supply", 0, 0], [b"Beta", 0, 1], [None, 0, 0]],
        [[b"Acme Supply", 0, 0], [b"Beta", 0, 1], [b"\xff", 0, 0]],
        [[b"AcmeSupply", 0, 0], [b"Beta", 0, 1]],
        [[b"Acme\\Supply", 0, 0], [b"Beta", 0, 1]],
        [[b"Acme Supply", 0, 0], [b"Beta", 0, 1]] + [[str(i), 0, 0] for i in range(99)],
    ],
)
def test_ambiguous_or_incomplete_catalog(rows):
    assert name_from_domain(plan_count_name(BASE, "mysql", S), rows) is None


@pytest.mark.parametrize(
    "proof",
    [
        [[1, 3, 2]],
        [[0, 0, 2]],
        [[0, 3, 0]],
        [[0, 3, -2]],
        [[0, 4, 2]],
        [[0, 3, 2.5]],
        [[False, 3, 2]],
        [[0, None, 2]],
        [[0, 3, 2, 1]],
        [],
        [[0, 3, 2], [0, 3, 2]],
    ],
)
def test_failed_counterfactual(proof):
    p = plan_count_name(BASE, "mysql", S)
    w = name_from_domain(p, ROWS)
    assert proved_result(p, w, [[-3]], proof) is None


def test_right_operand_preserves_sign():
    sql = (
        BASE.replace("'Acme'", "'Temporary'")
        .replace("'Beta'", "'Acme'")
        .replace("'Temporary'", "'Beta'")
    )
    p = plan_count_name(sql, "mysql", S)
    w = name_from_domain(p, [[b"Beta", 1, 0], [b"Acme Supply", 0, 0]])
    assert w.index == 1
    assert proved_result(p, w, [[3]], [[3, 0, 2]]) == 1


def test_join_binds_same_real_column():
    sql = BASE.replace(
        "FROM orders WHERE vendor=",
        "FROM orders o JOIN vendors v ON o.id=v.id WHERE v.label=",
    )
    p = plan_count_name(sql, "mysql", S)
    assert p and p.table == "vendors" and p.column == "label"


def test_apostrophe_in_full_name_is_doubled():
    p = plan_count_name(BASE, "mysql", S)
    w = name_from_domain(p, [[b"Acme O'Neil", 0, 0], [b"Beta", 0, 1]])
    assert w
    assert "'Acme O''Neil'" in candidate_sql(p, w)
