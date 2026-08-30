from __future__ import annotations

from dataclasses import dataclass

import pytest

from features.ask.correction_intent_routing import (
    CORRECTION_INTENT_EXPERIMENTAL_SCOPE,
)
from features.ask.encoded_identifier_storage import (
    encoded_identifier_shape,
    normalize_encoded_identifier_storage_sql,
)
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import (
    ColumnInfo,
    ExecutionResult,
    SchemaInfo,
    TableInfo,
)
from features.ask.month_axis_storage import (
    month_axis_storage_shape,
    normalize_month_axis_storage_sql,
)
from features.ask.service import AskService
from features.ask.temporal_text_storage import (
    normalize_temporal_text_storage_sql,
    temporal_text_storage_shape,
)

EDGE_SQL = (
    "SELECT COUNT(*) FROM connected WHERE atom_id = 'atom19' OR atom_id2 = 'atom19'"
)
TIME_SQL = (
    "SELECT d.code FROM qualifying q "
    "JOIN drivers d ON q.driverId = d.driverId "
    "WHERE q.raceId = 45 AND q.q3 = '1:33'"
)
MONTH_SQL = (
    "SELECT p.description FROM products p "
    "WHERE p.date >= '2013-09-01' AND p.date < '2013-10-01'"
)


def _edge_schema(dialect: str = "mysql") -> SchemaInfo:
    return SchemaInfo(
        target="fixture",
        db_type=dialect,
        tables={
            "connected": TableInfo(
                name="connected",
                columns={
                    "atom_id": ColumnInfo("atom_id", "varchar"),
                    "atom_id2": ColumnInfo("atom_id2", "varchar"),
                    "bond_id": ColumnInfo("bond_id", "varchar"),
                },
            )
        },
    )


def _time_schema(dialect: str = "mysql") -> SchemaInfo:
    return SchemaInfo(
        target="fixture",
        db_type=dialect,
        tables={
            "qualifying": TableInfo(
                name="qualifying",
                columns={
                    "raceId": ColumnInfo("raceId", "int"),
                    "driverId": ColumnInfo("driverId", "int"),
                    "q3": ColumnInfo("q3", "varchar"),
                },
            ),
            "drivers": TableInfo(
                name="drivers",
                columns={
                    "driverId": ColumnInfo("driverId", "int"),
                    "code": ColumnInfo("code", "varchar"),
                },
            ),
        },
    )


def _month_schema(dialect: str = "mysql") -> SchemaInfo:
    return SchemaInfo(
        target="fixture",
        db_type=dialect,
        tables={
            "products": TableInfo(
                name="products",
                columns={
                    "product_id": ColumnInfo("product_id", "int"),
                    "date": ColumnInfo("date", "date"),
                    "description": ColumnInfo("description", "varchar"),
                },
            ),
            "date_mapping": TableInfo(
                name="date_mapping",
                columns={
                    "product_id": ColumnInfo("product_id", "int"),
                    "date": ColumnInfo("date", "varchar"),
                },
            ),
        },
    )


def test_encoded_identifier_repair_is_ast_and_database_proven():
    calls = []

    def execute(sql, config):
        calls.append((sql, config))
        return {"success": True, "rows": [[12, 0, 12, 12, 12]]}

    repaired, diagnostics = normalize_encoded_identifier_storage_sql(
        sql=EDGE_SQL,
        dialect="mysql",
        schema_info=_edge_schema(),
        db_executor=execute,
        target_config={"engine": "mysql"},
    )

    assert "COUNT(DISTINCT bond_id)" in repaired
    assert "RIGHT(atom_id, 3) = '_19'" in repaired
    assert "RIGHT(atom_id2, 3) = '_19'" in repaired
    assert diagnostics["status"] == "normalized"
    assert len(calls) == 1
    assert encoded_identifier_shape(EDGE_SQL, "mysql")


def test_encoded_identifier_repair_accepts_a_plain_numeric_endpoint():
    sql = EDGE_SQL.replace("atom19", "19")
    repaired, diagnostics = normalize_encoded_identifier_storage_sql(
        sql=sql,
        dialect="mysql",
        schema_info=_edge_schema(),
        db_executor=lambda *_args: {
            "success": True,
            "rows": [[12, 0, 12, 12, 12]],
        },
        target_config={"engine": "mysql"},
    )

    assert "RIGHT(atom_id, 3) = '_19'" in repaired
    assert "RIGHT(atom_id2, 3) = '_19'" in repaired
    assert diagnostics["status"] == "normalized"
    assert encoded_identifier_shape(sql, "mysql")


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_encoded_identifier_repair_supports_product_dialects(dialect):
    repaired, diagnostics = normalize_encoded_identifier_storage_sql(
        sql=EDGE_SQL,
        dialect=dialect,
        schema_info=_edge_schema(dialect),
        db_executor=lambda *_args: {
            "success": True,
            "rows": [[4, 0, 4, 4, 4]],
        },
        target_config={"engine": dialect},
    )

    assert repaired != EDGE_SQL
    assert diagnostics["status"] == "normalized"


def test_encoded_identifier_repair_rejects_a_non_global_storage_pattern():
    repaired, diagnostics = normalize_encoded_identifier_storage_sql(
        sql=EDGE_SQL,
        dialect="mysql",
        schema_info=_edge_schema(),
        db_executor=lambda *_args: {
            "success": True,
            "rows": [[12, 0, 12, 11, 11]],
        },
        target_config={"engine": "mysql"},
    )

    assert repaired == EDGE_SQL
    assert diagnostics["reason"] == "mirrored-suffix-not-proven"


@pytest.mark.parametrize(
    ("literal", "prefix"),
    [("1:33", "1:33"), ("1:27.000", "1:27"), ("00:01:27", "1:27")],
)
def test_temporal_repair_accepts_observed_clock_encodings(literal, prefix):
    sql = TIME_SQL.replace("1:33", literal)
    repaired, diagnostics = normalize_temporal_text_storage_sql(
        sql=sql,
        dialect="mysql",
        schema_info=_time_schema(),
        db_executor=lambda *_args: {"success": True, "rows": [[0, 1]]},
        target_config={"engine": "mysql"},
    )

    assert f"q.q3 = '{prefix}' OR q.q3 LIKE '{prefix}.%'" in repaired
    assert diagnostics["status"] == "normalized"
    assert temporal_text_storage_shape(sql, "mysql")


def test_temporal_repair_supports_postgres_text_columns():
    repaired, diagnostics = normalize_temporal_text_storage_sql(
        sql=TIME_SQL,
        dialect="postgresql",
        schema_info=_time_schema("postgresql"),
        db_executor=lambda *_args: {"success": True, "rows": [[0, 1]]},
        target_config={"engine": "postgresql"},
    )

    assert "q.q3 = '1:33' OR q.q3 LIKE '1:33.%'" in repaired
    assert diagnostics["status"] == "normalized"


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
def test_month_axis_repair_uses_sql_bounds_and_database_proofs(dialect):
    calls = []

    def execute(sql, _config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[0, 1, 1, 1, 0, 0, 0, 0, 1]],
        }

    repaired, diagnostics = normalize_month_axis_storage_sql(
        sql=MONTH_SQL,
        dialect=dialect,
        schema_info=_month_schema(dialect),
        db_executor=execute,
        target_config={"engine": dialect},
        intent_activated=True,
    )

    assert repaired != MONTH_SQL
    assert "201309" in repaired
    assert "date_mapping" in repaired
    assert diagnostics["status"] == "normalized"
    assert diagnostics["probe_count"] == 1
    assert len(calls) == 1
    assert month_axis_storage_shape(MONTH_SQL, dialect)


def test_month_axis_repair_requires_model_activation_before_probing():
    repaired, diagnostics = normalize_month_axis_storage_sql(
        sql=MONTH_SQL,
        dialect="mysql",
        schema_info=_month_schema(),
        db_executor=lambda *_args: pytest.fail("unselected repair must not probe"),
        target_config={"engine": "mysql"},
        intent_activated=False,
    )

    assert repaired == MONTH_SQL
    assert diagnostics["reason"] == "intent-not-activated"


@pytest.mark.parametrize("proof_index", range(9))
def test_month_axis_repair_requires_every_database_proof(proof_index):
    proofs = [0, 1, 1, 1, 0, 0, 0, 0, 1]
    proofs[proof_index] = 1 - proofs[proof_index]
    repaired, diagnostics = normalize_month_axis_storage_sql(
        sql=MONTH_SQL,
        dialect="mysql",
        schema_info=_month_schema(),
        db_executor=lambda *_args: {
            "success": True,
            "rows": [proofs],
        },
        target_config={"engine": "mysql"},
        intent_activated=True,
    )

    assert repaired == MONTH_SQL
    assert diagnostics["reason"] == "no-supported-period-route"


def test_month_axis_repair_rejects_two_supported_routes():
    schema = _month_schema()
    schema.tables["period_dimension"] = TableInfo(
        name="period_dimension",
        columns={
            "product_id": ColumnInfo("product_id", "int"),
            "date": ColumnInfo("date", "varchar"),
        },
    )
    calls = []

    repaired, diagnostics = normalize_month_axis_storage_sql(
        sql=MONTH_SQL,
        dialect="mysql",
        schema_info=schema,
        db_executor=lambda sql, _config: (
            calls.append(sql)
            or {"success": True, "rows": [[0, 1, 1, 1, 0, 0, 0, 0, 1]]}
        ),
        target_config={"engine": "mysql"},
        intent_activated=True,
    )

    assert repaired == MONTH_SQL
    assert diagnostics["reason"] == "ambiguous-supported-period-routes"
    assert len(calls) == 2


def test_month_axis_repair_abstains_before_more_than_four_route_probes():
    schema = _month_schema()
    for table_name in (
        "date_lookup",
        "period_dimension",
        "month_mapping",
        "time_table",
    ):
        schema.tables[table_name] = TableInfo(
            name=table_name,
            columns={
                "product_id": ColumnInfo("product_id", "int"),
                "date": ColumnInfo("date", "varchar"),
            },
        )

    repaired, diagnostics = normalize_month_axis_storage_sql(
        sql=MONTH_SQL,
        dialect="mysql",
        schema_info=schema,
        db_executor=lambda *_args: pytest.fail("ambiguous routes must not probe"),
        target_config={"engine": "mysql"},
        intent_activated=True,
    )

    assert repaired == MONTH_SQL
    assert diagnostics["reason"] == "too-many-alternate-period-routes"


@dataclass(frozen=True)
class _RoutingResult:
    intent: str

    def to_dict(self):
        return {
            "status": "activate",
            "verdict": "activate",
            "selected_intent": self.intent,
            "selected_intents": [self.intent],
        }


def _time_context() -> Ask3Context:
    ctx = Ask3Context(
        question="Wer fuhr in 1:33?",
        target="fixture",
        db_type="mysql",
        target_config={"engine": "mysql"},
        enforce_result_limit=False,
    )
    ctx.sql = TIME_SQL
    ctx.generated_sql = TIME_SQL
    ctx.schema_info = _time_schema()
    return ctx


def _edge_context() -> Ask3Context:
    ctx = Ask3Context(
        question="Combien de liaisons possède l’atome 19 ?",
        target="fixture",
        db_type="mysql",
        target_config={"engine": "mysql"},
        enforce_result_limit=False,
    )
    ctx.sql = EDGE_SQL
    ctx.generated_sql = EDGE_SQL
    ctx.schema_info = _edge_schema()
    return ctx


def _month_context() -> Ask3Context:
    ctx = Ask3Context(
        question="Dame los productos de septimbre de 2013",
        target="fixture",
        db_type="mysql",
        target_config={"engine": "mysql"},
        enforce_result_limit=False,
    )
    ctx.sql = MONTH_SQL
    ctx.generated_sql = MONTH_SQL
    ctx.schema_info = _month_schema()
    return ctx


@pytest.mark.asyncio
async def test_model_selected_encoded_identifier_repair_runs_without_english_rule():
    service = AskService(
        llm_manager=object(),
        db_executor=lambda *_args: {
            "success": True,
            "rows": [[12, 0, 12, 12, 12]],
        },
        correction_intent_routing_enabled=True,
        correction_intent_routing_intent_scope=CORRECTION_INTENT_EXPERIMENTAL_SCOPE,
        correction_intent_routing_fn=lambda **_kwargs: _RoutingResult(
            "encoded_identifier_storage"
        ),
        encoded_identifier_storage_enabled=True,
    )

    ctx = await service._apply_correction_intent_routing(_edge_context())

    assert "COUNT(DISTINCT bond_id)" in ctx.sql
    assert ctx.encoded_identifier_storage["status"] == "normalized"
    assert ctx.correction_intent_routing["selected_sql_applied"] is True


@pytest.mark.asyncio
async def test_unselected_encoded_identifier_repair_never_probes_database():
    service = AskService(
        llm_manager=object(),
        db_executor=lambda *_args: pytest.fail("unselected repair must not probe"),
        correction_intent_routing_enabled=True,
        correction_intent_routing_intent_scope=CORRECTION_INTENT_EXPERIMENTAL_SCOPE,
        correction_intent_routing_fn=lambda **_kwargs: {
            "status": "no_match",
            "verdict": "no_match",
            "selected_intent": "none",
            "selected_intents": [],
        },
        encoded_identifier_storage_enabled=True,
    )
    ctx = _edge_context()

    ctx = await service._apply_correction_intent_routing(ctx)

    assert ctx.sql == EDGE_SQL
    assert ctx.encoded_identifier_storage == {}


@pytest.mark.asyncio
async def test_model_selected_temporal_repair_waits_for_empty_execution():
    calls = []

    def execute(sql, _config):
        calls.append(sql)
        if "EXISTS(" in sql:
            return {"success": True, "rows": [[0, 1]], "columns": ["a", "b"]}
        if "LIKE '1:33.%'" in sql:
            return {"success": True, "rows": [["ALO"]], "columns": ["code"]}
        return {"success": True, "rows": [], "columns": ["code"]}

    service = AskService(
        llm_manager=object(),
        db_executor=execute,
        correction_intent_routing_enabled=True,
        correction_intent_routing_intent_scope=CORRECTION_INTENT_EXPERIMENTAL_SCOPE,
        correction_intent_routing_fn=lambda **_kwargs: _RoutingResult(
            "temporal_text_storage"
        ),
        temporal_text_storage_enabled=True,
    )
    ctx = _time_context()

    ctx = await service._apply_correction_intent_routing(ctx)
    assert ctx.sql == TIME_SQL
    assert ctx.correction_intent_routing["application_status"] == "deferred"

    ctx.execution_result = ExecutionResult(columns=["code"], rows=[], row_count=0)
    ctx = await service._apply_post_execution_repairs(ctx)

    assert "LIKE '1:33.%'" in ctx.sql
    assert ctx.execution_result.rows == [["ALO"]]
    assert ctx.temporal_text_storage["status"] == "accepted"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_temporal_candidate_that_stays_empty_reverts_to_primary_sql():
    def execute(sql, _config):
        if "EXISTS(" in sql:
            return {"success": True, "rows": [[0, 1]], "columns": ["a", "b"]}
        return {"success": True, "rows": [], "columns": ["code"]}

    service = AskService(
        llm_manager=object(),
        db_executor=execute,
        correction_intent_routing_enabled=True,
        correction_intent_routing_intent_scope=CORRECTION_INTENT_EXPERIMENTAL_SCOPE,
        correction_intent_routing_fn=lambda **_kwargs: _RoutingResult(
            "temporal_text_storage"
        ),
        temporal_text_storage_enabled=True,
    )
    ctx = await service._apply_correction_intent_routing(_time_context())
    ctx.execution_result = ExecutionResult(columns=["code"], rows=[], row_count=0)

    ctx = await service._apply_post_execution_repairs(ctx)

    assert ctx.sql == TIME_SQL
    assert ctx.execution_result.row_count == 0
    assert ctx.temporal_text_storage["status"] == "reverted"


@pytest.mark.asyncio
async def test_model_selected_month_axis_repair_waits_for_empty_execution():
    calls = []

    def execute(sql, _config):
        calls.append(sql)
        if sql.startswith("SELECT EXISTS("):
            return {
                "success": True,
                "rows": [[0, 1, 1, 1, 0, 0, 0, 0, 1]],
                "columns": [f"proof_{index}" for index in range(9)],
            }
        if "201309" in sql:
            return {
                "success": True,
                "rows": [["bike"]],
                "columns": ["description"],
            }
        return {"success": True, "rows": [], "columns": ["description"]}

    service = AskService(
        llm_manager=object(),
        db_executor=execute,
        correction_intent_routing_enabled=True,
        correction_intent_routing_intent_scope=CORRECTION_INTENT_EXPERIMENTAL_SCOPE,
        correction_intent_routing_fn=lambda **_kwargs: _RoutingResult(
            "month_axis_storage"
        ),
        month_axis_storage_enabled=True,
    )
    ctx = await service._apply_correction_intent_routing(_month_context())

    assert ctx.sql == MONTH_SQL
    assert ctx.correction_intent_routing["application_status"] == "deferred"

    ctx.execution_result = ExecutionResult(
        columns=["description"], rows=[], row_count=0
    )
    ctx = await service._apply_post_execution_repairs(ctx)

    assert "201309" in ctx.sql
    assert ctx.execution_result.rows == [["bike"]]
    assert ctx.month_axis_storage["status"] == "accepted"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_month_axis_repair_does_not_probe_after_a_nonempty_result():
    service = AskService(
        llm_manager=object(),
        db_executor=lambda *_args: pytest.fail("nonempty result must not probe"),
        correction_intent_routing_enabled=True,
        correction_intent_routing_intent_scope=CORRECTION_INTENT_EXPERIMENTAL_SCOPE,
        correction_intent_routing_fn=lambda **_kwargs: _RoutingResult(
            "month_axis_storage"
        ),
        month_axis_storage_enabled=True,
    )
    ctx = await service._apply_correction_intent_routing(_month_context())
    ctx.execution_result = ExecutionResult(
        columns=["description"], rows=[["existing"]], row_count=1
    )

    ctx = await service._apply_post_execution_repairs(ctx)

    assert ctx.sql == MONTH_SQL
    assert ctx.month_axis_storage["reason"] == ("primary-result-not-empty-and-complete")


@pytest.mark.asyncio
async def test_shadow_temporal_route_never_executes_or_probes():
    service = AskService(
        llm_manager=object(),
        db_executor=lambda *_args: pytest.fail("shadow route must not execute"),
        correction_intent_routing_shadow=True,
        correction_intent_routing_intent_scope=CORRECTION_INTENT_EXPERIMENTAL_SCOPE,
        correction_intent_routing_fn=lambda **_kwargs: _RoutingResult(
            "temporal_text_storage"
        ),
        temporal_text_storage_enabled=True,
    )

    ctx = await service._apply_correction_intent_routing(_time_context())

    assert ctx.sql == TIME_SQL
    assert ctx.correction_intent_routing["activation_allowed"] is False
    assert ctx.correction_intent_routing["application_status"] == "shadow_deferred"


def test_context_round_trip_preserves_storage_repair_diagnostics():
    ctx = _time_context()
    ctx.encoded_identifier_storage = {"status": "normalized"}
    ctx.temporal_text_storage = {"status": "accepted"}
    ctx.month_axis_storage = {"status": "accepted"}

    restored = Ask3Context.from_dict(ctx.to_dict())

    assert restored.encoded_identifier_storage == ctx.encoded_identifier_storage
    assert restored.temporal_text_storage == ctx.temporal_text_storage
    assert restored.month_axis_storage == ctx.month_axis_storage
