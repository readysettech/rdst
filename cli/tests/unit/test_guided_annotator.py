"""
Unit tests for GuidedAnnotator.

Tests the two-phase flow (analyze → review) with a mock LLM — no real
API calls needed. Validates prompt construction, JSON parsing, and
annotation application.
"""

import json
import pytest
from unittest.mock import Mock, MagicMock, patch, call

from lib.semantic_layer.guided_annotator import (
    GuidedAnnotator,
    TableAnalysis,
    ColumnDraft,
    Question,
    _parse_json,
    _parse_row_estimate,
    SYSTEM_MESSAGE,
)
from lib.data_structures.semantic_layer import (
    SemanticLayer,
    TableAnnotation,
    ColumnAnnotation,
    Relationship,
)
from lib.semantic_layer.data_profiler import TableProfile, ColumnProfile


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm():
    """Mock LLMManager that returns canned JSON responses."""
    llm = MagicMock()
    return llm


@pytest.fixture
def mock_console():
    return MagicMock()


@pytest.fixture
def layer():
    """Semantic layer with a single 'users' table."""
    sl = SemanticLayer(target="testdb")
    sl.tables["users"] = TableAnnotation(
        name="users",
        row_estimate="50K",
        columns={
            "id": ColumnAnnotation(name="id", data_type="int"),
            "email": ColumnAnnotation(name="email", data_type="text"),
            "status": ColumnAnnotation(
                name="status",
                data_type="enum",
                enum_values={"A": "TODO: describe 'A'", "S": "TODO: describe 'S'"},
            ),
        },
        relationships=[
            Relationship(
                target_table="orders",
                join_pattern="users.id = orders.user_id",
            )
        ],
    )
    return sl


@pytest.fixture
def target_config():
    return {
        "engine": "postgresql",
        "host": "localhost",
        "port": 5432,
        "database": "testdb",
        "user": "testuser",
        "password": "secret",
    }


# ── JSON parsing ─────────────────────────────────────────────────────


class TestParseJson:
    def test_plain_json(self):
        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_markdown_json_block(self):
        text = "Here's the result:\n```json\n{\"b\": 2}\n```\n"
        assert _parse_json(text) == {"b": 2}

    def test_markdown_generic_block(self):
        text = "```\n{\"c\": 3}\n```"
        assert _parse_json(text) == {"c": 3}

    def test_json_embedded_in_text(self):
        text = "Sure, here:\n{\"d\": 4}\nDone."
        assert _parse_json(text) == {"d": 4}

    def test_invalid_returns_none(self):
        assert _parse_json("not json at all") is None

    def test_empty_string(self):
        assert _parse_json("") is None


# ── Row estimate parsing ─────────────────────────────────────────────


class TestParseRowEstimate:
    def test_millions(self):
        assert _parse_row_estimate("1.2M") == 1_200_000

    def test_thousands(self):
        assert _parse_row_estimate("50K") == 50_000

    def test_plain_number(self):
        assert _parse_row_estimate("1234") == 1234

    def test_empty(self):
        assert _parse_row_estimate("") == 0

    def test_invalid(self):
        assert _parse_row_estimate("abc") == 0


# ── _parse_analysis ──────────────────────────────────────────────────


class TestParseAnalysis:
    def test_full_response(self, mock_llm, mock_console):
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)

        llm_json = json.dumps({
            "table_description": "Registered user accounts",
            "business_context": "Created when user signs up",
            "columns": {
                "id": {
                    "description": "Auto-incrementing PK",
                    "confidence": "high",
                },
                "email": {
                    "description": "User email address",
                    "confidence": "high",
                    "is_pii": True,
                },
                "status": {
                    "description": "Account status code",
                    "confidence": "medium",
                    "enum_mappings": {"A": "Active", "S": "Suspended"},
                },
            },
            "questions": [
                {
                    "target": "status",
                    "question": "Are A=Active, S=Suspended correct?",
                    "context": "Values: A (45K), S (5K)",
                    "options": ["Yes", "No"],
                    "default": "Yes",
                }
            ],
            "terminology": [
                {
                    "term": "active user",
                    "definition": "User with status A",
                    "sql_pattern": "status = 'A'",
                }
            ],
        })

        analysis = annotator._parse_analysis("users", llm_json)

        assert analysis.table_name == "users"
        assert analysis.table_description == "Registered user accounts"
        assert "id" in analysis.column_drafts
        assert analysis.column_drafts["email"].is_pii is True
        assert analysis.column_drafts["status"].confidence == "medium"
        assert len(analysis.questions) == 1
        assert analysis.questions[0].target == "status"
        assert len(analysis.terminology) == 1

    def test_malformed_response_returns_empty(self, mock_llm, mock_console):
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        analysis = annotator._parse_analysis("users", "not json")
        assert analysis.table_name == "users"
        assert analysis.column_drafts == {}
        assert analysis.questions == []


# ── _build_prompt ────────────────────────────────────────────────────


class TestBuildPrompt:
    def test_includes_column_stats(self, mock_llm, mock_console, layer):
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)

        profile = TableProfile(
            name="users",
            row_estimate=50000,
            row_estimate_str="50K",
            columns={
                "status": ColumnProfile(
                    name="status",
                    data_type="enum",
                    null_fraction=0.0,
                    distinct_count=3,
                    top_values={"A": 45000, "S": 3000, "D": 2000},
                    sample_values=["A", "S", "D"],
                ),
            },
            foreign_keys=["users.id = orders.user_id"],
        )

        prompt = annotator._build_prompt("users", profile, layer.tables["users"])

        assert "users" in prompt
        assert "50K" in prompt
        assert "status" in prompt
        assert "null: 0%" in prompt
        assert "distinct: 3" in prompt
        assert "A (45000)" in prompt
        assert "users.id = orders.user_id" in prompt


# ── _apply_draft ─────────────────────────────────────────────────────


class TestApplyDraft:
    def test_applies_description(self, mock_llm, mock_console, layer):
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        table = layer.tables["users"]

        draft = ColumnDraft(name="email", description="User email", is_pii=True)
        annotator._apply_draft(table, "email", draft)

        assert table.columns["email"].description == "User email"
        assert table.columns["email"].is_pii is True

    def test_replaces_todo_enum_values(self, mock_llm, mock_console, layer):
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        table = layer.tables["users"]

        draft = ColumnDraft(
            name="status",
            description="Account status",
            enum_mappings={"A": "Active", "S": "Suspended"},
        )
        annotator._apply_draft(table, "status", draft)

        assert table.columns["status"].enum_values["A"] == "Active"
        assert table.columns["status"].enum_values["S"] == "Suspended"

    def test_skips_nonexistent_column(self, mock_llm, mock_console, layer):
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        table = layer.tables["users"]
        draft = ColumnDraft(name="nonexistent", description="Nope")
        # Should not raise
        annotator._apply_draft(table, "nonexistent", draft)

    def test_preserves_existing_description(self, mock_llm, mock_console, layer):
        """Don't overwrite a user-authored description."""
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        table = layer.tables["users"]
        table.columns["email"].description = "Contact email for the user"

        draft = ColumnDraft(name="email", description="User email")
        annotator._apply_draft(table, "email", draft)

        assert table.columns["email"].description == "Contact email for the user"


# ── _apply_answer ────────────────────────────────────────────────────


class TestApplyAnswer:
    def test_nullable_meaning_stored(self, mock_llm, mock_console, layer):
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        table = layer.tables["users"]

        q = Question(
            target="email",
            question="What does NULL email mean?",
        )
        analysis = TableAnalysis(table_name="users")
        annotator._apply_answer(table, q, "User registered via SSO, no email needed", analysis)

        assert table.columns["email"].nullable_meaning == "User registered via SSO, no email needed"

    def test_enum_mapping_from_equals_format(self, mock_llm, mock_console, layer):
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        table = layer.tables["users"]

        q = Question(target="status", question="What do these codes mean?")
        analysis = TableAnalysis(table_name="users")
        annotator._apply_answer(table, q, "A=Active, S=Suspended", analysis)

        assert table.columns["status"].enum_values["A"] == "Active"
        assert table.columns["status"].enum_values["S"] == "Suspended"


# ── _analyze_table ───────────────────────────────────────────────────


class TestAnalyzeTable:
    def test_calls_llm_with_system_message(self, mock_llm, mock_console, layer):
        mock_llm.query.return_value = {
            "text": json.dumps({
                "table_description": "Users",
                "business_context": "",
                "columns": {},
                "questions": [],
                "terminology": [],
            }),
            "usage": {},
        }

        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        profile = TableProfile(name="users", row_estimate=100, row_estimate_str="100")

        annotator._analyze_table("users", profile, layer.tables["users"])

        mock_llm.query.assert_called_once()
        call_kwargs = mock_llm.query.call_args[1]
        assert call_kwargs["system_message"] == SYSTEM_MESSAGE
        assert call_kwargs["temperature"] == 0.2
        assert call_kwargs["max_tokens"] == 4096


# ── End-to-end run (mocked LLM + mocked DB) ─────────────────────────


class TestGuidedAnnotatorRun:
    @patch("lib.semantic_layer.guided_annotator.DataProfiler")
    @patch("lib.semantic_layer.guided_annotator.GuidedAnnotator._review_table")
    @patch("lib.semantic_layer.manager.SemanticLayerManager")
    def test_run_profiles_and_analyzes(
        self, MockManager, mock_review, MockProfiler, mock_llm, mock_console, layer, target_config,
    ):
        """run() profiles tables, calls LLM, then reviews."""
        # Mock profiler
        mock_profiler_instance = MagicMock()
        MockProfiler.return_value = mock_profiler_instance
        mock_profiler_instance.profile_table.return_value = TableProfile(
            name="users", row_estimate=50000, row_estimate_str="50K"
        )

        # Mock LLM response
        mock_llm.query.return_value = {
            "text": json.dumps({
                "table_description": "User accounts",
                "business_context": "Created on sign-up",
                "columns": {
                    "id": {"description": "PK", "confidence": "high"},
                },
                "questions": [],
                "terminology": [],
            }),
            "usage": {},
        }

        # Mock review to accept
        mock_review.return_value = True

        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        result = annotator.run(layer, target_config)

        assert result is layer
        mock_profiler_instance.profile_table.assert_called_once()
        mock_llm.query.assert_called_once()
        mock_review.assert_called_once()

    @patch("lib.semantic_layer.guided_annotator.DataProfiler")
    @patch("lib.semantic_layer.manager.SemanticLayerManager")
    def test_run_single_table(
        self, MockManager, MockProfiler, mock_llm, mock_console, layer, target_config,
    ):
        """run() with table_name only processes that table."""
        mock_profiler_instance = MagicMock()
        MockProfiler.return_value = mock_profiler_instance
        mock_profiler_instance.profile_table.return_value = TableProfile(
            name="users", row_estimate=50000, row_estimate_str="50K"
        )

        mock_llm.query.return_value = {
            "text": json.dumps({
                "table_description": "Users",
                "business_context": "",
                "columns": {},
                "questions": [],
                "terminology": [],
            }),
            "usage": {},
        }

        with patch.object(GuidedAnnotator, "_review_table", return_value=True):
            annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
            annotator.run(layer, target_config, table_name="users")

        # Should only profile "users"
        mock_profiler_instance.profile_table.assert_called_once()
        args = mock_profiler_instance.profile_table.call_args
        assert args[0][0] == "users"

    @patch("lib.semantic_layer.guided_annotator.DataProfiler")
    @patch("lib.semantic_layer.guided_annotator.GuidedAnnotator._review_table")
    @patch("lib.semantic_layer.manager.SemanticLayerManager")
    def test_run_auto_accept_passed_to_review(
        self, MockManager, mock_review, MockProfiler, mock_llm, mock_console, layer, target_config,
    ):
        """run() passes auto_accept to _review_table."""
        mock_profiler_instance = MagicMock()
        MockProfiler.return_value = mock_profiler_instance
        mock_profiler_instance.profile_table.return_value = TableProfile(
            name="users", row_estimate=50000, row_estimate_str="50K"
        )

        mock_llm.query.return_value = {
            "text": json.dumps({
                "table_description": "Users",
                "business_context": "",
                "columns": {},
                "questions": [],
                "terminology": [],
            }),
            "usage": {},
        }
        mock_review.return_value = True

        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        annotator.run(layer, target_config, auto_accept=True)

        # Verify auto_accept=True was passed to _review_table
        call_kwargs = mock_review.call_args[1]
        assert call_kwargs["auto_accept"] is True

    def test_run_empty_layer_warns(self, mock_llm, mock_console, target_config):
        """run() on a layer with no tables prints warning."""
        empty_layer = SemanticLayer(target="empty")
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        result = annotator.run(empty_layer, target_config)
        assert result is empty_layer
        mock_console.print.assert_called()  # Warning printed


# ── Auto-accept mode ────────────────────────────────────────────────


class TestAutoAccept:
    def test_review_table_auto_accepts_description(self, mock_llm, mock_console, layer):
        """auto_accept=True applies description without prompting."""
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        analysis = TableAnalysis(
            table_name="users",
            table_description="User accounts",
            business_context="Core entity",
            column_drafts={
                "id": ColumnDraft(name="id", description="Primary key", confidence="high"),
            },
        )
        profile = TableProfile(name="users", row_estimate=50000, row_estimate_str="50K")

        result = annotator._review_table(layer, "users", analysis, profile, auto_accept=True)

        assert result is True
        assert layer.tables["users"].description == "User accounts"
        assert layer.tables["users"].business_context == "Core entity"

    def test_review_table_auto_accepts_question_defaults(self, mock_llm, mock_console, layer):
        """auto_accept=True uses question defaults without prompting."""
        annotator = GuidedAnnotator(llm_manager=mock_llm, console=mock_console)
        analysis = TableAnalysis(
            table_name="users",
            table_description="Users",
            questions=[
                Question(
                    target="status",
                    question="What do status codes mean?",
                    options=["A=Active, S=Suspended"],
                    default="A=Active, S=Suspended",
                ),
            ],
        )
        profile = TableProfile(name="users", row_estimate=50000, row_estimate_str="50K")

        annotator._review_table(layer, "users", analysis, profile, auto_accept=True)

        assert layer.tables["users"].columns["status"].enum_values["A"] == "Active"
        assert layer.tables["users"].columns["status"].enum_values["S"] == "Suspended"
