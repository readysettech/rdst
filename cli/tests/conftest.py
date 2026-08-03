"""
Common pytest fixtures and configuration for RDST tests.
"""

import os
import sys
import tempfile

# Protect the developer profile before pytest imports test modules. Some
# production modules derive paths from Path.home() at import time; the autouse
# fixture below provides a fresh per-test home for call-time path resolution.
_TEST_SESSION_HOME = tempfile.mkdtemp(prefix="rdst-tests-")
_TEST_HOME_DRIVE, _TEST_HOME_PATH = os.path.splitdrive(_TEST_SESSION_HOME)
os.environ["HOME"] = _TEST_SESSION_HOME
os.environ["USERPROFILE"] = _TEST_SESSION_HOME
os.environ["HOMEDRIVE"] = _TEST_HOME_DRIVE
os.environ["HOMEPATH"] = _TEST_HOME_PATH

# Keep developer-specific remote-Docker routing out of unit tests. Individual
# topology tests set these variables explicitly when needed.
for variable in (
    "RDST_DOCKER_REMOTE",
    "RDST_DOCKER_PUBLISHED_HOST",
    "RDST_DOCKER_UPSTREAM_HOST",
):
    os.environ.pop(variable, None)

# Disable telemetry for all tests before shared.telemetry_manager is imported.
os.environ.setdefault("RDST_TESTING", "true")

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add the rdst directory to the path for proper imports
rdst_root = Path(__file__).parent.parent
if str(rdst_root) not in sys.path:
    sys.path.insert(0, str(rdst_root))
if str(rdst_root / "lib") not in sys.path:
    sys.path.insert(0, str(rdst_root / "lib"))


# Test suites excluded from default runs.  Each entry maps a path
# fragment to the skip reason shown when the suite is auto-skipped.
_EXCLUDED_SUITES = {
    "ask_experimental": "ask_experimental tests excluded by default. Run with: pytest tests/ask_experimental/ -v",
    "tests/e2e": "e2e tests excluded by default. Run with: pytest tests/e2e/ -v",
}


def _matches_suite(path_str: str, fragment: str) -> bool:
    """True if *path_str* belongs to the test suite identified by *fragment*."""
    return fragment in path_str or fragment.replace("/", "\\") in path_str


def _is_explicit_run(items, fragment: str) -> bool:
    """True if the user explicitly targeted *fragment* (and nothing else)."""
    return (
        any(_matches_suite(str(it.fspath), fragment) for it in items)
        and all(
            _matches_suite(str(it.fspath), fragment) or "conftest" in str(it.fspath)
            for it in items
        )
    )


# Register custom markers
def pytest_configure(config):
    config.addinivalue_line(
        "markers", "ask_experimental: tests for Ask/Schema features (not run by default, require database)"
    )
    config.addinivalue_line(
        "markers", "e2e: end-to-end tests via tmux (not run by default, require database + API key)"
    )
    config.addinivalue_line(
        "markers", "asyncio: mark test to run in asyncio event loop"
    )
    config.addinivalue_line(
        "markers",
        "realdb: API integration tests that require a real database container "
        "(managed by .buildkite/run_api_integration_tests.sh)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip excluded test suites unless the user explicitly targeted them."""
    for fragment, reason in _EXCLUDED_SUITES.items():
        if _is_explicit_run(items, fragment):
            return
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if _matches_suite(str(item.fspath), fragment):
                item.add_marker(skip)


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


@pytest.fixture(autouse=True)
def tmp_rdst_home(monkeypatch, tmp_path: Path) -> Path:
    """Relocate ``~/.rdst/`` to a fresh tmp dir for the duration of one test.

    RDST resolves its data directory at call time. Set both POSIX and Windows
    home variables so ``Path.home()`` cannot reach the developer profile.
    """
    rdst_home = tmp_path / "rdst-home"
    rdst_data_dir = rdst_home / ".rdst"
    rdst_data_dir.mkdir(parents=True, exist_ok=True)

    home = str(rdst_home)
    home_drive, home_path = os.path.splitdrive(home)
    monkeypatch.setenv("HOME", home)
    monkeypatch.setenv("USERPROFILE", home)
    monkeypatch.setenv("HOMEDRIVE", home_drive)
    monkeypatch.setenv("HOMEPATH", home_path)
    for variable in (
        "RDST_DOCKER_REMOTE",
        "RDST_DOCKER_PUBLISHED_HOST",
        "RDST_DOCKER_UPSTREAM_HOST",
    ):
        monkeypatch.delenv(variable, raising=False)

    return rdst_data_dir


@pytest.fixture
def run_blocking_inline(monkeypatch):
    """Run mocked executor work on the caller's event loop.

    Mocked blocking boundaries have nothing real to block on, and a worker
    thread makes the interleaving non-deterministic. Modules that mock those
    boundaries opt in with ``pytest.mark.usefixtures("run_blocking_inline")``.
    """
    import asyncio

    async def _inline(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _inline)


@pytest.fixture
def run_executor_inline(monkeypatch):
    """Run mocked ``run_in_executor`` work on the caller's event loop."""
    import asyncio

    def _executor_inline(loop, _executor, func, *args):
        future = loop.create_future()
        try:
            future.set_result(func(*args))
        except BaseException as exc:
            future.set_exception(exc)
        return future

    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", _executor_inline)
