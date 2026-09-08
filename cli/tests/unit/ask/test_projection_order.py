import asyncio
import json

import pytest
import sqlglot

from features.ask.projection_order import plan_projection_order, projection_order_shape
from features.ask.projection_contract import accepts_projection_subset
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.value_probe import create_projection_order_probe_executor
from tests.unit.ask.test_projection_contract import context


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_permutation_preserves_query_and_rows(dialect):
    sql = "SELECT id,email FROM contacts WHERE id>2 ORDER BY id LIMIT 8"
    p = plan_projection_order(sql, dialect, [1, 0])
    assert p
    old = sqlglot.parse_one(sql, read=dialect)
    new = sqlglot.parse_one(p.candidate_sql, read=dialect)
    new.set("expressions", old.expressions)
    assert old == new
    original = [[3, None], [4, "b"], [4, "b"]]
    assert accepts_projection_subset(p, original, [[None, 3], ["b", 4], ["b", 4]])
    assert not accepts_projection_subset(p, original, [["b", 4], [None, 3], ["b", 4]])
    assert not accepts_projection_subset(p, original, [[None, 3], ["b", 4]])


@pytest.mark.parametrize("indices", [[], [0], [0, 0], [0, 1], [True, 0], [2, 0]])
def test_requires_complete_nonidentity_permutation(indices):
    assert (
        plan_projection_order("SELECT id,email FROM contacts", "mysql", indices) is None
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id,email FROM contacts ORDER BY 2",
        "SELECT DISTINCT id,email FROM contacts",
        "SELECT email,COUNT(*) FROM contacts GROUP BY email",
        "SELECT id,CONCAT(id,email) FROM contacts",
        "SELECT id,email FROM contacts; DELETE FROM contacts",
    ],
)
def test_unsupported_shape(sql):
    assert projection_order_shape(sql, "mysql") is None


class Decision:
    def __init__(self, indices=(1, 0), explicit=True, excerpt="email, then ID"):
        self.indices, self.explicit, self.excerpt = indices, explicit, excerpt

    def generate_response(self, **kwargs):
        assert kwargs["purpose"] == "projection_order_routing"
        return {
            "response": json.dumps(
                {
                    "enumeration_resolves_every_projection": self.explicit,
                    "projection_order": list(self.indices),
                    "source_excerpt": self.excerpt,
                }
            ),
            "model": "scripted",
            "tokens_used": 12,
        }


def ctx():
    c = context()
    c.question = "Return email, then ID"
    return c


@pytest.mark.parametrize(
    "rows,status",
    [
        ([["a", 1], ["b", 2]], "normalized"),
        ([["a", 1], ["c", 2]], "reverted"),
        ([["a", 1]], "reverted"),
    ],
)
def test_service_proof_restoration_and_serialization(rows, status):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": rows, "columns": ["email", "id"]}

    c = ctx()
    before = c.sql
    c = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_projection_order(
            c
        )
    )
    assert c.projection_order["status"] == status and len(calls) == 1
    assert len(c.llm_calls) == 1 and c.projection_order_probe_diagnostics["calls"] == 1
    if status == "reverted":
        assert c.sql == before and c.execution_result.rows == [[1, "a"], [2, "b"]]
    else:
        assert c.sql != before and c.execution_result.rows == rows
    restored = Ask3Context.from_dict(c.to_dict())
    assert restored.projection_order == c.projection_order
    assert (
        restored.projection_order_probe_diagnostics
        == c.projection_order_probe_diagnostics
    )


@pytest.mark.parametrize(
    "decision",
    [
        Decision([0, 1]),
        Decision([1, 0], False),
        Decision([1]),
        Decision([1, 0], True, "invented"),
    ],
)
def test_abstention_never_executes(decision):
    def forbidden(*args):
        raise AssertionError("No query expected")

    c = asyncio.run(
        AskService(llm_manager=decision, db_executor=forbidden)._apply_projection_order(
            ctx()
        )
    )
    assert c.projection_order["reason"] == "no-safe-requested-permutation"


def test_error_restores_and_budget_is_read_only_single_call():
    c = ctx()
    calls = []

    def execute(*args):
        calls.append(args)
        return {"success": False, "error": "Unavailable"}

    c = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_projection_order(
            c
        )
    )
    assert c.projection_order["status"] == "reverted"
    assert c.sql == "SELECT id,email FROM contacts"
    assert c.execution_result.rows == [[1, "a"], [2, "b"]]
    bounded = create_projection_order_probe_executor(ctx(), execute)
    bounded("SELECT id FROM contacts", c.target_config)
    bounded("SELECT id FROM contacts", c.target_config)
    bounded("DELETE FROM contacts", c.target_config)
    assert len(calls) == 2


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize(
    "prefix",
    [
        "WITH ranked AS (SELECT category, COUNT(*) AS n FROM items WHERE active=1 GROUP BY category ORDER BY n DESC LIMIT 1)",
        "WITH ranked AS (SELECT category FROM items ORDER BY 1 LIMIT 1)",
        "WITH first_stage AS (SELECT category FROM items), ranked AS (SELECT category FROM first_stage)",
        "WITH first_stage AS (SELECT category FROM items), second_stage AS (SELECT category FROM first_stage), ranked AS (SELECT category FROM second_stage)",
        "WITH ranked(cat) AS (SELECT category FROM items)",
    ],
)
def test_cte_permutation_preserves_all_inner_and_outer_clauses(dialect, prefix):
    sql = (
        prefix
        + " SELECT i.name, i.category FROM items i JOIN ranked r ON r.category=i.category WHERE i.active=1 ORDER BY i.name LIMIT 8"
    )
    if "ranked(cat)" in prefix:
        sql = sql.replace("r.category", "r.cat")
    p = plan_projection_order(sql, dialect, [1, 0])
    assert p
    old = sqlglot.parse_one(sql, read=dialect)
    new = sqlglot.parse_one(p.candidate_sql, read=dialect)
    assert old.args["with_"] == new.args["with_"]
    new.set("expressions", old.expressions)
    assert new == old


@pytest.mark.parametrize(
    "sql",
    [
        "WITH RECURSIVE r AS (SELECT id,name FROM items) SELECT id,name FROM r",
        "WITH r AS (DELETE FROM items RETURNING id,name) SELECT id,name FROM r",
        "WITH r AS (SELECT id,name FROM items FOR UPDATE) SELECT id,name FROM r",
        "WITH r AS (SELECT id,name FROM items UNION ALL SELECT id,name FROM items) SELECT id,name FROM r",
        "WITH r AS (WITH z AS (SELECT id,name FROM items) SELECT id,name FROM z) SELECT id,name FROM r",
        "WITH r AS (SELECT id,name FROM items), r AS (SELECT id,name FROM items) SELECT id,name FROM r",
        "WITH r AS (SELECT id,name FROM items) SELECT id,name FROM r ORDER BY 2",
        "WITH r AS (SELECT id,name FROM items) SELECT DISTINCT id,name FROM r",
        "WITH r AS (SELECT id,name FROM items) SELECT id,COUNT(*) FROM r GROUP BY id",
        "WITH r AS (SELECT id,name FROM items) SELECT id,CONCAT(name,id) FROM r",
        "WITH r AS (SELECT id,name FROM items) SELECT id,name FROM (SELECT * FROM r) x",
        "WITH a AS (SELECT id,name FROM items), b AS (SELECT id,name FROM a), c AS (SELECT id,name FROM b), d AS (SELECT id,name FROM c) SELECT id,name FROM d",
    ],
)
def test_cte_order_rejects_unsupported_or_mutating_scope(sql):
    assert projection_order_shape(sql, "postgres") is None


def test_cte_service_restores_on_lost_duplicate():
    c = ctx()
    c.sql = (
        "WITH eligible AS (SELECT id,email FROM contacts) SELECT id,email FROM eligible"
    )
    original = c.sql
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": [["a", 1]], "columns": ["email", "id"]}

    c = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_projection_order(
            c
        )
    )
    assert c.sql == original and c.projection_order["status"] == "reverted"
    assert len(calls) == 1
