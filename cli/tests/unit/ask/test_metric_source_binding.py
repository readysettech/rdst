from decimal import Decimal
from types import SimpleNamespace as NS
import pytest, sqlglot
from sqlglot import exp
from features.ask.metric_source_binding import (
    plan_annual_binding,
    proves_dimension_key,
    proved_annual_winner,
    accepts_annual_winner,
)

SQL = "SELECT YEAR(i.billed_on) AS year FROM invoices i JOIN sites s ON i.site_id=s.site_id WHERE s.region='West' GROUP BY YEAR(i.billed_on) ORDER BY SUM(i.charge) DESC LIMIT 1"


def schema():
    def table(name, cols, rel=None):
        return NS(
            name=name,
            columns={n: NS(name=n, data_type=t) for n, t in cols.items()},
            relationships=rel or [],
        )

    return NS(
        tables={
            "sites": table("sites", {"site_id": "int", "region": "varchar"}),
            "invoices": table(
                "invoices",
                {
                    "invoice_id": "int",
                    "site_id": "int",
                    "billed_on": "date",
                    "charge": "decimal",
                },
            ),
            "energy_usage": table(
                "energy_usage",
                {
                    "site_id": "int",
                    "reporting_month": "varchar",
                    "energy_used": "double",
                },
                [
                    NS(
                        target_table="sites",
                        join_pattern="energy_usage.site_id=sites.site_id",
                        relationship_type="many_to_one",
                    )
                ],
            ),
            "water_usage": table(
                "water_usage",
                {
                    "site_id": "int",
                    "reporting_month": "varchar",
                    "water_used": "double",
                },
                [
                    NS(
                        target_table="sites",
                        join_pattern="water_usage.site_id=sites.site_id",
                        relationship_type="many_to_one",
                    )
                ],
            ),
        }
    )


def test_binding_changes_only_declared_fact_measure_period_and_key():
    p = plan_annual_binding(SQL, "mysql", schema())
    assert p and len(p.options) == 2
    assert p.facts()["current_binding"] == {
        "table": "invoices",
        "measure": "charge",
        "period": "billed_on",
    }
    assert p.options[0]["table"] == "energy_usage"
    a = sqlglot.parse_one(SQL, read="mysql")
    b = sqlglot.parse_one(p.candidate_sql("b0"), read="mysql")
    proof = sqlglot.parse_one(p.period_proof_sql("b0"), read="mysql")
    assert a.args["where"] == b.args["where"] == proof.args["where"]
    assert a.args["joins"] == b.args["joins"] == proof.args["joins"]
    assert a.args["limit"] == b.args["limit"]
    assert a.expressions[0].alias == b.expressions[0].alias
    assert isinstance(b.expressions[0].this, exp.Substring)
    assert next(b.find_all(exp.Table)).name == "energy_usage"
    assert next(b.find_all(exp.Sum)).this.name == "energy_used"
    assert proof.args["limit"].expression.this == "1001"
    assert len(proof.expressions) == 4
    assert p.candidate_sql("invented") is None


@pytest.mark.parametrize(
    "sql",
    [
        SQL + " OFFSET 1",
        SQL + " FOR UPDATE",
        SQL.replace("DESC", "ASC"),
        SQL.replace("LIMIT 1", "LIMIT 2"),
        SQL.replace("SUM(i.charge)", "AVG(i.charge)"),
        SQL.replace("SUM(i.charge)", "SUM(DISTINCT i.charge)"),
        SQL.replace("i.billed_on", "s.billed_on"),
        SQL.replace("i.charge", "charge"),
        SQL.replace("SELECT YEAR", "SELECT DISTINCT YEAR"),
        SQL.replace("s.region='West'", "s.region='West' AND i.charge>0"),
        SQL.replace("s.region='West'", "s.region='West' OR s.region='East'"),
        SQL.replace("JOIN sites", "LEFT JOIN sites"),
        SQL.replace("s.site_id", "s.missing"),
        SQL.replace("i.site_id=s.site_id", "i.site_id=s.site_id AND i.charge=1"),
        SQL.replace("invoices i", "external.invoices i"),
        SQL.replace("invoices i", "(SELECT * FROM invoices) i"),
        SQL.replace("i.charge", "RAND()"),
        SQL.replace("s.region='West'", "1=1"),
        SQL + ";SELECT 1",
        SQL.replace("GROUP BY YEAR(i.billed_on)", "GROUP BY 1"),
        SQL.replace("ORDER BY SUM(i.charge)", "ORDER BY year"),
    ],
)
def test_unsupported_scope_or_metric_shape_abstains(sql):
    assert plan_annual_binding(sql, "mysql", schema()) is None


def test_postgresql_is_unchanged():
    assert sqlglot.parse_one(SQL, read="postgres")
    assert plan_annual_binding(SQL, "postgres", schema()) is None


def test_missing_or_wrong_relationship_never_guessed_from_names():
    s = schema()
    s.tables["energy_usage"].relationships = []
    s.tables["water_usage"].relationships = []
    assert plan_annual_binding(SQL, "mysql", s) is None
    s = schema()
    s.tables["energy_usage"].relationships[0].relationship_type = "one_to_many"
    s.tables.pop("water_usage")
    assert plan_annual_binding(SQL, "mysql", s) is None


def test_different_fact_key_is_bound_but_dimension_key_preserved():
    s = schema()
    t = s.tables["energy_usage"]
    t.columns["owner"] = t.columns.pop("site_id")
    t.relationships[0].join_pattern = "energy_usage.owner=sites.site_id"
    p = plan_annual_binding(SQL, "mysql", s)
    assert p
    c = sqlglot.parse_one(p.candidate_sql("b0"), read="mysql")
    assert c.args["joins"][0].args["on"].this.name == "owner"
    assert c.args["joins"][0].args["on"].expression.name == "site_id"


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([[4, 4, 4]], True),
        ([[4, 3, 3]], False),
        ([[4, 4, 3]], False),
        ([[0, 0, 0]], False),
        ([[1.5, 1.5, 1.5]], False),
        ([[Decimal("NaN"), 1, 1]], False),
        ([], False),
        ([[1, 1, 1], [1, 1, 1]], False),
    ],
)
def test_dimension_key_uniqueness_is_proved(rows, expected):
    assert proves_dimension_key(rows) == expected


def test_period_totals_select_unique_year_with_numeric_margin():
    rows = [
        ["202301", 10.0, 10.0, 2],
        ["202302", 20.0, 20.0, 3],
        ["202401", 100.0, 100.0, 4],
    ]
    assert proved_annual_winner(rows) == "2024"
    assert accepts_annual_winner("2024", [["2024"]])
    assert not accepts_annual_winner("2024", [[2024]])
    assert not accepts_annual_winner("2024", [["2024"], ["2024"]])


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [["202301", 10, 10, 1]],
        [["202301", 10, 10, 1], ["202401", 10, 10, 1]],
        [["202301", 10, 10, 1], ["202401", 10.0000000001, 10.0000000001, 1]],
        [["202313", 1, 1, 1], ["202401", 2, 2, 1]],
        [[None, 1, 1, 1], ["202401", 2, 2, 1]],
        [["2023-01", 1, 1, 1], ["202401", 2, 2, 1]],
        [["202301", None, 0, 0], ["202401", 2, 2, 1]],
        [["202301", float("inf"), 1, 1], ["202401", 2, 2, 1]],
        [["202301", 10, 1, 1], ["202401", 20, 20, 1]],
        [["202301", 10, 10, 1000001], ["202401", 20, 20, 1]],
        [["202301", 10, 10, 1], ["202301", 20, 20, 1]],
        [["202301", 10, 10, 1.5], ["202401", 20, 20, 1]],
    ],
)
def test_incomplete_invalid_tied_and_close_period_proofs_abstain(rows):
    assert proved_annual_winner(rows) is None


def test_result_limit_sentinel_abstains():
    assert proved_annual_winner([["202301", 1, 1, 1]] * 1001) is None


def test_unaliased_source_keeps_its_qualifier_as_explicit_alias():
    sql = SQL.replace("invoices i", "invoices").replace("i.", "invoices.")
    plan = plan_annual_binding(sql, "mysql", schema())
    assert plan
    table = next(
        sqlglot.parse_one(plan.candidate_sql("b0"), read="mysql").find_all(exp.Table)
    )
    assert table.name == "energy_usage" and table.alias == "invoices"


def test_annual_accumulation_guard_includes_cross_period_sum_order():
    rows = [[f"2023{m:02d}", 1000000000.0, 1000000000.0, 1] for m in range(1, 13)]
    rows += [["202401", 12000000000.00001, 12000000000.00001, 1]]
    assert proved_annual_winner(rows) is None


@pytest.mark.parametrize("order", ["SUM(i.charge)", "volume"])
def test_redundant_rank_measure_can_be_planned_for_year_only_contract(order):
    sql = SQL.replace("AS year FROM", "AS year,SUM(i.charge) AS volume FROM").replace(
        "ORDER BY SUM(i.charge)", "ORDER BY " + order
    )
    before = sqlglot.parse_one(sql, read="mysql")
    plan = plan_annual_binding(sql, "mysql", schema())
    assert plan
    staged = sqlglot.parse_one(plan.original_sql, read="mysql")
    assert len(staged.expressions) == 1
    for key in ("from_", "joins", "where", "group", "limit"):
        assert staged.args.get(key) == before.args.get(key)
    assert staged.args["order"].expressions[0].this == exp.Sum(
        this=exp.column("charge", table="i")
    )
    candidate = sqlglot.parse_one(plan.candidate_sql("b0"), read="mysql")
    assert len(candidate.expressions) == 1
    assert candidate.args["where"] == before.args["where"]


@pytest.mark.parametrize(
    "projection,order",
    [
        ("YEAR(i.billed_on),AVG(i.charge)", "AVG(i.charge)"),
        ("YEAR(i.billed_on),SUM(DISTINCT i.charge)", "SUM(DISTINCT i.charge)"),
        ("YEAR(i.billed_on),SUM(i.charge)+1", "SUM(i.charge)"),
        ("YEAR(i.billed_on),COUNT(*)", "SUM(i.charge)"),
        ("YEAR(i.billed_on),SUM(i.charge)", "COUNT(*)"),
        ("YEAR(i.billed_on) AS y,SUM(i.charge) AS y", "y"),
        ("YEAR(i.billed_on),SUM(i.charge)", "2"),
        ("YEAR(i.billed_on),SUM(i.charge) AS n", "i.n"),
        ("SUM(i.charge),YEAR(i.billed_on)", "SUM(i.charge)"),
        ("YEAR(i.billed_on),SUM(i.charge),COUNT(*)", "SUM(i.charge)"),
    ],
)
def test_only_identical_extra_ranking_measure_is_supported(projection, order):
    sql = SQL.replace("YEAR(i.billed_on) AS year FROM", projection + " FROM").replace(
        "ORDER BY SUM(i.charge)", "ORDER BY " + order
    )
    assert plan_annual_binding(sql, "mysql", schema()) is None


def test_postgres_new_projection_remains_noop():
    sql = SQL.replace("AS year FROM", "AS year,SUM(i.charge) AS volume FROM")
    assert plan_annual_binding(sql, "postgres", schema()) is None


def test_unaliased_extra_sum_is_now_supported():
    assert (
        plan_annual_binding(
            SQL.replace(" AS year", " AS year,SUM(i.charge)"), "mysql", schema()
        )
        is not None
    )
