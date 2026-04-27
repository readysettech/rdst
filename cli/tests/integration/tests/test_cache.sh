#!/usr/bin/env bash

test_cache_commands() {
  log_section "3. Cache Command Variations (${DB_ENGINE})"

  # Readyset analysis now runs automatically if a cache target exists.
  # No --readyset-cache flag needed. We need a deployed cache first.
  local simple_query="SELECT * FROM title_basics WHERE tconst = 'tt0000001'"

  # Deploy Readyset cache if not already deployed
  CACHE_TARGET_NAME="${TARGET_NAME}-cache"
  run_cmd "Deploy Readyset cache for tests" \
    "${RDST_CMD[@]}" cache deploy --target "$TARGET_NAME" --mode docker
  # May say "already deployed" — that's fine

  run_cmd "Analyze with auto Readyset (SQL text)" \
    "${RDST_CMD[@]}" analyze \
    --target "$TARGET_NAME" \
    --skip-warning \
    "$simple_query"
  assert_contains "Readyset Performance" "readyset performance section"
  assert_contains "PERFORMANCE COMPARISON" "cache performance comparison"

  # Get hash of the query we just analyzed
  local CACHE_HASH
  CACHE_HASH=$(latest_hash_from_list)
  [[ -n "$CACHE_HASH" ]] || fail "Failed to capture query hash from list output"

  run_cmd "Analyze with auto Readyset (by hash ${CACHE_HASH})" \
    "${RDST_CMD[@]}" analyze --target "$TARGET_NAME" --hash "$CACHE_HASH" --skip-warning
  assert_contains "Readyset Performance" "readyset performance by hash"

  run_cmd "Analyze duplicate query first run" \
    "${RDST_CMD[@]}" analyze \
    --target "$TARGET_NAME" \
    --skip-warning \
    "SELECT * FROM title_basics WHERE tconst = 'tt0000003' LIMIT 5"
  assert_contains "Readyset Performance" "readyset perf duplicate first run"

  run_cmd "Analyze duplicate query second run" \
    "${RDST_CMD[@]}" analyze \
    --target "$TARGET_NAME" \
    --skip-warning \
    "SELECT * FROM title_basics WHERE tconst = 'tt0000003' LIMIT 5"
  assert_contains "Readyset Performance" "readyset perf duplicate second run"

  # Verify --readyset-cache flag is gone
  run_expect_fail "Verify --readyset-cache removed" \
    "${RDST_CMD[@]}" analyze --readyset-cache --target "$TARGET_NAME" --skip-warning "$simple_query"
  assert_contains "unrecognized arguments" "flag should be rejected"

  # Test cache-compare command
  run_cmd "Query cache-compare (inline SQL)" \
    "${RDST_CMD[@]}" query cache-compare "$simple_query" --target "$TARGET_NAME" --count 5 --skip-warning
  assert_contains "Performance Comparison" "cache-compare table"
  assert_contains "Upstream" "cache-compare upstream column"
  assert_contains "Improvement" "cache-compare improvement column"
  assert_contains "Warm-up query has been excluded" "cache-compare warmup note"

  # Test cache-compare without deployed cache (after remove)
  run_cmd "Remove cache for error test" \
    "${RDST_CMD[@]}" cache remove --target "$TARGET_NAME" --yes
  assert_contains "removed" "cache should be removed"

  run_cmd "Cache-compare without cache (expect warning)" \
    "${RDST_CMD[@]}" query cache-compare "$simple_query" --target "$TARGET_NAME" --count 5 --skip-warning
  assert_contains "No Readyset cache deployed" "should say no cache"
  assert_contains "rdst cache deploy" "should hint to deploy"

  # Re-deploy for subsequent tests
  run_cmd "Re-deploy Readyset for subsequent tests" \
    "${RDST_CMD[@]}" cache deploy --target "$TARGET_NAME" --mode docker
}
