"""Tests for Ask3 schema filtering with semantic-first approach."""

import sys
sys.path.insert(0, '.')

import pytest
from unittest.mock import Mock, patch
from lib.engines.ask3.phases.filter import (
    _extract_semantic_concepts,
    _match_tables_and_columns,
    _detect_negative_clause_tables,
    _expand_via_fk_relationships,
    filter_schema,
)


class MockSchemaInfo:
    """Mock schema info for testing."""

    def __init__(self, tables: dict):
        self.tables = tables
        self.terminology = {}


class MockTableInfo:
    """Mock table info with columns."""

    def __init__(self, columns: list):
        self.columns = {col: {} for col in columns}
        self.relationships = []


class TestSemanticExtraction:
    """Tests for _extract_semantic_concepts function."""

    def test_returns_suggested_tables(self):
        """Should return suggested tables from LLM response."""
        mock_llm = Mock()
        mock_llm.query.return_value = {
            'text': '{"suggested_tables": ["users", "posts"], "reasoning": "test"}'
        }

        result = _extract_semantic_concepts(
            "users who posted about marijuana",
            ["users", "posts", "comments", "tags"],
            mock_llm
        )

        assert "users" in result["suggested_tables"]
        assert "posts" in result["suggested_tables"]

    def test_validates_tables_exist(self):
        """Should only return tables that exist in the schema."""
        mock_llm = Mock()
        mock_llm.query.return_value = {
            'text': '{"suggested_tables": ["users", "nonexistent"], "reasoning": "test"}'
        }

        result = _extract_semantic_concepts(
            "find users",
            ["users", "posts"],
            mock_llm
        )

        assert "users" in result["suggested_tables"]
        assert "nonexistent" not in result["suggested_tables"]

    def test_handles_llm_failure_gracefully(self):
        """Should return empty list on LLM failure."""
        mock_llm = Mock()
        mock_llm.query.side_effect = Exception("API error")

        result = _extract_semantic_concepts(
            "find users",
            ["users", "posts"],
            mock_llm
        )

        assert result["suggested_tables"] == []
        assert "Error" in result["reasoning"]

    def test_handles_empty_response(self):
        """Should handle empty LLM response."""
        mock_llm = Mock()
        mock_llm.query.return_value = None

        result = _extract_semantic_concepts(
            "find users",
            ["users", "posts"],
            mock_llm
        )

        assert result["suggested_tables"] == []


class TestSemanticExtractionJsonRobustness:
    """Bug rdst-9cq.1: Semantic extraction must handle non-pure-JSON LLM responses.

    When Haiku returns JSON wrapped in markdown code fences or with preamble text,
    _extract_semantic_concepts fails with json.JSONDecodeError and returns empty
    suggested_tables. This causes the ask pipeline to miss tables.
    """

    def test_handles_markdown_wrapped_json(self):
        """LLM response wrapped in ```json ... ``` should still parse."""
        mock_llm = Mock()
        mock_llm.query.return_value = {
            'text': '```json\n{"suggested_tables": ["title_ratings", "title_basics"], "reasoning": "ratings"}\n```'
        }

        result = _extract_semantic_concepts(
            "What are the top 10 highest rated titles?",
            ["title_basics", "title_ratings", "title_crew"],
            mock_llm
        )

        assert "title_ratings" in result["suggested_tables"]
        assert "title_basics" in result["suggested_tables"]

    def test_handles_preamble_text_before_json(self):
        """LLM response with text before JSON should still parse."""
        mock_llm = Mock()
        mock_llm.query.return_value = {
            'text': 'Here are the tables:\n{"suggested_tables": ["title_ratings"], "reasoning": "needs ratings"}'
        }

        result = _extract_semantic_concepts(
            "What is the average rating?",
            ["title_basics", "title_ratings"],
            mock_llm
        )

        assert "title_ratings" in result["suggested_tables"]

    def test_handles_text_after_json(self):
        """LLM response with text after JSON should still parse."""
        mock_llm = Mock()
        mock_llm.query.return_value = {
            'text': '{"suggested_tables": ["title_ratings"], "reasoning": "ratings table"}\n\nI hope this helps!'
        }

        result = _extract_semantic_concepts(
            "Show me ratings",
            ["title_basics", "title_ratings"],
            mock_llm
        )

        assert "title_ratings" in result["suggested_tables"]


class TestHeuristicMatchingUnderscoreTables:
    """Bug rdst-9cq.1: Heuristic matching must handle underscore-separated table names.

    When semantic extraction fails, the heuristic fallback in _match_tables_and_columns
    misses tables like 'title_ratings' because 'rating' doesn't match the full name.
    """

    def test_matches_underscore_table_via_component(self):
        """'ratings' in question should match 'title_ratings' table."""
        schema_info = MockSchemaInfo({
            "title_basics": MockTableInfo(["tconst", "primarytitle"]),
            "title_ratings": MockTableInfo(["tconst", "averagerating"]),
            "name_basics": MockTableInfo(["nconst", "primaryname"]),
        })

        result = _match_tables_and_columns(
            "What are the top 10 highest rated titles?",
            schema_info
        )

        assert "title_ratings" in result

    def test_matches_underscore_table_via_plural_component(self):
        """'rating' (singular) should match 'title_ratings' table via component."""
        schema_info = MockSchemaInfo({
            "title_basics": MockTableInfo(["tconst", "primarytitle"]),
            "title_ratings": MockTableInfo(["tconst", "averagerating"]),
        })

        result = _match_tables_and_columns(
            "What is the average rating per genre?",
            schema_info
        )

        assert "title_ratings" in result


class TestHeuristicMatching:
    """Tests for heuristic table/column matching."""

    def test_matches_table_name(self):
        """Should match when table name appears in question."""
        schema_info = MockSchemaInfo({
            "users": MockTableInfo(["id", "name"]),
            "posts": MockTableInfo(["id", "body"]),
        })

        result = _match_tables_and_columns("find all users", schema_info)

        assert "users" in result

    def test_matches_column_name(self):
        """Should match table when its column name appears in question."""
        schema_info = MockSchemaInfo({
            "users": MockTableInfo(["id", "email"]),
            "posts": MockTableInfo(["id", "body"]),
        })

        result = _match_tables_and_columns("find by email", schema_info)

        assert "users" in result

    def test_handles_plural_forms(self):
        """Should match singular/plural variants."""
        schema_info = MockSchemaInfo({
            "users": MockTableInfo(["id"]),
            "post": MockTableInfo(["id"]),
        })

        # "user" should match "users" table
        result = _match_tables_and_columns("find user by id", schema_info)
        assert "users" in result

        # "posts" should match "post" table
        result = _match_tables_and_columns("show all posts", schema_info)
        assert "post" in result


class TestNegativeClauseDetection:
    """Tests for negative clause detection."""

    def test_detects_never_asked(self):
        """Should detect 'never asked' pattern."""
        schema_info = MockSchemaInfo({
            "users": MockTableInfo(["id"]),
            "posts": MockTableInfo(["id"]),
        })

        result = _detect_negative_clause_tables(
            "users who never asked a question",
            schema_info
        )

        assert "posts" in result

    def test_detects_without_comments(self):
        """Should detect 'without X' pattern."""
        schema_info = MockSchemaInfo({
            "posts": MockTableInfo(["id"]),
            "comments": MockTableInfo(["id"]),
        })

        result = _detect_negative_clause_tables(
            "posts without comments",
            schema_info
        )

        assert "comments" in result


class TestFKExpansion:
    """Tests for FK relationship expansion."""

    def test_expands_userid_to_users(self):
        """Should expand userid FK to users table."""
        schema_info = MockSchemaInfo({
            "users": MockTableInfo(["id", "name"]),
            "posts": MockTableInfo(["id", "userid", "body"]),
        })

        result = _expand_via_fk_relationships({"posts"}, schema_info)

        assert "posts" in result
        assert "users" in result

    def test_reverse_expansion(self):
        """Should expand from parent to child tables."""
        schema_info = MockSchemaInfo({
            "users": MockTableInfo(["id", "name"]),
            "posts": MockTableInfo(["id", "userid"]),
            "comments": MockTableInfo(["id", "userid"]),
        })

        result = _expand_via_fk_relationships({"users"}, schema_info)

        assert "users" in result
        assert "posts" in result
        assert "comments" in result


class TestFilterSchemaIntegration:
    """Integration tests for filter_schema function."""

    def test_combines_semantic_and_heuristic(self):
        """Should combine semantic LLM results with heuristic matches."""
        mock_llm = Mock()
        mock_llm.query.return_value = {
            'text': '{"suggested_tables": ["posts"], "reasoning": "posted implies posts"}'
        }

        schema_info = MockSchemaInfo({
            "users": MockTableInfo(["id", "name"]),
            "posts": MockTableInfo(["id", "userid", "body"]),
            "comments": MockTableInfo(["id", "postid"]),
        })

        mock_ctx = Mock()
        mock_ctx.question = "users who posted about marijuana"
        mock_ctx.schema_info = schema_info
        mock_ctx.schema_formatted = "Table: users\n  id\n  name\nTable: posts\n  id\n  userid\n  body\nTable: comments\n  id\n  postid\n"

        mock_presenter = Mock()

        result = filter_schema(mock_ctx, mock_presenter, mock_llm)

        # Should include both semantic (posts) and heuristic (users) matches
        assert "users" in result.filtered_tables
        assert "posts" in result.filtered_tables

    def test_fallback_to_full_schema(self):
        """Should use full schema when all methods fail."""
        mock_llm = Mock()
        mock_llm.query.return_value = {
            'text': '{"suggested_tables": [], "reasoning": "not sure"}'
        }

        schema_info = MockSchemaInfo({
            "users": MockTableInfo(["id"]),
            "posts": MockTableInfo(["id"]),
        })

        mock_ctx = Mock()
        mock_ctx.question = "xyz123"  # Gibberish that won't match anything
        mock_ctx.schema_info = schema_info
        mock_ctx.schema_formatted = "Table: users\n  id\nTable: posts\n  id\n"

        mock_presenter = Mock()

        result = filter_schema(mock_ctx, mock_presenter, mock_llm)

        # Should fall back to all tables
        assert len(result.filtered_tables) == 2


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
