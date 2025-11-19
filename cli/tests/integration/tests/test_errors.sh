#!/usr/bin/env bash

test_error_handling() {
  log_section "9. Error Handling Scenarios (${DB_ENGINE})"

  run_expect_fail "Analyze with invalid target" \
    "${RDST_CMD[@]}" analyze --target "does-not-exist" --query "SELECT 1"
  assert_contains "Target 'does-not-exist' not found" "invalid target error message"

  # Malformed SQL - analyze succeeds but reports error in output
  run_cmd "Analyze malformed SQL" \
    "${RDST_CMD[@]}" analyze --target "$TARGET_NAME" --query "SELCT * FORM title_basics"
  assert_contains "ERROR:" "malformed SQL should show error in output"
  assert_regex "syntax|SyntaxError" "malformed SQL error message"

  run_expect_fail "Analyze using unknown hash id" \
    "${RDST_CMD[@]}" analyze "deadbeefcafe"
  assert_contains "Query hash 'deadbeefcafe' not found" "missing hash error message"

  export BAD_DB_PASSWORD="incorrect-password"
  run_cmd "Configure target with wrong password" \
    "${RDST_CMD[@]}" configure add \
    --target "bad-creds" \
    --engine postgresql \
    --host "$DB_HOST" \
    --port "$DB_PORT" \
    --user "$DB_USER" \
    --database "$DB_NAME" \
    --password-env BAD_DB_PASSWORD

  # Analyze with wrong credentials - succeeds but reports error in output
  run_cmd "Analyze with wrong credentials" \
    "${RDST_CMD[@]}" analyze --target "bad-creds" --query "SELECT 1"
  assert_contains "ERROR:" "wrong credentials should show error in output"
  assert_regex "password|authentication|OperationalError" "authentication failure message"

  run_cmd "Remove bad credential target" "${RDST_CMD[@]}" configure remove "bad-creds" --confirm
  unset BAD_DB_PASSWORD
}
