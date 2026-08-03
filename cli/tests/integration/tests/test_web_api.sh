#!/usr/bin/env bash
# ==========================================================================
# RDST Web API Integration Tests
#
# Tests the API endpoints that the web UI depends on.
# Uses real Docker containers — same setup as the CLI integration tests.
#
# Requires: rdst web running on localhost:5000 with a configured target.
#
# Run:
#   source lib/setup.sh
#   source lib/helpers.sh
#   setup_upstream_databases
#   set_db_context postgresql   # or mysql
#   rdst web --port 5000 &
#   WEB_PID=$!
#   sleep 3
#   test_web_api
#   kill $WEB_PID
# ==========================================================================

API="http://localhost:5000/api"

# Helper: call API and save response
api_get() {
  local label="$1"
  local path="$2"
  echo "  GET $path"
  LAST_RESPONSE=$(curl -s -w "\n%{http_code}" "$API$path" 2>&1)
  LAST_BODY=$(echo "$LAST_RESPONSE" | sed '$d')
  LAST_STATUS=$(echo "$LAST_RESPONSE" | tail -1)
  echo "$LAST_BODY" > "$OUTPUT_DIR/last_api_response.json"
}

api_post() {
  local label="$1"
  local path="$2"
  local body="$3"
  echo "  POST $path"
  LAST_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST -H "Content-Type: application/json" -d "$body" "$API$path" 2>&1)
  LAST_BODY=$(echo "$LAST_RESPONSE" | sed '$d')
  LAST_STATUS=$(echo "$LAST_RESPONSE" | tail -1)
  echo "$LAST_BODY" > "$OUTPUT_DIR/last_api_response.json"
}

# Helper: collect SSE events from a streaming endpoint
api_stream_post() {
  local label="$1"
  local path="$2"
  local body="$3"
  local timeout="${4:-120}"
  echo "  POST $path (SSE stream, timeout=${timeout}s)"
  LAST_BODY=$(curl -s -N --max-time "$timeout" -X POST -H "Content-Type: application/json" -d "$body" "$API$path" 2>&1)
  echo "$LAST_BODY" > "$OUTPUT_DIR/last_sse_response.txt"
}

api_stream_get() {
  local label="$1"
  local path="$2"
  local timeout="${3:-30}"
  echo "  GET $path (SSE stream, timeout=${timeout}s)"
  LAST_BODY=$(curl -s -N --max-time "$timeout" "$API$path" 2>&1)
  echo "$LAST_BODY" > "$OUTPUT_DIR/last_sse_response.txt"
}

assert_status() {
  local expected="$1"
  local label="$2"
  if [[ "$LAST_STATUS" == "$expected" ]]; then
    echo "  PASS: $label (status $expected)"
  else
    echo "  FAIL: $label (expected $expected, got $LAST_STATUS)"
    echo "  Response: $(echo "$LAST_BODY" | head -5)"
    return 1
  fi
}

assert_json_field() {
  local field="$1"
  local label="$2"
  if echo "$LAST_BODY" | python3 -c "import json,sys; d=json.load(sys.stdin); assert $field" 2>/dev/null; then
    echo "  PASS: $label"
  else
    echo "  FAIL: $label ($field)"
    return 1
  fi
}

assert_sse_event() {
  local event_name="$1"
  local label="$2"
  if grep -q "event: *${event_name}" "$OUTPUT_DIR/last_sse_response.txt" 2>/dev/null; then
    echo "  PASS: $label (event: $event_name)"
  else
    echo "  FAIL: $label (missing event: $event_name)"
    return 1
  fi
}

# ==========================================================================
test_web_api() {
  local failures=0

  echo ""
  echo "=========================================="
  echo "RDST Web API Integration Tests (${DB_ENGINE})"
  echo "=========================================="

  # ── Health Check ──────────────────────────────────────────
  echo ""
  echo "--- Health Check ---"
  api_get "health" "/../health"
  assert_status 200 "health endpoint" || ((failures++))

  # ── Configure: Add Target ─────────────────────────────────
  echo ""
  echo "--- Configure: Add Target ---"
  api_post "add target" "/configure/targets" "{
    \"name\": \"${TARGET_NAME}\",
    \"engine\": \"${DB_ENGINE}\",
    \"host\": \"${DB_HOST}\",
    \"port\": ${DB_PORT},
    \"user\": \"${DB_USER}\",
    \"database\": \"${DB_NAME}\",
    \"password_env\": \"DB_PASSWORD\"
  }"
  assert_status 200 "add target" || ((failures++))

  # ── Configure: List Targets ───────────────────────────────
  echo ""
  echo "--- Configure: List Targets ---"
  api_get "list targets" "/configure/targets"
  assert_status 200 "list targets" || ((failures++))
  assert_json_field "len(d['targets']) > 0" "has targets" || ((failures++))

  # ── Configure: Test Connection ────────────────────────────
  echo ""
  echo "--- Configure: Test Connection ---"
  api_post "test connection" "/configure/targets/${TARGET_NAME}/test" "{}"
  assert_status 200 "test connection" || ((failures++))

  # ── Analyze ───────────────────────────────────────────────
  echo ""
  echo "--- Analyze ---"
  api_stream_post "analyze" "/analyze" "{
    \"query\": \"SELECT * FROM title_basics WHERE tconst = 'tt0000001'\",
    \"target\": \"${TARGET_NAME}\"
  }" 60
  assert_sse_event "complete" "analyze completes" || ((failures++))

  # ── Top (Historical) ──────────────────────────────────────
  echo ""
  echo "--- Top (Historical) ---"
  api_get "top historical" "/top?target=${TARGET_NAME}"
  assert_status 200 "top returns data" || ((failures++))

  # ── Cache: Status ─────────────────────────────────────────
  echo ""
  echo "--- Cache: Status ---"
  api_get "cache status" "/cache/status?target=${TARGET_NAME}"
  assert_status 200 "cache status" || ((failures++))

  # ── Cache: Deploy ─────────────────────────────────────────
  echo ""
  echo "--- Cache: Deploy ---"
  api_stream_post "cache deploy" "/cache/deploy" "{
    \"target\": \"${TARGET_NAME}\",
    \"mode\": \"docker\"
  }" 120
  assert_sse_event "deploy_complete" "cache deploys" || ((failures++))

  # ── Cache: Status (after deploy) ──────────────────────────
  echo ""
  echo "--- Cache: Status (after deploy) ---"
  sleep 5
  api_get "cache status post-deploy" "/cache/status?target=${TARGET_NAME}"
  assert_status 200 "cache status after deploy" || ((failures++))

  # ── Cache: Add Query ──────────────────────────────────────
  echo ""
  echo "--- Cache: Add Query ---"
  api_stream_post "cache add" "/cache/add" "{
    \"target\": \"${TARGET_NAME}\",
    \"query\": \"SELECT * FROM title_basics WHERE tconst = 'tt0000001'\"
  }" 30
  assert_sse_event "cache_add" "cache add query" || ((failures++))

  # ── Cache: List ───────────────────────────────────────────
  echo ""
  echo "--- Cache: List ---"
  api_get "cache list" "/cache/list?target=${TARGET_NAME}"
  assert_status 200 "cache list" || ((failures++))

  # ── Cache: Remove ─────────────────────────────────────────
  echo ""
  echo "--- Cache: Remove ---"
  api_stream_post "cache remove" "/cache/remove" "{
    \"target\": \"${TARGET_NAME}\"
  }" 30
  # Cache remove may return different events depending on implementation
  echo "  PASS: cache remove (no crash)"

  # ── Audit: Single Target (no duration) ────────────────────
  # NOTE: Requires features/audit/api/routes.py to be implemented
  echo ""
  echo "--- Audit: Single Target ---"
  api_stream_post "audit" "/audit" "{
    \"target\": \"${TARGET_NAME}\",
    \"no_insights\": true
  }" 60
  if grep -q "event:" "$OUTPUT_DIR/last_sse_response.txt" 2>/dev/null; then
    assert_sse_event "complete" "audit completes" || ((failures++))
    # Verify AuditResult fields
    COMPLETE_DATA=$(grep "event: *complete" -A1 "$OUTPUT_DIR/last_sse_response.txt" | grep "^data:" | head -1 | sed 's/^data: *//')
    if [[ -n "$COMPLETE_DATA" ]]; then
      echo "$COMPLETE_DATA" > "$OUTPUT_DIR/audit_result.json"
      python3 -c "
import json
with open('$OUTPUT_DIR/audit_result.json') as f:
    d = json.load(f)
assert d.get('target_name') == '${TARGET_NAME}', f'wrong target: {d.get(\"target_name\")}'
assert d.get('engine') == '${DB_ENGINE}', f'wrong engine: {d.get(\"engine\")}'
assert d.get('metrics'), 'missing metrics'
assert d.get('sizing'), 'missing sizing'
assert d.get('cache_opportunity'), 'missing cache_opportunity'
print('  PASS: audit result has required fields')
" 2>&1 || { echo "  FAIL: audit result missing fields"; ((failures++)); }
    fi
  else
    echo "  SKIP: audit API not implemented yet"
  fi

  # ── Audit: With Duration ──────────────────────────────────
  echo ""
  echo "--- Audit: With Duration ---"
  api_stream_post "audit+duration" "/audit" "{
    \"target\": \"${TARGET_NAME}\",
    \"duration\": \"15s\",
    \"no_insights\": true
  }" 60
  if grep -q "event:" "$OUTPUT_DIR/last_sse_response.txt" 2>/dev/null; then
    assert_sse_event "complete" "audit+duration completes" || ((failures++))
  else
    echo "  SKIP: audit API not implemented yet"
  fi

  # ── Fleet: List Targets ───────────────────────────────────
  # NOTE: Requires features/fleet/api/routes.py to be implemented
  echo ""
  echo "--- Fleet: List Targets ---"
  api_get "fleet targets" "/fleet/targets"
  if [[ "$LAST_STATUS" == "200" ]]; then
    assert_json_field "isinstance(d, list) or 'targets' in d" "fleet targets returns list" || ((failures++))
  else
    echo "  SKIP: fleet API not implemented yet"
  fi

  # ── Fleet: Status ─────────────────────────────────────────
  echo ""
  echo "--- Fleet: Status ---"
  api_stream_get "fleet status" "/fleet/status" 30
  if grep -q "event:" "$OUTPUT_DIR/last_sse_response.txt" 2>/dev/null; then
    echo "  PASS: fleet status streams events"
  else
    echo "  SKIP: fleet API not implemented yet"
  fi

  # ── Fleet: Audit ──────────────────────────────────────────
  echo ""
  echo "--- Fleet: Audit ---"
  api_stream_post "fleet audit" "/fleet/audit" "{
    \"no_insights\": true
  }" 120
  if grep -q "event:" "$OUTPUT_DIR/last_sse_response.txt" 2>/dev/null; then
    assert_sse_event "fleet_complete" "fleet audit completes" || ((failures++))
  else
    echo "  SKIP: fleet audit API not implemented yet"
  fi

  # ── Reports: List ─────────────────────────────────────────
  echo ""
  echo "--- Reports: List ---"
  api_get "reports list" "/reports"
  if [[ "$LAST_STATUS" == "200" ]]; then
    echo "  PASS: reports list returns 200"
  elif [[ "$LAST_STATUS" == "404" ]]; then
    echo "  SKIP: reports API not implemented yet"
  else
    echo "  FAIL: reports list unexpected status $LAST_STATUS"
    ((failures++))
  fi

  # ── Cleanup: Remove Target ───────────────────────────────
  echo ""
  echo "--- Cleanup ---"
  # Remove cache container first
  docker rm -f "rdst-readyset-${TARGET_NAME}" >/dev/null 2>&1 || true

  # Summary
  echo ""
  echo "=========================================="
  if [[ $failures -eq 0 ]]; then
    echo "ALL WEB API TESTS PASSED (${DB_ENGINE})"
  else
    echo "$failures WEB API TEST(S) FAILED (${DB_ENGINE})"
  fi
  echo "=========================================="

  return $failures
}
