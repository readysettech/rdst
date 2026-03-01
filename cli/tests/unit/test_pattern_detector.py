"""Tests for lib.semantic_layer.pattern_detector."""

from lib.semantic_layer.pattern_detector import (
    detect_delimiter_columns_sql_postgres,
    detect_delimiter_columns_sql_mysql,
    DELIMITER_FRACTION_THRESHOLD,
)


class TestDelimiterDetectionSqlPostgres:
    """Test SQL generation for delimiter detection (Postgres)."""

    def test_empty_columns_returns_empty(self):
        assert detect_delimiter_columns_sql_postgres([], "t", 100) == ""

    def test_single_column_small_table(self):
        sql = detect_delimiter_columns_sql_postgres(["tags"], "products", 1000)
        assert '"tags"' in sql
        assert 'LIKE' in sql
        assert 'TABLESAMPLE' not in sql

    def test_single_column_large_table(self):
        sql = detect_delimiter_columns_sql_postgres(["directors"], "title_crew", 7_000_000)
        assert '"directors"' in sql
        assert 'TABLESAMPLE SYSTEM' in sql

    def test_multiple_columns(self):
        sql = detect_delimiter_columns_sql_postgres(
            ["directors", "writers"], "title_crew", 7_000_000,
        )
        assert '"directors"' in sql
        assert '"writers"' in sql
        # Should be a single query with multiple aggregations
        assert sql.count("SELECT") == 1

    def test_tablesample_percentage_scales(self):
        sql = detect_delimiter_columns_sql_postgres(["x"], "big", 10_000_000)
        assert 'TABLESAMPLE' in sql
        sql_small = detect_delimiter_columns_sql_postgres(["x"], "small", 30_000)
        assert 'TABLESAMPLE' not in sql_small


class TestDelimiterDetectionSqlMysql:
    """Test SQL generation for delimiter detection (MySQL)."""

    def test_empty_columns_returns_empty(self):
        assert detect_delimiter_columns_sql_mysql([], "t", 100) == ""

    def test_single_column_small_table(self):
        sql = detect_delimiter_columns_sql_mysql(["tags"], "products", 1000)
        assert '`tags`' in sql
        assert 'LIKE' in sql

    def test_large_table_uses_limit(self):
        sql = detect_delimiter_columns_sql_mysql(["tags"], "products", 100_000)
        assert 'LIMIT 10000' in sql


class TestThreshold:
    """Test the delimiter fraction threshold value."""

    def test_threshold_is_reasonable(self):
        """10% threshold: if 10% of values contain commas, it's a list."""
        assert DELIMITER_FRACTION_THRESHOLD == 0.10
