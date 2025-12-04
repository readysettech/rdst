#!/usr/bin/env bash

test_analyze_inputs() {
  log_section "2. Analyze Command Input Methods (${DB_ENGINE})"

  local inline_query="SELECT tb.titleType, COUNT(*) AS count \
FROM title_basics tb \
JOIN title_ratings tr ON tb.tconst = tr.tconst \
WHERE tr.numVotes > 1000 \
GROUP BY tb.titleType \
ORDER BY count DESC \
LIMIT 25"

  run_cmd "Analyze inline query (text input)" \
    "${RDST_CMD[@]}" analyze \
    --target "$TARGET_NAME" \
    --query "$inline_query" \
    --save-as "$PRIMARY_TAG"
  assert_contains "RDST Query Analysis" "analysis header"
  assert_contains "Query:" "analysis query section"
  assert_contains "NEXT STEPS" "analysis footer"
  assert_not_contains "ERROR:" "analysis inline should not error"

  PRIMARY_HASH="$(latest_hash_from_list)"
  [[ -n "$PRIMARY_HASH" ]] || fail "Failed to capture primary query hash from list output"

  run_cmd "Analyze by registry hash (${PRIMARY_HASH})" \
    "${RDST_CMD[@]}" analyze "$PRIMARY_HASH"
  assert_not_contains "ERROR:" "analysis by hash should succeed"

  run_cmd "Analyze by name (${PRIMARY_TAG})" \
    "${RDST_CMD[@]}" analyze --name "$PRIMARY_TAG"
  assert_contains "RDST Query Analysis" "analysis by name header"

  local query_file="$TMP_RUN/from_file.sql"
  cat >"$query_file" <<'SQL'
SELECT titleType, COUNT(*) AS count
FROM title_basics
GROUP BY titleType
ORDER BY count DESC
LIMIT 10;
SQL
  run_cmd "Analyze from file input" \
    "${RDST_CMD[@]}" analyze --fast --target "$TARGET_NAME" --file "$query_file"
  assert_contains "RDST Query Analysis" "analysis from file header"

  run_cmd_pipe "Analyze from stdin input" \
    "echo 'SELECT * FROM title_basics LIMIT 10;' | ${RDST_CMD[@]} analyze --fast --stdin --target '${TARGET_NAME}'"
  assert_contains "RDST Query Analysis" "analysis via stdin header"

  local years=(2020 2019 1995)
  for year in "${years[@]}"; do
    local year_query="SELECT * FROM title_basics WHERE startYear = ${year} LIMIT 5"
    run_cmd "Analyze hash consistency variant (startYear=${year})" \
      "${RDST_CMD[@]}" analyze --fast --target "$TARGET_NAME" --query "$year_query"
    local current_hash
    current_hash="$(latest_hash_from_list)"
    [[ -n "$current_hash" ]] || fail "Unable to capture hash after analyzing year ${year}"
    if [[ -z "$STRUCTURE_HASH" ]]; then
      STRUCTURE_HASH="$current_hash"
    elif [[ "$current_hash" != "$STRUCTURE_HASH" ]]; then
      fail "Expected hash '${STRUCTURE_HASH}' but found '${current_hash}' for year ${year}"
    fi
  done

  run_cmd "Analyze using normalized structure hash (${STRUCTURE_HASH})" \
    "${RDST_CMD[@]}" analyze "$STRUCTURE_HASH"
  assert_not_contains "ERROR:" "analysis using structure hash should succeed"

  # Verify token usage tracking in stats.json
  local stats_file="$HOME/.rdst/stats.json"
  if [[ -f "$stats_file" ]]; then
    assert_file_contains "$stats_file" "total_tokens" "stats should track total tokens"
    assert_file_contains "$stats_file" "token_usage_by_model" "stats should track per-model usage"
    echo "✓ Token usage tracking verified in stats.json"
  else
    echo "⚠ stats.json not found (token tracking not verified)"
  fi
}

test_analyze_interactive_flag() {
  log_section "2b. Analyze Command - Interactive Flag (${DB_ENGINE})"

  # Test that --interactive flag is accepted (without actually entering interactive mode)
  # We use --fast to skip interactive conversation in test environment
  local test_query="SELECT * FROM title_basics WHERE titleType = 'movie' LIMIT 5"

  # The --interactive flag should be accepted but won't enter interactive mode with --fast
  run_cmd "Analyze with --interactive flag (validate acceptance)" \
    "${RDST_CMD[@]}" analyze --fast --interactive \
    --target "$TARGET_NAME" \
    --query "$test_query"
  assert_contains "RDST Query Analysis" "analysis should complete"
  assert_not_contains "ERROR:" "interactive flag should not cause errors"

  echo "✓ Interactive mode flag validated (full interactive testing requires manual verification)"
}

test_readyset_flag() {
  log_section "4. Analyze with --readyset Flag (${DB_ENGINE})"

  # Note: --readyset flag requires a running ReadySet container
  # Cache tests have already created ReadySet containers we can use

  # Use simple queries that ReadySet can cache (equality comparisons)
  local simple_query="SELECT * FROM title_basics WHERE tconst = 'tt0000005'"

  run_cmd "Analyze with --readyset using SQL text" \
    "${RDST_CMD[@]}" analyze \
    --target "$TARGET_NAME" \
    --readyset \
    --query "$simple_query"
  assert_contains "RDST Query Analysis" "analyze --readyset should show analysis"
  # Check that ReadySet analysis was attempted (may succeed or fail gracefully)
  assert_regex "READYSET|ReadySet|readyset" "analyze --readyset should mention ReadySet"

  # Store query to get hash for next test
  local READYSET_HASH
  READYSET_HASH=$(latest_hash_from_list)
  [[ -n "$READYSET_HASH" ]] || fail "Failed to capture readyset query hash"

  run_cmd "Analyze with --readyset using hash (${READYSET_HASH})" \
    "${RDST_CMD[@]}" analyze --readyset "$READYSET_HASH"
  assert_contains "RDST Query Analysis" "analyze --readyset by hash should run analysis"
  assert_regex "READYSET|ReadySet|readyset" "analyze --readyset by hash should mention ReadySet"

  run_cmd "Analyze with --readyset and save tag" \
    "${RDST_CMD[@]}" analyze \
    --target "$TARGET_NAME" \
    --readyset \
    --save-as "readyset-test" \
    --query "SELECT * FROM title_basics WHERE tconst = 'tt0000006'"
  assert_contains "RDST Query Analysis" "analyze --readyset with tag should run analysis"
  assert_regex "READYSET|ReadySet|readyset" "analyze --readyset with tag should mention ReadySet"

  run_cmd "Analyze with --readyset using name" \
    "${RDST_CMD[@]}" analyze --readyset --name "readyset-test"
  assert_contains "RDST Query Analysis" "analyze --readyset by name should run analysis"
  assert_regex "READYSET|ReadySet|readyset" "analyze --readyset by name should mention ReadySet"

  # Test with a basic SELECT to verify functionality
  run_cmd "Analyze with --readyset using basic query" \
    "${RDST_CMD[@]}" analyze \
    --target "$TARGET_NAME" \
    --readyset \
    --query "SELECT * FROM title_basics WHERE tconst = 'tt0000007' LIMIT 1"
  assert_contains "RDST Query Analysis" "basic query should run analysis"
  # Just verify the command ran - ReadySet analysis may or may not succeed
}
