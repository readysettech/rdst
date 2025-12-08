#!/usr/bin/env bash

test_cache_commands() {
  log_section "3. Cache Command Variations (${DB_ENGINE})"

  # Note: Cache tests check for section existence, not exact values
  # This ensures tests are robust against performance variations
  # Use simple queries that ReadySet can definitely cache (single table, equality WHERE)

  # Use simplest possible queries that work across both MySQL and PostgreSQL
  # ReadySet has best support for: SELECT * FROM table WHERE column = value LIMIT n
  local simple_query="SELECT * FROM title_basics WHERE tconst = 'tt0000001'"

  run_cmd "Cache query by SQL text" \
    "${RDST_CMD[@]}" analyze \
    --readyset-cache \
    --target "$TARGET_NAME" \
    "$simple_query"
  assert_contains "ReadySet Cache Performance Analysis" "cache text header"
  assert_contains "Performance Comparison" "cache performance comparison"
  assert_not_contains "ERROR:" "cache should not error"

  # Get hash of the simple query we just cached
  local CACHE_HASH
  CACHE_HASH=$(latest_hash_from_list)
  [[ -n "$CACHE_HASH" ]] || fail "Failed to capture cache query hash from list output"

  run_cmd "Cache query by registry hash (${CACHE_HASH})" \
    "${RDST_CMD[@]}" analyze --readyset-cache --target "$TARGET_NAME" --hash "$CACHE_HASH"
  assert_contains "ReadySet Cache Performance Analysis" "cache hash header"
  assert_not_contains "ERROR:" "cache by hash should not error"

  # Note: analyze command doesn't have --json flag, removed from test
  # JSON output is available through the workflow result data structure

  run_cmd "Cache duplicate query first run" \
    "${RDST_CMD[@]}" analyze \
    --readyset-cache \
    --target "$TARGET_NAME" \
    "SELECT * FROM title_basics WHERE tconst = 'tt0000003' LIMIT 5"
  assert_contains "ReadySet Cache Performance Analysis" "cache duplicate first run"

  run_cmd "Cache duplicate query second run" \
    "${RDST_CMD[@]}" analyze \
    --readyset-cache \
    --target "$TARGET_NAME" \
    "SELECT * FROM title_basics WHERE tconst = 'tt0000003' LIMIT 5"
  assert_contains "ReadySet Cache Performance Analysis" "cache duplicate second run"
}
