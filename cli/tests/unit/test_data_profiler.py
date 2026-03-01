"""
Unit tests for DataProfiler.

Tests column-stats gathering, top-value frequencies, and delimiter detection
using mock database cursors — no real DB connection needed.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch

from lib.semantic_layer.data_profiler import (
    DataProfiler,
    TableProfile,
    ColumnProfile,
    _safe_str,
)
from lib.data_structures.semantic_layer import ColumnAnnotation, Relationship


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def pg_config():
    return {
        "engine": "postgresql",
        "host": "localhost",
        "port": 5432,
        "database": "testdb",
        "user": "testuser",
        "password": "secret",
    }


@pytest.fixture
def columns():
    """Minimal column annotations for a users table."""
    return {
        "id": ColumnAnnotation(name="id", data_type="int"),
        "email": ColumnAnnotation(name="email", data_type="text"),
        "status": ColumnAnnotation(name="status", data_type="enum"),
        "created_at": ColumnAnnotation(name="created_at", data_type="timestamptz"),
    }


@pytest.fixture
def relationships():
    return [
        Relationship(
            target_table="orders",
            join_pattern="users.id = orders.user_id",
            relationship_type="one_to_many",
        )
    ]


# ── _safe_str ────────────────────────────────────────────────────────


class TestSafeStr:
    def test_none(self):
        assert _safe_str(None) == "NULL"

    def test_short_string(self):
        assert _safe_str("hello") == "hello"

    def test_truncates_long_string(self):
        long = "x" * 300
        assert len(_safe_str(long)) == 200

    def test_non_string(self):
        assert _safe_str(42) == "42"


# ── DataProfiler init ────────────────────────────────────────────────


class TestDataProfilerInit:
    def test_engine_extracted(self, pg_config):
        profiler = DataProfiler(pg_config)
        assert profiler.engine == "postgresql"

    def test_unsupported_engine_raises(self):
        profiler = DataProfiler({"engine": "oracle"})
        with pytest.raises(ValueError, match="Unsupported engine"):
            profiler.profile_table("t", {}, 0, "0", [])


# ── PostgreSQL profiling (mocked) ────────────────────────────────────


class TestPostgresProfile:
    """Tests with mock psycopg2 connection."""

    @pytest.fixture(autouse=True)
    def _require_psycopg2(self):
        pytest.importorskip("psycopg2")

    @patch("lib.semantic_layer.data_profiler.DataProfiler._connect")
    def test_profile_populates_stats(self, mock_connect, pg_config, columns, relationships):
        """Column stats (null fraction, distinct count) are populated."""
        # Stats query: total=1000, then per-column: cnt, dist, nulls
        stats_row = (
            1000,
            1000, 1000, 0,    # id
            990, 990, 10,     # email
            1000, 3, 0,       # status
            1000, 500, 0,     # created_at
        )

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn

        # cursor() as context manager returns the mock cursor
        cm = MagicMock()
        cm.__enter__ = Mock(return_value=mock_cursor)
        cm.__exit__ = Mock(return_value=False)
        mock_conn.cursor.return_value = cm

        # fetchone: stats query returns the row, then None for subsequent calls
        mock_cursor.fetchone.side_effect = [stats_row]

        # fetchall: top-value queries (email, status are "interesting"), then empty for rest
        mock_cursor.fetchall.side_effect = [
            [("alice@example.com", 5), ("bob@example.com", 3)],  # email
            [("A", 500), ("S", 300), ("D", 200)],                # status
        ]

        # Mock connection attribute for sample rows (uses conn.cursor(cursor_factory=...))
        mock_cursor.connection = mock_conn
        mock_dict_cursor = MagicMock()
        mock_dict_cursor.fetchall.return_value = [
            {"id": 1, "email": "a@b.com", "status": "A", "created_at": "2024-01-01"}
        ]
        # Second cursor() call (for RealDictCursor) returns dict cursor
        mock_conn.cursor.side_effect = [cm, mock_dict_cursor]

        profiler = DataProfiler(pg_config)
        profile = profiler._profile_postgres(
            "users", columns, 1000, "1.0K", relationships, sample_rows=3,
        )

        assert profile.name == "users"
        assert profile.row_estimate == 1000
        assert "id" in profile.columns
        id_profile = profile.columns["id"]
        assert id_profile.null_fraction == 0.0
        assert id_profile.distinct_count == 1000

    @patch("lib.semantic_layer.data_profiler.DataProfiler._connect")
    def test_profile_table_name_and_fks(self, mock_connect, pg_config, columns, relationships):
        """Table-level metadata is captured."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_connect.return_value = mock_conn

        cm = MagicMock()
        cm.__enter__ = Mock(return_value=mock_cursor)
        cm.__exit__ = Mock(return_value=False)
        mock_conn.cursor.return_value = cm

        # Return None/empty for all queries
        mock_cursor.fetchone.return_value = None
        mock_cursor.fetchall.return_value = []
        mock_cursor.connection = mock_conn

        # Sample rows cursor
        mock_dict_cursor = MagicMock()
        mock_dict_cursor.fetchall.return_value = []
        mock_conn.cursor.side_effect = [cm, mock_dict_cursor]

        profiler = DataProfiler(pg_config)
        profile = profiler._profile_postgres(
            "users", columns, 100, "100", relationships, sample_rows=3,
        )

        assert profile.name == "users"
        assert profile.row_estimate_str == "100"
        assert "users.id = orders.user_id" in profile.foreign_keys


# ── ColumnProfile defaults ───────────────────────────────────────────


class TestColumnProfileDefaults:
    def test_defaults(self):
        cp = ColumnProfile(name="test", data_type="int")
        assert cp.null_fraction == 0.0
        assert cp.distinct_count == 0
        assert cp.top_values == {}
        assert cp.sample_values == []
        assert cp.detected_pattern == ""


# ── TableProfile defaults ────────────────────────────────────────────


class TestTableProfileDefaults:
    def test_defaults(self):
        tp = TableProfile(name="t")
        assert tp.row_estimate == 0
        assert tp.columns == {}
        assert tp.sample_rows == []
        assert tp.foreign_keys == []
