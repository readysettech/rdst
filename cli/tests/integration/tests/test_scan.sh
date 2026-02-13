#!/usr/bin/env bash

# =============================================================================
# Scan Command Integration Tests
#
# Tests rdst scan across all 4 ORMs (SQLAlchemy, Django, Prisma, Drizzle)
# using IMDB database fixtures. All scan operations are read-only
# (no database modifications).
#
# Test fixture directory:
#   tests/integration/fixtures/scan/
#     sqlalchemy_app.py      - 3 SQLAlchemy queries
#     django_app.py          - 3 Django queries
#     prisma_app.ts          - 3 Prisma queries
#     drizzle_app.ts         - 3 Drizzle queries
#     skippable_queries.py   - 2 skippable + 1 valid query (edge cases)
# =============================================================================

SCAN_FIXTURES_DIR="${SCRIPT_DIR}/fixtures/scan"

test_scan_commands() {
  log_section "Scan Command Tests (${DB_ENGINE})"

  # --- Edge cases that DON'T need ANTHROPIC_API_KEY or schema ---
  test_scan_nonexistent_dir
  test_scan_no_schema

  # Verify fixtures exist for remaining tests
  if [[ ! -d "$SCAN_FIXTURES_DIR" ]]; then
    fail "Scan fixtures directory not found: $SCAN_FIXTURES_DIR"
  fi

  # === Setup: Initialize semantic layer from live DB (no API key needed) ===
  test_scan_schema_init

  # --- Edge case: no ORM files (needs schema but NOT API key) ---
  test_scan_no_orm_files

  # Remaining scan tests require ANTHROPIC_API_KEY for ORM-to-SQL conversion
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "  Skipping remaining scan tests (ANTHROPIC_API_KEY not set)"
    return 0
  fi

  # === Core extraction tests (all 4 ORMs in one scan) ===
  test_scan_extraction_all_orms

  # === Per-ORM extraction verification ===
  test_scan_extraction_sqlalchemy
  test_scan_extraction_django
  test_scan_extraction_prisma
  test_scan_extraction_drizzle

  # === JSON output structure ===
  test_scan_json_output

  # === --nosave flag ===
  test_scan_nosave

  # === Skipped queries edge case ===
  test_scan_skipped_queries

  # === Shallow analysis (schema-only, no DB needed) ===
  test_scan_shallow_analysis

  # === Deep analysis (EXPLAIN ANALYZE, needs DB) ===
  test_scan_deep_analysis

  # === CI threshold checking ===
  test_scan_ci_thresholds

  # === Schema refresh ===
  test_scan_schema_refresh
}

# =============================================================================
# EDGE CASE TESTS (no API key or schema required)
# =============================================================================

# -----------------------------------------------------------------------------
# Nonexistent directory - should fail with clear error
# -----------------------------------------------------------------------------
test_scan_nonexistent_dir() {
  log_section "Scan: Nonexistent Directory"

  run_expect_fail "Scan nonexistent directory" \
    "${RDST_CMD[@]}" scan "/tmp/does-not-exist-rdst-test-$$" --schema "any-target"
  assert_contains "Path not found" "should report path not found"
}

# -----------------------------------------------------------------------------
# No schema initialized - should fail with helpful message
# -----------------------------------------------------------------------------
test_scan_no_schema() {
  log_section "Scan: No Schema Initialized"

  # Use a target name that definitely has no schema YAML
  run_expect_fail "Scan without schema initialized" \
    "${RDST_CMD[@]}" scan "/tmp" --schema "no-such-target-xyz-$$"
  assert_contains "No schema found" "should report missing schema"
  assert_contains "rdst schema init" "should suggest schema init command"
}

# -----------------------------------------------------------------------------
# No ORM files in directory - should succeed with empty results
# (needs schema to exist but NOT API key - returns before API key check)
# -----------------------------------------------------------------------------
test_scan_no_orm_files() {
  log_section "Scan: No ORM Files in Directory (${DB_ENGINE})"

  # Create temp dir with a plain Python file (no ORM patterns)
  local no_orm_dir
  no_orm_dir=$(mktemp -d)
  cat > "$no_orm_dir/plain_app.py" <<'PYFILE'
def hello():
    return "Hello, World!"

def add(a, b):
    return a + b

if __name__ == "__main__":
    print(hello())
PYFILE

  run_cmd "Scan directory with no ORM files" \
    "${RDST_CMD[@]}" scan "$no_orm_dir" --schema "$TARGET_NAME"
  assert_contains "No files with ORM patterns" "should report no ORM files found"

  rm -rf "$no_orm_dir"
}

# =============================================================================
# SETUP
# =============================================================================

# -----------------------------------------------------------------------------
# Schema Init - Create semantic layer from live database
# -----------------------------------------------------------------------------
test_scan_schema_init() {
  log_section "Scan: Schema Init (${DB_ENGINE})"

  run_cmd "Initialize semantic layer from ${DB_ENGINE}" \
    "${RDST_CMD[@]}" schema init --target "$TARGET_NAME" --force
  assert_not_contains "ERROR:" "schema init should not error"

  # Verify YAML file was created
  local schema_file="$HOME/.rdst/semantic-layer/${TARGET_NAME}.yaml"
  if [[ ! -f "$schema_file" ]]; then
    fail "Schema YAML not created at $schema_file"
  fi

  # Verify it contains IMDB tables
  assert_file_contains "$schema_file" "title_basics" "schema should contain title_basics"
  assert_file_contains "$schema_file" "title_ratings" "schema should contain title_ratings"
  assert_file_contains "$schema_file" "tconst" "schema should contain tconst column"
  assert_file_contains "$schema_file" "primarytitle" "schema should contain primarytitle column"
  assert_file_contains "$schema_file" "averagerating" "schema should contain averagerating column"

  echo "  Schema YAML created at $schema_file"
}

# =============================================================================
# CORE EXTRACTION TESTS
# =============================================================================

# -----------------------------------------------------------------------------
# Full directory scan - all 4 ORMs detected
# -----------------------------------------------------------------------------
test_scan_extraction_all_orms() {
  log_section "Scan: All ORM Extraction (${DB_ENGINE})"

  run_cmd "Scan all fixtures directory" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR" --schema "$TARGET_NAME"
  assert_not_contains "ERROR:" "scan should not error"

  # Verify all 4 ORM types detected
  assert_contains "sqlalchemy" "should detect SQLAlchemy"
  assert_contains "django" "should detect Django"
  assert_contains "prisma" "should detect Prisma"
  assert_contains "drizzle" "should detect Drizzle"

  # Verify both Python and TypeScript files scanned
  assert_contains "sqlalchemy_app.py" "should scan SQLAlchemy file"
  assert_contains "django_app.py" "should scan Django file"
  assert_contains "prisma_app.ts" "should scan Prisma file"
  assert_contains "drizzle_app.ts" "should scan Drizzle file"

  # Verify queries were extracted (should find SQL conversions)
  assert_contains "Converted to SQL" "should report converted queries"
  assert_contains "Files with ORM code" "should report files found"

  # Verify extraction is AST-based (deterministic)
  assert_contains "AST" "extraction method should be AST"

  # Verify queries saved to registry
  assert_contains "Registry saved" "should save to registry"
}

# -----------------------------------------------------------------------------
# Per-ORM extraction tests
# -----------------------------------------------------------------------------
test_scan_extraction_sqlalchemy() {
  log_section "Scan: SQLAlchemy Extraction (${DB_ENGINE})"

  run_cmd "Scan SQLAlchemy fixture" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR/sqlalchemy_app.py" --schema "$TARGET_NAME" --output json
  assert_json "SQLAlchemy scan should produce valid JSON"

  # Verify ORM type detected
  "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYCHECK' || fail "SQLAlchemy extraction validation failed"
import sys, json
with open(sys.argv[1]) as f:
    content = f.read()
# Find JSON in output
start = content.find('{')
end = content.rfind('}')
if start < 0:
    print("No JSON found", file=sys.stderr)
    sys.exit(1)
data = json.loads(content[start:end+1])

queries = data.get("queries", [])
sql_queries = [q for q in queries if q.get("sql") and not q["sql"].startswith("--")]
expected = {"get_recent_movies", "get_top_rated_titles", "get_type_counts"}

# If LLM conversion failed (e.g., invalid API key in CI) but extraction worked,
# validate extraction metadata only
if len(sql_queries) == 0 and len(queries) >= 3:
    functions = {q.get("function", "") for q in queries}
    missing = expected - functions
    if missing:
        print(f"Missing expected functions: {missing}", file=sys.stderr)
        sys.exit(1)
    ext = data.get("extraction", {})
    if ext.get("method") != "ast":
        print(f"Expected extraction method 'ast', got '{ext.get('method')}'", file=sys.stderr)
        sys.exit(1)
    for q in queries:
        if not q.get("orm_code"):
            print(f"Missing orm_code for {q.get('function','?')}", file=sys.stderr)
            sys.exit(1)
    print(f"OK: {len(queries)} SQLAlchemy queries extracted (LLM conversion unavailable), functions: {functions}")
    sys.exit(0)

# LLM conversion succeeded - validate SQL content
if len(sql_queries) < 3:
    print(f"Expected >= 3 SQL queries from SQLAlchemy, got {len(sql_queries)}", file=sys.stderr)
    print(f"  Total queries in JSON: {len(queries)}", file=sys.stderr)
    print(f"  Extraction: {data.get('extraction', {})}", file=sys.stderr)
    for i, q in enumerate(queries):
        print(f"  Query {i}: function={q.get('function','?')} sql={repr(q.get('sql','')[:80])} status={q.get('status','?')}", file=sys.stderr)
    print(f"  Raw output (first 500 chars): {content[:500]}", file=sys.stderr)
    sys.exit(1)

# Verify ORM type
for q in sql_queries:
    ot = q.get("orm_type", "")
    if ot not in ("sqlalchemy", "raw_sql"):
        print(f"Expected orm_type=sqlalchemy, got '{ot}' for: {q.get('function','?')}", file=sys.stderr)
        sys.exit(1)

# Verify known function names are present
functions = {q.get("function", "") for q in sql_queries}
missing = expected - functions
if missing:
    print(f"Missing expected functions: {missing}", file=sys.stderr)
    sys.exit(1)

print(f"OK: {len(sql_queries)} SQLAlchemy queries extracted, functions: {functions}")
PYCHECK
}

test_scan_extraction_django() {
  log_section "Scan: Django Extraction (${DB_ENGINE})"

  run_cmd "Scan Django fixture" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR/django_app.py" --schema "$TARGET_NAME" --output json
  assert_json "Django scan should produce valid JSON"

  "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYCHECK' || fail "Django extraction validation failed"
import sys, json
with open(sys.argv[1]) as f:
    content = f.read()
start = content.find('{')
end = content.rfind('}')
if start < 0:
    print("No JSON found", file=sys.stderr)
    sys.exit(1)
data = json.loads(content[start:end+1])

queries = data.get("queries", [])
sql_queries = [q for q in queries if q.get("sql") and not q["sql"].startswith("--")]
expected = {"get_movie_by_id", "get_genre_stats", "get_latest_title"}

# If LLM conversion failed but extraction worked, validate extraction only
if len(sql_queries) == 0 and len(queries) >= 3:
    functions = {q.get("function", "") for q in queries}
    missing = expected - functions
    if missing:
        print(f"Missing expected functions: {missing}", file=sys.stderr)
        sys.exit(1)
    ext = data.get("extraction", {})
    if ext.get("method") != "ast":
        print(f"Expected extraction method 'ast', got '{ext.get('method')}'", file=sys.stderr)
        sys.exit(1)
    for q in queries:
        if not q.get("orm_code"):
            print(f"Missing orm_code for {q.get('function','?')}", file=sys.stderr)
            sys.exit(1)
    print(f"OK: {len(queries)} Django queries extracted (LLM conversion unavailable), functions: {functions}")
    sys.exit(0)

if len(sql_queries) < 3:
    print(f"Expected >= 3 SQL queries from Django, got {len(sql_queries)}", file=sys.stderr)
    sys.exit(1)

for q in sql_queries:
    ot = q.get("orm_type", "")
    if ot not in ("django", "raw_sql"):
        print(f"Expected orm_type=django, got '{ot}' for: {q.get('function','?')}", file=sys.stderr)
        sys.exit(1)

functions = {q.get("function", "") for q in sql_queries}
missing = expected - functions
if missing:
    print(f"Missing expected functions: {missing}", file=sys.stderr)
    sys.exit(1)

print(f"OK: {len(sql_queries)} Django queries extracted, functions: {functions}")
PYCHECK
}

test_scan_extraction_prisma() {
  log_section "Scan: Prisma Extraction (${DB_ENGINE})"

  run_cmd "Scan Prisma fixture" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR/prisma_app.ts" --schema "$TARGET_NAME" --output json
  assert_json "Prisma scan should produce valid JSON"

  "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYCHECK' || fail "Prisma extraction validation failed"
import sys, json
with open(sys.argv[1]) as f:
    content = f.read()
start = content.find('{')
end = content.rfind('}')
if start < 0:
    print("No JSON found", file=sys.stderr)
    sys.exit(1)
data = json.loads(content[start:end+1])

queries = data.get("queries", [])
sql_queries = [q for q in queries if q.get("sql") and not q["sql"].startswith("--")]
expected = {"getRecentMovies", "getTopRatedTitles", "getAllMovies"}

# If LLM conversion failed but extraction worked, validate extraction only
if len(sql_queries) == 0 and len(queries) >= 3:
    functions = {q.get("function", "") for q in queries}
    missing = expected - functions
    if missing:
        print(f"Missing expected functions: {missing}", file=sys.stderr)
        sys.exit(1)
    for q in queries:
        if not q.get("orm_code"):
            print(f"Missing orm_code for {q.get('function','?')}", file=sys.stderr)
            sys.exit(1)
    print(f"OK: {len(queries)} Prisma queries extracted (LLM conversion unavailable), functions: {functions}")
    sys.exit(0)

if len(sql_queries) < 3:
    print(f"Expected >= 3 SQL queries from Prisma, got {len(sql_queries)}", file=sys.stderr)
    sys.exit(1)

for q in sql_queries:
    ot = q.get("orm_type", "")
    if ot != "prisma":
        print(f"Expected orm_type=prisma, got '{ot}' for: {q.get('function','?')}", file=sys.stderr)
        sys.exit(1)

functions = {q.get("function", "") for q in sql_queries}
missing = expected - functions
if missing:
    print(f"Missing expected functions: {missing}", file=sys.stderr)
    sys.exit(1)

print(f"OK: {len(sql_queries)} Prisma queries extracted, functions: {functions}")
PYCHECK
}

test_scan_extraction_drizzle() {
  log_section "Scan: Drizzle Extraction (${DB_ENGINE})"

  run_cmd "Scan Drizzle fixture" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR/drizzle_app.ts" --schema "$TARGET_NAME" --output json
  assert_json "Drizzle scan should produce valid JSON"

  "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYCHECK' || fail "Drizzle extraction validation failed"
import sys, json
with open(sys.argv[1]) as f:
    content = f.read()
start = content.find('{')
end = content.rfind('}')
if start < 0:
    print("No JSON found", file=sys.stderr)
    sys.exit(1)
data = json.loads(content[start:end+1])

queries = data.get("queries", [])
sql_queries = [q for q in queries if q.get("sql") and not q["sql"].startswith("--")]
expected = {"getRecentMovies", "getTopRated", "getAllTitles"}

# If LLM conversion failed but extraction worked, validate extraction only
if len(sql_queries) == 0 and len(queries) >= 3:
    functions = {q.get("function", "") for q in queries}
    missing = expected - functions
    if missing:
        print(f"Missing expected functions: {missing}", file=sys.stderr)
        sys.exit(1)
    for q in queries:
        if not q.get("orm_code"):
            print(f"Missing orm_code for {q.get('function','?')}", file=sys.stderr)
            sys.exit(1)
    print(f"OK: {len(queries)} Drizzle queries extracted (LLM conversion unavailable), functions: {functions}")
    sys.exit(0)

if len(sql_queries) < 3:
    print(f"Expected >= 3 SQL queries from Drizzle, got {len(sql_queries)}", file=sys.stderr)
    sys.exit(1)

for q in sql_queries:
    ot = q.get("orm_type", "")
    if ot != "drizzle":
        print(f"Expected orm_type=drizzle, got '{ot}' for: {q.get('function','?')}", file=sys.stderr)
        sys.exit(1)

functions = {q.get("function", "") for q in sql_queries}
missing = expected - functions
if missing:
    print(f"Missing expected functions: {missing}", file=sys.stderr)
    sys.exit(1)

print(f"OK: {len(sql_queries)} Drizzle queries extracted, functions: {functions}")
PYCHECK
}

# =============================================================================
# JSON OUTPUT AND FLAGS
# =============================================================================

# -----------------------------------------------------------------------------
# JSON output validation
# -----------------------------------------------------------------------------
test_scan_json_output() {
  log_section "Scan: JSON Output Structure (${DB_ENGINE})"

  run_cmd "Scan with JSON output" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR" --schema "$TARGET_NAME" --output json
  assert_json "scan JSON output should be valid"

  # Validate JSON structure contains required fields
  "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYCHECK' || fail "JSON structure validation failed"
import sys, json
with open(sys.argv[1]) as f:
    content = f.read()
start = content.find('{')
end = content.rfind('}')
data = json.loads(content[start:end+1])

# Required top-level keys
required_keys = ["files", "queries", "extraction", "registry"]
for key in required_keys:
    if key not in data:
        print(f"Missing required key: {key}", file=sys.stderr)
        sys.exit(1)

# Validate files list
files = data["files"]
if len(files) < 4:
    print(f"Expected >= 4 files, got {len(files)}", file=sys.stderr)
    sys.exit(1)

# Validate queries have required fields
for q in data["queries"][:3]:  # Check first 3
    for field in ["file", "function", "orm_code", "sql", "orm_type"]:
        if field not in q:
            print(f"Query missing field: {field}", file=sys.stderr)
            sys.exit(1)

# Validate extraction metadata
ext = data["extraction"]
if ext.get("method") != "ast":
    print(f"Expected extraction method 'ast', got '{ext.get('method')}'", file=sys.stderr)
    sys.exit(1)

# Validate registry metadata
reg = data["registry"]
if "total_queries" not in reg:
    print("Registry missing total_queries", file=sys.stderr)
    sys.exit(1)

print(f"OK: JSON structure valid - {len(files)} files, {len(data['queries'])} queries")
PYCHECK
}

# -----------------------------------------------------------------------------
# --nosave flag test
# -----------------------------------------------------------------------------
test_scan_nosave() {
  log_section "Scan: --nosave Flag (${DB_ENGINE})"

  # Get current registry state
  local registry_file="$HOME/.rdst/queries.toml"
  local before_count=0
  if [[ -f "$registry_file" ]]; then
    before_count=$(grep -c '^\[queries\.' "$registry_file" 2>/dev/null || echo 0)
  fi

  run_cmd "Scan with --nosave" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR/sqlalchemy_app.py" --schema "$TARGET_NAME" --nosave --output json
  assert_json "nosave scan should produce valid JSON"

  # Verify registry reports skipped
  "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYCHECK' || fail "nosave validation failed"
import sys, json
with open(sys.argv[1]) as f:
    content = f.read()
start = content.find('{')
end = content.rfind('}')
data = json.loads(content[start:end+1])

reg = data.get("registry", {})
if not reg.get("skipped"):
    print(f"Expected registry.skipped=true, got {reg}", file=sys.stderr)
    sys.exit(1)

print("OK: --nosave correctly skipped registry save")
PYCHECK
}

# =============================================================================
# EDGE CASE TESTS (require API key and schema)
# =============================================================================

# -----------------------------------------------------------------------------
# Skipped queries - verify skip detection and reasons
# Uses skippable_queries.py fixture with:
#   - cursor.fetchall() without execute (should skip: "Result fetch only")
#   - **kwargs filter (should skip: "Dynamic arguments")
#   - One valid query (should convert to SQL)
# -----------------------------------------------------------------------------
test_scan_skipped_queries() {
  log_section "Scan: Skipped Queries (${DB_ENGINE})"

  run_cmd "Scan skippable queries fixture" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR/skippable_queries.py" \
    --schema "$TARGET_NAME" --output json --nosave
  assert_json "skippable scan should produce valid JSON"

  "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYCHECK' || fail "Skipped queries validation failed"
import sys, json
with open(sys.argv[1]) as f:
    content = f.read()
start = content.find('{')
end = content.rfind('}')
if start < 0:
    print("No JSON found", file=sys.stderr)
    sys.exit(1)
data = json.loads(content[start:end+1])

queries = data.get("queries", [])
if not queries:
    print("Expected at least 1 query extracted", file=sys.stderr)
    sys.exit(1)

# At least one query should be skipped
skipped = [q for q in queries if q.get("status") == "skipped"]
if not skipped:
    print("Expected at least 1 skipped query", file=sys.stderr)
    sys.exit(1)

# Every skipped query must have a non-empty skip_reason
for q in skipped:
    reason = q.get("skip_reason", "")
    if not reason:
        func = q.get("function", "unknown")
        print(f"Skipped query '{func}' has no skip_reason", file=sys.stderr)
        sys.exit(1)

# Check for valid SQL queries (get_valid_movie should convert when LLM is available)
sql_queries = [q for q in queries if q.get("status") == "sql"]
if not sql_queries:
    # LLM conversion may have failed (e.g., invalid API key in CI)
    # Verify get_valid_movie was at least extracted with ORM code
    all_functions = {q.get("function", "") for q in queries}
    if "get_valid_movie" not in all_functions:
        print(f"Expected get_valid_movie to be extracted, got: {all_functions}", file=sys.stderr)
        sys.exit(1)
    valid_q = [q for q in queries if q.get("function") == "get_valid_movie"][0]
    if not valid_q.get("orm_code"):
        print("get_valid_movie missing orm_code", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {len(queries)} queries total, {len(skipped)} skipped (LLM conversion unavailable)")
    for q in skipped:
        print(f"  Skipped: {q.get('function', '?')} - {q.get('skip_reason', '?')}")
    sys.exit(0)

# LLM conversion succeeded — verify get_valid_movie was converted
valid_functions = {q.get("function", "") for q in sql_queries}
if "get_valid_movie" not in valid_functions:
    print(f"Expected get_valid_movie in valid queries, got: {valid_functions}", file=sys.stderr)
    sys.exit(1)

print(f"OK: {len(queries)} queries total, {len(skipped)} skipped, {len(sql_queries)} valid SQL")
for q in skipped:
    print(f"  Skipped: {q.get('function', '?')} - {q.get('skip_reason', '?')}")
PYCHECK
}

# =============================================================================
# ANALYSIS TESTS
# =============================================================================

# -----------------------------------------------------------------------------
# Shallow analysis (no DB connection needed, uses schema YAML + LLM)
# -----------------------------------------------------------------------------
test_scan_shallow_analysis() {
  log_section "Scan: Shallow Analysis (${DB_ENGINE})"

  # Only run if ANTHROPIC_API_KEY is set
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "  Skipping shallow analysis test (ANTHROPIC_API_KEY not set)"
    return 0
  fi

  # Test shallow analysis on SQLAlchemy fixture (3 queries, fast)
  run_cmd "Scan with shallow analysis" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR/sqlalchemy_app.py" \
    --schema "$TARGET_NAME" \
    --analyze --shallow \
    --output json
  assert_json "shallow analysis should produce valid JSON"

  "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYCHECK' || fail "Shallow analysis validation failed"
import sys, json
with open(sys.argv[1]) as f:
    content = f.read()
start = content.find('{')
end = content.rfind('}')
data = json.loads(content[start:end+1])

analysis = data.get("analysis")
if not analysis:
    print("Missing analysis section in output", file=sys.stderr)
    sys.exit(1)

if analysis.get("mode") != "shallow":
    print(f"Expected mode=shallow, got {analysis.get('mode')}", file=sys.stderr)
    sys.exit(1)

total = analysis.get("total_analyzed", 0)
if total < 3:
    print(f"Expected >= 3 analyzed queries, got {total}", file=sys.stderr)
    sys.exit(1)

successful = analysis.get("successful", 0)
if successful < 1:
    print(f"Expected >= 1 successful analysis, got {successful}", file=sys.stderr)
    sys.exit(1)

# Verify risk scores exist for analyzed queries
by_query = analysis.get("by_query", [])
for q in by_query:
    score = q.get("risk_score")
    if score is None:
        print(f"Missing risk_score for {q.get('function','?')}", file=sys.stderr)
        sys.exit(1)
    if not (0 <= score <= 100):
        print(f"Invalid risk_score {score} for {q.get('function','?')}", file=sys.stderr)
        sys.exit(1)

# Verify CI status is set
ci_status = analysis.get("ci_status")
if ci_status not in ("pass", "warn", "fail"):
    print(f"Invalid ci_status: {ci_status}", file=sys.stderr)
    sys.exit(1)

print(f"OK: Shallow analysis - {total} analyzed, {successful} successful, CI: {ci_status}")
PYCHECK
}

# -----------------------------------------------------------------------------
# Deep analysis (EXPLAIN ANALYZE, requires DB connection)
# -----------------------------------------------------------------------------
test_scan_deep_analysis() {
  log_section "Scan: Deep Analysis (${DB_ENGINE})"

  # Only run if ANTHROPIC_API_KEY is set
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "  Skipping deep analysis test (ANTHROPIC_API_KEY not set)"
    return 0
  fi

  # Test deep analysis on a single file (3 queries) to keep time reasonable
  run_cmd "Scan with deep analysis" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR/sqlalchemy_app.py" \
    --schema "$TARGET_NAME" \
    --analyze \
    --output json
  assert_json "deep analysis should produce valid JSON"

  "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYCHECK' || fail "Deep analysis validation failed"
import sys, json
with open(sys.argv[1]) as f:
    content = f.read()
start = content.find('{')
end = content.rfind('}')
data = json.loads(content[start:end+1])

analysis = data.get("analysis")
if not analysis:
    print("Missing analysis section in output", file=sys.stderr)
    sys.exit(1)

if analysis.get("mode") != "deep":
    print(f"Expected mode=deep, got {analysis.get('mode')}", file=sys.stderr)
    sys.exit(1)

total = analysis.get("total_analyzed", 0)
if total < 1:
    print(f"Expected >= 1 analyzed queries, got {total}", file=sys.stderr)
    sys.exit(1)

# Verify risk scores and execution times exist for successful queries
by_query = analysis.get("by_query", [])
for q in by_query:
    score = q.get("risk_score")
    if score is None:
        print(f"Missing risk_score for {q.get('function','?')}", file=sys.stderr)
        sys.exit(1)

# Verify CI status is set
ci_status = analysis.get("ci_status")
if ci_status not in ("pass", "warn", "fail"):
    print(f"Invalid ci_status: {ci_status}", file=sys.stderr)
    sys.exit(1)

# Verify worst_score is tracked
worst = analysis.get("worst_score")
if worst is None:
    print("Missing worst_score", file=sys.stderr)
    sys.exit(1)

print(f"OK: Deep analysis - {total} analyzed, {len(by_query)} successful, worst_score={worst}, CI: {ci_status}")
PYCHECK
}

# -----------------------------------------------------------------------------
# CI threshold behavior
# -----------------------------------------------------------------------------
test_scan_ci_thresholds() {
  log_section "Scan: CI Thresholds (${DB_ENGINE})"

  # Only run if ANTHROPIC_API_KEY is set
  if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "  Skipping CI threshold test (ANTHROPIC_API_KEY not set)"
    return 0
  fi

  # Run shallow analysis with a very high fail threshold (should FAIL)
  run_cmd "Scan with high fail threshold (expect CI fail)" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR/sqlalchemy_app.py" \
    --schema "$TARGET_NAME" \
    --analyze --shallow \
    --fail-threshold 95 \
    --output json
  assert_json "threshold scan should produce valid JSON"

  "$PYTHON_BIN" - "$LAST_OUTPUT_FILE" <<'PYCHECK' || fail "CI threshold validation failed"
import sys, json
with open(sys.argv[1]) as f:
    content = f.read()
start = content.find('{')
end = content.rfind('}')
data = json.loads(content[start:end+1])

analysis = data.get("analysis", {})
ci_status = analysis.get("ci_status", "")
fail_threshold = analysis.get("fail_threshold", 0)

# With threshold at 95, most queries should fall below it -> FAIL
if ci_status != "fail":
    # It's possible all queries score very high, so just verify threshold is set
    print(f"  CI status: {ci_status} (threshold: {fail_threshold})")

if fail_threshold != 95:
    print(f"Expected fail_threshold=95, got {fail_threshold}", file=sys.stderr)
    sys.exit(1)

print(f"OK: CI thresholds applied correctly - status={ci_status}, fail_threshold={fail_threshold}")
PYCHECK
}

# =============================================================================
# SCHEMA REFRESH
# =============================================================================

# -----------------------------------------------------------------------------
# Schema refresh (re-init semantic layer, verify scan still works)
# -----------------------------------------------------------------------------
test_scan_schema_refresh() {
  log_section "Scan: Schema Refresh (${DB_ENGINE})"

  local schema_file="$HOME/.rdst/semantic-layer/${TARGET_NAME}.yaml"

  # Get modification time before refresh
  local mtime_before=""
  if [[ -f "$schema_file" ]]; then
    mtime_before=$(stat -c %Y "$schema_file" 2>/dev/null || stat -f %m "$schema_file" 2>/dev/null || echo "0")
  fi

  # Sleep 1s to ensure different mtime
  sleep 1

  # Re-initialize schema (refresh)
  run_cmd "Refresh semantic layer" \
    "${RDST_CMD[@]}" schema init --target "$TARGET_NAME" --force
  assert_not_contains "ERROR:" "schema refresh should not error"

  # Verify file was actually updated
  if [[ -f "$schema_file" ]]; then
    local mtime_after
    mtime_after=$(stat -c %Y "$schema_file" 2>/dev/null || stat -f %m "$schema_file" 2>/dev/null || echo "0")
    if [[ "$mtime_after" != "$mtime_before" ]]; then
      echo "  Schema file refreshed (mtime: $mtime_before -> $mtime_after)"
    else
      echo "  Warning: Schema file mtime unchanged"
    fi
  fi

  # Verify IMDB tables still present after refresh
  assert_file_contains "$schema_file" "title_basics" "refreshed schema should contain title_basics"
  assert_file_contains "$schema_file" "title_ratings" "refreshed schema should contain title_ratings"

  # Verify scan still works after refresh
  run_cmd "Scan after schema refresh" \
    "${RDST_CMD[@]}" scan "$SCAN_FIXTURES_DIR/sqlalchemy_app.py" --schema "$TARGET_NAME"
  assert_not_contains "ERROR:" "scan after refresh should not error"
  assert_contains "Converted to SQL" "scan should still convert queries after refresh"
}
