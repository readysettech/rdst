from __future__ import annotations

import pytest

from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ColumnInfo, SchemaInfo, TableInfo
from features.ask.service import AskService
from features.ask.value_probe import create_value_probe_executor


def test_value_probe_allows_two_read_only_calls_then_stops():
    calls = []

    def database(sql, _config):
        calls.append(sql)
        return {"success": True, "rows": [[1]], "columns": ["count"]}

    ctx = Ask3Context(question="q", target="test", db_type="mysql")
    probe = create_value_probe_executor(ctx, database)

    assert probe("SELECT COUNT(*) FROM t", {})["success"] is True
    assert probe("SELECT COUNT(*) FROM t", {})["success"] is True
    blocked = probe("SELECT COUNT(*) FROM t", {})

    assert blocked["success"] is False
    assert blocked["error_kind"] == "probe_budget_exhausted"
    assert len(calls) == 2
    assert ctx.db_probe_diagnostics["blocked_calls"] == 1


def test_value_probe_rejects_writes_before_executor():
    ctx = Ask3Context(question="q", target="test", db_type="postgresql")
    probe = create_value_probe_executor(
        ctx,
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("unsafe SQL reached executor")
        ),
    )

    result = probe("DELETE FROM t", {})

    assert result["success"] is False
    assert result["error_kind"] == "probe_validation_failed"


@pytest.mark.asyncio
async def test_service_keeps_a_valid_dominant_sibling_rewrite():
    def database(sql, _config):
        support = 20 if "categories" in sql else 0
        return {"success": True, "rows": [[support]], "columns": ["count"]}

    ctx = Ask3Context(
        question="Return products in the Hardware category",
        target="test",
        db_type="mysql",
        target_config={"engine": "mysql"},
    )
    ctx.sql = "SELECT id FROM products WHERE category LIKE '%Hardware%'"
    ctx.generated_sql = ctx.sql
    ctx.schema_info = SchemaInfo(
        target="test",
        db_type="mysql",
        tables={
            "products": TableInfo(
                name="products",
                columns={
                    "id": ColumnInfo(name="id", data_type="int"),
                    "category": ColumnInfo(name="category", data_type="varchar"),
                    "categories": ColumnInfo(
                        name="categories",
                        data_type="varchar",
                    ),
                },
            )
        },
    )
    service = AskService(
        db_executor=database,
        value_location_normalization_enabled=True,
    )

    ctx = await service._apply_value_location_normalization(ctx)

    assert "categories = 'Hardware'" in ctx.sql
    assert ctx.value_location_normalization["status"] == "normalized"
    assert ctx.db_probe_diagnostics["calls"] == 2


@pytest.mark.asyncio
async def test_service_reverts_an_invalid_grounded_candidate():
    def invalid_normalizer(ctx, _probe):
        ctx.sql = "SELECT missing_column FROM orders"
        ctx.generated_sql = ctx.sql
        ctx.value_location_normalization = {"status": "normalized"}
        return ctx

    ctx = Ask3Context(
        question="Return orders",
        target="test",
        db_type="mysql",
        target_config={"engine": "mysql"},
    )
    ctx.sql = "SELECT id FROM orders"
    ctx.generated_sql = ctx.sql
    ctx.schema_info = SchemaInfo(
        target="test",
        db_type="mysql",
        tables={
            "orders": TableInfo(
                name="orders",
                columns={"id": ColumnInfo(name="id", data_type="int")},
            )
        },
    )
    service = AskService(
        db_executor=lambda *_args: {},
        value_location_normalization_enabled=True,
        value_location_normalization_fn=invalid_normalizer,
    )

    ctx = await service._apply_value_location_normalization(ctx)

    assert ctx.sql == "SELECT id FROM orders"
    assert ctx.value_location_normalization["status"] == "reverted"
