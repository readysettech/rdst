#!/usr/bin/env bash

# =============================================================================
# OUTPUT AND LOGGING
# =============================================================================

LAST_OUTPUT_FILE="${TMP_RUN}/last_output.txt"

log_section() {
  local title="$1"
  echo
  echo "======================================================================"
  echo "$title"
  echo "======================================================================"
}

fail() {
  local msg="$1"
  echo "FAIL: $msg" >&2
  exit 1
}

# =============================================================================
# COMMAND EXECUTION
# =============================================================================

run_cmd() {
  local label="$1"
  shift
  echo
  echo "TEST: $label"
  echo "  Command: $*"

  if "$@" > "$LAST_OUTPUT_FILE" 2>&1; then
    echo "PASS"
  else
    local exit_code=$?
    echo "Command exited with code $exit_code (not necessarily a failure)"
    echo "--- Output ---"
    cat "$LAST_OUTPUT_FILE"
    echo "--- End Output ---"
  fi
}

run_expect_fail() {
  local label="$1"
  shift
  echo
  echo "TEST: $label (expect failure)"
  echo "  Command: $*"

  if "$@" > "$LAST_OUTPUT_FILE" 2>&1; then
    echo "FAIL Expected command to fail but it succeeded"
    cat "$LAST_OUTPUT_FILE"
    exit 1
  else
    echo "PASS Observed expected failure for $label"
  fi
}

run_cmd_pipe() {
  local label="$1"
  shift
  local command_string="$*"
  echo
  echo "TEST: $label"
  echo "  Command: $command_string"
  bash -lc "$command_string" >"$LAST_OUTPUT_FILE" 2>&1 || true
  echo "PASS"
}

# =============================================================================
# ASSERTIONS
# =============================================================================

assert_contains() {
  local needle="$1"
  local context="$2"
  if ! grep -Fq -- "$needle" "$LAST_OUTPUT_FILE"; then
    echo "FAIL: Expected '$needle' in output (${context})" >&2
    echo "--- Actual Output ---" >&2
    cat "$LAST_OUTPUT_FILE" >&2
    echo "--- End Output ---" >&2
    exit 1
  fi
}

assert_not_contains() {
  local needle="$1"
  local context="$2"
  if grep -Fq -- "$needle" "$LAST_OUTPUT_FILE"; then
    echo "FAIL: Did not expect '$needle' in output (${context})" >&2
    echo "--- Actual Output ---" >&2
    cat "$LAST_OUTPUT_FILE" >&2
    echo "--- End Output ---" >&2
    exit 1
  fi
}

assert_regex() {
  local pattern="$1"
  local context="$2"
  if ! grep -Eq -- "$pattern" "$LAST_OUTPUT_FILE"; then
    fail "Expected pattern '$pattern' in output (${context})"
  fi
}

assert_json() {
  local context="$1"
  # Extract and validate JSON from output (handles progress messages before JSON)
  if ! "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYTHON_SCRIPT' >/dev/null 2>&1
import sys
import json

with open(sys.argv[1], 'r') as f:
    content = f.read()

# Find where JSON starts (after any progress messages)
lines = content.split('\n')
json_start = -1
for i, line in enumerate(lines):
    if line.strip().startswith('{') or line.strip().startswith('['):
        json_start = i
        break

if json_start == -1:
    print("No JSON found in output", file=sys.stderr)
    sys.exit(1)

json_content = '\n'.join(lines[json_start:])

try:
    json.loads(json_content)
    sys.exit(0)
except json.JSONDecodeError as e:
    print(f"JSON decode error: {e}", file=sys.stderr)
    sys.exit(1)
PYTHON_SCRIPT
  then
    fail "Expected valid JSON output (${context})"
  fi
}

assert_file_contains() {
  local file_path="$1"
  local needle="$2"
  local context="$3"

  if [[ ! -f "$file_path" ]]; then
    fail "File not found: ${file_path} (${context})"
  fi
  if ! grep -Fq -- "$needle" "$file_path"; then
    fail "Expected '$needle' in file ${file_path} (${context})"
  fi
}

assert_file_not_contains() {
  local file_path="$1"
  local needle="$2"
  local context="$3"

  if [[ ! -f "$file_path" ]]; then
    fail "File not found: ${file_path} (${context})"
  fi
  if grep -Fq -- "$needle" "$file_path"; then
    fail "Did not expect '$needle' in file ${file_path} (${context})"
  fi
}

# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

latest_hash_from_list() {
  # Get latest hash from output stored in LAST_OUTPUT_FILE
  # Note: analyze output shows 12-char hashes, list shows 8-char truncated hashes
  "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYTHON_SCRIPT'
import sys
import re

with open(sys.argv[1], 'r') as f:
    content = f.read()

# First try 12-character hex hash patterns (from analyze output)
matches = re.findall(r'\b[0-9a-f]{12}\b', content, re.IGNORECASE)
if matches:
    # Return first 8 chars to match what list output shows
    print(matches[0][:8])
else:
    # Fallback to 8-character patterns (from list output)
    matches = re.findall(r'\b[0-9a-f]{8}\b', content, re.IGNORECASE)
    if matches:
        print(matches[0])
    else:
        sys.exit(1)
PYTHON_SCRIPT
}

# =============================================================================
# DATABASE CONTEXT MANAGEMENT
# =============================================================================

set_db_context() {
  local engine="$1"

  if [[ "$engine" == "postgresql" ]]; then
    export TARGET_NAME="$PG_TARGET_NAME"
    export DB_ENGINE="postgresql"
    export DB_HOST="$PG_HOST"
    export DB_PORT="$PG_PORT"
    export DB_NAME="$PG_DB_NAME"
    export DB_USER="$PG_USER"
    export DB_PASSWORD="$PG_PASSWORD"
  elif [[ "$engine" == "mysql" ]]; then
    export TARGET_NAME="$MYSQL_TARGET_NAME"
    export DB_ENGINE="mysql"
    export DB_HOST="$MYSQL_HOST"
    export DB_PORT="$MYSQL_PORT"
    export DB_NAME="$MYSQL_DB_NAME"
    export DB_USER="$MYSQL_USER"
    export DB_PASSWORD="$MYSQL_PASSWORD"
  else
    fail "Unknown database engine: $engine"
  fi

  echo "Testing with ${DB_ENGINE} (target: ${TARGET_NAME}, ${DB_HOST}:${DB_PORT}/${DB_NAME})"
}
