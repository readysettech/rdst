import asyncio
import json
import pytest
import sqlglot
from features.ask.name_format import (
    plan_name_format,
    accepts_name_format,
    route_name_format,
)
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import (
    ExecutionResult,
    SchemaInfo,
    TableInfo,
    ColumnInfo,
)
from features.ask.service import AskService

BASE = "SELECT CONCAT(given,' ',family) AS name,phone FROM contacts WHERE id IN (SELECT contact_id FROM bills GROUP BY contact_id HAVING SUM(amount)>10) ORDER BY phone"


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_native_fields_preserve_other_outputs_and_nested_population(dialect):
    plan = plan_name_format(BASE, dialect)
    assert plan
    before, after = [
        sqlglot.parse_one(s, read=dialect) for s in (BASE, plan.candidate_sql)
    ]
    for k, v in before.args.items():
        if k != "expressions":
            assert after.args.get(k) == v
    assert after.expressions[-1] == before.expressions[-1]
    assert [e.name for e in after.expressions[:2]] == ["given", "family"]
    assert accepts_name_format(
        [["Ari Vale", "123"], ["Bo Lin", "234"]],
        [["Ari", "Vale", "123"], ["Bo", "Lin", "234"]],
        plan,
    )
    assert not accepts_name_format(
        [["Ari Vale", "123"], ["Bo Lin", "234"]],
        [["Bo", "Lin", "234"], ["Ari", "Vale", "123"]],
        plan,
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT CONCAT(given,' employee ',family) FROM contacts",
        "SELECT CONCAT(UPPER(given),' ',family) FROM contacts",
        "SELECT CONCAT(given,' ',family) AS n FROM contacts ORDER BY n",
        "SELECT CONCAT(given,' ',family) FROM contacts ORDER BY 1",
        "SELECT CONCAT(given,' ',family) FROM contacts WHERE RAND()>0.5",
        "SELECT CONCAT(given,' ',family) FROM contacts; DELETE FROM contacts",
        "SELECT CONCAT(given,' ',family), CONCAT(prefix,'-',id) FROM contacts",
        "SELECT CONCAT(given,' ',family), given FROM contacts",
        "SELECT CONCAT(given,' ',family) FROM contacts GROUP BY given",
        "SELECT given || ' ' || family FROM contacts",
    ],
)
def test_ambiguous_or_unsupported_shape_abstains(sql):
    assert plan_name_format(sql, "postgres") is None


@pytest.mark.parametrize(
    "original,candidate,accepted",
    [
        ([["Ari Vale"]], [["Ari", "Vale"]], True),
        ([["Ari Vale"], ["Ari Vale"]], [["Ari", "Vale"], ["Ari", "Vale"]], True),
        ([["Ari Vale"], ["Ari Vale"]], [["Ari", "Vale"]], False),
        ([["Ari Vale"]], [["Other", "Person"]], False),
        ([[None]], [[None, "Vale"]], False),
        ([["12 34"]], [[12, 34]], False),
        ([], [], False),
        ([["Ari Vale"]], [["Ari", "Vale", "extra"]], False),
    ],
)
def test_complete_reconstruction_and_multiplicity(original, candidate, accepted):
    plan = plan_name_format("SELECT CONCAT(given,' ',family) FROM contacts", "mysql")
    assert accepts_name_format(original, candidate, plan) == accepted


def test_reconstruction_preserves_adjacent_values_and_column_position():
    plan = plan_name_format(
        "SELECT id,CONCAT(given,' ',family) AS name,phone FROM contacts", "mysql"
    )
    assert plan.index == 1
    assert accepts_name_format(
        [[1, "Ari Vale", "123"]], [[1, "Ari", "Vale", "123"]], plan
    )
    assert not accepts_name_format(
        [[1, "Ari Vale", "123"]], [[2, "Ari", "Vale", "123"]], plan
    )
    assert not accepts_name_format(
        [[1, "Ari Vale", "123"]], [[1, "Ari", "Vale", "456"]], plan
    )


class Route:
    def generate_response(self, **kwargs):
        return {
            "response": json.dumps(
                {
                    "requested_human_name_parts": True,
                    "explicit_combined_format": False,
                    "source_excerpt": "List full names and phone numbers.",
                }
            ),
            "model": "scripted",
        }


def context():
    ctx = Ask3Context(
        question="List full names and phone numbers.",
        target="contacts",
        db_type="mysql",
    )
    ctx.sql = ctx.generated_sql = (
        "SELECT CONCAT(given,' ',family) AS name,phone FROM contacts"
    )
    ctx.schema_info = SchemaInfo(
        target="contacts",
        db_type="mysql",
        tables={
            "contacts": TableInfo(
                name="contacts",
                columns={
                    name: ColumnInfo(name=name, data_type="varchar")
                    for name in ["given", "family", "phone"]
                },
            )
        },
    )
    ctx.target_config = {"engine": "mysql"}
    ctx.enforce_result_limit = False
    ctx.execution_result = ExecutionResult(
        columns=["name", "phone"], rows=[["Ari Vale", "123"]], row_count=1
    )
    return ctx


@pytest.mark.parametrize(
    "candidate,status",
    [
        ([["Ari", "Vale", "123"]], "normalized"),
        ([["Other", "Person", "123"]], "reverted"),
        ([[None, "Vale", "123"]], "reverted"),
        ([["Ari", "Vale", "456"]], "reverted"),
        ([], "reverted"),
    ],
)
def test_service_acceptance_restoration_and_serialization(candidate, status):
    ctx = context()
    original = ctx.sql
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "columns": ["given", "family", "phone"],
            "rows": candidate,
        }

    ctx = asyncio.run(
        AskService(llm_manager=Route(), db_executor=execute)._apply_name_format(ctx)
    )
    assert ctx.name_format["status"] == status
    assert len(calls) == 1
    if status != "normalized":
        assert ctx.sql == original
    assert ctx.name_format_probe_diagnostics["max_calls"] == 1
    assert ctx.name_format_probe_diagnostics["timeout_seconds"] == 2
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.name_format == ctx.name_format
    assert restored.name_format_probe_diagnostics == ctx.name_format_probe_diagnostics
    assert ctx.llm_calls[-1]["phase"] == "name_format_routing"


@pytest.mark.parametrize(
    "change",
    [
        {"explicit_combined_format": True},
        {"requested_human_name_parts": False},
        {"source_excerpt": "not in question"},
        {"requested_human_name_parts": "true"},
    ],
)
def test_format_constraints_and_unknown_content_preserve(change):
    class Changed(Route):
        def generate_response(self, **kwargs):
            r = super().generate_response(**kwargs)
            v = json.loads(r["response"])
            v.update(change)
            r["response"] = json.dumps(v)
            return r

    ctx = context()
    assert not route_name_format(ctx.question, ctx.sql, "mysql", Changed())["activate"]


@pytest.mark.parametrize(
    "mode", ["context", "conversation", "truncated", "error", "timeout"]
)
def test_incomplete_results_and_failed_probe_preserve(mode):
    ctx = context()
    original = ctx.sql
    calls = []
    if mode == "context":
        ctx.provided_context = "Additional request"
    if mode == "conversation":
        ctx.conversation_context = "Earlier context"
    if mode == "truncated":
        ctx.execution_result.truncated = True
    if mode == "error":
        ctx.execution_result.error = "Unavailable"

    def execute(sql, config):
        calls.append(sql)
        return {"success": False, "error": "timeout"}

    ctx = asyncio.run(
        AskService(llm_manager=Route(), db_executor=execute)._apply_name_format(ctx)
    )
    assert ctx.sql == original
    assert ctx.name_format["status"] != "normalized"
    if mode != "timeout":
        assert not calls
