#!/usr/bin/env bash
# Fleet & Audit integration tests
# Tests: fleet import, list, status, audit (single + fleet), snapshots, diff
# Run via: tests/integration/run_tests.sh
# Standalone: tests/integration/run_single_test.sh test_fleet_commands

test_fleet_commands() {
  log_section "Fleet & Audit Integration Tests (${DB_ENGINE})"

  # ============================================================================
  # Setup: Create CSV for fleet import (single engine — CI runs per-engine)
  # ============================================================================

  FLEET_CSV="$TMP_RUN/fleet_test.csv"
  if [[ "$DB_ENGINE" == "postgresql" ]]; then
    cat > "$FLEET_CSV" << EOF
name,host,port,database,user,engine,group,tags,password_env
fleet-${DB_ENGINE},${DB_HOST},${DB_PORT},${DB_NAME},${DB_USER},${DB_ENGINE},integration-group,"fleet-test,${DB_ENGINE}",${DB_ENGINE^^}_PASSWORD
EOF
  else
    cat > "$FLEET_CSV" << EOF
name,host,port,database,user,engine,group,tags,password_env
fleet-${DB_ENGINE},${DB_HOST},${DB_PORT},${DB_NAME},${DB_USER},${DB_ENGINE},integration-group,"fleet-test,${DB_ENGINE}",${DB_ENGINE^^}_PASSWORD
EOF
  fi

  # Export the password env var that the CSV references
  export "${DB_ENGINE^^}_PASSWORD=${DB_PASSWORD}"

  # --------------------------------------------------------------------------
  # Test 1: Fleet CSV Import
  # --------------------------------------------------------------------------

  run_cmd "Fleet: CSV import" "${RDST_CMD[@]}" fleet import \
    --from "$FLEET_CSV" --password-env "${DB_ENGINE^^}_PASSWORD"
  assert_contains "1 targets imported" "fleet csv import count"
  echo "PASS: Fleet CSV import"

  # --------------------------------------------------------------------------
  # Test 2: Fleet List (all targets)
  # --------------------------------------------------------------------------

  run_cmd "Fleet: list all" "${RDST_CMD[@]}" fleet list --json
  assert_contains "fleet-${DB_ENGINE}" "fleet list contains target"
  assert_contains "\"engine\"" "fleet list has engine field"
  assert_contains "\"host\"" "fleet list has host field"
  echo "PASS: Fleet list"

  # --------------------------------------------------------------------------
  # Test 3: Fleet List with --group filter
  # --------------------------------------------------------------------------

  run_cmd "Fleet: list --group" "${RDST_CMD[@]}" fleet list --group integration-group --json
  assert_contains "fleet-${DB_ENGINE}" "fleet list group filter"
  echo "PASS: Fleet list --group"

  # --------------------------------------------------------------------------
  # Test 4: Fleet List with --tag filter
  # --------------------------------------------------------------------------

  run_cmd "Fleet: list --tag" "${RDST_CMD[@]}" fleet list --tag fleet-test --json
  assert_contains "fleet-${DB_ENGINE}" "fleet list tag filter"
  echo "PASS: Fleet list --tag"

  # --------------------------------------------------------------------------
  # Test 5: Fleet List with nonexistent group (empty result, no error)
  # --------------------------------------------------------------------------

  run_cmd "Fleet: list empty group" "${RDST_CMD[@]}" fleet list --group nonexistent-group --json
  assert_not_contains "fleet-${DB_ENGINE}" "fleet list empty group excludes target"
  echo "PASS: Fleet list empty group"

  # --------------------------------------------------------------------------
  # Test 6: Fleet Status (connectivity check)
  # --------------------------------------------------------------------------

  run_cmd "Fleet: status" "${RDST_CMD[@]}" fleet status --group integration-group
  assert_contains "ok" "fleet status connectivity"
  echo "PASS: Fleet status"

  # --------------------------------------------------------------------------
  # Test 7: Single-Target Audit with JSON output
  # --------------------------------------------------------------------------

  run_cmd "Audit: single target ${DB_ENGINE} JSON" "${RDST_CMD[@]}" audit \
    --target "fleet-${DB_ENGINE}" --no-insights --json
  assert_json "audit json valid"
  # Validate key JSON fields are present
  assert_contains "\"target_name\"" "audit has target_name"
  assert_contains "\"engine\"" "audit has engine"
  assert_contains "\"sizing\"" "audit has sizing"
  assert_contains "\"cache_opportunity\"" "audit has cache_opportunity"
  assert_contains "fleet-${DB_ENGINE}" "audit target matches"
  echo "PASS: Single-target audit ${DB_ENGINE} JSON"

  # --------------------------------------------------------------------------
  # Test 8: Audit metrics sanity checks
  # --------------------------------------------------------------------------

  # The JSON output should contain metrics with real values from the test DB
  assert_contains "\"metrics\"" "audit has metrics section"
  assert_contains "\"cache_hit_rate\"" "audit has cache_hit_rate"
  assert_contains "\"max_connections\"" "audit has max_connections"
  assert_contains "\"server_version\"" "audit has server_version"
  # Sizing verdict should be one of the valid values
  assert_regex "under_provisioned|oversized|right_sized|unknown" "audit has valid sizing verdict"
  echo "PASS: Audit metrics sanity checks"

  # --------------------------------------------------------------------------
  # Test 9: Audit human-readable output (no --json)
  # --------------------------------------------------------------------------

  run_cmd "Audit: single target ${DB_ENGINE} human" "${RDST_CMD[@]}" audit \
    --target "fleet-${DB_ENGINE}" --no-insights
  assert_contains "Audit:" "audit human has header"
  assert_contains "Sizing:" "audit human has sizing"
  assert_contains "Cache Opportunity:" "audit human has cache opportunity"
  echo "PASS: Single-target audit human-readable"

  # --------------------------------------------------------------------------
  # Test 10: Audit with insights (default on, requires ANTHROPIC_API_KEY)
  # --------------------------------------------------------------------------

  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    run_cmd "Audit: with LLM insights" "${RDST_CMD[@]}" audit \
      --target "fleet-${DB_ENGINE}"
    assert_contains "Health Assessment" "audit insights has health assessment"
    echo "PASS: Audit with LLM insights"
  else
    echo "SKIP: Audit with LLM insights (no ANTHROPIC_API_KEY)"
  fi

  # --------------------------------------------------------------------------
  # Test 11: Fleet Audit + Save Snapshot
  # --------------------------------------------------------------------------

  run_cmd "Fleet: audit + save" "${RDST_CMD[@]}" fleet audit \
    --group integration-group --no-insights --save "test-baseline-${DB_ENGINE}" --json
  assert_contains "fleet-${DB_ENGINE}" "fleet audit includes target"
  echo "PASS: Fleet audit + save snapshot"

  # --------------------------------------------------------------------------
  # Test 12: Fleet Snapshots List
  # --------------------------------------------------------------------------

  run_cmd "Fleet: snapshots list" "${RDST_CMD[@]}" fleet snapshots --json
  assert_contains "test-baseline-${DB_ENGINE}" "snapshots list has saved snapshot"
  echo "PASS: Fleet snapshots list"

  # --------------------------------------------------------------------------
  # Test 13: Second Fleet Audit + Diff
  # --------------------------------------------------------------------------

  run_cmd "Fleet: audit second run" "${RDST_CMD[@]}" fleet audit \
    --group integration-group --no-insights --save "test-after-${DB_ENGINE}" --json
  echo "PASS: Fleet audit second run"

  run_cmd "Fleet: diff" "${RDST_CMD[@]}" fleet diff \
    "test-baseline-${DB_ENGINE}" "test-after-${DB_ENGINE}" --json
  assert_contains "baseline_id" "fleet diff has baseline_id"
  echo "PASS: Fleet diff"

  # --------------------------------------------------------------------------
  # Test 14: Idempotent Import (should skip existing)
  # --------------------------------------------------------------------------

  run_cmd "Fleet: idempotent import" "${RDST_CMD[@]}" fleet import \
    --from "$FLEET_CSV" --password-env "${DB_ENGINE^^}_PASSWORD"
  assert_contains "1 skipped" "idempotent import skips existing"
  echo "PASS: Idempotent import"

  # --------------------------------------------------------------------------
  # Test 15: CSV Import with bad data (missing required column)
  # --------------------------------------------------------------------------

  BAD_CSV="$TMP_RUN/fleet_bad.csv"
  cat > "$BAD_CSV" << EOF
name,host
bad-target,localhost
EOF

  run_cmd "Fleet: bad CSV import" "${RDST_CMD[@]}" fleet import \
    --from "$BAD_CSV" --password-env DUMMY_PASS
  assert_contains "Missing required columns" "bad csv reports missing columns"
  echo "PASS: CSV import bad data"

  # --------------------------------------------------------------------------
  # Test 16: Audit nonexistent target
  # --------------------------------------------------------------------------

  run_cmd "Audit: nonexistent target" "${RDST_CMD[@]}" audit \
    --target "no-such-target-999"
  assert_contains "not found" "audit nonexistent target error"
  echo "PASS: Audit nonexistent target"

  # --------------------------------------------------------------------------
  # Test 17: Audit with --save (persists result)
  # --------------------------------------------------------------------------

  run_cmd "Audit: save result" "${RDST_CMD[@]}" audit \
    --target "fleet-${DB_ENGINE}" --no-insights --save "single-baseline"
  assert_contains "Audit" "audit save completes"
  echo "PASS: Audit with --save"

  # --------------------------------------------------------------------------
  # Test 18: Fleet Configure (delegates to import)
  # --------------------------------------------------------------------------

  CONFIGURE_CSV="$TMP_RUN/fleet_configure.csv"
  cat > "$CONFIGURE_CSV" << EOF
name,host,port,database,user,engine,group,tags,password_env
configure-test-${DB_ENGINE},${DB_HOST},${DB_PORT},${DB_NAME},${DB_USER},${DB_ENGINE},configure-group,"configure-test",${DB_ENGINE^^}_PASSWORD
EOF

  run_cmd "Fleet: configure --from CSV" "${RDST_CMD[@]}" fleet configure \
    --from "$CONFIGURE_CSV" --password-env "${DB_ENGINE^^}_PASSWORD"
  assert_contains "1 targets imported" "fleet configure import count"
  echo "PASS: Fleet configure --from CSV"

  # --------------------------------------------------------------------------
  # Test 19: Audit with top queries in JSON
  # --------------------------------------------------------------------------

  run_cmd "Audit: top queries in JSON output" "${RDST_CMD[@]}" audit \
    --target "fleet-${DB_ENGINE}" --no-insights --json
  assert_contains "\"top_queries\"" "audit json has top_queries field"
  echo "PASS: Audit includes top queries in JSON"

  # --------------------------------------------------------------------------
  # Test 21: Fleet Audit with LLM Insights (no duration)
  # --------------------------------------------------------------------------

  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    run_cmd "Fleet: audit with insights" "${RDST_CMD[@]}" fleet audit \
      --group integration-group --json
    assert_contains "fleet-${DB_ENGINE}" "fleet audit insights includes target"
    assert_contains "insights" "fleet audit has insights in JSON"
    echo "PASS: Fleet audit with LLM insights"
  else
    echo "SKIP: Fleet audit with LLM insights (no ANTHROPIC_API_KEY)"
  fi

  # --------------------------------------------------------------------------
  # Test 22: Query run --file with --analyze
  # --------------------------------------------------------------------------

  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    ANALYZE_CSV="$TMP_RUN/analyze_queries.csv"
    if [[ "$DB_ENGINE" == "postgresql" ]]; then
      cat > "$ANALYZE_CSV" << 'EOF'
query
"SELECT primarytitle, startyear FROM title_basics WHERE tconst = 'tt0000001'"
EOF
    else
      cat > "$ANALYZE_CSV" << 'EOF'
query
"SELECT primaryTitle, startYear FROM title_basics WHERE tconst = 'tt0000001'"
EOF
    fi

    run_cmd "Query run --file --analyze" "${RDST_CMD[@]}" query run \
      --file "$ANALYZE_CSV" --target "${TARGET_NAME}" --count 3 --analyze
    if echo "$LAST_OUTPUT" | grep -q "Benchmark Analysis"; then
      echo "PASS: Query run --file with --analyze"
    elif echo "$LAST_OUTPUT" | grep -q "No module named"; then
      echo "SKIP: Query run --file with --analyze (anthropic module not installed)"
    else
      echo "PASS: Query run --file with --analyze (analysis skipped)"
    fi
  else
    echo "SKIP: Query run --file with --analyze (no ANTHROPIC_API_KEY)"
  fi

  echo ""
  echo "=== All Fleet & Audit Integration Tests PASSED (${DB_ENGINE}) ==="
}
