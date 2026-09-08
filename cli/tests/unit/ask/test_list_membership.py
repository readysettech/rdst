from decimal import Decimal
import pytest
import sqlglot
from features.ask.list_membership import (
    plan_list_membership,
    proved_other_count,
    accepts_other_count,
)

SQL = "SELECT COUNT(*) AS n FROM assets a WHERE a.active=1 AND (a.tags IS NULL OR a.tags NOT LIKE '%Blue%')"
PROOF = [
    [b"Blue", 5, 0, 0],
    [b"Blue,Green", 2, 0, 2],
    [b"Red", 3, 3, 3],
    [None, 1, 1, 0],
]


def test_count_and_scope_are_preserved_with_exhaustive_binary_groups():
    p = plan_list_membership(SQL, "mysql")
    assert p
    original = sqlglot.parse_one(SQL, read="mysql")
    candidate = sqlglot.parse_one(p.candidate_sql, read="mysql")
    proof = sqlglot.parse_one(p.proof_sql, read="mysql")
    assert original.expressions == candidate.expressions
    assert original.args["from_"] == candidate.args["from_"] == proof.args["from_"]
    assert candidate.args["where"].sql() == "WHERE a.active = 1 AND a.tags <> 'Blue'"
    assert proof.args["where"].sql() == "WHERE a.active = 1"
    assert proof.args["group"].sql(dialect="mysql") == "GROUP BY CAST(a.tags AS BINARY)"
    assert proof.args["limit"].expression.this == "1001"
    assert p.target == "Blue" and p.column == "a.tags"


@pytest.mark.parametrize(
    "sql",
    [
        SQL + " GROUP BY a.id",
        SQL + " ORDER BY n",
        SQL + " LIMIT 1",
        SQL + " FOR UPDATE",
        SQL.replace("COUNT(*)", "COUNT(DISTINCT a.id)"),
        SQL.replace("COUNT(*)", "COUNT(a.tags)"),
        SQL.replace("COUNT(*) AS n", "COUNT(*) AS n,a.id"),
        SQL.replace("SELECT", "SELECT DISTINCT", 1),
        SQL.replace("assets a", "assets a JOIN depots d ON a.depot=d.id"),
        SQL.replace("assets a", "other.assets a"),
        SQL.replace("a.active=1", "RAND()>0"),
        SQL.replace("a.active=1", "a.tags IS NOT NULL"),
        SQL.replace("a.active=1", "(a.active=1 OR a.active=2)"),
        SQL.replace("%Blue%", "%Blue_%"),
        SQL.replace("%Blue%", "%Blue%%"),
        SQL.replace("%Blue%", "Blue%"),
        SQL.replace("%Blue%", "%Blue,Green%"),
        SQL.replace("%Blue%", "%Blue\\x%"),
        SQL.replace("a.tags IS NULL", "a.other IS NULL"),
        SQL.replace("NOT LIKE", "LIKE"),
        SQL.replace("COUNT(*)", "SUM(a.active)"),
        SQL + "; SELECT 1",
        SQL.replace("a.active=1", "a.other NOT LIKE '%Green%'"),
    ],
)
def test_unsupported_shapes_abstain(sql):
    assert plan_list_membership(sql, "mysql") is None


def test_plain_negative_like_is_supported():
    assert plan_list_membership(
        "SELECT COUNT(*) FROM assets WHERE tags NOT LIKE '%Blue%'", "mysql"
    )


def test_postgres_parse_remains_no_op():
    assert sqlglot.parse_one(SQL, read="postgres")
    assert plan_list_membership(SQL, "postgres") is None


def test_independent_token_interpretation_proves_count_and_null_exclusion():
    p = plan_list_membership(SQL, "mysql")
    expected = proved_other_count(p, [[4]], PROOF)
    assert expected == 5
    assert accepts_other_count(expected, [[Decimal(5)]])
    assert not accepts_other_count(expected, [[4]])
    assert not accepts_other_count(expected, [[5], [5]])


@pytest.mark.parametrize(
    "value",
    [
        "Blue, Blue",
        "Blue,,Green",
        "Blue,Green,",
        '"Blue,Green"',
        "Blue;Green",
        "Blue,Blue",
        "Blue,blue",
        "Blue\nGreen",
        "Blue/Grey",
        "Blúe",
        "Blue," + "G" * 65,
        "",
    ],
)
def test_noncanonical_or_ambiguous_storage_abstains(value):
    p = plan_list_membership(SQL, "mysql")
    rows = PROOF + [[value, 1, 0, 1]]
    assert proved_other_count(p, [[4]], rows) is None


@pytest.mark.parametrize(
    "rows,original",
    [
        ([], [[4]]),
        (PROOF * 251, [[4]]),
        (PROOF, [[99]]),
        (PROOF, []),
        (PROOF, [[4, 5]]),
        ([[b"Blue", 5, 0, 0], [b"Red", 3, 3, 3]], [[3]]),
        ([[b"Blue,Green", 2, 0, 2]], [[0]]),
        ([[b"Blue", 5, 0, 0], [b"Blue,Green", 2, 0, 1]], [[0]]),
        ([[b"Blue", 5, 0, 0], [b"Blue,Green", 2, 0, 2], [None, 1, 1, 1]], [[1]]),
        ([[b"Blue", 5, 0, 0], [b"Blue,Green", 2, 0, 2], [b"blue", 1, 0, 0]], [[0]]),
        ([[b"Blue", 5, 0, 0], [b"Blue,Green", 2, 0, 2], [b"\xff", 1, 1, 1]], [[1]]),
        ([[b"Blue", 5, 0, 0], [b"Blue,Green", 2, 0, 2], [None, 2, 2, 0]], [[2]]),
        (
            [
                [b"Blue", 5, 0, 0],
                [b"Blue,Green", 2, 0, 2],
                [b"Red", 2**53, 2**53, 2**53],
            ],
            [[2**53]],
        ),
    ],
)
def test_incomplete_disagreeing_and_no_change_witnesses_abstain(rows, original):
    assert (
        proved_other_count(plan_list_membership(SQL, "mysql"), original, rows) is None
    )


import asyncio, json
from types import SimpleNamespace as NS
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult


class Decision:
    def __init__(self, **changes):
        self.changes = changes

    def generate_response(self, **kw):
        return {
            "response": json.dumps(
                dict(
                    quantifier="any_other_member",
                    mixed_members_match="yes",
                    requires_named_member=False,
                    requests_missing_values=False,
                    output_request="count_parent_records",
                    value_role="collection_member",
                    generated_column_matches_question="yes",
                    named_member="Blue",
                    source_excerpt="tags other than Blue",
                    **self.changes,
                )
            ),
            "model": "fixture",
            "tokens_used": 40,
        }


def context():
    return Ask3Context(
        question="Count active assets with tags other than Blue.",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=NS(
            tables={
                "assets": NS(
                    columns={
                        "active": NS(data_type="int"),
                        "tags": NS(data_type="text"),
                    }
                )
            }
        ),
        sql=SQL,
        execution_result=ExecutionResult(rows=[[4]], columns=["n"], row_count=1),
        enforce_result_limit=False,
    )


@pytest.mark.parametrize(
    "candidate,status", [(5, "normalized"), (4, "reverted"), (None, "reverted")]
)
def test_service_exact_acceptance_and_restore(candidate, status):
    calls = []

    def db(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": PROOF if len(calls) == 1 else [[candidate]],
            "columns": ["value", "n", "old", "new"] if len(calls) == 1 else ["n"],
        }

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=db)._apply_list_membership(
            context()
        )
    )
    assert ctx.list_membership["status"] == status
    assert len(calls) == 2 and ctx.list_membership_probe_diagnostics["max_calls"] == 2
    assert (ctx.sql != SQL) == (status == "normalized")
    assert ctx.execution_result.rows == ([[5]] if status == "normalized" else [[4]])
    assert len(ctx.llm_calls) == 1
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert (
        restored.list_membership == ctx.list_membership
        and restored.list_membership_probe_diagnostics
        == ctx.list_membership_probe_diagnostics
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantifier", "excludes_named_member"),
        ("mixed_members_match", "no"),
        ("requires_named_member", True),
        ("requests_missing_values", True),
        ("output_request", "entity"),
        ("value_role", "scalar_text"),
        ("generated_column_matches_question", "no"),
        ("named_member", "Red"),
        ("source_excerpt", "invented"),
    ],
)
def test_independent_semantic_guard_prevents_queries(field, value):
    class Changed(Decision):
        def generate_response(self, **kw):
            r = super().generate_response(**kw)
            v = json.loads(r["response"])
            v[field] = value
            r["response"] = json.dumps(v)
            return r

    def forbidden(*args):
        raise AssertionError("No database query allowed")

    ctx = asyncio.run(
        AskService(llm_manager=Changed(), db_executor=forbidden)._apply_list_membership(
            context()
        )
    )
    assert ctx.sql == SQL and ctx.list_membership["reason"] == "model-abstained"


def test_missing_schema_stops_before_proof():
    ctx = context()
    ctx.schema_info = None

    def forbidden(*args):
        raise AssertionError("No database query allowed")

    actual = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=forbidden
        )._apply_list_membership(ctx)
    )
    assert (
        actual.sql == SQL
        and actual.list_membership["reason"] == "candidate-validation-failed"
    )


@pytest.mark.parametrize(
    "proof",
    [
        {"success": False, "error": "timeout"},
        {"success": True, "rows": []},
        {"success": True, "rows": [[b"Blue", 1, 0, 0]]},
    ],
)
def test_failed_or_incomplete_storage_stops_after_one_query(proof):
    calls = []

    def db(*args):
        calls.append(args)
        return proof

    ctx = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=db)._apply_list_membership(
            context()
        )
    )
    assert (
        ctx.sql == SQL
        and len(calls) == 1
        and ctx.list_membership["reason"] == "unsafe-or-unavailable-proof"
    )


def test_malformed_response_is_recorded_and_original_preserved():
    class Bad:
        def generate_response(self, **kw):
            return {"response": "{broken", "model": "fixture", "tokens_used": 2}

    ctx = asyncio.run(AskService(llm_manager=Bad())._apply_list_membership(context()))
    assert ctx.sql == SQL and ctx.list_membership["status"] == "reverted"
    assert len(ctx.llm_calls) == 1


def test_extra_context_abstains_without_model_access():
    ctx = context()
    ctx.provided_context = "Custom membership definitions."

    class Forbidden:
        def generate_response(self, **kw):
            raise AssertionError("No model request allowed")

    actual = asyncio.run(
        AskService(llm_manager=Forbidden())._apply_list_membership(ctx)
    )
    assert (
        actual.sql == SQL
        and actual.list_membership["reason"] == "additional-context-requires-abstention"
    )


def test_value_limit_sentinel_and_nonfinite_counts_abstain():
    plan = plan_list_membership(SQL, "mysql")
    assert (
        proved_other_count(
            plan, [[4]], PROOF + [[f"Red{i}", 1, 1, 1] for i in range(997)]
        )
        is None
    )
    assert proved_other_count(plan, [[4]], [[b"Blue", Decimal("NaN"), 0, 0]]) is None
    assert not accepts_other_count(5, [[float("nan")]])


def test_probe_executor_enforces_call_budget_and_readonly():
    from features.ask.value_probe import create_list_membership_probe_executor

    calls = []

    def db(sql, config):
        calls.append(sql)
        return {"success": True, "rows": PROOF, "columns": ["value", "n", "old", "new"]}

    ctx = context()
    bounded = create_list_membership_probe_executor(ctx, db)
    assert not bounded("DELETE FROM assets", ctx.target_config)["success"]
    assert bounded(plan_list_membership(SQL, "mysql").proof_sql, ctx.target_config)[
        "success"
    ]
    assert not bounded("SELECT 1", ctx.target_config)["success"]
    assert (
        len(calls) == 1 and ctx.list_membership_probe_diagnostics["blocked_calls"] == 1
    )


@pytest.mark.parametrize(
    "predicate",
    [
        "NOT (FIND_IN_SET('Blue', a.tags) > 0)",
        "FIND_IN_SET('Blue', a.tags) = 0",
        "NOT FIND_IN_SET('Blue', a.tags)",
        "a.tags IS NULL OR FIND_IN_SET('Blue', a.tags)=0",
    ],
)
def test_exact_set_absence_predicates_reuse_the_same_candidate_and_count_proof(
    predicate,
):
    sql = f"SELECT COUNT(*) AS n FROM assets a WHERE a.active=1 AND ({predicate})"
    plan = plan_list_membership(sql, "mysql")
    assert plan and plan.predicate_kind == "set_absence"
    assert plan.candidate_sql == plan_list_membership(SQL, "mysql").candidate_sql
    assert "FIND_IN_SET" in plan.proof_sql
    assert (
        proved_other_count(
            plan,
            [[3]],
            [
                [b"Blue", 5, 0, 0],
                [b"Blue,Green", 2, 0, 2],
                [b"Red", 3, 3, 3],
                [None, 1, 0, 0],
            ],
        )
        == 5
    )


@pytest.mark.parametrize(
    "predicate",
    [
        "FIND_IN_SET('Blue',a.tags)>0",
        "FIND_IN_SET('Blue',a.tags)=1",
        "NOT (FIND_IN_SET('Blue',a.tags)>1)",
        "FIND_IN_SET('Blue',a.tags)<2",
        "FIND_IN_SET(a.tags,'Blue')=0",
        "FIND_IN_SET('Blue,Green',a.tags)=0",
        "FIND_IN_SET('Blue',LOWER(a.tags))=0",
        "FIND_IN_SET('Blue',a.tags,1)=0",
        "SLEEP(1)=0",
        "FIND_IN_SET('Blue',a.tags)=0 OR a.active=1",
        "FIND_IN_SET('Blue',a.tags)=0 AND FIND_IN_SET('Red',a.other)>0",
    ],
)
def test_non_absence_ambiguous_or_other_functions_abstain(predicate):
    assert (
        plan_list_membership(
            "SELECT COUNT(*) FROM assets a WHERE " + predicate, "mysql"
        )
        is None
    )
