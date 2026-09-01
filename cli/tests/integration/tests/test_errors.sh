#!/usr/bin/env bash

test_error_handling() {
  log_section "9. Error Handling Scenarios (${DB_ENGINE})"

  # Test early AI-access validation. Isolate the command from both environment
  # and keyring credentials so persistent CI agents cannot accidentally satisfy
  # the preflight with credentials left by another run.
  local saved_api_key="${ANTHROPIC_API_KEY:-}"
  unset ANTHROPIC_API_KEY
  run_expect_fail "Analyze without API key" \
    env -u RDST_ACCOUNT_ACCESS_TOKEN \
      -u RDST_ACCOUNT_REFRESH_TOKEN \
      -u RDST_ACCOUNT_EXPIRES_AT \
      PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \
      "${RDST_CMD[@]}" analyze --target "$TARGET_NAME" --query "SELECT 1" --skip-warning
  assert_contains "AI access is required" "missing AI access should fail early"
  # Restore API key
  if [[ -n "$saved_api_key" ]]; then
    export ANTHROPIC_API_KEY="$saved_api_key"
  fi

  run_expect_fail "Analyze with invalid target" \
    "${RDST_CMD[@]}" analyze --target "does-not-exist" --query "SELECT 1" --skip-warning
  assert_contains "Target 'does-not-exist' not found" "invalid target error message"

  # Malformed SQL is refused before it reaches the database: an unrecognised
  # leading keyword cannot be shown to be read-only, and analyze executes what
  # it is given via EXPLAIN ANALYZE.
  run_cmd "Analyze malformed SQL" \
    "${RDST_CMD[@]}" analyze --target "$TARGET_NAME" --query "SELCT * FORM title_basics" --skip-warning
  assert_regex "FAILED|SyntaxError|syntax error|must begin with" "malformed SQL should show error in output"

  run_expect_fail "Analyze using unknown hash id" \
    "${RDST_CMD[@]}" analyze "deadbeefcafe" --skip-warning
  assert_contains "Query hash 'deadbeefcafe' not found" "missing hash error message"

  export BAD_DB_PASSWORD="incorrect-password"
  # Pipe 'y' to accept saving config despite connection failure
  run_cmd_pipe "Configure target with wrong password" \
    "echo 'y' | ${RDST_CMD[*]} configure add --target bad-creds --engine postgresql --host $DB_HOST --port $DB_PORT --user $DB_USER --database $DB_NAME --password-env BAD_DB_PASSWORD"

  # Supply a non-empty BYOK value so this assertion reaches the database
  # connection path instead of stopping at the independent AI access gate.
  run_cmd "Analyze with wrong credentials" \
    env ANTHROPIC_API_KEY=integration-test-placeholder \
    "${RDST_CMD[@]}" analyze --target "bad-creds" --query "SELECT 1" --skip-warning
  assert_regex "FAILED|password|authentication|OperationalError" "wrong credentials should show error in output"

  run_cmd "Remove bad credential target" "${RDST_CMD[@]}" configure remove "bad-creds" --confirm
  unset BAD_DB_PASSWORD
}
