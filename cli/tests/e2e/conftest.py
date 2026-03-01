"""E2E test fixtures: env loading, DB checks, target provisioning, tmux sessions."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tests.e2e.tmux_client import TmuxClient, TmuxError

# Required env vars — tests skip if any are missing.
_REQUIRED_VARS = [
    "RDST_E2E_DB_HOST",
    "RDST_E2E_DB_DATABASE",
    "RDST_E2E_DB_PASSWORD",
    "ANTHROPIC_API_KEY",
]

_TARGET_NAME = "e2e-imdb"

_RDST_PY = Path(__file__).resolve().parent.parent.parent / "rdst.py"


def _load_dotenv() -> None:
    """Load tests/e2e/.env into os.environ if it exists."""
    dotenv = Path(__file__).parent / ".env"
    if not dotenv.exists():
        return
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value:
            os.environ.setdefault(key, value)


# Load .env early so fixtures see the values.
_load_dotenv()


def _run_rdst(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run rdst as a subprocess (for non-interactive setup/teardown)."""
    cmd = [sys.executable, str(_RDST_PY)] + list(args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(_RDST_PY.parent),
    )


@pytest.fixture(scope="session")
def e2e_config():
    """Load and validate e2e configuration from env vars.

    Skips the entire session if required vars are missing.
    """
    missing = [v for v in _REQUIRED_VARS if not os.environ.get(v)]
    if missing:
        pytest.skip(
            f"E2E env vars missing: {', '.join(missing)}. "
            "Set them in the environment or copy tests/e2e/.env.example → tests/e2e/.env"
        )

    return {
        "host": os.environ["RDST_E2E_DB_HOST"],
        "port": int(os.environ.get("RDST_E2E_DB_PORT", "5432")),
        "user": os.environ.get("RDST_E2E_DB_USER", "rdst"),
        "database": os.environ["RDST_E2E_DB_DATABASE"],
        "engine": os.environ.get("RDST_E2E_DB_ENGINE", "postgresql"),
        "password_env": "RDST_E2E_DB_PASSWORD",
    }


@pytest.fixture(scope="session")
def db_check(e2e_config):
    """Verify the database is reachable and has IMDb data loaded.

    Skips with a helpful message if the DB is down or data is missing.
    """
    engine = e2e_config["engine"]
    host = e2e_config["host"]
    port = e2e_config["port"]
    user = e2e_config["user"]
    database = e2e_config["database"]
    password = os.environ[e2e_config["password_env"]]

    if engine == "postgresql":
        try:
            import psycopg2
        except ImportError:
            pytest.skip("psycopg2 not installed — needed for postgresql e2e DB check")
        try:
            conn = psycopg2.connect(
                host=host, port=port, user=user,
                password=password, dbname=database,
                connect_timeout=5,
            )
        except Exception as exc:
            pytest.skip(f"Cannot connect to PostgreSQL at {host}:{port}/{database}: {exc}")
    elif engine == "mysql":
        try:
            import pymysql
        except ImportError:
            pytest.skip("pymysql not installed — needed for mysql e2e DB check")
        try:
            conn = pymysql.connect(
                host=host, port=port, user=user,
                password=password, database=database,
                connect_timeout=5,
            )
        except Exception as exc:
            pytest.skip(f"Cannot connect to MySQL at {host}:{port}/{database}: {exc}")
    else:
        pytest.skip(f"Unsupported engine for e2e DB check: {engine}")

    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM title_basics")
        count = cur.fetchone()[0]
        if count == 0:
            pytest.skip(
                "title_basics table is empty. "
                "Load IMDb data first: python scripts/data/load_imdb.py"
            )
    except Exception as exc:
        pytest.skip(f"Cannot query title_basics: {exc}")
    finally:
        conn.close()

    return {"row_count": count}


@pytest.fixture(scope="session")
def e2e_target(e2e_config, db_check):
    """Provision an rdst target for e2e tests.

    Creates ``e2e-imdb`` target using ``rdst configure add``.
    Tears it down after the session.
    """
    # Remove stale target if it exists (ignore errors).
    _run_rdst("configure", "remove", _TARGET_NAME, "--confirm")

    # Add the target.
    _run_rdst(
        "configure", "add",
        "--target", _TARGET_NAME,
        "--engine", e2e_config["engine"],
        "--host", e2e_config["host"],
        "--port", str(e2e_config["port"]),
        "--user", e2e_config["user"],
        "--database", e2e_config["database"],
        "--password-env", e2e_config["password_env"],
        "--skip-verify",
    )

    yield _TARGET_NAME

    # Teardown: remove the target.
    _run_rdst("configure", "remove", _TARGET_NAME, "--confirm")


@pytest.fixture
def tmux(request):
    """Fresh tmux session per test. Killed on teardown."""
    short_id = uuid.uuid4().hex[:8]
    client = TmuxClient(f"e2e-{short_id}")
    client.start()

    yield client

    try:
        client.kill()
    except TmuxError:
        pass
