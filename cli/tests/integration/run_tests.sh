#!/usr/bin/env bash

# Usage:
#   Test both PostgreSQL and MySQL:
#     ./run_tests.sh
#
#   Test single database:
#     ./run_tests.sh postgresql
#     ./run_tests.sh mysql
#
#   Run only specific areas (comma-separated):
#     ./run_tests.sh postgresql --areas analyze,top
#     ./run_tests.sh mysql --areas fleet,audit
#
#   With connection strings (skip container creation):
#     PSQL_CONNECTION_STRING="postgresql://user:pass@host:port/db" ./run_tests.sh postgresql
#     MYSQL_CONNECTION_STRING="mysql://user:pass@host:port/db" ./run_tests.sh mysql

set -euo pipefail

# Determine test scope from arguments
TEST_AREAS=""
DB_ARG="${1:-}"
shift || true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --areas) TEST_AREAS="$2"; shift 2 ;;
    *) echo "Unknown flag: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$DB_ARG" ]]; then
  TEST_POSTGRESQL=true
  TEST_MYSQL=true
elif [[ "$DB_ARG" == "postgresql" ]]; then
  TEST_POSTGRESQL=true
  TEST_MYSQL=false
elif [[ "$DB_ARG" == "mysql" ]]; then
  TEST_POSTGRESQL=false
  TEST_MYSQL=true
else
  echo "Usage: $0 [postgresql|mysql] [--areas area1,area2,...]" >&2
  exit 1
fi

export TEST_POSTGRESQL
export TEST_MYSQL

# Area-based test selection. If TEST_AREAS is empty or "ALL", run everything.
should_run_area() {
  local area="$1"
  [[ -z "$TEST_AREAS" || "$TEST_AREAS" == "ALL" ]] && return 0
  echo ",$TEST_AREAS," | grep -q ",$area," && return 0
  return 1
}

if [[ -n "$TEST_AREAS" && "$TEST_AREAS" != "ALL" ]]; then
  echo "=== Running selective integration tests: $TEST_AREAS ==="
fi

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
source "${SCRIPT_DIR}/tests/test_top_and_registry.sh"
source "${SCRIPT_DIR}/tests/test_query_command.sh"
source "${SCRIPT_DIR}/tests/test_errors.sh"
source "${SCRIPT_DIR}/tests/test_scan.sh"
source "${SCRIPT_DIR}/tests/test_fleet.sh"
source "${SCRIPT_DIR}/tests/test_audit_duration.sh"

# =============================================================================
# AREA FIXTURES
# =============================================================================
# Each area declares the fixtures it needs.  The runner resolves the union of
# fixtures for all selected areas, deduplicates, and runs them once before any
# area tests execute.  This guarantees every area passes standalone without
# relying on side effects from other areas.

fixture_seed_registry_query() {
  log_section "Fixture: seed registry query (${DB_ENGINE})"
  local tag="fixture-probe-${DB_ENGINE}"
  run_cmd "Add fixture query to registry" \
    "${RDST_CMD[@]}" query add "$tag" \
    --query "SELECT tb.titleType, COUNT(*) AS count FROM title_basics tb JOIN title_ratings tr ON tb.tconst = tr.tconst WHERE tr.numVotes > 1000 GROUP BY tb.titleType ORDER BY count DESC LIMIT 25" \
    --target "$TARGET_NAME"
  PRIMARY_TAG="$tag"
  PRIMARY_HASH="$(latest_hash_from_list)"
}

# Area -> required fixtures (space-separated).  Empty = no fixtures needed.
fixtures_for_area() {
  case "$1" in
    top|query) echo "fixture_seed_registry_query" ;;
    *)         echo "" ;;
  esac
}

# Resolve selected areas, collect their fixtures (deduped), and run them.
run_area_fixtures() {
  local resolved=""
  for area in analyze top cache query scan fleet audit; do
    should_run_area "$area" || continue
    for fn in $(fixtures_for_area "$area"); do
      case " $resolved " in
        *" $fn "*) ;;
        *) "$fn"; resolved="$resolved $fn" ;;
      esac
    done
  done
}

# =============================================================================
# TEST SUITE EXECUTION
# =============================================================================

run_test_suite() {
  local engine="$1"
  set_db_context "$engine"

  # Clean up any previous test artifacts
  rm -rf "$HOME/.rdst" 2>/dev/null || true

  # Reset global state
  PRIMARY_HASH=""
  PRIMARY_TAG=""
  STRUCTURE_HASH=""
  LIST_HASH=""

  # Run test suite
  local suite_failed=0
  (
    # Config setup always runs (other tests depend on it)
    test_config_commands
    test_config_connection_string
    test_config_connection_string_override
    test_config_connection_string_no_password

    # Error handling always runs (fast, catches CLI regressions)
    test_error_handling

    # Run fixtures for selected areas before any area tests
    run_area_fixtures

    if should_run_area "analyze"; then
      test_analyze_inputs
    fi

    if should_run_area "top"; then
      test_list_command
      test_top_command
      test_top_interactive_flow
    fi

    if should_run_area "cache"; then
      if [[ "${SKIP_READYSET_CACHE_TESTS:-false}" != "true" ]]; then
        test_cache_commands
        test_readyset_flag
      else
        echo ""
        echo "=== SKIPPING: Cache tests (SKIP_READYSET_CACHE_TESTS=true) ==="
        echo ""
      fi
    fi

    if should_run_area "query"; then
      test_query_commands
      test_registry_and_files
    fi

    if should_run_area "scan"; then
      test_scan_commands
    fi

    if should_run_area "fleet"; then
      test_fleet_commands
    fi

    if should_run_area "audit"; then
      test_audit_duration_commands
    fi
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

# Run architecture sync check (no database required)
run_architecture_sync_check() {
  log_section "Running Architecture Sync Check"
  python3 "${SCRIPT_DIR}/tests/test_architecture_sync.py"
  if [[ $? -ne 0 ]]; then
    echo "✗ Architecture sync check failed - ARCHITECTURE.md is out of sync with features/"
    exit 1
  fi
  echo "✓ Architecture sync check passed"
  echo
}

# Main execution
main() {
  # Run sync checks first (no database needed)
  run_mcp_sync_check
  run_architecture_sync_check

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
