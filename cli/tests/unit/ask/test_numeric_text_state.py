from types import SimpleNamespace as NS
import pytest, sqlglot
from sqlglot import exp
from features.ask.state_lookup import plan_state_lookup

SQL = "SELECT COUNT(DISTINCT j.id) FROM jobs j WHERE j.region='Harbor' AND j.position_text REGEXP '^[0-9]+$' AND EXISTS(SELECT 1 FROM jobs k WHERE k.id=j.id)"


def schema():
    return NS(
        tables={
            "jobs": NS(
                columns={
                    n: NS(data_type=t)
                    for n, t in [
                        ("id", "int"),
                        ("state_id", "int"),
                        ("position_text", "varchar"),
                        ("region", "text"),
                    ]
                },
                relationships=[
                    {
                        "target": "states",
                        "join": "jobs.state_id=states.id",
                        "type": "many_to_one",
                    }
                ],
            ),
            "states": NS(
                columns={"id": NS(data_type="int"), "label": NS(data_type="varchar")},
                relationships=[],
            ),
        }
    )


def plan(sql=SQL):
    return plan_state_lookup(sql, "mysql", schema())


def test_preserved_predicate_grain_and_exists_scope():
    p = plan()
    assert p
    original = sqlglot.parse_one(SQL, read="mysql")
    candidate = sqlglot.parse_one(p.candidate_sql(7), read="mysql")
    proof = sqlglot.parse_one(p.scope_proof_sql(7), read="mysql")
    for key in set(original.args) | set(candidate.args):
        if key != "where":
            assert original.args.get(key) == candidate.args.get(key)
    assert candidate.find(exp.RegexpLike) == original.find(exp.RegexpLike)
    assert (
        candidate.find(exp.Exists)
        == proof.find(exp.Exists)
        == original.find(exp.Exists)
    )
    assert proof.expressions[2].find(exp.RegexpLike) == original.find(exp.RegexpLike)
    assert proof.expressions[2].find(exp.Case).args["default"] == exp.Null()
    assert (
        "numeric_text_predicate" in p.structural_facts()
        and "non_null_predicate" not in p.structural_facts()
    )


@pytest.mark.parametrize(
    "sql",
    [
        SQL.replace("^[0-9]+$", "[0-9]+"),
        SQL.replace("^[0-9]+$", "^[0-9]*$"),
        SQL.replace("^[0-9]+$", "^[A-Z]+$"),
        SQL.replace(
            "j.position_text REGEXP '^[0-9]+$'", "NOT j.position_text REGEXP '^[0-9]+$'"
        ),
        SQL.replace(
            "j.position_text REGEXP '^[0-9]+$'",
            "j.position_text REGEXP '^[0-9]+$' OR j.state_id=7",
        ),
        SQL.replace("j.position_text REGEXP", "j.id REGEXP"),
        SQL.replace("j.position_text REGEXP", "k.position_text REGEXP"),
        SQL.replace("j.position_text REGEXP", "LOWER(j.position_text) REGEXP"),
        SQL.replace("AND EXISTS", "AND j.id IS NOT NULL AND EXISTS"),
        SQL.replace("COUNT(DISTINCT j.id)", "SUM(j.id)"),
        SQL + " LIMIT 1",
        SQL + " GROUP BY j.region",
        SQL + ";DELETE FROM jobs",
    ],
)
def test_unsupported_regex_or_shape_abstains(sql):
    assert plan(sql) is None


def test_postgres_abstains():
    assert plan_state_lookup(SQL, "postgres", schema()) is None


@pytest.mark.parametrize("stage", [None, 0, 1, 2])
def test_canonical_service_complete_result_guard(stage):
    import asyncio, json
    from features.ask.service import AskService
    from features.ask.engine.ask3.context import Ask3Context
    from features.ask.engine.ask3.types import ExecutionResult

    class Decision:
        def generate_response(self, **kw):
            return {
                "response": json.dumps(
                    {
                        "lookup_describes_requested_state": True,
                        "nonnull_value_requested": False,
                        "state_excerpt": "Completed",
                        "state_is_name_or_title": False,
                    }
                ),
                "model": "scripted",
                "tokens_used": 10,
            }

    calls = []

    def execute(sql, config):
        i = len(calls)
        calls.append(sql)
        return {
            "success": True,
            "rows": [[7, "Completed", 1]]
            if i == 0
            else [[1, 1, 1]]
            if i == 1
            else [[1]],
            "columns": ["a", "b", "c"] if i < 2 else ["n"],
            "truncated": i == stage,
        }

    ctx = Ask3Context(
        question="How many jobs in Harbor are Completed?",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=schema(),
        sql=SQL,
        execution_result=ExecutionResult(rows=[[2]], columns=["n"], row_count=1),
        enforce_result_limit=False,
    )
    actual = asyncio.run(
        AskService(llm_manager=Decision(), db_executor=execute)._apply_state_lookup(ctx)
    )
    assert len(calls) == (3 if stage is None else stage + 1)
    assert (actual.state_lookup["status"] == "normalized") == (stage is None)
    assert actual.execution_result.rows == ([[1]] if stage is None else [[2]])
    assert (actual.sql != SQL) == (stage is None)
