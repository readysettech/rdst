import json
from unittest.mock import MagicMock

import pytest

from features.schema.inference_context import schema_cache_key, schema_context_prefix


def test_schema_cache_identity_is_stable_and_target_scoped():
    key = schema_cache_key("Table: items", "postgresql", "target")
    assert key == schema_cache_key("Table: items", "postgresql", "target")
    assert key != schema_cache_key("Table: items", "postgresql", "other")
    assert key != schema_cache_key("Table: items changed", "postgresql", "target")
    assert key != schema_cache_key("Table: items", "mysql", "target")
    assert schema_cache_key("Table: items", "postgresql", "") is None


@pytest.mark.parametrize("shallow", [False, True])
def test_analyze_queries_share_schema_prefix_and_routing_key(monkeypatch, shallow):
    from features.analyze.functions import llm_analysis, shallow_analysis
    module = shallow_analysis if shallow else llm_analysis
    manager = MagicMock()
    manager.generate_response.return_value = {"response": json.dumps({})}
    monkeypatch.setattr(module, "LLMManager", lambda: manager)
    schema = "Table: items\n  id integer"
    for sql in ["SELECT id FROM items", "SELECT COUNT(*) FROM items"]:
        if shallow:
            module.analyze_shallow_with_llm(
                sql, schema_info=schema, target="target", database_engine="postgresql"
            )
        else:
            module.analyze_with_llm(
                {"database_engine": "postgresql", "success": True}, {}, sql,
                schema_info=schema, target="target"
            )
    assert manager.generate_response.call_count == 2
    calls = [call.kwargs for call in manager.generate_response.call_args_list]
    for call in calls:
        assert call["system_message"].startswith(schema_context_prefix(schema, "postgresql"))
        assert schema not in call["prompt"]
        assert call["extra"]["_rdst_schema_cache_key"] == schema_cache_key(
            schema, "postgresql", "target"
        )
    assert calls[0]["prompt"] != calls[1]["prompt"]


def test_analyze_collector_keeps_relevant_schema_and_indexes(monkeypatch):
    from features.schema import schema_collector
    from features.schema.semantic_layer.manager import SemanticLayerManager
    from features.schema.semantic_models import SemanticLayer, TableAnnotation, ColumnAnnotation

    layer = SemanticLayer(target="target", tables={
        name: TableAnnotation(name=name, columns={
            "id": ColumnAnnotation(name="id", data_type="integer")
        }) for name in ["items", "unrelated_table"]
    })
    monkeypatch.setattr(SemanticLayerManager, "exists", lambda *args: True)
    monkeypatch.setattr(SemanticLayerManager, "load", lambda *args: layer)
    monkeypatch.setattr(schema_collector, "collect_engine_version", lambda *args, **kwargs: {})
    subset = "Table: items\nRow estimate: 100\nIndexes: items_pkey"
    collector = MagicMock(return_value=subset)
    monkeypatch.setattr(schema_collector, "collect_schema_for_query", collector)
    result = schema_collector.collect_target_schema(
        "SELECT id FROM items", target="target", target_config={"engine": "postgresql"}
    )
    assert result["success"], result
    assert result["schema_info"] == subset
    assert "unrelated_table" not in result["schema_info"]
    assert "items_pkey" in result["schema_info"]
    assert result["tables_analyzed"] == ["items"]
    assert collector.call_args.args[0] == "SELECT id FROM items"
