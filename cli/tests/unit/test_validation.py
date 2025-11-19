"""
Unit tests for validation.py

Tests the LLM recommendation validation functionality that prevents hallucinated suggestions.
"""

import pytest
import importlib.util
import sys
from pathlib import Path

# Import module directly to avoid package __init__.py issues
def _import_module_directly(module_name, file_path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

_lib_path = Path(__file__).parent.parent.parent / "lib"
validation = _import_module_directly("validation", _lib_path / "functions" / "validation.py")

validate_recommendations = validation.validate_recommendations
_extract_existing_indexes = validation._extract_existing_indexes
_extract_index_name_from_create = validation._extract_index_name_from_create


class TestValidateRecommendations:
    """Tests for the main validate_recommendations function."""

    def test_valid_new_index_recommendation(self, sample_schema_info, sample_llm_analysis):
        """Test validation passes for new index recommendations."""
        result = validate_recommendations(sample_llm_analysis, sample_schema_info)

        assert result["success"] is True
        assert result["is_valid"] is True
        assert len(result["warnings"]) == 0

    def test_duplicate_index_detected(self, sample_schema_info, sample_llm_analysis_with_duplicate):
        """Test that duplicate index recommendations are detected."""
        result = validate_recommendations(sample_llm_analysis_with_duplicate, sample_schema_info)

        assert result["success"] is False
        assert result["is_valid"] is False
        assert len(result["warnings"]) > 0
        assert any("already exists" in warning for warning in result["warnings"])

    def test_empty_recommendations(self, sample_schema_info):
        """Test validation with no recommendations."""
        llm_analysis = {"index_recommendations": []}
        result = validate_recommendations(llm_analysis, sample_schema_info)

        assert result["success"] is True
        assert result["is_valid"] is True
        assert result["suggested_indexes_count"] == 0

    def test_empty_schema_info(self, sample_llm_analysis):
        """Test validation with empty schema info."""
        result = validate_recommendations(sample_llm_analysis, "")

        assert result["success"] is True
        assert result["existing_indexes_count"] == 0

    def test_missing_index_recommendations_key(self, sample_schema_info):
        """Test handling of missing index_recommendations key."""
        llm_analysis = {"optimization_suggestions": ["Some suggestion"]}
        result = validate_recommendations(llm_analysis, sample_schema_info)

        assert result["success"] is True
        assert result["suggested_indexes_count"] == 0

    def test_counts_are_tracked(self, sample_schema_info, sample_llm_analysis):
        """Test that counts are properly tracked in result."""
        result = validate_recommendations(sample_llm_analysis, sample_schema_info)

        assert "existing_indexes_count" in result
        assert "suggested_indexes_count" in result
        assert result["existing_indexes_count"] >= 0
        assert result["suggested_indexes_count"] == 2

    def test_case_insensitive_duplicate_detection(self, sample_schema_info):
        """Test that duplicate detection is case insensitive."""
        # Schema has idx_users_email, suggest IDX_USERS_EMAIL (different case)
        llm_analysis = {
            "index_recommendations": [
                {
                    "sql": "CREATE INDEX IDX_USERS_EMAIL ON users (email)",
                    "reason": "Duplicate with different case"
                }
            ]
        }
        result = validate_recommendations(llm_analysis, sample_schema_info)

        assert result["is_valid"] is False
        assert len(result["warnings"]) > 0


class TestExtractExistingIndexes:
    """Tests for _extract_existing_indexes function."""

    def test_extract_postgresql_indexes(self):
        """Test extraction of PostgreSQL CREATE INDEX statements."""
        schema_info = """
        Indexes:
        - CREATE INDEX idx_users_email ON users (email)
        - CREATE UNIQUE INDEX idx_users_unique ON users (unique_id)
        - CREATE INDEX IF NOT EXISTS idx_orders_date ON orders (created_at)
        """
        indexes = _extract_existing_indexes(schema_info)

        assert "idx_users_email" in indexes
        assert "idx_users_unique" in indexes
        assert "idx_orders_date" in indexes

    def test_extract_mysql_indexes(self):
        """Test extraction of MySQL index format."""
        schema_info = """
        Indexes:
        - idx_users_email USING BTREE (email)
        - idx_orders_status USING HASH (status)
        - idx_products_name USING FULLTEXT (name)
        """
        indexes = _extract_existing_indexes(schema_info)

        assert "idx_users_email" in indexes
        assert "idx_orders_status" in indexes
        assert "idx_products_name" in indexes

    def test_extract_simple_format(self):
        """Test extraction of simple list format."""
        schema_info = """
        Table: users
        Columns: id, name, email
        Indexes:
        - primary_key
        """
        indexes = _extract_existing_indexes(schema_info)

        assert "primary_key" in indexes

    def test_empty_schema_returns_empty_list(self):
        """Test that empty schema returns empty list."""
        indexes = _extract_existing_indexes("")

        assert indexes == []

    def test_no_indexes_in_schema(self):
        """Test schema with no indexes."""
        schema_info = """
        Table: users
        Columns:
        - id INT PRIMARY KEY
        - name VARCHAR(255)
        """
        indexes = _extract_existing_indexes(schema_info)

        assert indexes == []

    def test_deduplication(self):
        """Test that duplicate index names are deduplicated."""
        schema_info = """
        - CREATE INDEX idx_test ON table1 (col1)
        - idx_test USING BTREE (col1)
        """
        indexes = _extract_existing_indexes(schema_info)

        # Should only have one instance
        assert indexes.count("idx_test") == 1

    def test_mixed_formats(self):
        """Test extraction with mixed index formats."""
        schema_info = """
        PostgreSQL format:
        - CREATE INDEX idx_pg ON table1 (col1)

        MySQL format:
        - idx_mysql USING BTREE (col2)
        """
        indexes = _extract_existing_indexes(schema_info)

        assert "idx_pg" in indexes
        assert "idx_mysql" in indexes


class TestExtractIndexNameFromCreate:
    """Tests for _extract_index_name_from_create function."""

    def test_basic_create_index(self):
        """Test extraction from basic CREATE INDEX."""
        sql = "CREATE INDEX idx_users_email ON users (email)"
        name = _extract_index_name_from_create(sql)

        assert name == "idx_users_email"

    def test_create_unique_index(self):
        """Test extraction from CREATE UNIQUE INDEX."""
        sql = "CREATE UNIQUE INDEX idx_users_unique ON users (unique_id)"
        name = _extract_index_name_from_create(sql)

        assert name == "idx_users_unique"

    def test_create_index_if_not_exists(self):
        """Test extraction with IF NOT EXISTS clause."""
        sql = "CREATE INDEX IF NOT EXISTS idx_orders_date ON orders (created_at)"
        name = _extract_index_name_from_create(sql)

        assert name == "idx_orders_date"

    def test_create_unique_index_if_not_exists(self):
        """Test extraction with both UNIQUE and IF NOT EXISTS."""
        sql = "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_sku ON products (sku)"
        name = _extract_index_name_from_create(sql)

        assert name == "idx_products_sku"

    def test_case_insensitive_extraction(self):
        """Test that extraction works regardless of keyword case."""
        sql = "create index idx_test on table (col)"
        name = _extract_index_name_from_create(sql)

        assert name == "idx_test"

    def test_non_create_index_returns_none(self):
        """Test that non-CREATE INDEX statements return None."""
        sql = "SELECT * FROM users"
        name = _extract_index_name_from_create(sql)

        assert name is None

    def test_empty_string_returns_none(self):
        """Test that empty string returns None."""
        name = _extract_index_name_from_create("")

        assert name is None

    def test_partial_create_returns_none(self):
        """Test that incomplete statements return None."""
        sql = "CREATE INDEX"
        name = _extract_index_name_from_create(sql)

        assert name is None

    def test_index_name_with_underscores(self):
        """Test extraction of names with multiple underscores."""
        sql = "CREATE INDEX idx_user_email_domain_active ON users (email, domain, active)"
        name = _extract_index_name_from_create(sql)

        assert name == "idx_user_email_domain_active"

    def test_index_name_with_numbers(self):
        """Test extraction of names containing numbers."""
        sql = "CREATE INDEX idx_v2_users_2024 ON users_v2 (created_at)"
        name = _extract_index_name_from_create(sql)

        assert name == "idx_v2_users_2024"


class TestEdgeCases:
    """Tests for edge cases and unusual inputs."""

    def test_recommendation_without_sql_key(self, sample_schema_info):
        """Test handling of recommendation without 'sql' key."""
        llm_analysis = {
            "index_recommendations": [
                {"reason": "Missing SQL"}
            ]
        }
        result = validate_recommendations(llm_analysis, sample_schema_info)

        # Should not raise exception
        assert "success" in result

    def test_recommendation_with_empty_sql(self, sample_schema_info):
        """Test handling of recommendation with empty SQL."""
        llm_analysis = {
            "index_recommendations": [
                {"sql": "", "reason": "Empty SQL"}
            ]
        }
        result = validate_recommendations(llm_analysis, sample_schema_info)

        # Should handle gracefully
        assert "success" in result

    def test_complex_schema_info(self):
        """Test extraction from complex schema with multiple formats."""
        schema_info = """
        Database: production

        Table: users
        -----------------
        Columns:
          id INT PRIMARY KEY AUTO_INCREMENT
          email VARCHAR(255) NOT NULL
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP

        Indexes:
          - CREATE INDEX idx_users_email ON users USING btree (email)
          - CREATE INDEX idx_users_created ON users (created_at DESC)

        Table: orders
        -----------------
        Columns:
          id INT PRIMARY KEY
          user_id INT REFERENCES users(id)
          total DECIMAL(10,2)

        Indexes:
          - idx_orders_user USING BTREE (user_id)
          - CREATE INDEX idx_orders_total ON orders (total)
        """
        indexes = _extract_existing_indexes(schema_info)

        assert "idx_users_email" in indexes
        assert "idx_users_created" in indexes
        assert "idx_orders_user" in indexes
        assert "idx_orders_total" in indexes

    def test_unicode_in_schema(self):
        """Test handling of unicode characters in schema info."""
        schema_info = """
        Table: utilisateurs
        Indexes:
        - CREATE INDEX idx_utilisateurs_nom ON utilisateurs (nom)
        """
        indexes = _extract_existing_indexes(schema_info)

        assert "idx_utilisateurs_nom" in indexes

    def test_warning_message_format(self, sample_schema_info):
        """Test that warning messages are properly formatted."""
        llm_analysis = {
            "index_recommendations": [
                {"sql": "CREATE INDEX idx_users_email ON users (email)"}
            ]
        }
        result = validate_recommendations(llm_analysis, sample_schema_info)

        assert len(result["warnings"]) > 0
        warning = result["warnings"][0]
        assert "idx_users_email" in warning
        assert "already exists" in warning
        assert "hallucination" in warning.lower()


class TestIntegration:
    """Integration tests combining multiple functions."""

    def test_full_validation_workflow(self):
        """Test complete validation workflow."""
        schema_info = """
        Table: products
        Indexes:
        - CREATE INDEX idx_products_category ON products (category_id)
        - CREATE INDEX idx_products_price ON products (price)
        """

        llm_analysis = {
            "index_recommendations": [
                {
                    "sql": "CREATE INDEX idx_products_name ON products (name)",
                    "reason": "Speed up name searches"
                },
                {
                    "sql": "CREATE INDEX idx_products_category ON products (category_id)",
                    "reason": "This already exists!"
                }
            ]
        }

        result = validate_recommendations(llm_analysis, schema_info)

        # Should fail because of duplicate
        assert result["is_valid"] is False
        assert result["existing_indexes_count"] >= 2  # At least the two indexes we defined
        assert result["suggested_indexes_count"] == 2
        assert len(result["warnings"]) == 1
        assert "idx_products_category" in result["warnings"][0]
