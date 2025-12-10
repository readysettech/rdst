#!/bin/bash
set -e

export IMDB_POSTGRES_PASSWORD="7349aeac9f316c8b2f585721"
export RDST_LLM_SHARED_KEY="ALPHA-STATIC-SHARED-KEY"
TMP_RUN=$(mktemp -d)
PASS_COUNT=0
FAIL_COUNT=0

pass() {
    echo "✓ PASS: $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
    echo "✗ FAIL: $1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

echo "=========================================="
echo "RDST Local Integration Tests"
echo "=========================================="

# Clean slate
rm -rf ~/.rdst 2>/dev/null || true

# Configure target
echo ""
echo "=== SETUP: Configure target ==="
python3 rdst.py configure add --target test-db-pg --host 3.143.167.48 --port 8201 --user postgres --password-env IMDB_POSTGRES_PASSWORD --database testdb --engine postgresql --default 2>&1
echo "Target configured"

# TEST 1: Analyze inline query
echo ""
echo "=== TEST 1: Analyze inline query ==="
python3 rdst.py analyze --target test-db-pg --query "SELECT tb.titleType, COUNT(*) AS count FROM title_basics tb JOIN title_ratings tr ON tb.tconst = tr.tconst WHERE tr.numVotes > 1000 GROUP BY tb.titleType ORDER BY count DESC LIMIT 25" --save-as "film-popularity" --fast 2>&1 | tee "$TMP_RUN/test1.txt"
if grep -q "RDST Query Analysis" "$TMP_RUN/test1.txt"; then pass "Analyze inline query"; else fail "Analyze inline query"; fi

# TEST 2: Analyze by name
echo ""
echo "=== TEST 2: Analyze by --name ==="
python3 rdst.py analyze --name "film-popularity" --fast 2>&1 | tee "$TMP_RUN/test2.txt"
if grep -q "RDST Query Analysis" "$TMP_RUN/test2.txt"; then pass "Analyze by --name"; else fail "Analyze by --name"; fi

# TEST 3: Analyze by hash
echo ""
echo "=== TEST 3: Analyze by --hash ==="
HASH=$(python3 rdst.py list --limit 1 2>&1 | grep -oE '[a-f0-9]{8}' | head -1)
echo "Using hash: $HASH"
python3 rdst.py analyze --hash "$HASH" --fast 2>&1 | tee "$TMP_RUN/test3.txt"
if grep -q "RDST Query Analysis" "$TMP_RUN/test3.txt"; then pass "Analyze by --hash"; else fail "Analyze by --hash"; fi

# TEST 4: List command
echo ""
echo "=== TEST 4: List command ==="
python3 rdst.py list 2>&1 | tee "$TMP_RUN/test4.txt"
if grep -q "Query Registry" "$TMP_RUN/test4.txt"; then pass "List command"; else fail "List command"; fi

# TEST 5: List with --target filter
echo ""
echo "=== TEST 5: List with --target filter ==="
python3 rdst.py list --target test-db-pg 2>&1 | tee "$TMP_RUN/test5.txt"
if grep -q "Query Registry" "$TMP_RUN/test5.txt"; then pass "List with --target"; else fail "List with --target"; fi

# TEST 6: Query add
echo ""
echo "=== TEST 6: Query add ==="
python3 rdst.py query add test-movie-query --query "SELECT * FROM title_basics WHERE titleType = 'movie' LIMIT 5" --target test-db-pg 2>&1 | tee "$TMP_RUN/test6.txt"
if grep -q "Query added" "$TMP_RUN/test6.txt"; then pass "Query add"; else fail "Query add"; fi

# TEST 7: Query list shows added query
echo ""
echo "=== TEST 7: Query list shows added query ==="
python3 rdst.py query list 2>&1 | tee "$TMP_RUN/test7.txt"
if grep -q "test-movie-query" "$TMP_RUN/test7.txt"; then pass "Query list shows added query"; else fail "Query list shows added query"; fi

# TEST 8: Analyze from file
echo ""
echo "=== TEST 8: Analyze from file ==="
echo "SELECT titleType, COUNT(*) AS count FROM title_basics GROUP BY titleType ORDER BY count DESC LIMIT 10;" > "$TMP_RUN/query.sql"
python3 rdst.py analyze --fast --target test-db-pg --file "$TMP_RUN/query.sql" 2>&1 | tee "$TMP_RUN/test8.txt"
if grep -q "RDST Query Analysis" "$TMP_RUN/test8.txt"; then pass "Analyze from file"; else fail "Analyze from file"; fi

# TEST 9: Query delete
echo ""
echo "=== TEST 9: Query delete ==="
python3 rdst.py query delete test-movie-query --force 2>&1 | tee "$TMP_RUN/test9.txt"
if grep -q "Query deleted" "$TMP_RUN/test9.txt"; then pass "Query delete"; else fail "Query delete"; fi

# TEST 10: Verify deletion
echo ""
echo "=== TEST 10: Verify query deleted ==="
python3 rdst.py query list 2>&1 | tee "$TMP_RUN/test10.txt"
if grep -q "test-movie-query" "$TMP_RUN/test10.txt"; then fail "Query should be deleted"; else pass "Query deleted successfully"; fi

# Summary
echo ""
echo "=========================================="
echo "TEST SUMMARY"
echo "=========================================="
echo "Passed: $PASS_COUNT"
echo "Failed: $FAIL_COUNT"
echo ""

if [ $FAIL_COUNT -eq 0 ]; then
    echo "✓ ALL TESTS PASSED"
    exit 0
else
    echo "✗ SOME TESTS FAILED"
    exit 1
fi
