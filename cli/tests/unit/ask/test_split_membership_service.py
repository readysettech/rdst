import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import patch
import pytest
from features.ask.service import AskService
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.list_membership import route_list_membership as route_split_membership

SQL = "SELECT COUNT(*) FROM equipment WHERE active=1 AND (skills IS NULL OR skills='' OR FIND_IN_SET('Brazing',skills)=0)"
PROOF = [
    [b"Brazing", 5, 0, 0],
    [b"Brazing,Welding", 2, 0, 2],
    [b"Painting", 3, 3, 3],
    [None, 1, 1, 0],
]
QUALIFIER = dict(
    output="parent_count",
    collection_binding="yes",
    value_kind="collection",
    named_member="Brazing",
    member_relation="different_from_named",
    lower_bound=1,
    upper_bound=-1,
    named_presence="optional",
    member_qualifiers=[],
    allows_missing=False,
    combination="single_requirement",
    source_excerpt="skills besides Brazing",
    coverage="complete",
)
MEMBERSHIP = dict(
    mixed_members_match="yes",
    quantifier="any_other_member",
    requires_named_member=False,
    requests_missing_values=False,
    output_request="count_parent_records",
    value_role="collection_member",
    generated_column_matches_question="yes",
    named_member="Brazing",
    source_excerpt="skills besides Brazing",
)


def context():
    return Ask3Context(
        question="Count active equipment with skills besides Brazing.",
        target="local",
        db_type="mysql",
        target_config={"engine": "mysql"},
        schema_info=NS(
            tables={
                "equipment": NS(
                    columns={
                        "active": NS(data_type="int"),
                        "skills": NS(data_type="text"),
                    }
                )
            }
        ),
        sql=SQL,
        execution_result=ExecutionResult(rows=[[4]], columns=["n"], row_count=1),
        enforce_result_limit=False,
    )


class Adapter:
    def __init__(self, qualifier=QUALIFIER, membership=MEMBERSHIP):
        self.qualifier, self.membership = qualifier, membership
        self.calls = []

    def generate_response(self, **kw):
        self.calls.append(kw)
        value = (
            self.qualifier
            if kw["purpose"] == "collection_request_routing"
            else self.membership
        )
        return {"response": json.dumps(value), "model": "fixture", "tokens_used": 10}


@pytest.mark.parametrize("candidate", [5, 4, None])
def test_two_parsers_feed_one_existing_canonical_storage_proof(candidate):
    calls = []

    def db(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": PROOF if len(calls) == 1 else [[candidate]],
            "columns": ["v", "n", "old", "new"] if len(calls) == 1 else ["n"],
        }

    adapter = Adapter()
    with patch("features.ask.service.route_list_membership", route_split_membership):
        ctx = asyncio.run(
            AskService(llm_manager=adapter, db_executor=db)._apply_list_membership(
                context()
            )
        )
    assert len(adapter.calls) == 2 and len(ctx.llm_calls) == 2 and len(calls) == 2
    assert ctx.list_membership_probe_diagnostics["max_calls"] == 2
    assert (ctx.sql != SQL) == (candidate == 5)
    assert ctx.execution_result.rows == ([[5]] if candidate == 5 else [[4]])
    assert ctx.list_membership["status"] == (
        "normalized" if candidate == 5 else "reverted"
    )
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.list_membership == ctx.list_membership


@pytest.mark.parametrize(
    "first,second,expected_calls",
    [
        ({**QUALIFIER, "member_qualifiers": ["approved"]}, MEMBERSHIP, 1),
        ({**QUALIFIER, "upper_bound": 1}, MEMBERSHIP, 1),
        (QUALIFIER, {**MEMBERSHIP, "requires_named_member": True}, 2),
        (QUALIFIER, {**MEMBERSHIP, "mixed_members_match": "no"}, 2),
        ({**QUALIFIER, "source_excerpt": "invented"}, MEMBERSHIP, 1),
    ],
)
def test_either_owner_can_reject_before_any_database_probe(
    first, second, expected_calls
):
    adapter = Adapter(first, second)
    calls = []

    def db(*args):
        calls.append(args)
        raise AssertionError("No database access allowed")

    with patch("features.ask.service.route_list_membership", route_split_membership):
        ctx = asyncio.run(
            AskService(llm_manager=adapter, db_executor=db)._apply_list_membership(
                context()
            )
        )
    assert not calls and len(adapter.calls) == expected_calls
    assert ctx.sql == SQL and ctx.execution_result.rows == [[4]]
    assert ctx.list_membership["reason"] == "model-abstained"


@pytest.mark.parametrize("truncated_call", [1, 2])
def test_explicitly_incomplete_proof_or_result_restores_original(truncated_call):
    calls = []

    def db(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": PROOF if len(calls) == 1 else [[5]],
            "columns": ["v", "n", "old", "new"] if len(calls) == 1 else ["n"],
            "truncated": len(calls) == truncated_call,
        }

    adapter = Adapter()
    ctx = asyncio.run(
        AskService(llm_manager=adapter, db_executor=db)._apply_list_membership(
            context()
        )
    )
    assert len(adapter.calls) == 2 and len(calls) == truncated_call
    assert ctx.sql == SQL and ctx.execution_result.rows == [[4]]
    assert ctx.list_membership["status"] != "normalized"
