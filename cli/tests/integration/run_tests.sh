#!/usr/bin/env bash

# Usage:
#   Test both PostgreSQL and MySQL:
#     ./run_tests.sh
#
#   Test single database:
#     ./run_tests.sh postgresql
#     ./run_tests.sh mysql
#
#   With connection strings (skip container creation):
#     PSQL_CONNECTION_STRING="postgresql://user:pass@host:port/db" ./run_tests.sh postgresql
#     MYSQL_CONNECTION_STRING="mysql://user:pass@host:port/db" ./run_tests.sh mysql

set -euo pipefail

# Determine test scope from arguments
if [[ $# -eq 0 ]]; then
  TEST_POSTGRESQL=true
  TEST_MYSQL=true
elif [[ "$1" == "postgresql" ]]; then
  TEST_POSTGRESQL=true
  TEST_MYSQL=false
elif [[ "$1" == "mysql" ]]; then
  TEST_POSTGRESQL=false
  TEST_MYSQL=true
else
  echo "Usage: $0 [postgresql|mysql]" >&2
  echo "  No args: test both databases" >&2
  echo "  postgresql: test PostgreSQL only" >&2
  echo "  mysql: test MySQL only" >&2
  exit 1
fi

export TEST_POSTGRESQL
export TEST_MYSQL

# Suppress interactive prompts (telemetry feedback, NPS) during tests
export RDST_NON_INTERACTIVE=1

# Find script directory and source setup
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/setup.sh"
source "${SCRIPT_DIR}/lib/helpers.sh"

# Source all test modules
source "${SCRIPT_DIR}/tests/test_config.sh"
source "${SCRIPT_DIR}/tests/test_analyze.sh"
source "${SCRIPT_DIR}/tests/test_cache.sh"
source "${SCRIPT_DIR}/tests/test_cache_commands.sh"
source "${SCRIPT_DIR}/tests/test_top_and_registry.sh"
source "${SCRIPT_DIR}/tests/test_query_command.sh"
source "${SCRIPT_DIR}/tests/test_errors.sh"
source "${SCRIPT_DIR}/tests/test_scan.sh"

# Test suite execution
run_test_suite() {
  local engine="$1"
  set_db_context "$engine"

  # Clean up any previous test artifacts
  rm -rf "$HOME/.rdst" 2>/dev/null || true

  # Reset global state
  PRIMARY_HASH=""
  PRIMARY_TAG="film-popularity"
  STRUCTURE_HASH=""
  LIST_HASH=""

  # Run test suite
  local suite_failed=0
  (
    # Basic tests (no Readyset required)
    test_config_commands
    test_config_connection_string
    test_config_connection_string_override
    test_config_connection_string_no_password
    test_analyze_inputs
    # TEMPORARILY DISABLED: test_analyze_interactive_flag (LMStudio not running)
    test_list_command
    test_top_command

    # Interactive test (optional - may skip if TTY unavailable)
    test_top_interactive_flow

    # Cache tests (create Readyset containers via analyze --readyset-cache)
    test_cache_commands

    # Cache subcommand tests (deploy ReadySet + cache add/show/delete/drop-all)
    test_cache_subcommands

    # Readyset analysis tests (use containers from cache tests)
    test_readyset_flag

    # Query command tests
    test_query_commands

    # Registry and error tests
    test_registry_and_files
    test_error_handling

    # Scan command tests (all 4 ORMs, shallow + deep analysis)
    test_scan_commands
  ) || suite_failed=1

  if [[ $suite_failed -eq 1 ]]; then
    echo "✗ ${DB_ENGINE} tests failed"
    exit 1
  fi

  echo "✓ All ${DB_ENGINE} tests passed"
  echo
}

# Run MCP sync check (no database required)
run_mcp_sync_check() {
  log_section "Running MCP Sync Check"
  python3 "${SCRIPT_DIR}/tests/test_mcp_sync.py"
  if [[ $? -ne 0 ]]; then
    echo "✗ MCP sync check failed - CLI and MCP server are out of sync"
    exit 1
  fi
  echo "✓ MCP sync check passed"
  echo
}

# Main execution
main() {
  # Run MCP sync check first (no database needed)
  run_mcp_sync_check

  setup_upstream_databases

  if [[ "$TEST_POSTGRESQL" == "true" ]]; then
    log_section "Running PostgreSQL Tests"
    run_test_suite "postgresql"
  fi

  if [[ "$TEST_MYSQL" == "true" ]]; then
    log_section "Running MySQL Tests"
    run_test_suite "mysql"
  fi

  echo "================================================================="
  echo "✓✓✓ ALL TESTS PASSED ✓✓✓"
  echo "================================================================="
}

main
