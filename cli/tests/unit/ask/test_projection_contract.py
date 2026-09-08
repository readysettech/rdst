import pytest
import sqlglot
from sqlglot import exp
from features.ask.projection_contract import (
    projection_shape,
    plan_projection_subset,
    accepts_projection_subset,
)


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_projection_only_preserves_cross_scope_extremum(dialect):
    sql = "SELECT d.label,d.capacity FROM depots d WHERE d.capacity=(SELECT MAX(x.capacity) FROM depots x)"
    p = plan_projection_subset(sql, dialect, [0])
    assert p
    old = sqlglot.parse_one(sql, read=dialect)
    new = sqlglot.parse_one(p.candidate_sql, read=dialect)
    new.set("expressions", [e.copy() for e in old.expressions])
    assert old == new
    assert accepts_projection_subset(
        p, [["West", 7], ["East", 7]], [["East"], ["West"]]
    )
    assert not accepts_projection_subset(
        p, [["West", 7], ["East", 7]], [["West"], ["West"]]
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT DISTINCT id,name FROM customers",
        "SELECT name,SUM(amount) FROM customers GROUP BY name",
        "SELECT c.* FROM customers c",
        "SELECT name,CONCAT(id,name) FROM customers",
        "SELECT id,name FROM customers; DELETE FROM customers",
        "WITH x AS (SELECT * FROM customers) SELECT id,name FROM x",
    ],
)
def test_unsupported_shape(sql):
    assert projection_shape(sql, "mysql") is None


@pytest.mark.parametrize("indices", [[], [0, 0], [1, 0], [2], [True], [0, 1]])
def test_invalid_projection_contract(indices):
    assert (
        plan_projection_subset("SELECT id,email FROM contacts", "mysql", indices)
        is None
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id,email AS contact FROM contacts ORDER BY contact",
        "SELECT id,email FROM contacts ORDER BY 2",
    ],
)
def test_removed_alias_or_ordinal_order_abstains(sql):
    assert plan_projection_subset(sql, "mysql", [0]) is None


def test_ordered_result_requires_same_row_sequence():
    p = plan_projection_subset(
        "SELECT id,email FROM contacts ORDER BY id", "mysql", [0]
    )
    assert p
    assert accepts_projection_subset(p, [[1, "a"], [2, "b"]], [[1], [2]])
    assert not accepts_projection_subset(p, [[1, "a"], [2, "b"]], [[2], [1]])


def test_duplicate_rows_and_nulls_are_preserved():
    p = plan_projection_subset("SELECT id,email FROM contacts", "mysql", [1])
    assert p
    assert accepts_projection_subset(p, [[1, None], [2, None]], [[None], [None]])
    assert not accepts_projection_subset(p, [[1, None], [2, None]], [[None]])
    assert not accepts_projection_subset(p, [], [])


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize("order", ["frequency", "COUNT(*)"])
def test_grouped_count_removal_preserves_rank_and_grouping(dialect, order):
    sql = (
        "SELECT category,COUNT(*) AS frequency FROM parcels "
        f"WHERE active=1 GROUP BY category ORDER BY {order} DESC LIMIT 1"
    )
    plan = plan_projection_subset(sql, dialect, [0])
    assert plan
    tree = sqlglot.parse_one(plan.candidate_sql, read=dialect)
    assert isinstance(tree.args["order"].expressions[0].this, exp.Count)
    assert tree.args["limit"].expression.this == "1"
    assert tree.args["group"].expressions == [exp.column("category")]
    assert tree.args["where"] == sqlglot.parse_one(sql, read=dialect).args["where"]
    assert accepts_projection_subset(plan, [["Fragile", 8]], [["Fragile"]])
    assert not accepts_projection_subset(
        plan, [["Fragile", 8]], [["Fragile"], ["Cold"]]
    )
    assert plan_projection_subset(sql, dialect, [1]) is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT category,COUNT(*) AS category FROM parcels GROUP BY category ORDER BY category",
        "SELECT category,COUNT(*) FROM parcels GROUP BY 1",
        "SELECT category,COUNT(*) FROM parcels GROUP BY category WITH ROLLUP",
        "SELECT category,COUNT(*) AS n FROM parcels GROUP BY category HAVING n>2",
        "SELECT category,COUNT(*) AS n FROM parcels GROUP BY category ORDER BY (SELECT n FROM other)",
        "SELECT category,COUNT(*) AS n FROM parcels GROUP BY category ORDER BY 2",
        "SELECT category,COUNT(DISTINCT item) FROM parcels GROUP BY category",
        "SELECT category,COUNT(*) FROM parcels",
        "SELECT category AS kind,COUNT(*) FROM parcels GROUP BY kind",
    ],
)
def test_grouped_count_ambiguous_or_unsupported_references_abstain(sql):
    assert plan_projection_subset(sql, "mysql", [0]) is None


import asyncio, json
from types import SimpleNamespace as NS
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.value_probe import create_projection_contract_probe_executor


class Decision:
    def __init__(self, indices=(0,), decision="subset"):
        self.indices, self.decision = indices, decision

    def generate_response(self, **kwargs):
        return {
            "response": json.dumps(
                {
                    "decision": self.decision,
                    "keep_indices": list(self.indices),
                    "source_excerpt": "only contact IDs",
                }
            ),
            "model": "scripted",
            "tokens_used": 12,
        }


def context():
    return Ask3Context(
        question="Return only contact IDs",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=NS(
            tables={
                "contacts": NS(
                    columns={"id": NS(data_type="int"), "email": NS(data_type="text")}
                )
            }
        ),
        sql="SELECT id,email FROM contacts",
        execution_result=ExecutionResult(
            rows=[[1, "a"], [2, "b"]], columns=["id", "email"], row_count=2
        ),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "rows,status",
    [([[1], [2]], "normalized"), ([[1], [3]], "reverted"), ([[1]], "reverted")],
)
def test_service_projection_identity_and_restoration(rows, status):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": rows, "columns": ["id"]}

    ctx = context()
    original = ctx.sql
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=execute
        )._apply_projection_contract(ctx)
    )
    assert ctx.projection_contract["status"] == status and len(calls) == 1
    assert len(ctx.llm_calls) == 1
    assert ctx.projection_contract_probe_diagnostics["calls"] == 1
    if status == "reverted":
        assert ctx.sql == original and ctx.execution_result.rows == [[1, "a"], [2, "b"]]
    else:
        assert ctx.sql != original and ctx.execution_result.rows == rows
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.projection_contract == ctx.projection_contract
    assert (
        restored.projection_contract_probe_diagnostics
        == ctx.projection_contract_probe_diagnostics
    )


@pytest.mark.parametrize(
    "indices,decision",
    [([0, 1], "preserve"), ([0, 1], "subset"), ([1, 0], "subset"), ([2], "subset")],
)
def test_unsafe_or_preserved_contract_does_not_execute(indices, decision):
    def forbidden(*a):
        raise AssertionError("No candidate expected")

    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(indices, decision), db_executor=forbidden
        )._apply_projection_contract(context())
    )
    assert ctx.projection_contract["reason"] == "no-safe-requested-subset"


@pytest.mark.parametrize("kind", ["empty", "truncated", "context"])
def test_incomplete_input_never_routes(kind):
    class Forbidden:
        def generate_response(self, **kw):
            raise AssertionError("No model call expected")

    ctx = context()
    if kind == "empty":
        ctx.execution_result.rows = []
    elif kind == "truncated":
        ctx.execution_result.truncated = True
    else:
        ctx.provided_context = "Preserve extra fields."
    ctx = asyncio.run(
        AskService(llm_manager=Forbidden())._apply_projection_contract(ctx)
    )
    assert not ctx.llm_calls
    assert ctx.projection_contract["reason"] in {
        "complete-nonempty-primary-unavailable",
        "additional-context-requires-abstention",
    }


def test_one_candidate_budget_and_read_only():
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": [[1], [2]], "columns": ["id"]}

    ctx = context()
    bounded = create_projection_contract_probe_executor(ctx, execute)
    assert bounded("SELECT id FROM contacts", ctx.target_config)["success"]
    assert not bounded("SELECT id FROM contacts", ctx.target_config)["success"]
    assert len(calls) == 1
    bounded = create_projection_contract_probe_executor(ctx, execute)
    assert not bounded("DELETE FROM contacts", ctx.target_config)["success"]
    assert len(calls) == 1


def test_candidate_error_preserves_primary_result():
    def execute(*a):
        return {"success": False, "error": "Unavailable"}

    ctx = context()
    original = ctx.sql
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=execute
        )._apply_projection_contract(ctx)
    )
    assert ctx.sql == original and ctx.execution_result.rows == [[1, "a"], [2, "b"]]
    assert ctx.projection_contract["status"] == "reverted"


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize("order", ["SUM(units)", "volume"])
def test_ranked_sum_removal_preserves_measure_and_population(dialect, order):
    sql = (
        "SELECT depot,SUM(units) AS volume FROM shipments WHERE active=1 "
        f"GROUP BY depot ORDER BY {order} DESC LIMIT 3 OFFSET 1"
    )
    p = plan_projection_subset(sql, dialect, [0])
    assert p
    old, new = [sqlglot.parse_one(s, read=dialect) for s in (sql, p.candidate_sql)]
    assert new.args["order"].expressions[0].this == exp.Sum(this=exp.column("units"))
    for key in ("from_", "where", "group", "limit", "offset"):
        assert old.args.get(key) == new.args.get(key)
    assert accepts_projection_subset(p, [["N", 12], ["S", 10]], [["N"], ["S"]])
    assert not accepts_projection_subset(p, [["N", 12], ["S", 10]], [["S"], ["N"]])
    assert plan_projection_subset(sql, dialect, [1]) is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT depot,SUM(units) FROM shipments GROUP BY depot",
        "SELECT depot,SUM(units) FROM shipments GROUP BY depot ORDER BY depot LIMIT 2",
        "SELECT depot,SUM(DISTINCT units) FROM shipments GROUP BY depot ORDER BY SUM(DISTINCT units) LIMIT 2",
        "SELECT depot,SUM(units*price) FROM shipments GROUP BY depot ORDER BY SUM(units*price) LIMIT 2",
        "SELECT depot,AVG(units) FROM shipments GROUP BY depot ORDER BY AVG(units) LIMIT 2",
        "SELECT depot,SUM(units) FROM shipments GROUP BY depot ORDER BY SUM(units) LIMIT 101",
        "SELECT depot,SUM(units) FROM shipments GROUP BY depot ORDER BY SUM(units) LIMIT 0",
        "SELECT depot,SUM(units) AS depot FROM shipments GROUP BY depot ORDER BY depot LIMIT 2",
        "SELECT depot,SUM(units) AS n FROM shipments GROUP BY depot ORDER BY 2 LIMIT 2",
        "SELECT depot,SUM(units) AS n FROM shipments GROUP BY depot HAVING n>1 ORDER BY n LIMIT 2",
        "SELECT depot,SUM(units) AS n FROM shipments GROUP BY depot ORDER BY (SELECT n FROM other) LIMIT 2",
        "SELECT depot,SUM(units) AS n FROM shipments GROUP BY depot ORDER BY n+1 LIMIT 2",
        "SELECT depot,SUM(units),COUNT(*) FROM shipments GROUP BY depot ORDER BY SUM(units) LIMIT 2",
    ],
)
def test_ranked_sum_rejects_ambiguous_or_unbounded_forms(sql):
    assert plan_projection_subset(sql, "mysql", [0]) is None


def test_ranked_sum_keeps_duplicate_category_rows_and_nulls():
    p = plan_projection_subset(
        "SELECT depot,SUM(units) AS n FROM shipments GROUP BY depot,route ORDER BY n DESC LIMIT 3",
        "mysql",
        [0],
    )
    assert p
    assert accepts_projection_subset(
        p, [["N", 5], ["N", 3], [None, None]], [["N"], ["N"], [None]]
    )
    assert not accepts_projection_subset(p, [["N", 5], ["N", 3]], [["N"]])


@pytest.mark.parametrize("accept", [True, False])
def test_ranked_sum_service_then_calendar_component(accept):
    class Routes:
        def generate_response(self, **kw):
            if kw["purpose"] == "projection_contract_routing":
                answer = dict(
                    decision="subset",
                    keep_indices=[0],
                    source_excerpt="peak shipment month",
                )
            else:
                assert kw["purpose"] == "month_component_routing"
                answer = dict(decision="activate", source_excerpt="peak shipment month")
            return dict(response=json.dumps(answer), model="scripted", tokens_used=12)

    ctx = Ask3Context(
        question="What was the peak shipment month in 2024?",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=NS(
            tables={
                "shipments": NS(
                    columns={
                        "month_key": NS(data_type="varchar(6)"),
                        "units": NS(data_type="int"),
                    }
                )
            }
        ),
        sql="SELECT month_key,SUM(units) AS n FROM shipments WHERE month_key LIKE '2024%' GROUP BY month_key ORDER BY n DESC LIMIT 1",
        execution_result=ExecutionResult(
            rows=[["202407", 40]], columns=["month_key", "n"], row_count=1
        ),
        enforce_result_limit=False,
    )
    original = ctx.sql
    calls = []

    def execute(sql, config):
        calls.append(sql)
        if "DISTINCT" in sql:
            return dict(
                success=True, rows=[["202406"], ["202407"]], columns=["month_key"]
            )
        if "SUBSTRING" in sql:
            return dict(success=True, rows=[["07"]], columns=["month"])
        return dict(
            success=True,
            rows=[["202407" if accept else "202406"]],
            columns=["month_key"],
        )

    service = AskService(llm_manager=Routes(), db_executor=execute)
    ctx = asyncio.run(service._apply_projection_contract(ctx))
    if not accept:
        assert ctx.projection_contract["status"] == "reverted" and ctx.sql == original
        assert ctx.execution_result.rows == [["202407", 40]] and len(calls) == 1
        return
    assert ctx.projection_contract["status"] == "normalized"
    ctx = asyncio.run(service._apply_month_component(ctx))
    assert ctx.month_component[
        "status"
    ] == "normalized" and ctx.execution_result.rows == [["07"]]
    assert len(calls) == 3 and len(ctx.llm_calls) == 2
    tree = sqlglot.parse_one(ctx.sql, read="mysql")
    assert tree.args["group"].expressions == [exp.column("month_key")]
    assert tree.args["order"].expressions[0].this == exp.Sum(this=exp.column("units"))
    assert ctx.projection_contract_probe_diagnostics["calls"] == 1
    assert ctx.month_component_probe_diagnostics["calls"] == 2


def test_count_projection_keeps_legacy_rejection_of_sum_order():
    sql = "SELECT depot,COUNT(*) AS n FROM shipments GROUP BY depot ORDER BY SUM(units) DESC LIMIT 3"
    assert projection_shape(sql, "mysql") is not None
    assert plan_projection_subset(sql, "mysql", [0]) is None


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize(
    "label",
    [
        "SUBSTRING(batch_code,1,3)",
        "SUBSTRING(batch_code,4,2)",
    ],
)
def test_computed_group_label_subset_preserves_full_group_and_rank(dialect, label):
    sql = f"SELECT {label} AS bucket,SUM(units) AS volume FROM shipments WHERE active=1 GROUP BY {label} ORDER BY volume DESC LIMIT 3"
    p = plan_projection_subset(sql, dialect, [0])
    assert p
    old = sqlglot.parse_one(sql, read=dialect)
    new = sqlglot.parse_one(p.candidate_sql, read=dialect)
    assert (
        new.args["group"] == old.args["group"]
        and new.args["where"] == old.args["where"]
    )
    assert new.expressions == [old.expressions[0]]
    assert new.args["order"].expressions[0].this == old.expressions[1].this
    assert new.args["limit"] == old.args["limit"]
    assert accepts_projection_subset(p, [["abc", 7], ["def", 4]], [["abc"], ["def"]])
    assert not accepts_projection_subset(
        p, [["abc", 7], ["def", 4]], [["def"], ["abc"]]
    )


def test_multiple_exact_component_groups_preserve_all_labels():
    sql = "SELECT SUBSTRING(code,1,2) AS prefix,SUBSTRING(region_code,3,2) AS zone,COUNT(*) AS n FROM shipments GROUP BY SUBSTRING(code,1,2),SUBSTRING(region_code,3,2) ORDER BY n DESC"
    p = plan_projection_subset(sql, "mysql", [0, 1])
    assert p
    old = sqlglot.parse_one(sql, read="mysql")
    new = sqlglot.parse_one(p.candidate_sql, read="mysql")
    assert (
        new.args["group"] == old.args["group"]
        and new.expressions == old.expressions[:2]
    )
    assert plan_projection_subset(sql, "mysql", [0, 2]) is None


@pytest.mark.parametrize(
    "label,group",
    [
        ("SUBSTRING(code,1,2)", "SUBSTRING(code,1,3)"),
        ("SUBSTRING(code,1,2)", "bucket"),
        ("SUBSTRING(code,0,2)", "SUBSTRING(code,0,2)"),
        ("SUBSTRING(code,-2,2)", "SUBSTRING(code,-2,2)"),
        ("SUBSTRING(code,1,length)", "SUBSTRING(code,1,length)"),
        ("SUBSTRING(code,1,2048)", "SUBSTRING(code,1,2048)"),
        ("SUBSTRING(code,1)", "SUBSTRING(code,1)"),
        ("SUBSTRING(CONCAT(code,kind),1,2)", "SUBSTRING(CONCAT(code,kind),1,2)"),
        ("YEAR(NOW())", "YEAR(NOW())"),
        ("YEAR(created_at)", "YEAR(created_at)"),
        ("MONTH(created_at)", "MONTH(created_at)"),
        ("RAND()", "RAND()"),
        ("YEAR(created_at)+1", "YEAR(created_at)+1"),
    ],
)
def test_computed_labels_reject_mismatches_dynamic_or_unbounded_parts(label, group):
    sql = f"SELECT {label} AS bucket,SUM(units) AS volume FROM shipments GROUP BY {group} ORDER BY volume DESC LIMIT 3"
    assert plan_projection_subset(sql, "mysql", [0]) is None


def test_computed_label_measure_alias_cannot_shadow_source_field():
    sql = "SELECT SUBSTRING(code,1,2) AS prefix,SUM(units) AS code FROM shipments GROUP BY SUBSTRING(code,1,2) ORDER BY code DESC LIMIT 3"
    assert plan_projection_subset(sql, "mysql", [0]) is None


def test_computed_tie_order_is_preserved_only_for_identical_group_label():
    sql = "SELECT SUBSTRING(code,1,2) AS prefix,SUM(units) AS volume FROM shipments GROUP BY SUBSTRING(code,1,2) ORDER BY volume DESC,SUBSTRING(code,1,2) LIMIT 3"
    p = plan_projection_subset(sql, "mysql", [0])
    assert p
    old = sqlglot.parse_one(sql, read="mysql")
    new = sqlglot.parse_one(p.candidate_sql, read="mysql")
    assert old.args["order"].expressions[1] == new.args["order"].expressions[1]
    assert (
        plan_projection_subset(
            sql.replace("DESC,SUBSTRING(code,1,2)", "DESC,SUBSTRING(code,2,2)"),
            "mysql",
            [0],
        )
        is None
    )


def test_component_quote_instruction_keeps_legacy_request_bytes():
    from features.ask.projection_contract import route_projection_contract, SYSTEM

    class Capture:
        def __init__(self):
            self.requests = []

        def generate_response(self, **kwargs):
            self.requests.append(kwargs)
            return {
                "response": json.dumps(
                    {
                        "decision": "preserve",
                        "keep_indices": [0, 1],
                        "source_excerpt": "Show the report",
                    }
                ),
                "model": "scripted",
            }

    old = "SELECT region,SUM(units) AS total FROM shipments GROUP BY region ORDER BY total DESC LIMIT 3"
    new = "SELECT SUBSTRING(code,1,2) AS prefix,SUM(units) AS total FROM shipments GROUP BY SUBSTRING(code,1,2) ORDER BY total DESC LIMIT 3"
    a = Capture()
    route_projection_contract("Show the report", old, "mysql", a)
    route_projection_contract("Show the report", new, "mysql", a)
    assert a.requests[0]["system_message"] == SYSTEM
    assert a.requests[1]["system_message"].startswith(SYSTEM)
    assert "Never correct the excerpt" in a.requests[1]["system_message"]
    assert a.requests[0]["extra"] == a.requests[1]["extra"]


def test_component_excerpt_still_requires_exact_original_characters():
    from features.ask.projection_contract import route_projection_contract

    class InvalidExcerpt:
        def generate_response(self, **kwargs):
            return {
                "response": json.dumps(
                    {
                        "decision": "subset",
                        "keep_indices": [0],
                        "source_excerpt": "What month had the lowest shipments in 2024?",
                    }
                ),
                "model": "scripted",
            }

    sql = "SELECT SUBSTRING(code,5,2) AS month,SUM(units) AS total FROM shipments GROUP BY SUBSTRING(code,5,2) ORDER BY total ASC LIMIT 1"
    r = route_projection_contract(
        "What month had the lowest shipments in2024?", sql, "mysql", InvalidExcerpt()
    )
    assert r["keep_indices"] == [0, 1]
