#!/usr/bin/env bash

# =============================================================================
# ENVIRONMENT AND CONFIGURATION
# =============================================================================
#
# This script sets up the test environment for RDST integration tests.
# It requires database connection strings to be provided via environment variables.
# Use docker-compose to spin up test databases before running tests.
#
# Required environment variables:
#   PSQL_CONNECTION_STRING - PostgreSQL connection (if testing PostgreSQL)
#   MYSQL_CONNECTION_STRING - MySQL connection (if testing MySQL)
#
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "$ROOT_DIR"

# Resolve Python to absolute path so subshells use the same one
PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTHON_BIN="$(command -v "$PYTHON_BIN")"

# Install Python packages needed for workload scripts
echo "Installing Python packages for workload scripts..."
PIP_SCOPE_ARGS=(--user)
if "$PYTHON_BIN" -c \
  'import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)'; then
  PIP_SCOPE_ARGS=()
fi
"$PYTHON_BIN" -m pip install "${PIP_SCOPE_ARGS[@]}" --quiet \
  mysql-connector-python psycopg2-binary || {
  echo "Warning: Failed to install Python packages. Workload-dependent tests may fail."
}

# Target names for RDST configuration
PG_TARGET_NAME="${PG_TARGET_NAME:-test-db-pg}"
MYSQL_TARGET_NAME="${MYSQL_TARGET_NAME:-test-db-mysql}"

# Database configuration (set by setup_upstream_databases)
PG_HOST=""
PG_PORT=""
PG_DB_NAME=""
PG_USER=""
PG_PASSWORD=""

MYSQL_HOST=""
MYSQL_PORT=""
MYSQL_DB_NAME=""
MYSQL_USER=""
MYSQL_PASSWORD=""

# Test database selection
TEST_POSTGRESQL="${TEST_POSTGRESQL:-true}"
TEST_MYSQL="${TEST_MYSQL:-true}"

# Connection strings (required - provided by docker-compose or manually)
PSQL_CONNECTION_STRING="${PSQL_CONNECTION_STRING:-}"
MYSQL_CONNECTION_STRING="${MYSQL_CONNECTION_STRING:-}"

# Validate connection strings are provided
if [[ "$TEST_POSTGRESQL" == "true" && -z "$PSQL_CONNECTION_STRING" ]]; then
  echo "ERROR: PSQL_CONNECTION_STRING must be set for PostgreSQL tests" >&2
  echo "" >&2
  echo "Run tests using docker-compose:" >&2
  echo "  ./run_tests_containerized.sh postgresql" >&2
  echo "" >&2
  echo "Or provide a connection string manually:" >&2
  echo "  export PSQL_CONNECTION_STRING=\"postgresql://user:pass@host:port/db\"" >&2
  exit 1
fi

if [[ "$TEST_MYSQL" == "true" && -z "$MYSQL_CONNECTION_STRING" ]]; then
  echo "ERROR: MYSQL_CONNECTION_STRING must be set for MySQL tests" >&2
  echo "" >&2
  echo "Run tests using docker-compose:" >&2
  echo "  ./run_tests_containerized.sh mysql" >&2
  echo "" >&2
  echo "Or provide a connection string manually:" >&2
  echo "  export MYSQL_CONNECTION_STRING=\"mysql://user:pass@host:port/db\"" >&2
  exit 1
fi

# Current test context (set by set_db_context)
TARGET_NAME=""
DB_ENGINE=""
DB_HOST=""
DB_PORT=""
DB_NAME=""
DB_USER=""
DB_PASSWORD=""

# Test state variables
PRIMARY_HASH=""
PRIMARY_TAG=""
STRUCTURE_HASH=""
LIST_HASH=""

# LLM and environment configuration
# Fetch ANTHROPIC_API_KEY from AWS Secrets Manager when running in Buildkite
if [[ -z "${ANTHROPIC_API_KEY:-}" && -n "${BUILDKITE:-}" ]]; then
  if command -v aws >/dev/null 2>&1; then
    echo "Fetching ANTHROPIC_API_KEY from AWS Secrets Manager..."
    SECRET_JSON=$(aws secretsmanager get-secret-value \
      --secret-id ANTHROPIC_API_KEY \
      --region us-east-2 \
      --query SecretString \
      --output text 2>/dev/null || echo "")
    if [[ -n "$SECRET_JSON" ]]; then
      ANTHROPIC_API_KEY=$(echo "$SECRET_JSON" | "$PYTHON_BIN" -c "import sys,json; print(json.load(sys.stdin).get('ANTHROPIC_API_KEY',''))" 2>/dev/null || echo "")
      if [[ -n "$ANTHROPIC_API_KEY" ]]; then
        export ANTHROPIC_API_KEY
        echo "✓ ANTHROPIC_API_KEY loaded from Secrets Manager"
      else
        echo "Warning: Failed to parse ANTHROPIC_API_KEY from secret JSON"
      fi
    else
      echo "Warning: Failed to fetch ANTHROPIC_API_KEY from Secrets Manager"
    fi
  else
    echo "Warning: AWS CLI not available, ANTHROPIC_API_KEY not set"
  fi
fi

# Disable telemetry during tests
export RDST_TESTING=true

export RICH_NO_COLOR=1
export TERM=dumb

# IMPORTANT: Clear any inherited PYTHONPATH to avoid version mismatches
# (e.g., pyenv 3.12 packages being picked up by system Python 3.9)
unset PYTHONPATH

# Print Python info for debugging
echo "Python binary: $PYTHON_BIN"
echo "Python version: $($PYTHON_BIN --version 2>&1)"
echo "Python path: $(which $PYTHON_BIN)"

# Capture Python site-packages paths BEFORE changing HOME
# This is critical because changing HOME will break site.getusersitepackages()
# Only capture paths for the SPECIFIC Python version we're using
ORIGINAL_SITE_PATHS="$("$PYTHON_BIN" - <<'PY'
import site
import sys
paths = []

# Get the major.minor version to filter paths
version = f"{sys.version_info.major}.{sys.version_info.minor}"

try:
    for p in site.getsitepackages():
        # Only include paths for our Python version
        if version in p or "site-packages" not in p:
            paths.append(p)
except Exception:
    pass
try:
    user_site = site.getusersitepackages()
    if user_site and version in user_site:
        paths.append(user_site)
except Exception:
    pass

seen = []
for path in paths:
    if path and path not in seen:
        seen.append(path)
print(":".join(seen))
PY
)"

# Temporary directories
TMP_HOME="$(mktemp -d)"
TMP_RUN="$(mktemp -d)"
export HOME="$TMP_HOME"

# Readyset container configuration
READYSET_CONTAINER_NAME="rdst-integration-test-readyset"
READYSET_PORT="${READYSET_PORT:-5433}"

# Comparisons prepare the managed sandbox; pull its image up front so the
# first test never races a cold multi-hundred-MB download. Best-effort:
# if the pull fails, `docker run` still pulls on demand.
READYSET_TEST_IMAGE="$("$PYTHON_BIN" -c 'from shared.deploy import READYSET_IMAGE; print(READYSET_IMAGE)' 2>/dev/null || true)"
if [[ -n "$READYSET_TEST_IMAGE" ]]; then
  echo "Pre-pulling Readyset image for cache tests: $READYSET_TEST_IMAGE"
  docker pull "$READYSET_TEST_IMAGE" >/dev/null 2>&1 || true
fi

# =============================================================================
# CLEANUP
# =============================================================================

cleanup() {
  echo "Cleaning up test environment..."

  # Clean up the singleton sandbox and containers from older RDST versions.
  docker rm -f "rdst-readyset-sandbox" >/dev/null 2>&1 || true
  if [[ "$TEST_POSTGRESQL" == "true" ]]; then
    docker rm -f "rdst-readyset-${PG_TARGET_NAME}" >/dev/null 2>&1 || true
    docker rm -f "rdst-test-psql-${PG_TARGET_NAME}" >/dev/null 2>&1 || true
    docker rm -f "readyset-cache-${PG_TARGET_NAME}" >/dev/null 2>&1 || true
  fi

  if [[ "$TEST_MYSQL" == "true" ]]; then
    docker rm -f "rdst-readyset-${MYSQL_TARGET_NAME}" >/dev/null 2>&1 || true
    docker rm -f "rdst-test-mysql-${MYSQL_TARGET_NAME}" >/dev/null 2>&1 || true
    docker rm -f "readyset-cache-${MYSQL_TARGET_NAME}" >/dev/null 2>&1 || true
  fi

  # Always attempt to remove integration test containers
  docker rm -f "$READYSET_CONTAINER_NAME" >/dev/null 2>&1 || true
  rm -rf "$TMP_HOME" "$TMP_RUN"
}
trap cleanup EXIT

# =============================================================================
# INITIALIZATION
# =============================================================================

# Clean up leftover containers from previous runs
echo "Checking for leftover test containers from previous runs..."
if docker ps -a --filter "name=${READYSET_CONTAINER_NAME}" --format '{{.Names}}' | grep -q "^${READYSET_CONTAINER_NAME}$"; then
  echo "Removing leftover container: ${READYSET_CONTAINER_NAME}"
  docker rm -f "$READYSET_CONTAINER_NAME" >/dev/null 2>&1 || true
fi

for container in "rdst-readyset-${PG_TARGET_NAME}" "rdst-test-psql-${PG_TARGET_NAME}" \
                 "rdst-readyset-${MYSQL_TARGET_NAME}" "rdst-test-mysql-${MYSQL_TARGET_NAME}" \
                 "readyset-cache-${PG_TARGET_NAME}" "readyset-cache-${MYSQL_TARGET_NAME}"; do
  if docker ps -a --filter "name=${container}" --format '{{.Names}}' | grep -q "^${container}$"; then
    echo "Removing leftover cache container: ${container}"
    docker rm -f "$container" >/dev/null 2>&1 || true
  fi
done

# Check for port conflicts
CONTAINERS_ON_PORT=$(docker ps --format '{{.Names}}' --filter "publish=${READYSET_PORT}" 2>/dev/null || true)
if [[ -n "$CONTAINERS_ON_PORT" ]]; then
  echo "WARNING: Found containers using port ${READYSET_PORT}: ${CONTAINERS_ON_PORT}"
  echo "These may interfere with tests. Consider stopping them with: docker stop ${CONTAINERS_ON_PORT}"
fi

# Configure Python environment using ONLY the site paths for our Python version
# (captured before HOME was changed)
if [[ -n "$ORIGINAL_SITE_PATHS" ]]; then
  export PYTHONPATH="${ROOT_DIR}:${ORIGINAL_SITE_PATHS}"
else
  export PYTHONPATH="${ROOT_DIR}"
fi
echo "PYTHONPATH: $PYTHONPATH"

# Set up output directory
OUTPUT_DIR="$TMP_RUN/output"
mkdir -p "$OUTPUT_DIR"

# RDST command array
if [[ -n "${RDST_BINARY:-}" ]]; then
  # Use pre-built binary if specified (for testing compiled AL23/RPM/DEB builds)
  RDST_CMD=("$RDST_BINARY")
  echo "Using RDST binary: $RDST_BINARY"
else
  # Default: use Python source (for development)
  RDST_CMD=("$PYTHON_BIN" "rdst.py")
  echo "Using RDST Python source: rdst.py"
fi

# Change to rdst directory so relative path works
cd "$ROOT_DIR"

# =============================================================================
# CONNECTION STRING PARSING
# =============================================================================

parse_connection_string() {
  local conn_str="$1"
  local var_prefix="$2"

  # Remove query parameters if present (e.g., ?sslmode=disable)
  local clean_str="${conn_str%%\?*}"

  # Extract components using regex
  # Format: protocol://user:password@host:port/database
  if [[ "$clean_str" =~ ^([^:]+)://([^:]+):([^@]+)@([^:]+):([^/]+)/(.+)$ ]]; then
    eval "${var_prefix}_USER=\"${BASH_REMATCH[2]}\""
    eval "${var_prefix}_PASSWORD=\"${BASH_REMATCH[3]}\""
    eval "${var_prefix}_HOST=\"${BASH_REMATCH[4]}\""
    eval "${var_prefix}_PORT=\"${BASH_REMATCH[5]}\""
    eval "${var_prefix}_DB_NAME=\"${BASH_REMATCH[6]}\""
    return 0
  else
    echo "ERROR: Failed to parse connection string: $conn_str" >&2
    echo "Expected format: protocol://user:password@host:port/database" >&2
    return 1
  fi
}

# =============================================================================
# DATABASE SETUP
# =============================================================================

setup_upstream_databases() {
  echo
  echo "======================================================================"
  echo "Setting up Upstream Databases"
  echo "======================================================================"

  # Setup PostgreSQL if needed
  if [[ "$TEST_POSTGRESQL" == "true" ]]; then
    echo "Using PostgreSQL connection string"
    parse_connection_string "$PSQL_CONNECTION_STRING" "PG" || fail "Failed to parse PSQL_CONNECTION_STRING"

    # Export for password environment variable
    export POSTGRESQL_PASSWORD="$PG_PASSWORD"
    export DB_PASSWORD="$PG_PASSWORD"

    echo "✓ PostgreSQL configuration:"
    echo "  Host: $PG_HOST"
    echo "  Port: $PG_PORT"
    echo "  Database: $PG_DB_NAME"
    echo "  User: $PG_USER"
  fi

  # Setup MySQL if needed
  if [[ "$TEST_MYSQL" == "true" ]]; then
    echo "Using MySQL connection string"
    parse_connection_string "$MYSQL_CONNECTION_STRING" "MYSQL" || fail "Failed to parse MYSQL_CONNECTION_STRING"

    # Export for password environment variable
    export MYSQL_PASSWORD="$MYSQL_PASSWORD"
    export DB_PASSWORD="$MYSQL_PASSWORD"

    echo "✓ MySQL configuration:"
    echo "  Host: $MYSQL_HOST"
    echo "  Port: $MYSQL_PORT"
    echo "  Database: $MYSQL_DB_NAME"
    echo "  User: $MYSQL_USER"
  fi
}
