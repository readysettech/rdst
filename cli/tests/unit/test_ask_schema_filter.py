"""Regression tests for annotation-aware schema retrieval."""

import json
from unittest.mock import Mock

from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.phases.expand import expand_schema
from features.ask.engine.ask3.phases.filter import (
    _extract_semantic_concepts,
    _filter_schema_text,
    _match_tables_and_columns,
    filter_schema,
)
from features.ask.engine.ask3.types import ColumnInfo, SchemaInfo, TableInfo


def _california_schema() -> SchemaInfo:
    return SchemaInfo(
        target="california_schools",
        db_type="mysql",
        tables={
            "schools": TableInfo(
                name="schools",
                columns={
                    "School": ColumnInfo("School", "text"),
                    "DOCType": ColumnInfo("DOCType", "text"),
                },
                description="California school directory records",
            ),
            "frpm": TableInfo(
                name="frpm",
                columns={
                    "Enrollment (K-12)": ColumnInfo(
                        "Enrollment (K-12)",
                        "integer",
                        description="Total K-12 student enrollment",
                    ),
                    "Enrollment (Ages 5-17)": ColumnInfo(
                        "Enrollment (Ages 5-17)",
                        "integer",
                        description="Enrollment of students aged 5 through 17",
                    ),
                },
                description="Free and reduced-price meal and enrollment facts",
                business_context="Used for annual school funding eligibility reviews",
            ),
        },
    )


def test_human_readable_column_names_retrieve_q28_required_table() -> None:
    question = (
        "Consider the average difference between K-12 enrollment and 15-17 "
        "enrollment of schools that are locally funded"
    )

    matched = _match_tables_and_columns(question, _california_schema())

    assert "schools" in matched
    assert "frpm" in matched


def test_business_context_participates_in_retrieval() -> None:
    matched = _match_tables_and_columns(
        "Which records are used for funding eligibility reviews?",
        _california_schema(),
    )

    assert "frpm" in matched


class _RecordingLLM:
    def __init__(self) -> None:
        self.user_query = ""

    def query(self, **kwargs):
        self.user_query = kwargs["user_query"]
        return {"text": json.dumps({"suggested_tables": [], "reasoning": ""})}


def test_semantic_filter_receives_descriptions_columns_and_every_table() -> None:
    schema = _california_schema()
    llm = _RecordingLLM()

    _extract_semantic_concepts(
        "compare enrollment",
        list(schema.tables),
        llm,
        schema_info=schema,
    )

    assert "- schools" in llm.user_query
    assert "- frpm" in llm.user_query
    assert "Enrollment (K-12)" in llm.user_query
    assert "Free and reduced-price meal" in llm.user_query
    assert "annual school funding eligibility reviews" in llm.user_query


def test_small_schema_bypasses_semantic_filter_call() -> None:
    schema = _california_schema()
    context = Ask3Context(question="compare enrollment", target="california_schools")
    context.schema_info = schema
    context.schema_formatted = "Table: schools\nTable: frpm"
    presenter = Mock()
    llm = Mock()

    result = filter_schema(context, presenter, llm)

    assert result is context
    assert result.filtered_tables == ["schools", "frpm"]
    assert result.schema_filter_strategy == "full-schema-below-budget"
    assert result.schema_formatted == "Table: schools\nTable: frpm"
    llm.query.assert_not_called()
    presenter.schema_filtered.assert_called_once_with(
        original=2,
        filtered=2,
        tables=["schools", "frpm"],
    )


def test_schema_text_filter_handles_non_word_table_names() -> None:
    schema = """Table: sales-orders -- Order facts
  order id (integer)

Table: User Accounts -- User facts
  user id (integer)
"""

    filtered = _filter_schema_text(schema, ["User Accounts"])

    assert "Table: User Accounts" in filtered
    assert "Table: sales-orders" not in filtered


def test_filter_schema_preserves_source_table_order(monkeypatch) -> None:
    table_names = [f"table_{index}" for index in range(9)]
    schema = SchemaInfo(
        target="fixture",
        db_type="postgresql",
        tables={
            name: TableInfo(
                name=name,
                columns={"id": ColumnInfo("id", "integer")},
            )
            for name in table_names
        },
    )
    context = Ask3Context(question="show records", target="fixture")
    context.schema_info = schema
    context.schema_formatted = "\n\n".join(
        f"Table: {name}\n  id (integer)" for name in table_names
    )
    monkeypatch.setattr(
        "features.ask.engine.ask3.phases.filter._extract_semantic_concepts",
        lambda *_args, **_kwargs: {
            "suggested_tables": ["table_7", "table_2"],
            "llm_call": None,
        },
    )

    result = filter_schema(context, Mock(), Mock())

    assert result.filtered_tables == ["table_2", "table_7"]


def test_schema_expansion_refilters_original_rich_schema() -> None:
    context = Ask3Context(question="show orders", target="fixture")
    context.schema_info = SchemaInfo(
        target="fixture",
        db_type="postgresql",
        tables={
            "users": TableInfo(
                name="users", columns={"status": ColumnInfo("status", "text")}
            ),
            "orders": TableInfo(
                name="orders", columns={"total": ColumnInfo("total", "numeric")}
            ),
        },
    )
    context.all_available_tables = ["users", "orders"]
    context.filtered_tables = ["users"]
    context.schema_full_formatted = """Table: users -- User records
  Business context: Account lifecycle
  status (text) [enum: active, disabled]

Table: orders -- Purchase records
  Business context: Completed purchases
  total (numeric) -- Charged amount
"""
    context.schema_formatted = _filter_schema_text(
        context.schema_full_formatted, context.filtered_tables
    )

    result = expand_schema(context, Mock(), ["orders"], ["orders"])

    assert result.filtered_tables == ["users", "orders"]
    assert "Account lifecycle" in result.schema_formatted
    assert "[enum: active, disabled]" in result.schema_formatted
    assert "Completed purchases" in result.schema_formatted
