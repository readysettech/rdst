import asyncio, json
import pytest, sqlglot
from sqlglot import exp
from features.ask.ranked_union import plan_ranked_union, accepts_ranked_union
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.value_probe import create_ranked_union_probe_executor
from tests.unit.ask.test_projection_contract import context

SQL = "SELECT id,email FROM contacts ORDER BY id DESC LIMIT 1 OFFSET 4 UNION ALL SELECT id,email FROM contacts ORDER BY id DESC LIMIT 1 OFFSET 5"


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_adjacent_slice_has_identical_population_and_order(dialect):
    p = plan_ranked_union(SQL, dialect)
    assert p and p.positions == (5, 6)
    t = sqlglot.parse_one(p.candidate_sql, read=dialect)
    assert t.args["limit"].expression == exp.Literal.number(2)
    t.set("limit", exp.Limit(expression=exp.Literal.number(1)))
    assert t == sqlglot.parse_one(p.branch_sql[0], read=dialect)
    assert accepts_ranked_union(p, [[[5, None]], [[4, "b"]]], [[5, None], [4, "b"]])
    assert not accepts_ranked_union(p, [[[5, None]], [[4, "b"]]], [[4, "b"], [5, None]])
    assert not accepts_ranked_union(p, [[[5, None]], []], [[5, None]])


@pytest.mark.parametrize(
    "sql",
    [
        SQL.replace("UNION ALL", "UNION"),
        SQL.replace("OFFSET 5", "OFFSET 7"),
        SQL.replace(
            "ORDER BY id DESC LIMIT 1 OFFSET 5", "ORDER BY id ASC LIMIT 1 OFFSET 5"
        ),
        SQL.replace(
            "FROM contacts ORDER BY id DESC LIMIT 1 OFFSET 5",
            "FROM other ORDER BY id DESC LIMIT 1 OFFSET 5",
        ),
        SQL.replace("id,email", "id,RAND()"),
        SQL + ";DELETE FROM contacts",
        "SELECT id FROM contacts UNION ALL SELECT id FROM contacts LIMIT 2",
        "(SELECT id FROM contacts ORDER BY id LIMIT 1 OFFSET 4) UNION ALL (SELECT id FROM contacts ORDER BY id LIMIT 1 OFFSET 5)",
        SQL.replace("SELECT id,email", "SELECT DISTINCT id,email"),
    ],
)
def test_invalid_ambiguous_or_already_valid_union_rejected(sql):
    assert plan_ranked_union(sql, "mysql") is None


class Decision:
    def __init__(self, positions=(5, 6), kind="listed_row_positions", matches=True):
        self.positions, self.kind, self.matches = positions, kind, matches

    def generate_response(self, **kw):
        return {
            "response": json.dumps(
                {
                    "request_kind": self.kind,
                    "ranking_matches_question": self.matches,
                    "positions": list(self.positions),
                    "source_excerpt": "fifth and sixth highest IDs",
                }
            ),
            "model": "scripted",
            "tokens_used": 12,
        }


def ctx():
    c = context()
    c.question = "Show email and ID for the fifth and sixth highest IDs"
    c.sql = SQL
    c.execution_result = ExecutionResult(error="Syntax error")
    return c


@pytest.mark.parametrize(
    "last,status",
    [
        ([[5, None], [4, "b"]], "normalized"),
        ([[4, "b"], [5, None]], "reverted"),
        ([[5, None]], "reverted"),
        (None, "reverted"),
    ],
)
def test_service_singleton_proof_and_original_error_restoration(last, status):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        rows = [[[5, None]], [[4, "b"]], last][len(calls) - 1]
        return {
            "success": rows is not None,
            "rows": rows or [],
            "columns": ["id", "email"],
            "error": "Unavailable" if rows is None else None,
        }

    c = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_ranked_union(
            ctx()
        )
    )
    assert c.ranked_union["status"] == status and len(calls) == 3
    assert len(c.llm_calls) == 1 and c.ranked_union_probe_diagnostics["calls"] == 3
    if status == "reverted":
        assert c.sql == SQL and c.execution_result.error == "Syntax error"
    else:
        assert c.execution_result.rows == last and not c.execution_result.error
    restored = Ask3Context.from_dict(c.to_dict())
    assert (
        restored.ranked_union == c.ranked_union
        and restored.ranked_union_probe_diagnostics == c.ranked_union_probe_diagnostics
    )


@pytest.mark.parametrize(
    "decision",
    [Decision([4, 5]), Decision(matches=False), Decision(kind="other_or_ambiguous")],
)
def test_semantic_abstention_does_not_probe(decision):
    def forbidden(*a):
        raise AssertionError("No query expected")

    c = asyncio.run(
        AskService(llm_manager=decision, db_executor=forbidden)._apply_ranked_union(
            ctx()
        )
    )
    assert c.ranked_union["reason"] == "requested-row-positions-not-established"


def test_successful_original_never_routes():
    class Forbidden:
        def generate_response(self, **kw):
            raise AssertionError("No model call expected")

    c = ctx()
    c.execution_result = ExecutionResult(rows=[[1, "x"]], columns=["id", "email"])
    c = asyncio.run(AskService(llm_manager=Forbidden())._apply_ranked_union(c))
    assert not c.llm_calls


def test_missing_singleton_keeps_error_and_budget_is_bounded():
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": [], "columns": ["id", "email"]}

    c = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_ranked_union(
            ctx()
        )
    )
    assert c.sql == SQL and c.execution_result.error and len(calls) == 1
    bounded = create_ranked_union_probe_executor(ctx(), execute)
    for _ in range(4):
        bounded("SELECT id FROM contacts", c.target_config)
    bounded("DELETE FROM contacts", c.target_config)
    assert len(calls) == 4
