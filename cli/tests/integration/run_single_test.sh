#!/usr/bin/env bash
# Run a single test function from the integration test suite
#
# Usage:
#   PSQL_CONNECTION_STRING="postgresql://user:pass@host:port/db" ./run_single_test.sh test_error_handling
#   PSQL_CONNECTION_STRING="postgresql://user:pass@host:port/db" ./run_single_test.sh test_query_commands
#
# Available tests:
#   test_config_commands, test_analyze_inputs, test_list_command, test_top_command,
#   test_cache_commands, test_readyset_flag, test_query_commands, test_error_handling

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <test_function_name> [postgresql|mysql]"
  echo ""
  echo "Example:"
  echo "  PSQL_CONNECTION_STRING=\"postgresql://user:pass@host:port/db\" $0 test_error_handling"
  echo ""
  echo "Available test functions:"
  echo "  test_config_commands    - Configuration commands"
  echo "  test_analyze_inputs     - Analyze command input methods"
  echo "  test_list_command       - List command scenarios"
  echo "  test_top_command        - Top command"
  echo "  test_cache_commands     - Cache command variations"
  echo "  test_readyset_flag      - Analyze with --readyset flag"
  echo "  test_query_commands     - Query command tests (add/list/show/edit/delete)"
  echo "  test_error_handling     - Error handling scenarios"
  exit 1
fi

TEST_FUNCTION="$1"
DB_TYPE="${2:-postgresql}"

# Set test scope
if [[ "$DB_TYPE" == "postgresql" ]]; then
  export TEST_POSTGRESQL=true
  export TEST_MYSQL=false
else
  export TEST_POSTGRESQL=false
  export TEST_MYSQL=true
fi

# Require connection string for standalone test
if [[ "$DB_TYPE" == "postgresql" && -z "${PSQL_CONNECTION_STRING:-}" ]]; then
  echo "ERROR: PSQL_CONNECTION_STRING must be set for postgresql tests"
  echo "Example: PSQL_CONNECTION_STRING=\"postgresql://postgres:pass@host:port/testdb\""
  exit 1
fi

if [[ "$DB_TYPE" == "mysql" && -z "${MYSQL_CONNECTION_STRING:-}" ]]; then
  echo "ERROR: MYSQL_CONNECTION_STRING must be set for mysql tests"
  exit 1
fi

# Dummy API_BASE_URL (not used when connection string is provided)
export API_BASE_URL="${API_BASE_URL:-http://dummy-not-used}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source setup and helpers
source "${SCRIPT_DIR}/lib/setup.sh"
source "${SCRIPT_DIR}/lib/helpers.sh"

# Source all test modules
source "${SCRIPT_DIR}/tests/test_config.sh"
source "${SCRIPT_DIR}/tests/test_analyze.sh"
source "${SCRIPT_DIR}/tests/test_cache.sh"
source "${SCRIPT_DIR}/tests/test_top_and_registry.sh"
source "${SCRIPT_DIR}/tests/test_query_command.sh"
source "${SCRIPT_DIR}/tests/test_errors.sh"

# Setup databases
setup_upstream_databases

# Set DB context
set_db_context "$DB_TYPE"

# Clean slate for this test
rm -rf "$HOME/.rdst" 2>/dev/null || true

# Always run config first to set up target
echo "=== Setting up target ==="
test_config_commands

# Run the requested test
echo ""
echo "=== Running: $TEST_FUNCTION ==="
$TEST_FUNCTION

echo ""
echo "=== $TEST_FUNCTION PASSED ==="
