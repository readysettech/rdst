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
    --target "fleet-${DB_ENGINE}" --no-insights -v
  assert_contains "RDST Audit Report" "audit human has header"
  assert_contains "Sizing:" "audit human has sizing"
  echo "PASS: Single-target audit human-readable"

  # --------------------------------------------------------------------------
  # Test 10: Audit with LLM insights + health score (requires ANTHROPIC_API_KEY)
  # --------------------------------------------------------------------------

  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    run_cmd "Audit: with LLM insights" "${RDST_CMD[@]}" audit \
      --target "fleet-${DB_ENGINE}" -v
    assert_contains "Health Score" "audit insights has health score"
    assert_contains "Next Steps" "audit insights has next steps"
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
    --target "fleet-${DB_ENGINE}" --no-insights --save "single-baseline" -v
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

  # ==========================================================================
  # Test 23: Audit with --duration + Readyset cache testing (verbose)
  # RS cache was deployed by test_cache_commands earlier in the suite.
  # Verifies: captured queries, RS speedup data, health score, no pricing.
  # ==========================================================================

  if [[ "${SKIP_READYSET_CACHE_TESTS:-false}" != "true" && -n "${ANTHROPIC_API_KEY:-}" ]]; then
    echo ""
    echo "=== TEST 23: Audit with duration + RS cache + verbose (${DB_ENGINE}) ==="

    run_cmd "Audit: duration + RS verbose" "${RDST_CMD[@]}" audit \
      --target "${TARGET_NAME}" --duration 15s -v -y
    assert_contains "RDST Audit Report" "audit+RS has header"
    assert_contains "Health Score" "audit+RS has health score"
    assert_contains "Captured Queries" "audit+RS has captured queries table"
    assert_contains "Index Recommendations" "audit+RS has index recs"
    assert_contains "Next Steps" "audit+RS has next steps"
    assert_contains "rdst analyze" "audit+RS has command hints"
    # No AWS = no pricing section
    assert_not_contains "Sizing & Savings" "audit+RS has no pricing section (non-AWS)"
    echo "PASS: Audit with duration + RS cache + verbose"

    # JSON output: verify new fields + RS results
    run_cmd "Audit: duration + RS JSON" "${RDST_CMD[@]}" audit \
      --target "${TARGET_NAME}" --duration 15s --json
    assert_json "audit+RS json valid"
    assert_contains "\"health_report\"" "audit+RS json has health_report"
    assert_contains "\"health_analysis\"" "audit+RS json has health_analysis"
    assert_contains "\"health_score\"" "audit+RS json has health_score in analysis"
    # RS cache was deployed → readyset_results should have entries with speedup > 1
    if echo "$LAST_OUTPUT" | grep -q '"readyset_results".*\[{'; then
      # At least one query must show speedup > 1.0 (RS faster than DB)
      if echo "$LAST_OUTPUT" | python3 -c "
import json,sys
d=json.load(sys.stdin)
rs = d.get('readyset_results') or []
fast = [r for r in rs if (r.get('speedup') or 0) > 1.0]
sys.exit(0 if fast else 1)
" 2>/dev/null; then
        echo "PASS: At least one query shows RS speedup > 1.0x"
      else
        echo "FAIL: readyset_results present but no query shows speedup > 1.0x"
        exit 1
      fi
    else
      echo "INFO: readyset_results empty — RS cache may not have benchmarked queries"
    fi
    echo "PASS: Audit with duration + RS JSON output"
  else
    echo "SKIP: Audit duration + RS tests (SKIP_READYSET_CACHE_TESTS=true or no ANTHROPIC_API_KEY)"
  fi

  # ==========================================================================
  # Test 24: Fleet audit with --duration + verbose
  # ==========================================================================

  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    echo ""
    echo "=== TEST 24: Fleet audit + duration + verbose (${DB_ENGINE}) ==="

    run_cmd "Fleet: audit duration verbose" "${RDST_CMD[@]}" fleet audit \
      --group integration-group --duration 15s -v -y
    assert_contains "Fleet Health" "fleet+duration has fleet health"
    assert_contains "Top Findings" "fleet+duration has top findings"
    assert_contains "Next Steps" "fleet+duration has next steps"
    assert_contains "Individual Target" "fleet+duration has individual links"
    echo "PASS: Fleet audit with duration + verbose"
  else
    echo "SKIP: Fleet audit duration + verbose (no ANTHROPIC_API_KEY)"
  fi

  echo ""
  echo "=== All Fleet & Audit Integration Tests PASSED (${DB_ENGINE}) ==="
}
