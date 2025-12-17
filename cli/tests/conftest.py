"""
Common pytest fixtures and configuration for RDST tests.
"""

import pytest


# Register custom markers
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "ask_experimental: tests for Ask/Schema features (not run by default, require database)"
    )


# Exclude ask_experimental tests from default runs
def pytest_collection_modifyitems(config, items):
    """Skip ask_experimental tests unless explicitly targeted."""
    # Check if user is explicitly running ask_experimental tests
    # by looking at the test paths being collected
    running_ask_explicit = any(
        "ask_experimental" in str(item.fspath)
        for item in items
    ) and all(
        "ask_experimental" in str(item.fspath) or "conftest" in str(item.fspath)
        for item in items
    )

    if running_ask_explicit:
        # User explicitly ran: pytest tests/ask_experimental/
        return

    skip_ask = pytest.mark.skip(reason="ask_experimental tests excluded by default. Run with: pytest tests/ask_experimental/ -v")
    for item in items:
        if "ask_experimental" in str(item.fspath):
            item.add_marker(skip_ask)
import tempfile
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add the rdst directory to the path for proper imports
rdst_root = Path(__file__).parent.parent
if str(rdst_root) not in sys.path:
    sys.path.insert(0, str(rdst_root))
if str(rdst_root / "lib") not in sys.path:
    sys.path.insert(0, str(rdst_root / "lib"))


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_logger():
    """Create a mock logger for testing."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.debug = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


@pytest.fixture
def sample_select_query():
    """Return a sample SELECT query for testing."""
    return "SELECT * FROM users WHERE id = 123"


@pytest.fixture
def sample_complex_query():
    """Return a more complex query for testing."""
    return """
    SELECT u.name, o.order_id, o.total
    FROM users u
    JOIN orders o ON u.id = o.user_id
    WHERE o.created_at > '2024-01-01'
    AND o.total > 100.50
    ORDER BY o.total DESC
    LIMIT 10
    """


@pytest.fixture
def sample_dangerous_query():
    """Return a dangerous query that should be rejected."""
    return "DELETE FROM users WHERE id = 123"


@pytest.fixture
def sample_schema_info():
    """Return sample schema information for validation testing."""
    return """
Table: users
Columns:
  - id INT PRIMARY KEY
  - name VARCHAR(255)
  - email VARCHAR(255)
  - created_at TIMESTAMP

Indexes:
  - CREATE INDEX idx_users_email ON users (email)
  - CREATE INDEX idx_users_created ON users USING btree (created_at)
"""


@pytest.fixture
def sample_llm_analysis():
    """Return sample LLM analysis results for validation testing."""
    return {
        'index_recommendations': [
            {
                'sql': 'CREATE INDEX idx_orders_user ON orders (user_id)',
                'reason': 'Speed up user order lookups'
            },
            {
                'sql': 'CREATE INDEX idx_orders_total ON orders (total)',
                'reason': 'Speed up total-based filtering'
            }
        ],
        'optimization_suggestions': [
            'Consider adding covering index',
            'Query could benefit from partitioning'
        ]
    }


@pytest.fixture
def sample_llm_analysis_with_duplicate():
    """Return LLM analysis with a duplicate index recommendation."""
    return {
        'index_recommendations': [
            {
                'sql': 'CREATE INDEX idx_users_email ON users (email)',
                'reason': 'This index already exists!'
            }
        ]
    }


@pytest.fixture
def mock_connection():
    """Create a mock database connection."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    return conn


@pytest.fixture
def mock_config_manager():
    """Create a mock ConfigurationManager."""
    config = MagicMock()
    config.db_user = "test_user"
    config.db_password = "test_pass"
    config.db_name = "test_db"
    config.db_type = "postgresql"
    config.readyset_port = 5433
    config.region_name = "us-east-1"
    config.cluster_id = "test-cluster"
    config.env = "test"
    config.instance_id = "test-instance"
    config.user = "testuser"
    config.readyset_data_exchange_s3_bucket = "test-bucket"
    config.enable_async_query_caching = False
    return config
