#!/usr/bin/env bash

test_error_handling() {
  log_section "9. Error Handling Scenarios (${DB_ENGINE})"

  # Test early API key validation - unset ANTHROPIC_API_KEY and verify early failure
  local saved_api_key="${ANTHROPIC_API_KEY:-}"
  unset ANTHROPIC_API_KEY
  run_expect_fail "Analyze without API key" \
    "${RDST_CMD[@]}" analyze --target "$TARGET_NAME" --query "SELECT 1"
  assert_contains "No LLM API key configured" "missing API key should fail early"
  # Restore API key
  if [[ -n "$saved_api_key" ]]; then
    export ANTHROPIC_API_KEY="$saved_api_key"
  fi

  run_expect_fail "Analyze with invalid target" \
    "${RDST_CMD[@]}" analyze --target "does-not-exist" --query "SELECT 1"
  assert_contains "Target 'does-not-exist' not found" "invalid target error message"

  # Malformed SQL - analyze succeeds but reports error in output
  run_cmd "Analyze malformed SQL" \
    "${RDST_CMD[@]}" analyze --target "$TARGET_NAME" --query "SELCT * FORM title_basics"
  assert_regex "FAILED|SyntaxError|syntax error" "malformed SQL should show error in output"

  run_expect_fail "Analyze using unknown hash id" \
    "${RDST_CMD[@]}" analyze "deadbeefcafe"
  assert_contains "Query hash 'deadbeefcafe' not found" "missing hash error message"

  export BAD_DB_PASSWORD="incorrect-password"
  # Pipe 'y' to accept saving config despite connection failure
  run_cmd_pipe "Configure target with wrong password" \
    "echo 'y' | ${RDST_CMD[*]} configure add --target bad-creds --engine postgresql --host $DB_HOST --port $DB_PORT --user $DB_USER --database $DB_NAME --password-env BAD_DB_PASSWORD"

  # Analyze with wrong credentials - succeeds but reports error in output
  run_cmd "Analyze with wrong credentials" \
    "${RDST_CMD[@]}" analyze --target "bad-creds" --query "SELECT 1"
  assert_regex "FAILED|password|authentication|OperationalError" "wrong credentials should show error in output"

  run_cmd "Remove bad credential target" "${RDST_CMD[@]}" configure remove "bad-creds" --confirm
  unset BAD_DB_PASSWORD
}
