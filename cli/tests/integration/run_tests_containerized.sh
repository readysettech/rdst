#!/usr/bin/env bash
# RDST Integration Tests - Local Containerized Runner
#
# Usage:
#   ./run_tests_containerized.sh              # Run both PostgreSQL and MySQL
#   ./run_tests_containerized.sh postgresql   # PostgreSQL only
#   ./run_tests_containerized.sh mysql        # MySQL only
#
# Requires Docker. Set ANTHROPIC_API_KEY for LLM tests.

set -euo pipefail

DB_TYPE="${1:-both}"

if [[ "$DB_TYPE" != "postgresql" && "$DB_TYPE" != "mysql" && "$DB_TYPE" != "both" ]]; then
  echo "ERROR: Invalid database type: $DB_TYPE" >&2
  echo "Must be 'postgresql', 'mysql', or 'both'" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RDST_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "======================================================================"
echo "RDST Integration Tests - Local Containerized"
echo "======================================================================"
echo ""
echo "Database: $DB_TYPE"
echo ""

echo "Checking prerequisites..."

if ! command -v docker &> /dev/null; then
  echo "ERROR: Docker is not installed"
  exit 1
fi
echo "  ✓ Docker: $(docker --version | head -1)"

if ! docker compose version &> /dev/null; then
  echo "ERROR: Docker Compose is not installed"
  exit 1
fi
echo "  ✓ Docker Compose: $(docker compose version | head -1)"

if ! command -v python3 &> /dev/null; then
  echo "ERROR: Python3 is not installed"
  exit 1
fi
echo "  ✓ Python: $(python3 --version)"

echo ""

cd "$SCRIPT_DIR"

echo "Starting database containers..."

COMPOSE_SERVICES=""
if [[ "$DB_TYPE" == "postgresql" || "$DB_TYPE" == "both" ]]; then
  COMPOSE_SERVICES="$COMPOSE_SERVICES postgres"
fi
if [[ "$DB_TYPE" == "mysql" || "$DB_TYPE" == "both" ]]; then
  COMPOSE_SERVICES="$COMPOSE_SERVICES mysql"
fi

docker compose up -d $COMPOSE_SERVICES

echo "Waiting for containers to be healthy..."

wait_for_container() {
  local container=$1
  local timeout=120
  local start_time=$(date +%s)

  while true; do
    local status=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "not_found")

    if [[ "$status" == "healthy" ]]; then
      echo "  ✓ $container is healthy"
      return 0
    elif [[ "$status" == "not_found" ]]; then
      echo "  Container $container not found"
      return 1
    fi

    local now=$(date +%s)
    local elapsed=$((now - start_time))

    if [[ $elapsed -ge $timeout ]]; then
      echo "  ✗ $container did not become healthy within ${timeout}s"
      docker logs "$container" --tail 30
      return 1
    fi

    echo "  Waiting for $container... (${elapsed}s / ${timeout}s)"
    sleep 5
  done
}

if [[ "$DB_TYPE" == "postgresql" || "$DB_TYPE" == "both" ]]; then
  wait_for_container "rdst-test-postgres"
fi

if [[ "$DB_TYPE" == "mysql" || "$DB_TYPE" == "both" ]]; then
  wait_for_container "rdst-test-mysql"
fi

echo ""
echo "✓ All database containers are ready"
echo ""

export PSQL_CONNECTION_STRING="postgresql://testuser:testpassword@localhost:15432/testdb"
export MYSQL_CONNECTION_STRING="mysql://testuser:testpassword@localhost:13306/testdb"
export SKIP_READYSET_CACHE_TESTS=true

unset PYTHONPATH

PYTHON_BIN="${PYTHON_BIN:-python3}"
PYTHON_BIN="$(command -v "$PYTHON_BIN")"
echo "Using Python: $($PYTHON_BIN --version) at $PYTHON_BIN"

echo "Installing Python dependencies..."
cd "$RDST_ROOT"
if ! INSTALL_OUTPUT=$(
  "$PYTHON_BIN" -m pip install --quiet --user -r requirements.txt 2>&1
); then
  echo "$INSTALL_OUTPUT" | grep -v "WARNING:" >&2 || true
  echo "ERROR: Failed to install Python dependencies" >&2
  exit 1
fi
echo "$INSTALL_OUTPUT" | grep -v "WARNING:" || true

USER_SITE=$($PYTHON_BIN -c "import site; print(site.getusersitepackages())" 2>/dev/null || echo "")
if [[ -n "$USER_SITE" ]]; then
  export PYTHONPATH="${USER_SITE}"
fi

export PYTHON_BIN

cleanup() {
  local exit_code=$?
  echo ""
  echo "Cleaning up containers..."
  cd "$SCRIPT_DIR"
  docker compose down -v --remove-orphans 2>/dev/null || true
  echo "✓ Cleanup complete"
  exit $exit_code
}

trap cleanup EXIT

echo ""
echo "Configuration:"
echo "  PostgreSQL: localhost:15432/testdb"
echo "  MySQL: localhost:13306/testdb"
echo ""

cd "$SCRIPT_DIR"
chmod +x run_tests.sh

TEST_EXIT_CODE=0

if [[ "$DB_TYPE" == "both" ]]; then
  echo "Running PostgreSQL tests..."
  ./run_tests.sh postgresql || TEST_EXIT_CODE=$?

  if [[ $TEST_EXIT_CODE -eq 0 ]]; then
    echo ""
    echo "Running MySQL tests..."
    ./run_tests.sh mysql || TEST_EXIT_CODE=$?
  fi
else
  ./run_tests.sh "$DB_TYPE" || TEST_EXIT_CODE=$?
fi

exit $TEST_EXIT_CODE
