from __future__ import annotations

from dataclasses import dataclass

import pytest

from features.ask.correction_intent_routing import CORRECTION_INTENT_PRODUCT_SCOPE
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ColumnInfo, SchemaInfo, TableInfo
from features.ask.service import AskService


@dataclass(frozen=True)
class _RoutingResult:
    status: str = "activate"
    verdict: str = "activate"
    selected_intent: str = "ratio_output"

    def to_dict(self):
        return {
            "status": self.status,
            "verdict": self.verdict,
            "selected_intent": self.selected_intent,
            "selected_intents": [self.selected_intent],
        }


def _context(question: str = "Return the avergae completed orders per member"):
    ctx = Ask3Context(question=question, target="test", dry_run=True)
    ctx.db_type = "mysql"
    ctx.sql = "SELECT SUM(completed) / COUNT(*) FROM orders"
    ctx.generated_sql = ctx.sql
    ctx.schema_info = SchemaInfo(
        target="test",
        db_type="mysql",
        tables={
            "orders": TableInfo(
                name="orders",
                columns={
                    "completed": ColumnInfo(
                        name="completed",
                        data_type="int",
                    )
                },
            )
        },
    )
    return ctx


def test_router_is_off_by_default():
    service = AskService(
        correction_intent_routing_fn=lambda **_kwargs: pytest.fail(
            "disabled router must not run"
        )
    )

    ctx = service._route_correction_intent(_context())

    assert ctx.correction_intent_routing == {
        "status": "disabled",
        "activation_allowed": False,
        "shadow": False,
    }


def test_router_receives_question_sql_and_complete_catalog():
    captured = {}

    def router(**kwargs):
        captured.update(kwargs)
        return _RoutingResult()

    service = AskService(
        llm_manager=object(),
        correction_intent_routing_enabled=True,
        correction_intent_routing_fn=router,
    )
    ctx = _context()

    service._route_correction_intent(ctx)

    assert captured["effective_question"] == ctx.question
    assert captured["proposed_sql"] == ctx.sql
    assert captured["allowed_intents"] == CORRECTION_INTENT_PRODUCT_SCOPE
    assert captured["allowed_intent_sets"] is None


@pytest.mark.asyncio
async def test_selected_ratio_correction_handles_typo_without_question_regex():
    service = AskService(
        llm_manager=object(),
        correction_intent_routing_enabled=True,
        correction_intent_routing_fn=lambda **_kwargs: _RoutingResult(),
    )
    ctx = _context()

    ctx = await service._apply_correction_intent_routing(ctx)

    assert "CAST(SUM(completed) AS DOUBLE)" in ctx.sql
    assert ctx.correction_intent_routing["selected_sql_applied"] is True
    assert ctx.correction_intent_routing["application_status"] == "validated"


@pytest.mark.asyncio
async def test_router_failure_keeps_generated_sql():
    def broken_router(**_kwargs):
        raise RuntimeError("offline")

    service = AskService(
        llm_manager=object(),
        correction_intent_routing_enabled=True,
        correction_intent_routing_fn=broken_router,
    )
    ctx = _context()
    original_sql = ctx.sql

    ctx = await service._apply_correction_intent_routing(ctx)

    assert ctx.sql == original_sql
    assert ctx.correction_intent_routing["status"] == "error"
    assert ctx.correction_intent_routing["activation_allowed"] is False


@pytest.mark.asyncio
async def test_shadow_router_records_candidate_without_applying_it():
    service = AskService(
        llm_manager=object(),
        correction_intent_routing_shadow=True,
        correction_intent_routing_fn=lambda **_kwargs: _RoutingResult(),
    )
    ctx = _context()
    original_sql = ctx.sql

    ctx = await service._apply_correction_intent_routing(ctx)

    assert ctx.sql == original_sql
    assert ctx.correction_intent_routing["application_status"] == "validated"
    assert ctx.correction_intent_routing["selected_sql_applied"] is False
    assert ctx.correction_intent_routing["candidate_sql_sha256"]


def test_context_round_trip_preserves_router_diagnostics():
    ctx = _context()
    ctx.correction_intent_routing = {
        "status": "activate",
        "selected_intents": ["ratio_output"],
    }

    restored = Ask3Context.from_dict(ctx.to_dict())

    assert restored.correction_intent_routing == ctx.correction_intent_routing
