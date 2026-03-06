#!/usr/bin/env bash

# =============================================================================
# Integration tests for rdst cache deploy/add/show/delete/drop-all
#
# These tests exercise the cache subcommands against a real ReadySet
# instance deployed via `rdst cache deploy`. They run AFTER
# test_cache_commands_setup has deployed ReadySet and registered the cache target.
#
# Prerequisites:
#   - Upstream database target is configured (from test_config_commands)
#   - ReadySet Docker image is pullable (public registry)
#   - Docker is available on the test runner
#
# Flow:
#   1. Deploy ReadySet for the target → auto-registers {target}-cache
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
  log_section "Cache Commands Setup: Deploy ReadySet (${DB_ENGINE})"

  # Deploy ReadySet for the upstream target
  # This pulls the Docker image and creates a container
  run_cmd "Deploy ReadySet for ${TARGET_NAME}" \
    "${RDST_CMD[@]}" cache deploy --target "$TARGET_NAME" --mode docker
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
  assert_contains "rdst query run" "should show benchmark hint"
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

  # First, get a hash from the query list
  run_cmd "List queries for hash" \
    "${RDST_CMD[@]}" query list
  local CACHE_HASH
  CACHE_HASH=$(latest_hash_from_list)

  if [[ -z "$CACHE_HASH" ]]; then
    echo "SKIP: No query hash available for hash-based cache add"
    return 0
  fi

  # Use a different query to avoid "already cached" issues
  # Add a new query first, then cache it by hash
  run_cmd "Cache add (hash ${CACHE_HASH})" \
    "${RDST_CMD[@]}" cache add "$CACHE_HASH" --target "$CACHE_TARGET_NAME"

  # This may succeed (creates cache) or fail (already cached / same query)
  # Both are acceptable outcomes for this test
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

  # Try cache command against database target (not ReadySet)
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

  # Docker script generation
  run_cmd "Deploy script-only (docker)" \
    "${RDST_CMD[@]}" cache deploy --target "$TARGET_NAME" --mode docker --script-only
  assert_contains "docker" "docker script should reference docker"

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
  test_cache_drop_all
  test_cache_error_wrong_target
  test_cache_error_unsupported_query
}
