#!/usr/bin/env bash

test_cache_commands() {
  log_section "3. Temporary Readyset Comparisons (${DB_ENGINE})"

  local simple_query="SELECT * FROM title_basics WHERE tconst = 'tt0000001'"

  run_cmd "Query cache-compare auto-provisions the sandbox" \
    "${RDST_CMD[@]}" query cache-compare "$simple_query" \
    --target "$TARGET_NAME" --count 5 --skip-warning
  assert_contains "Readyset Comparison" "cache-compare table"
  assert_contains "Mean speedup" "cache-compare speedup"
  assert_contains "Comparison complete" "cache-compare completion"

  run_expect_fail "Persistent cache management command is absent" \
    "${RDST_CMD[@]}" cache deploy --target "$TARGET_NAME" --mode docker
  assert_contains "invalid choice" "cache command should be rejected"

  run_cmd "Cache comparison supports bounded concurrent load" \
    "${RDST_CMD[@]}" query cache-compare "$simple_query" \
    --target "$TARGET_NAME" --concurrency 2 --duration 5 \
    --count 5 --skip-warning
  assert_contains "Readyset Comparison" "concurrent cache-compare table"
  assert_contains "Comparison complete" "concurrent cache-compare completion"

  run_cmd "Analyze remains independent of Docker deployment management" \
    "${RDST_CMD[@]}" analyze --target "$TARGET_NAME" \
    --skip-warning --query "$simple_query"
  assert_contains "RDST Query Analysis" "analysis should complete"
  assert_contains "Readyset Performance" "analysis should show static screening"
}
