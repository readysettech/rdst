#!/usr/bin/env bash

# =============================================================================
# Integration tests for rdst cache deploy/add/show/delete/drop-all
#
# These tests exercise the cache subcommands against a real Readyset
# instance deployed via `rdst cache deploy`. They run AFTER
# test_cache_commands_setup has deployed Readyset and registered the cache target.
#
# Prerequisites:
#   - Upstream database target is configured (from test_config_commands)
#   - Readyset Docker image is pullable (public registry)
#   - Docker is available on the test runner
#
# Flow:
#   1. Deploy Readyset for the target → auto-registers {target}-cache
#   2. Verify cache target was registered
#   3. cache show → empty (no caches yet)
#   4. cache add (direct SQL) → creates shallow cache
#   5. cache show → shows the cache with TTL
#   6. cache add (by registry hash) → creates another cache
#   7. cache show → shows 2 caches
#   8. cache delete → removes one cache
#   9. cache drop-all → removes remaining caches
#  10. Error scenarios: wrong target type, unsupported query, missing password
# =============================================================================

# A simple cacheable query (works for both MySQL and PostgreSQL)
CACHE_TEST_QUERY="SELECT * FROM title_basics WHERE tconst = 'tt0000001'"

test_cache_commands_setup() {
  log_section "Cache Commands Setup: Deploy Readyset (${DB_ENGINE})"

  # Deploy Readyset for the upstream target. --force tears down any
  # leftover container from prior test modules (test_cache_commands runs
  # before this and ends with a deployed cache); without --force the new
  # stricter deploy gate would refuse with "already running" instead of
  # producing a fresh container.
  run_cmd "Deploy Readyset for ${TARGET_NAME}" \
    "${RDST_CMD[@]}" cache deploy --target "$TARGET_NAME" --mode docker --force
  assert_contains "${CACHE_TARGET_NAME}" "deploy should register cache target"

  # Verify the cache target was registered
  run_cmd "Verify cache target registered" \
    "${RDST_CMD[@]}" configure list
  assert_contains "${CACHE_TARGET_NAME}" "cache target in config list"

  # Clean slate — drop any leftover caches from previous runs
  # (container may be reused if CI agent is not ephemeral)
  run_cmd "Drop leftover caches" \
    "${RDST_CMD[@]}" cache drop-all --target "$CACHE_TARGET_NAME" --yes
}

test_cache_show_empty() {
  log_section "Cache Commands: Show Empty (${DB_ENGINE})"

  run_cmd "Cache show (empty)" \
    "${RDST_CMD[@]}" cache show --target "$CACHE_TARGET_NAME"
  assert_contains "No caches found" "should show no caches initially"
}

test_cache_add_sql() {
  log_section "Cache Commands: Add by SQL (${DB_ENGINE})"

  run_cmd "Cache add (SQL)" \
    "${RDST_CMD[@]}" cache add "$CACHE_TEST_QUERY" --target "$CACHE_TARGET_NAME"
  assert_contains "Cache Created" "cache add should succeed"
  assert_contains "Shallow cache created" "should confirm shallow cache"
  assert_contains "rdst cache show" "should show view hint"
  assert_contains "rdst cache delete" "should show delete hint"
  assert_contains "cache-compare" "should show compare hint"
}

test_cache_add_in_clause() {
  # CLD-1748: cache add must succeed on queries with IN(...) lists.
  # Verifies engine-aware placeholder denormalization (Postgres → $N,
  # MySQL → ? + IN-list collapse) and that we capture ReadySet's q_<hash>.
  log_section "Cache Commands: Add IN clause (CLD-1748) (${DB_ENGINE})"

  local IN_QUERY
  if [[ "$DB_ENGINE" == "postgresql" ]]; then
    IN_QUERY="SELECT * FROM title_basics WHERE titletype IN ('movie', 'short', 'tvSeries')"
  else
    IN_QUERY="SELECT * FROM title_basics WHERE titleType IN ('movie', 'short', 'tvSeries')"
  fi

  run_cmd "Cache add (IN clause)" \
    "${RDST_CMD[@]}" cache add "$IN_QUERY" --target "$CACHE_TARGET_NAME"
  assert_contains "Cache Created" "IN-clause cache add should succeed"
}

test_cache_show_populated() {
  log_section "Cache Commands: Show Populated (${DB_ENGINE})"

  run_cmd "Cache show (populated)" \
    "${RDST_CMD[@]}" cache show --target "$CACHE_TARGET_NAME"
  assert_contains "Cache Name" "should have Cache Name column"
  assert_contains "Query" "should have Query column"
  assert_contains "Type" "should have Type column"
  assert_contains "TTL" "should have TTL column"
  assert_contains "shallow" "should show shallow cache type"
  assert_contains "1 total" "should show 1 cache"
}

test_cache_show_json() {
  log_section "Cache Commands: Show JSON (${DB_ENGINE})"

  run_cmd "Cache show (JSON)" \
    "${RDST_CMD[@]}" cache show --target "$CACHE_TARGET_NAME" --json
  assert_json "cache show JSON output"
  assert_contains '"success": true' "JSON should have success"
  assert_contains '"count": 1' "JSON should show 1 cache"
}

test_cache_add_by_hash() {
  log_section "Cache Commands: Add by Hash (${DB_ENGINE})"

  # Add a fresh query so we have a known hash to test with
  run_cmd "Add query for hash test" \
    "${RDST_CMD[@]}" query add hash-cache-test \
    --query "SELECT * FROM title_basics WHERE tconst = 'tt0000005'" \
    --target "$TARGET_NAME"

  # Get the hash
  local CACHE_HASH
  CACHE_HASH=$(latest_hash_from_list)

  if [[ -z "$CACHE_HASH" ]]; then
    fail "Failed to get hash for cache add by hash test"
  fi

  # Drop existing caches so we get a clean test
  run_cmd "Drop caches before hash test" \
    "${RDST_CMD[@]}" cache drop-all --target "$CACHE_TARGET_NAME" --yes

  # Cache add by hash — must succeed
  run_cmd "Cache add (hash ${CACHE_HASH})" \
    "${RDST_CMD[@]}" cache add "$CACHE_HASH" --target "$CACHE_TARGET_NAME"
  assert_contains "Cache Created" "cache add by hash should succeed"
  assert_contains "Shallow cache created" "should confirm shallow cache"

  # Also test by query name
  run_cmd "Drop caches before name test" \
    "${RDST_CMD[@]}" cache drop-all --target "$CACHE_TARGET_NAME" --yes

  run_cmd "Cache add (name hash-cache-test)" \
    "${RDST_CMD[@]}" cache add hash-cache-test --target "$CACHE_TARGET_NAME"
  assert_contains "Cache Created" "cache add by name should succeed"

  # Verify cache exists
  run_cmd "Verify cache after hash/name add" \
    "${RDST_CMD[@]}" cache show --target "$CACHE_TARGET_NAME"
  assert_contains "1 total" "should have 1 cache"
}

test_cache_delete() {
  log_section "Cache Commands: Delete (${DB_ENGINE})"

  # Get cache ID from show --json
  run_cmd "Get cache ID" \
    "${RDST_CMD[@]}" cache show --target "$CACHE_TARGET_NAME" --json

  local CACHE_ID
  CACHE_ID=$("$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYTHON_SCRIPT'
import sys, json

with open(sys.argv[1], 'r') as f:
    content = f.read()

# Find JSON in output
lines = content.split('\n')
json_start = -1
for i, line in enumerate(lines):
    if line.strip().startswith('{'):
        json_start = i
        break
if json_start == -1:
    sys.exit(1)

data = json.loads('\n'.join(lines[json_start:]))
caches = data.get('caches', [])
if caches:
    # Use cache_name (preferred) or cache_id
    print(caches[0].get('cache_name') or caches[0].get('cache_id', ''))
else:
    sys.exit(1)
PYTHON_SCRIPT
  )

  if [[ -z "$CACHE_ID" ]]; then
    echo "SKIP: No cache ID found to delete"
    return 0
  fi

  run_cmd "Cache delete (${CACHE_ID})" \
    "${RDST_CMD[@]}" cache delete "$CACHE_ID" --target "$CACHE_TARGET_NAME"
  assert_contains "deleted" "cache delete should confirm removal"
}

test_cache_drop_all() {
  log_section "Cache Commands: Drop All (${DB_ENGINE})"

  # Add a cache so we have something to drop
  run_cmd "Add cache for drop-all test" \
    "${RDST_CMD[@]}" cache add "$CACHE_TEST_QUERY" --target "$CACHE_TARGET_NAME"

  run_cmd "Cache drop-all" \
    "${RDST_CMD[@]}" cache drop-all --target "$CACHE_TARGET_NAME" --yes
  assert_contains "dropped" "drop-all should confirm removal"

  # Verify empty
  run_cmd "Verify empty after drop-all" \
    "${RDST_CMD[@]}" cache show --target "$CACHE_TARGET_NAME"
  assert_contains "No caches found" "should be empty after drop-all"
}

test_cache_error_wrong_target() {
  log_section "Cache Commands: Error - Wrong Target Type (${DB_ENGINE})"

  # Try cache command against database target (not Readyset)
  run_expect_fail "Cache show on database target" \
    "${RDST_CMD[@]}" cache show --target "$TARGET_NAME"
  assert_contains "database target" "should explain target type issue"
  assert_contains "rdst cache deploy" "should hint to deploy"
}

test_cache_error_unsupported_query() {
  log_section "Cache Commands: Error - Unsupported Query (${DB_ENGINE})"

  # Try caching a non-SELECT query
  run_expect_fail "Cache add INSERT" \
    "${RDST_CMD[@]}" cache add "INSERT INTO title_basics (tconst) VALUES ('test')" \
    --target "$CACHE_TARGET_NAME"
  assert_contains "not cacheable" "should reject non-SELECT"

  # Try caching NOW() (non-deterministic)
  run_expect_fail "Cache add NOW()" \
    "${RDST_CMD[@]}" cache add "SELECT NOW()" --target "$CACHE_TARGET_NAME"
  assert_contains "not cacheable" "should reject NOW()"
}

test_cache_deploy_script_only() {
  log_section "Cache Commands: Deploy Script-Only (${DB_ENGINE})"

  # Docker script generation — defaults to in-request-path
  run_cmd "Deploy script-only (docker)" \
    "${RDST_CMD[@]}" cache deploy --target "$TARGET_NAME" --mode docker --script-only
  assert_contains "docker" "docker script should reference docker"
  assert_contains "in-request-path" "docker script should default to in-request-path"

  # --no-request-path opt-out switches to legacy explicit mode
  run_cmd "Deploy script-only (docker, --no-request-path)" \
    "${RDST_CMD[@]}" cache deploy --target "$TARGET_NAME" --mode docker \
    --no-request-path --script-only
  assert_contains "explicit" "no-request-path script should use explicit mode"
  assert_not_contains "in-request-path" "no-request-path script should NOT enable in-request-path"

  # Systemd script generation
  run_cmd "Deploy script-only (systemd)" \
    "${RDST_CMD[@]}" cache deploy --target "$TARGET_NAME" --mode systemd --script-only
  assert_contains "systemd" "systemd script should reference systemd"

  # Kubernetes script generation
  run_cmd "Deploy script-only (kubernetes)" \
    "${RDST_CMD[@]}" cache deploy --target "$TARGET_NAME" --mode kubernetes --script-only
  assert_contains "readyset" "k8s script should reference readyset"

  # Remote docker script generation
  run_cmd "Deploy script-only (remote docker)" \
    "${RDST_CMD[@]}" cache deploy --target "$TARGET_NAME" --mode docker --host 10.0.1.50 --script-only
  assert_contains "docker" "remote docker script should reference docker"
}

test_cache_lifecycle_commands() {
  log_section "Cache Commands: Lifecycle start/stop/restart (${DB_ENGINE})"

  # Container is running from test_cache_commands_setup. Stop it.
  run_cmd "cache stop" "${RDST_CMD[@]}" cache stop --target "$TARGET_NAME"

  # Verify the container is actually stopped
  if docker inspect "rdst-readyset-${TARGET_NAME}" --format '{{.State.Running}}' 2>/dev/null | grep -q '^false$'; then
    echo "PASS: container is stopped after cache stop"
  else
    echo "FAIL: container did not stop"
    return 1
  fi

  # Start it back up
  run_cmd "cache start" "${RDST_CMD[@]}" cache start --target "$TARGET_NAME"
  sleep 4
  if docker inspect "rdst-readyset-${TARGET_NAME}" --format '{{.State.Running}}' 2>/dev/null | grep -q '^true$'; then
    echo "PASS: container is running after cache start"
  else
    echo "FAIL: container did not start"
    return 1
  fi

  # cache start is idempotent on a running container
  run_cmd "cache start (idempotent)" "${RDST_CMD[@]}" cache start --target "$TARGET_NAME"
  assert_contains "already running" "second cache start should be idempotent"

  # cache restart works
  run_cmd "cache restart" "${RDST_CMD[@]}" cache restart --target "$TARGET_NAME"
  sleep 5
  if docker inspect "rdst-readyset-${TARGET_NAME}" --format '{{.State.Running}}' 2>/dev/null | grep -q '^true$'; then
    echo "PASS: container is running after cache restart"
  else
    echo "FAIL: container is not running after restart"
    return 1
  fi
}

# Master function that runs all cache command tests
test_cache_subcommands() {
  # Compute here (not at source time) so TARGET_NAME is set
  CACHE_TARGET_NAME="${TARGET_NAME}-cache"

  test_cache_deploy_script_only
  test_cache_commands_setup
  test_cache_show_empty
  test_cache_add_sql
  test_cache_show_populated
  test_cache_show_json
  test_cache_add_by_hash
  test_cache_delete
  test_cache_add_in_clause
  test_cache_drop_all
  test_cache_error_wrong_target
  test_cache_error_unsupported_query
  test_cache_lifecycle_commands
}
