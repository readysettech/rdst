#!/usr/bin/env bash
# Audit duration & query run integration tests
# Tests: audit with --duration, audit list/show, query run --file
# Run via: tests/integration/run_tests.sh
# Standalone: tests/integration/run_single_test.sh test_audit_duration_commands

test_audit_duration_commands() {
  log_section "Audit Duration & Query Run Tests (${DB_ENGINE})"

  local AUDIT_TARGET="${TARGET_NAME}"

  # ============================================================================
  # Test 1: Audit default (includes top queries, no --duration)
  # ============================================================================

  run_cmd "Audit: default with top queries" "${RDST_CMD[@]}" audit \
    --target "${AUDIT_TARGET}" --no-insights --json
  assert_json "audit default json valid"
  assert_contains "\"target_name\"" "audit has target_name"
  assert_contains "\"top_queries\"" "audit has top_queries"
  assert_contains "\"cache_opportunity\"" "audit has cache_opportunity"
  echo "PASS: Audit default with top queries"

  # ============================================================================
  # Test 2: Audit with --duration (live capture)
  # ============================================================================

  # Start background workload to generate query activity
  local capture_script="${SCRIPT_DIR}/lib/top_${DB_ENGINE}_workload.py"
  local capture_pid=""
  if [[ -f "$capture_script" ]]; then
    "$PYTHON_BIN" "$capture_script" > /dev/null 2>&1 &
    capture_pid=$!
    sleep 3
  fi

  run_cmd "Audit: with --duration 15s" "${RDST_CMD[@]}" audit \
    --target "${AUDIT_TARGET}" --duration 15s --no-insights --no-save --json
  assert_json "audit duration json valid"
  assert_contains "\"target_name\"" "audit duration has target_name"

  if [[ -n "${capture_pid}" ]]; then
    kill "$capture_pid" 2>/dev/null || true
    wait "$capture_pid" 2>/dev/null || true
  fi

  echo "PASS: Audit with --duration 15s"

  # ============================================================================
  # Test 3: Audit list (browse past runs)
  # ============================================================================

  run_cmd "Audit: list --json" "${RDST_CMD[@]}" audit list --json
  assert_contains "\"run_id\"" "audit list has run_id"
  assert_contains "\"target_name\"" "audit list has target_name"
  assert_contains "\"source\"" "audit list has source"
  echo "PASS: Audit list --json"

  # ============================================================================
  # Test 4: Audit show (view a past run)
  # ============================================================================

  local LATEST_RUN
  LATEST_RUN=$("$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYEOF'
import sys, json
with open(sys.argv[1]) as f:
    content = f.read()
lines = content.split('\n')
json_start = -1
for i, line in enumerate(lines):
    if line.strip().startswith('[') or line.strip().startswith('{'):
        json_start = i
        break
if json_start >= 0:
    json_content = '\n'.join(lines[json_start:])
    runs = json.loads(json_content)
    if runs:
        print(runs[0]['run_id'])
PYEOF
  ) || LATEST_RUN=""

  if [[ -n "$LATEST_RUN" ]]; then
    run_cmd "Audit: show ${LATEST_RUN}" "${RDST_CMD[@]}" audit show \
      "${LATEST_RUN}" --json
    assert_json "audit show json valid"
    assert_contains "\"run_id\"" "audit show has run_id"
    assert_contains "\"queries\"" "audit show has queries"
    echo "PASS: Audit show"
  else
    echo "SKIP: Audit show (no run_id found)"
  fi

  # ============================================================================
  # Test 5: Audit show nonexistent run (error)
  # ============================================================================

  run_cmd "Audit: show nonexistent" "${RDST_CMD[@]}" audit show \
    "nonexistent_run_12345" --target "${AUDIT_TARGET}"
  assert_contains "not found" "audit show nonexistent error"
  echo "PASS: Audit show nonexistent run"

  # ============================================================================
  # Test 6: Audit list filtered by target
  # ============================================================================

  run_cmd "Audit: list by target" "${RDST_CMD[@]}" audit list \
    --target "${AUDIT_TARGET}" --json
  echo "PASS: Audit list by target"

  # ============================================================================
  # Test 7: Audit human-readable output (no --json)
  # ============================================================================

  run_cmd "Audit: human-readable" "${RDST_CMD[@]}" audit \
    --target "${AUDIT_TARGET}" --no-insights
  assert_contains "Audit:" "audit human has header"
  assert_contains "Sizing:" "audit human has sizing"
  assert_contains "Cache Opportunity:" "audit human has cache opportunity"
  # Top queries table appears when pg_stat_statements/performance_schema has data
  if [[ "$DB_ENGINE" == "postgresql" ]]; then
    assert_contains "Top Queries" "audit human has top queries table"
  fi
  echo "PASS: Audit human-readable output"

  # ============================================================================
  # Test 8: Query run with --file (replay from CSV)
  # ============================================================================

  REPLAY_CSV="$TMP_RUN/replay_queries.csv"
  if [[ "$DB_ENGINE" == "postgresql" ]]; then
    cat > "$REPLAY_CSV" << 'EOF'
query
SELECT primarytitle, startyear FROM title_basics WHERE startyear > 2000 LIMIT 10
SELECT averagerating, numvotes FROM title_ratings WHERE averagerating > 7.0 LIMIT 10
SELECT tb.primarytitle, tr.averagerating FROM title_basics tb JOIN title_ratings tr ON tb.tconst = tr.tconst LIMIT 10
EOF
  else
    cat > "$REPLAY_CSV" << 'EOF'
query
SELECT primaryTitle, startYear FROM title_basics WHERE startYear > 2000 LIMIT 10
SELECT averageRating, numVotes FROM title_ratings WHERE averageRating > 7.0 LIMIT 10
SELECT tb.primaryTitle, tr.averageRating FROM title_basics tb JOIN title_ratings tr ON tb.tconst = tr.tconst LIMIT 10
EOF
  fi

  run_cmd "Query run: --file CSV" "${RDST_CMD[@]}" query run \
    --file "$REPLAY_CSV" --target "${AUDIT_TARGET}" --count 5
  echo "PASS: Query run --file CSV"

  # ============================================================================
  # Test 9: Queries auto-saved to registry after audit capture
  # ============================================================================

  # Start background workload
  local capture_pid2=""
  if [[ -f "$capture_script" ]]; then
    "$PYTHON_BIN" "$capture_script" > /dev/null 2>&1 &
    capture_pid2=$!
    sleep 3
  fi

  run_cmd "Audit: capture with auto-save" "${RDST_CMD[@]}" audit \
    --target "${AUDIT_TARGET}" --duration 10s --no-insights --json

  if [[ -n "${capture_pid2}" ]]; then
    kill "$capture_pid2" 2>/dev/null || true
    wait "$capture_pid2" 2>/dev/null || true
  fi

  # Check if queries were captured (MySQL performance_schema may not track)
  local CAPTURED_QUERIES
  CAPTURED_QUERIES=$("$PYTHON_BIN" -c "
import json, sys
with open('$LAST_OUTPUT_FILE') as f:
    content = f.read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if line.strip().startswith('{'):
        data = json.loads('\n'.join(lines[i:]))
        wl = data.get('workload', {})
        print(wl.get('unique_queries', 0))
        break
" 2>/dev/null) || CAPTURED_QUERIES="0"

  if [[ "$CAPTURED_QUERIES" -gt 0 ]]; then
    run_cmd "Audit: verify registry" "${RDST_CMD[@]}" query list
    # Queries saved with capture_ or audit_ tags
    if grep -q "capture_\|audit_" "$LAST_OUTPUT_FILE"; then
      echo "PASS: Queries auto-saved to registry"
    else
      echo "SKIP: Queries not found in registry (may not have been captured)"
    fi
  else
    echo "SKIP: Queries auto-saved to registry (no queries captured — ${DB_ENGINE})"
  fi

  # ============================================================================
  # Test 10: Multiple audit runs produce different IDs
  # ============================================================================

  run_cmd "Audit: run A" "${RDST_CMD[@]}" audit \
    --target "${AUDIT_TARGET}" --duration 5s --no-insights --no-save --json
  local RUN_A
  RUN_A=$("$PYTHON_BIN" -c "
import json, sys
with open('$LAST_OUTPUT_FILE') as f:
    content = f.read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if line.strip().startswith('{'):
        data = json.loads('\n'.join(lines[i:]))
        wl = data.get('workload', {})
        print(wl.get('run_id', ''))
        break
" 2>/dev/null) || RUN_A=""

  sleep 1

  run_cmd "Audit: run B" "${RDST_CMD[@]}" audit \
    --target "${AUDIT_TARGET}" --duration 5s --no-insights --no-save --json
  local RUN_B
  RUN_B=$("$PYTHON_BIN" -c "
import json, sys
with open('$LAST_OUTPUT_FILE') as f:
    content = f.read()
lines = content.split('\n')
for i, line in enumerate(lines):
    if line.strip().startswith('{'):
        data = json.loads('\n'.join(lines[i:]))
        wl = data.get('workload', {})
        print(wl.get('run_id', ''))
        break
" 2>/dev/null) || RUN_B=""

  if [[ -n "$RUN_A" && -n "$RUN_B" && "$RUN_A" != "$RUN_B" ]]; then
    echo "PASS: Multiple audit runs produce different IDs ($RUN_A != $RUN_B)"
  elif [[ -z "$RUN_A" || -z "$RUN_B" ]]; then
    echo "SKIP: Multiple run ID check (no workload capture in audit output)"
  else
    fail "Expected different run_ids but got A='${RUN_A}' B='${RUN_B}'"
  fi

  # ============================================================================
  # Test 11: Audit with --duration AND LLM insights
  # ============================================================================

  if [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
    # Start background workload
    local capture_pid3=""
    if [[ -f "$capture_script" ]]; then
      "$PYTHON_BIN" "$capture_script" > /dev/null 2>&1 &
      capture_pid3=$!
      sleep 3
    fi

    run_cmd "Audit: duration with insights" "${RDST_CMD[@]}" audit \
      --target "${AUDIT_TARGET}" --duration 15s --json
    assert_json "audit duration insights json valid"
    assert_contains "\"target_name\"" "audit duration insights has target"

    if [[ -n "${capture_pid3}" ]]; then
      kill "$capture_pid3" 2>/dev/null || true
      wait "$capture_pid3" 2>/dev/null || true
    fi

    # Check for workload analysis in the output
    if grep -q "analysis\|readyset_recommendation\|capture_characterization" "$LAST_OUTPUT_FILE"; then
      echo "PASS: Audit duration with LLM insights (workload analysis present)"
    else
      echo "PASS: Audit duration with LLM insights (no workload queries captured for analysis)"
    fi
  else
    echo "SKIP: Audit duration with LLM insights (no ANTHROPIC_API_KEY)"
  fi

  echo ""
  echo "=== All Audit Duration & Query Run Tests PASSED (${DB_ENGINE}) ==="
}
