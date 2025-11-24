#!/usr/bin/env bash
#
# Local Test Runner for RDST Integration Tests
#
# This script sets up the required environment variables and runs the integration tests locally.
# It handles both scenarios:
#   1. With admin API access (creates containers automatically)
#   2. With existing database connections (skips container creation)
#
# Usage:
#   # Quick start (requires admin API):
#   export API_BASE_URL="https://api-dev01.apps.readyset.cloud"
#   export ADMIN_API_TOKEN="your-admin-token-here"
#   ./run_tests_local.sh
#
#   # Or with existing database:
#   export API_BASE_URL="https://api-dev01.apps.readyset.cloud"
#   export PSQL_CONNECTION_STRING="postgresql://user:pass@host:port/imdb"
#   ./run_tests_local.sh postgresql
#
#   # Test single database:
#   ./run_tests_local.sh postgresql
#   ./run_tests_local.sh mysql
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# =============================================================================
# ENVIRONMENT VARIABLE SETUP
# =============================================================================

echo "======================================================================"
echo "RDST Integration Tests - Local Setup"
echo "======================================================================"
echo

# Auto-derive API_BASE_URL from DUPLO_TENANT if not set
if [[ -z "${API_BASE_URL:-}" ]]; then
  if [[ -n "${DUPLO_TENANT:-}" ]]; then
    API_BASE_URL="https://api-${DUPLO_TENANT}.apps.readyset.cloud"
    export API_BASE_URL
    echo "Auto-derived API_BASE_URL from DUPLO_TENANT: $API_BASE_URL"
  else
    echo "ERROR: API_BASE_URL environment variable is required"
    echo
    echo "Please export API_BASE_URL before running this script:"
    echo "  export API_BASE_URL=\"https://api-dev01.apps.readyset.cloud\""
    echo
    echo "Or export DUPLO_TENANT to auto-derive (e.g., DUPLO_TENANT=dev01):"
    echo "  export DUPLO_TENANT=\"dev01\""
    echo
    echo "For container creation, also export ADMIN_API_TOKEN:"
    echo "  export ADMIN_API_TOKEN=\"your-admin-token-here\""
    echo
    echo "Alternatively, provide existing database connection strings:"
    echo "  export PSQL_CONNECTION_STRING=\"postgresql://user:pass@host:port/imdb\""
    echo "  export MYSQL_CONNECTION_STRING=\"mysql://user:pass@host:port/imdb\""
    echo
    exit 1
  fi
fi

# Display configuration
echo "Configuration:"
echo "  API_BASE_URL: ${API_BASE_URL}"

if [[ -n "${ADMIN_API_TOKEN:-}" ]]; then
  echo "  ADMIN_API_TOKEN: ✓ Set (will create containers via admin API)"
else
  echo "  ADMIN_API_TOKEN: ✗ Not set"
fi

if [[ -n "${PSQL_CONNECTION_STRING:-}" ]]; then
  echo "  PSQL_CONNECTION_STRING: ✓ Provided (will skip PostgreSQL container creation)"
else
  echo "  PSQL_CONNECTION_STRING: ✗ Not provided"
  if [[ -z "${ADMIN_API_TOKEN:-}" ]]; then
    echo "    WARNING: No connection string and no admin token - PostgreSQL tests may fail"
  fi
fi

if [[ -n "${MYSQL_CONNECTION_STRING:-}" ]]; then
  echo "  MYSQL_CONNECTION_STRING: ✓ Provided (will skip MySQL container creation)"
else
  echo "  MYSQL_CONNECTION_STRING: ✗ Not provided"
  if [[ -z "${ADMIN_API_TOKEN:-}" ]]; then
    echo "    WARNING: No connection string and no admin token - MySQL tests may fail"
  fi
fi

echo

# =============================================================================
# OPTIONAL CONFIGURATION
# =============================================================================

# LLM Configuration (uses lmstudio by default, override if needed)
export RDST_LLM_PROVIDER="${RDST_LLM_PROVIDER:-lmstudio}"
export LMSTUDIO_BASE_URL="${LMSTUDIO_BASE_URL:-http://127.0.0.1:65535/v1/chat/completions}"
export RDST_LLM_SHARED_KEY="${RDST_LLM_SHARED_KEY:-ALPHA-STATIC-SHARED-KEY}"

# Test configuration
export TEST_POSTGRESQL="${TEST_POSTGRESQL:-true}"
export TEST_MYSQL="${TEST_MYSQL:-true}"

# Target names (can be customized)
export PG_TARGET_NAME="${PG_TARGET_NAME:-test-db-pg}"
export MYSQL_TARGET_NAME="${MYSQL_TARGET_NAME:-test-db-mysql}"

# Python binary
export PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "Test Configuration:"
echo "  LLM Provider: ${RDST_LLM_PROVIDER}"
if [[ "${RDST_LLM_PROVIDER}" == "lmstudio" ]]; then
  echo "  LMStudio URL: ${LMSTUDIO_BASE_URL}"
fi
echo "  PostgreSQL tests: ${TEST_POSTGRESQL}"
echo "  MySQL tests: ${TEST_MYSQL}"
echo "  Python: ${PYTHON_BIN}"
echo

# =============================================================================
# VALIDATE PREREQUISITES
# =============================================================================

echo "Validating prerequisites..."

# Check Docker
if ! command -v docker &> /dev/null; then
  echo "ERROR: Docker is not installed or not in PATH"
  echo "Docker is required for ReadySet containers created by cache tests"
  exit 1
fi
echo "  ✓ Docker: $(docker --version | head -1)"

# Check Python
if ! command -v "$PYTHON_BIN" &> /dev/null; then
  echo "ERROR: Python ($PYTHON_BIN) is not installed or not in PATH"
  exit 1
fi
echo "  ✓ Python: $($PYTHON_BIN --version)"

# Check rdst.py exists
if [[ ! -f "$SCRIPT_DIR/../../rdst.py" ]]; then
  echo "ERROR: rdst.py not found at expected location"
  echo "Expected: $SCRIPT_DIR/../../rdst.py"
  exit 1
fi
echo "  ✓ RDST source: $SCRIPT_DIR/../../rdst.py"

# Check for running containers on port 5433 (may conflict with tests)
if docker ps --filter "publish=5433" --format '{{.Names}}' 2>/dev/null | grep -q .; then
  echo
  echo "WARNING: Found containers using port 5433 (used by ReadySet tests):"
  docker ps --filter "publish=5433" --format '  - {{.Names}}'
  echo
  echo "These may interfere with tests. Consider stopping them first."
  echo
  read -p "Continue anyway? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    exit 1
  fi
fi

echo
echo "All prerequisites validated ✓"
echo

# =============================================================================
# RUN TESTS
# =============================================================================

cd "$SCRIPT_DIR"

# Determine which databases to test
if [[ $# -gt 0 ]]; then
  TEST_ARG="$1"
else
  TEST_ARG=""  # Run both PostgreSQL and MySQL
fi

echo "======================================================================"
echo "Starting Integration Tests"
echo "======================================================================"
echo

# Run the main test script
exec bash run_tests.sh $TEST_ARG
