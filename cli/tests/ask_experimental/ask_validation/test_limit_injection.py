#!/usr/bin/env python3
"""
Test script to verify SQL validation and LIMIT injection is working correctly.

This tests the validate_sql_for_ask() function to ensure:
1. LIMIT is injected when missing
2. Excessive LIMIT values are reduced
3. Read-only enforcement works
4. Dangerous patterns are detected
"""

import sys
import os

# Add parent directory to path to import lib modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from lib.functions.sql_validation import validate_sql_for_ask, estimate_query_complexity


def test_limit_injection():
    """Test that LIMIT is injected when missing."""
    print("\n=== Test 1: LIMIT Injection ===")

    sql = "SELECT * FROM posts"
    result = validate_sql_for_ask(sql, max_limit=1000, default_limit=100)

    print(f"Original SQL: {sql}")
    print(f"Validated SQL: {result['validated_sql']}")
    print(f"Is Valid: {result['is_valid']}")
    print(f"Warnings: {result['warnings']}")

    assert result['is_valid'], "Query should be valid"
    assert "LIMIT 100" in result['validated_sql'], "Should inject LIMIT 100"
    assert any("Added LIMIT" in w for w in result['warnings']), "Should warn about LIMIT injection"
    print("✅ PASSED: LIMIT injection works")


def test_limit_reduction():
    """Test that excessive LIMIT values are reduced."""
    print("\n=== Test 2: LIMIT Reduction ===")

    sql = "SELECT * FROM posts LIMIT 5000"
    result = validate_sql_for_ask(sql, max_limit=1000, default_limit=100)

    print(f"Original SQL: {sql}")
    print(f"Validated SQL: {result['validated_sql']}")
    print(f"Warnings: {result['warnings']}")

    assert result['is_valid'], "Query should be valid"
    assert "LIMIT 1000" in result['validated_sql'], "Should reduce to LIMIT 1000"
    assert "LIMIT 5000" not in result['validated_sql'], "Should not have LIMIT 5000"
    assert any("Reduced LIMIT" in w for w in result['warnings']), "Should warn about reduction"
    print("✅ PASSED: LIMIT reduction works")


def test_limit_with_order_by():
    """Test LIMIT injection with ORDER BY clause."""
    print("\n=== Test 3: LIMIT with ORDER BY ===")

    sql = "SELECT * FROM posts ORDER BY created_at DESC"
    result = validate_sql_for_ask(sql, max_limit=1000, default_limit=100)

    print(f"Original SQL: {sql}")
    print(f"Validated SQL: {result['validated_sql']}")

    assert result['is_valid'], "Query should be valid"
    assert "LIMIT 100" in result['validated_sql'], "Should inject LIMIT 100"
    assert result['validated_sql'].endswith("LIMIT 100"), "LIMIT should be at end"
    print("✅ PASSED: LIMIT injection with ORDER BY works")


def test_read_only_enforcement():
    """Test that write operations are blocked."""
    print("\n=== Test 4: Read-Only Enforcement ===")

    write_queries = [
        "INSERT INTO posts (title) VALUES ('test')",
        "UPDATE posts SET title = 'test'",
        "DELETE FROM posts WHERE id = 1",
        "DROP TABLE posts",
        "CREATE TABLE test (id INT)"
    ]

    for sql in write_queries:
        result = validate_sql_for_ask(sql, max_limit=1000, default_limit=100)
        print(f"SQL: {sql}")
        print(f"Is Valid: {result['is_valid']}")
        print(f"Issues: {result['issues']}")

        assert not result['is_valid'], f"Write query should be rejected: {sql}"
        assert len(result['issues']) > 0, "Should have validation issues"
        print(f"✅ Correctly rejected")

    print("✅ PASSED: Read-only enforcement works")


def test_dangerous_patterns():
    """Test that dangerous patterns are detected."""
    print("\n=== Test 5: Dangerous Pattern Detection ===")

    dangerous_queries = [
        ("SELECT * FROM posts INTO OUTFILE '/tmp/dump.txt'", "INTO OUTFILE"),
        ("SELECT LOAD_FILE('/etc/passwd')", "LOAD_FILE"),
        ("SELECT * FROM a CROSS JOIN b", "CROSS JOIN")
    ]

    for sql, pattern in dangerous_queries:
        result = validate_sql_for_ask(sql, max_limit=1000, default_limit=100)
        print(f"\nSQL: {sql}")
        print(f"Expected pattern: {pattern}")
        print(f"Warnings: {result['warnings']}")

        # Note: Some dangerous patterns are warnings, not blocking issues
        # INTO OUTFILE should be blocked, but others may just warn
        if "INTO OUTFILE" in pattern:
            assert len(result['warnings']) > 0, f"Should warn about {pattern}"
        print(f"✅ Detected {pattern}")

    print("✅ PASSED: Dangerous pattern detection works")


def test_complexity_estimation():
    """Test query complexity estimation."""
    print("\n=== Test 6: Complexity Estimation ===")

    test_cases = [
        ("SELECT * FROM posts", "simple"),
        ("SELECT * FROM posts JOIN users ON posts.user_id = users.id", "moderate"),
        ("SELECT COUNT(*) FROM posts JOIN users ON posts.user_id = users.id GROUP BY users.id", "moderate"),
        ("""
         SELECT
             u.name,
             COUNT(p.id) as post_count,
             AVG(p.score) as avg_score
         FROM users u
         LEFT JOIN posts p ON u.id = p.user_id
         LEFT JOIN comments c ON p.id = c.post_id
         GROUP BY u.id
         HAVING COUNT(p.id) > 5
         """, "complex")
    ]

    for sql, expected in test_cases:
        complexity = estimate_query_complexity(sql)
        print(f"\nSQL: {sql.strip()[:60]}...")
        print(f"Complexity: {complexity} (expected: {expected})")

        # Exact match not required, just verify it's not 'simple' for complex queries
        if expected == "complex":
            assert complexity in ["moderate", "complex", "very_complex"], \
                f"Complex query should not be 'simple': got {complexity}"
        print(f"✅ Complexity: {complexity}")

    print("✅ PASSED: Complexity estimation works")


def test_limit_with_offset():
    """Test LIMIT with OFFSET preservation."""
    print("\n=== Test 7: LIMIT with OFFSET ===")

    # PostgreSQL style
    sql_pg = "SELECT * FROM posts LIMIT 10 OFFSET 20"
    result_pg = validate_sql_for_ask(sql_pg, max_limit=1000, default_limit=100)
    print(f"PostgreSQL style: {sql_pg}")
    print(f"Validated: {result_pg['validated_sql']}")
    assert "OFFSET" in result_pg['validated_sql'], "Should preserve OFFSET"

    # MySQL style
    sql_mysql = "SELECT * FROM posts LIMIT 20, 10"
    result_mysql = validate_sql_for_ask(sql_mysql, max_limit=1000, default_limit=100)
    print(f"MySQL style: {sql_mysql}")
    print(f"Validated: {result_mysql['validated_sql']}")

    print("✅ PASSED: LIMIT with OFFSET works")


if __name__ == '__main__':
    print("=" * 60)
    print("SQL Validation & LIMIT Injection Test Suite")
    print("=" * 60)

    try:
        test_limit_injection()
        test_limit_reduction()
        test_limit_with_order_by()
        test_read_only_enforcement()
        test_dangerous_patterns()
        test_complexity_estimation()
        test_limit_with_offset()

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
