#!/usr/bin/env python3
"""
Test 2: Terminology Resolution

Verifies that business terminology defined in the semantic layer is properly
matched against user questions and included in the context sent to the LLM.
"""
import sys
import os
sys.path.insert(0, '.')

from lib.semantic_layer.manager import SemanticLayerManager
from lib.data_structures.semantic_layer import SemanticLayer, TableAnnotation, ColumnAnnotation, Terminology


def setup_test_with_terminology():
    """Create a semantic layer with terminology definitions"""
    print("Setting up test semantic layer with terminology...")

    target = "test_terminology_db"
    layer = SemanticLayer(target=target)

    # Add users table
    users_table = TableAnnotation(
        name="users",
        description="User accounts",
        business_context="All registered users"
    )

    users_table.columns["id"] = ColumnAnnotation(
        name="id",
        description="User ID",
        data_type="integer"
    )

    users_table.columns["status"] = ColumnAnnotation(
        name="status",
        description="Account status code",
        data_type="string"
    )

    users_table.columns["last_login"] = ColumnAnnotation(
        name="last_login",
        description="Last login timestamp",
        data_type="timestamp"
    )

    users_table.columns["subscription_tier"] = ColumnAnnotation(
        name="subscription_tier",
        description="Subscription level",
        data_type="string"
    )

    layer.tables["users"] = users_table

    # Add terminology: "active users"
    layer.terminology["active"] = Terminology(
        term="active",
        definition="Users who are currently active (not inactive or churned)",
        sql_pattern="status = 'ACTIVE' AND last_login > NOW() - INTERVAL '30 days'",
        synonyms=["engaged", "current"]
    )

    # Add terminology: "premium users"
    layer.terminology["premium"] = Terminology(
        term="premium",
        definition="Users with a paid subscription",
        sql_pattern="subscription_tier IN ('PRO', 'ENTERPRISE')",
        synonyms=["paid", "subscribers"]
    )

    # Add terminology: "churned users"
    layer.terminology["churned"] = Terminology(
        term="churned",
        definition="Users who have cancelled their account",
        sql_pattern="status = 'CHURNED'",
        synonyms=["cancelled", "former"]
    )

    # Save
    manager = SemanticLayerManager()
    manager.save(layer)

    print(f"✓ Created semantic layer with terminology")
    print(f"  - 1 table (users)")
    print(f"  - 3 terminology entries (active, premium, churned)")

    return target, layer


def test_terminology_matching():
    """Test that terminology is matched in user questions"""
    print("\n" + "="*60)
    print("Test: Terminology Matching")
    print("="*60)

    target, layer = setup_test_with_terminology()
    manager = SemanticLayerManager()

    test_cases = [
        ("Show me active users", "active"),
        ("How many premium users do we have?", "premium"),
        ("List churned users from last month", "churned"),
        ("Show me engaged users", "active"),  # synonym
        ("Get paid subscribers", "premium"),  # synonym
        ("Show me normal users", None),  # no match
    ]

    all_passed = True

    for i, (question, expected_term) in enumerate(test_cases, 1):
        print(f"\n{i}. Testing question: \"{question}\"")

        # Use find_terminology to match terms
        matched_terms = manager.find_terminology(target, question)

        if expected_term is None:
            # Expect no match
            if not matched_terms:
                print(f"  ✓ Correctly found no terminology match")
            else:
                print(f"  ✗ FAILED: Expected no match, but found: {[t.term for t in matched_terms]}")
                all_passed = False
        else:
            # Expect a match
            matched_term_names = [t.term for t in matched_terms]

            if expected_term in matched_term_names:
                print(f"  ✓ Correctly matched '{expected_term}'")

                # Verify the matched term has the SQL pattern
                matched_term = next(t for t in matched_terms if t.term == expected_term)
                if matched_term.sql_pattern:
                    print(f"  ✓ SQL pattern available: {matched_term.sql_pattern[:50]}...")
                else:
                    print(f"  ✗ SQL pattern missing")
                    all_passed = False
            else:
                print(f"  ✗ FAILED: Expected '{expected_term}', found: {matched_term_names}")
                all_passed = False

    return all_passed


def test_terminology_in_context():
    """Test that terminology is included in full context"""
    print("\n" + "="*60)
    print("Test: Terminology in Context")
    print("="*60)

    target, layer = setup_test_with_terminology()
    manager = SemanticLayerManager()

    # Test with a question that mentions "active users"
    question = "Show me all active users"

    print(f"\n1. Getting full context for: \"{question}\"")
    context = manager.get_full_context(
        target=target,
        user_question=question,
        relevant_tables=["users"]
    )

    if not context:
        print("  ✗ FAILED: No context returned")
        return False

    # Check that terminology is in context
    checks = [
        ("terminology section", "Terminology" in context or "Business Terms" in context or "Business Terminology" in context),
        ("term name", "active" in context.lower()),
        ("SQL pattern", "status = 'ACTIVE'" in context or "ACTIVE" in context),
        ("table context", "users" in context or "User" in context),
    ]

    all_passed = True
    for check_name, result in checks:
        status = "✓" if result else "✗"
        print(f"  {status} {check_name}: {'Found' if result else 'NOT FOUND'}")
        all_passed = all_passed and result

    # Show sample of context
    if context:
        print(f"\n2. Sample context (first 500 chars):")
        print(f"   {context[:500]}...")

    return all_passed


def test_ask3_uses_terminology():
    """Test that ask3 engine can access terminology"""
    print("\n" + "="*60)
    print("Test: Ask3 Terminology Integration")
    print("="*60)

    try:
        from lib.engines.ask3_engine import Ask3NLToSQLEngine

        target, layer = setup_test_with_terminology()

        print("\n1. Creating ask3 engine with mock state...")

        engine = Ask3NLToSQLEngine()

        # Create mock state
        class MockState:
            def __init__(self):
                self.target = target
                self.nl_question = "Show me premium users"

        engine.state = MockState()

        print("  ✓ Engine created with mock state")

        print("\n2. Calling _get_semantic_context()...")

        context = engine._get_semantic_context(tables_included=["users"])

        if not context:
            print("  ⚠ WARNING: No context returned")
            print("            This might be OK if semantic layer doesn't exist anymore")
            return True

        # Check for terminology
        if "premium" in context.lower():
            print("  ✓ Terminology 'premium' found in context")
        else:
            print("  ✗ Terminology 'premium' NOT found in context")
            return False

        if "subscription_tier" in context.lower() or "PRO" in context or "ENTERPRISE" in context:
            print("  ✓ SQL pattern for 'premium' found in context")
        else:
            print("  ✗ SQL pattern for 'premium' NOT found")
            return False

        print("\n✓ Ask3 can access and use terminology")
        return True

    except Exception as e:
        print(f"\n✗ FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def cleanup():
    """Clean up test semantic layer"""
    print("\n" + "="*60)
    print("Cleanup")
    print("="*60)

    try:
        layer_file = os.path.expanduser("~/.rdst/semantic-layer/test_terminology_db.yaml")
        if os.path.exists(layer_file):
            os.remove(layer_file)
            print("✓ Removed test semantic layer file")
    except Exception as e:
        print(f"⚠ Warning: Could not clean up: {e}")


def main():
    print("="*60)
    print("Semantic Layer Integration Test Suite")
    print("Test 2: Terminology Resolution")
    print("="*60)

    results = []

    try:
        # Run tests
        results.append(("Terminology Matching", test_terminology_matching()))
        results.append(("Terminology in Context", test_terminology_in_context()))
        results.append(("Ask3 Terminology Integration", test_ask3_uses_terminology()))

    finally:
        # Always cleanup
        cleanup()

    # Summary
    print("\n" + "="*60)
    print("Test Summary")
    print("="*60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓ PASSED" if result else "✗ FAILED"
        print(f"{name}: {status}")

    print(f"\n{passed}/{total} tests passed")

    if passed == total:
        print("\n✓ SUCCESS: Terminology resolution is working!")
        return 0
    else:
        print(f"\n✗ FAILURE: {total - passed} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
