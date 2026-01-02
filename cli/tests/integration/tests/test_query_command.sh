#!/usr/bin/env bash

test_query_commands() {
  log_section "Query Command Tests (${DB_ENGINE})"

  test_query_add
  test_query_list
  test_query_show
  test_query_edit
  test_query_edit_with_hash
  test_query_delete
  test_query_delete_with_hash
  test_query_bulk_import
  test_query_integration_with_analyze
  test_query_empty_registry
}

test_query_add() {
  log_section "1. Query Command - Add (${DB_ENGINE})"

  # Test adding a query with inline SQL
  local test_query="SELECT * FROM title_basics WHERE titleType = 'movie' LIMIT 5"
  run_cmd "Add query with inline SQL" \
    "${RDST_CMD[@]}" query add test-movie-query \
    --query "$test_query" \
    --target "$TARGET_NAME"
  assert_contains "✓ Query added" "query add should succeed"
  assert_regex "test-movie" "query add should show query name"

  # Verify the query is in the registry
  run_cmd "Verify query was added" \
    "${RDST_CMD[@]}" query list
  assert_regex "test-movie" "query list should show added query"

  # Test duplicate query name (should fail)
  run_expect_fail "Add duplicate query name" \
    "${RDST_CMD[@]}" query add test-movie-query \
    --query "SELECT 1"
  assert_contains "already exists" "duplicate query name should error"

  # Test adding query from file
  local query_file="$TMP_RUN/test_query.sql"
  cat >"$query_file" <<'SQL'
SELECT tconst, primaryTitle, startYear
FROM title_basics
WHERE startYear = 2020
LIMIT 10;
SQL

  run_cmd "Add query from file" \
    "${RDST_CMD[@]}" query add test-from-file \
    --file "$query_file" \
    --target "$TARGET_NAME"
  assert_contains "✓ Query added" "query add from file should succeed"
  assert_regex "test-from" "query add should show file-based query name"
}

test_query_list() {
  log_section "2. Query Command - List (${DB_ENGINE})"

  # List queries
  run_cmd "List all queries" \
    "${RDST_CMD[@]}" query list
  assert_regex "test-movie" "list should show first query"
  assert_regex "test-from" "list should show second query"

  # List with limit
  run_cmd "List queries with limit" \
    "${RDST_CMD[@]}" query list --limit 1
  # Should show at least one query
  assert_regex "Query Registry|test-" "list with limit should show queries"
}

test_query_show() {
  log_section "3. Query Command - Show (${DB_ENGINE})"

  run_cmd "Show query details" \
    "${RDST_CMD[@]}" query show test-movie-query
  assert_regex "test-movie" "show should display query name"
  assert_contains "Hash:" "show should display query hash"
  assert_contains "SELECT" "show should display SQL"
  assert_contains "title_basics" "show should display full query"
}

test_query_edit() {
  log_section "4. Query Command - Edit (${DB_ENGINE})"

  # Note: Edit command requires $EDITOR, which may not be available in test environment
  # Test that the command recognizes the query name
  echo "Note: Full edit testing requires interactive editor, testing query lookup only"

  # Test edit with non-existent query (should fail)
  run_expect_fail "Edit non-existent query" \
    "${RDST_CMD[@]}" query edit nonexistent-query
  assert_contains "No query found" "edit should fail for non-existent query"
}

test_query_edit_with_hash() {
  log_section "4b. Query Command - Edit with --hash (${DB_ENGINE})"

  # Get hash of test-movie-query
  local query_hash
  query_hash=$(latest_hash_from_list)

  if [[ -z "$query_hash" ]]; then
    echo "Warning: No queries in registry, skipping --hash edit test"
    return 0
  fi

  # Test edit with --hash and non-existent hash (should fail)
  run_expect_fail "Edit with non-existent hash" \
    "${RDST_CMD[@]}" query edit --hash "nonexistent123"
  assert_contains "No query found" "edit --hash should fail for non-existent hash"

  echo "✓ Edit with --hash argument validated"
}

test_query_delete() {
  log_section "5. Query Command - Delete (${DB_ENGINE})"

  # Delete with --force flag (skip confirmation)
  run_cmd "Delete query with force flag" \
    "${RDST_CMD[@]}" query delete test-from-file --force
  assert_contains "✓ Query deleted" "delete should succeed"

  # Verify deletion
  run_cmd "Verify query was deleted" \
    "${RDST_CMD[@]}" query list
  assert_not_contains "test-from" "deleted query should not appear in list"
  assert_regex "test-movie" "other query should still exist"

  # Try to delete non-existent query (should fail)
  run_expect_fail "Delete non-existent query" \
    "${RDST_CMD[@]}" query delete nonexistent-query --force
  assert_contains "No query found" "delete should fail for non-existent query"
}

test_query_delete_with_hash() {
  log_section "5b. Query Command - Delete with --hash (${DB_ENGINE})"

  # Add a query specifically for hash deletion test
  run_cmd "Add query for hash deletion test" \
    "${RDST_CMD[@]}" query add test-hash-delete \
    --query "SELECT * FROM title_basics WHERE startYear = 2021 LIMIT 5" \
    --target "$TARGET_NAME"
  assert_contains "✓ Query added" "query add should succeed"

  # Get the hash
  local query_hash
  query_hash=$(latest_hash_from_list)

  if [[ -z "$query_hash" ]]; then
    fail "Failed to get query hash for deletion test"
  fi

  # Delete using --hash
  run_cmd "Delete query using --hash" \
    "${RDST_CMD[@]}" query delete --hash "$query_hash" --force
  assert_contains "✓ Query deleted" "delete --hash should succeed"
  assert_contains "$query_hash" "delete should show the hash"

  # Try to delete with non-existent hash (should fail)
  run_expect_fail "Delete with non-existent hash" \
    "${RDST_CMD[@]}" query delete --hash "nonexistent123" --force
  assert_contains "No query found" "delete --hash should fail for non-existent hash"

  echo "✓ Delete with --hash argument validated"
}

test_query_bulk_import() {
  log_section "5c. Query Command - Bulk Import (${DB_ENGINE})"

  # Create test SQL file with multiple queries
  local import_file="$TMP_RUN/bulk_import.sql"
  cat >"$import_file" <<'SQL'
-- name: import-test-1
-- target: test-import
-- frequency: 100
SELECT tconst, primaryTitle, titleType FROM title_basics WHERE titleType = 'movie' ORDER BY startYear DESC LIMIT 5;

-- name: import-test-2
-- target: test-import
SELECT COUNT(*) FROM title_basics WHERE startYear > 2020;

-- name: import-test-3
SELECT tconst, primaryTitle FROM title_basics WHERE startYear = 2019 LIMIT 3;
SQL

  # Test import
  run_cmd "Import queries from SQL file" \
    "${RDST_CMD[@]}" query import "$import_file"
  assert_contains "✓ Import complete" "import should succeed"
  assert_contains "3 imported" "should import 3 queries"

  # Verify queries were imported
  run_cmd "Verify imported queries in registry" \
    "${RDST_CMD[@]}" query list
  assert_contains "import-test-1" "first imported query should be in registry"
  assert_contains "import-test-2" "second imported query should be in registry"
  assert_contains "import-test-3" "third imported query should be in registry"

  # Test duplicate handling (should skip)
  run_cmd "Import same file again (test duplicate handling)" \
    "${RDST_CMD[@]}" query import "$import_file"
  assert_contains "✓ Import complete" "re-import should succeed"
  assert_contains "3 skipped" "should skip all duplicates"

  # Test update mode
  run_cmd "Import with --update flag" \
    "${RDST_CMD[@]}" query import "$import_file" --update
  assert_contains "✓ Import complete" "import --update should succeed"
  assert_contains "3 updated" "should update 3 queries"

  # Test with non-existent file (should fail)
  run_expect_fail "Import from non-existent file" \
    "${RDST_CMD[@]}" query import "/nonexistent/file.sql"
  assert_contains "File not found" "import should fail with file not found error"

  # Clean up imported queries
  "${RDST_CMD[@]}" query delete import-test-1 --force >/dev/null 2>&1 || true
  "${RDST_CMD[@]}" query delete import-test-2 --force >/dev/null 2>&1 || true
  "${RDST_CMD[@]}" query delete import-test-3 --force >/dev/null 2>&1 || true

  echo "✓ Bulk import feature validated"
}

test_query_integration_with_analyze() {
  log_section "6. Query Command - Integration with Analyze (${DB_ENGINE})"

  # Add a query using query command
  local integration_query="SELECT tconst, primaryTitle FROM title_basics WHERE startYear = 2019 LIMIT 3"
  run_cmd "Add query for integration test" \
    "${RDST_CMD[@]}" query add integration-test \
    --query "$integration_query" \
    --target "$TARGET_NAME"
  assert_contains "✓ Query added" "query add should succeed"

  # Analyze using the name
  run_cmd "Analyze query by name" \
    "${RDST_CMD[@]}" analyze --name integration-test
  assert_contains "RDST Query Analysis" "analyze should work with query name"
  assert_contains "title_basics" "analyze should show query content"

  # Clean up
  run_cmd "Delete integration test query" \
    "${RDST_CMD[@]}" query delete integration-test --force
  assert_contains "✓ Query deleted" "cleanup delete should succeed"
}

test_query_empty_registry() {
  log_section "7. Query Command - Error Handling (${DB_ENGINE})"

  # Try to show non-existent query
  run_expect_fail "Show non-existent query" \
    "${RDST_CMD[@]}" query show nonexistent-query-xyz
  assert_contains "No query found" "show should fail gracefully for non-existent query"

  # Try to delete non-existent query (already tested above, but verify again)
  run_expect_fail "Delete non-existent query again" \
    "${RDST_CMD[@]}" query delete another-nonexistent --force
  assert_contains "No query found" "delete should fail gracefully for non-existent query"

  # Clean up: delete the query we added at the start
  run_cmd "Clean up test-movie-query" \
    "${RDST_CMD[@]}" query delete test-movie-query --force
  assert_contains "✓ Query deleted" "cleanup delete should succeed"

  # Verify it's gone
  run_expect_fail "Verify cleanup worked" \
    "${RDST_CMD[@]}" query show test-movie-query
  assert_contains "No query found" "deleted query should not be found"
}
