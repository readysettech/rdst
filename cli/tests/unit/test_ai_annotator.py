"""
Unit tests for AIAnnotator.annotate_table.

The batched path annotates a whole table in one LLM call (chunked only for
very wide tables), with profile stats and enum values folded into the prompt.
"""

import json
import pytest
from unittest.mock import Mock

from features.schema.semantic_layer.ai_annotator import AIAnnotator, COLUMNS_PER_CALL
from features.schema.semantic_models import TableAnnotation, ColumnAnnotation


def _llm_returning(*payloads):
    llm = Mock()
    llm.query.side_effect = [{"text": json.dumps(p)} for p in payloads]
    return llm


def _table(n_cols=3, with_stats=True):
    cols = {}
    for i in range(n_cols):
        name = f"col{i}"
        col = ColumnAnnotation(name=name, data_type="text")
        if with_stats:
            col.null_fraction = 0.25
            col.distinct_count = 42
            col.top_values = [["a", 120], ["b", 80]]
        cols[name] = col
    return TableAnnotation(name="users", row_estimate="1.2M", columns=cols)


class TestAnnotateTableSingleCall:
    def test_small_table_uses_exactly_one_llm_call(self):
        payload = {
            "description": "User accounts",
            "business_context": "Created at signup",
            "columns": {f"col{i}": {"description": f"c{i}"} for i in range(3)},
        }
        llm = _llm_returning(payload)

        result = AIAnnotator(llm_manager=llm).annotate_table("users", _table(3))

        assert llm.query.call_count == 1
        assert result["description"] == "User accounts"
        assert result["business_context"] == "Created at signup"
        assert result["columns"]["col1"]["description"] == "c1"

    def test_prompt_includes_profile_stats_enums_and_context(self):
        table = _table(2)
        table.columns["col0"].enum_values = {"A": "", "B": ""}
        llm = _llm_returning({"description": "d", "columns": {}})

        AIAnnotator(llm_manager=llm).annotate_table(
            "users", table, schema_context="imdb database"
        )

        prompt = llm.query.call_args.kwargs["user_query"]
        assert "Table: users" in prompt
        assert "null: 25%" in prompt
        assert "distinct: 42" in prompt
        assert "top values: a (120), b (80)" in prompt
        assert "enum values: [A, B]" in prompt
        assert "imdb database" in prompt

    def test_sample_rows_included_in_prompt(self):
        llm = _llm_returning({"description": "d", "columns": {}})
        samples = [{"col0": "alice"}, {"col0": "bob"}]

        AIAnnotator(llm_manager=llm).annotate_table("users", _table(1), sample_data=samples)

        prompt = llm.query.call_args.kwargs["user_query"]
        assert "Sample rows" in prompt
        assert "alice" in prompt

    def test_columns_not_asked_about_are_ignored(self):
        payload = {
            "description": "d",
            "columns": {"col0": {"description": "ok"}, "invented": {"description": "no"}},
        }
        llm = _llm_returning(payload)

        result = AIAnnotator(llm_manager=llm).annotate_table("users", _table(1))

        assert "invented" not in result["columns"]

    def test_unparseable_response_raises_value_error(self):
        llm = Mock()
        llm.query.return_value = {"text": "I cannot help with that."}

        with pytest.raises(ValueError, match="users"):
            AIAnnotator(llm_manager=llm).annotate_table("users", _table(1))

    def test_only_columns_restricts_prompt_and_result(self):
        llm = _llm_returning(
            {"description": "d", "columns": {"col1": {"description": "c"}}}
        )

        result = AIAnnotator(llm_manager=llm).annotate_table(
            "users", _table(3), only_columns=["col1"]
        )

        prompt = llm.query.call_args.kwargs["user_query"]
        assert "col1" in prompt
        assert "col0" not in prompt
        assert "col2" not in prompt
        assert list(result["columns"]) == ["col1"]

    def test_empty_only_columns_still_fills_table_fields(self):
        llm = _llm_returning({"description": "table desc", "columns": {}})

        result = AIAnnotator(llm_manager=llm).annotate_table(
            "users", _table(3), only_columns=[]
        )

        assert llm.query.call_count == 1
        assert result["description"] == "table desc"
        assert result["columns"] == {}


class TestAnnotateTableChunking:
    def test_wide_table_chunks_into_multiple_calls(self):
        n = COLUMNS_PER_CALL + 5
        first = {
            "description": "first chunk desc",
            "business_context": "first ctx",
            "columns": {
                f"col{i}": {"description": f"c{i}"} for i in range(COLUMNS_PER_CALL)
            },
        }
        second = {
            "description": "second chunk desc",
            "columns": {
                f"col{i}": {"description": f"c{i}"} for i in range(COLUMNS_PER_CALL, n)
            },
        }
        llm = _llm_returning(first, second)

        result = AIAnnotator(llm_manager=llm).annotate_table("users", _table(n))

        assert llm.query.call_count == 2
        assert len(result["columns"]) == n
        # Table-level fields come from the first chunk.
        assert result["description"] == "first chunk desc"
        assert result["business_context"] == "first ctx"
