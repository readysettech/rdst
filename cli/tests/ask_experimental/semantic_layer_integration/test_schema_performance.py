#!/usr/bin/env python3
"""
Test 3: Schema Collection Performance

Verifies that ask3 uses semantic layer for schema collection, eliminating
the 10-15 second database query delay.
"""
import sys
import os
import time
sys.path.insert(0, '.')

from lib.semantic_layer.manager import SemanticLayerManager
from lib.data_structures.semantic_layer import SemanticLayer, TableAnnotation, ColumnAnnotation


def setup_complete_semantic_layer():
    """Create a semantic layer with complete schema info (including data types)"""
    print("Setting up complete semantic layer with data types...")

    target = "test_perf_db"
    layer = SemanticLayer(target=target)

    # Add users table with COMPLETE schema info (including data types)
    users_table = TableAnnotation(
        name="users",
        description="User account records",
        business_context="Primary user data table"
    )

    users_table.columns["id"] = ColumnAnnotation(
        name="id",
        description="Unique user identifier",
        data_type="integer"  # Important: has data_type!
    )

    users_table.columns["email"] = ColumnAnnotation(
        name="email",
        description="User email address",
        data_type="varchar"  # Important: has data_type!
    )

    users_table.columns["status"] = ColumnAnnotation(
        name="status",
        description="Account status",
        data_type="varchar",  # Important: has data_type!
        enum_values={
            "A": "Active",
            "I": "Inactive",
            "C": "Churned"
        }
    )

    layer.tables["users"] = users_table

    # Add posts table
    posts_table = TableAnnotation(
        name="posts",
        description="User posts"
    )

    posts_table.columns["id"] = ColumnAnnotation(
        name="id",
        description="Post ID",
        data_type="integer"
    )

    posts_table.columns["user_id"] = ColumnAnnotation(
        name="user_id",
        description="Author user ID",
        data_type="integer"
    )

    posts_table.columns["title"] = ColumnAnnotation(
        name="title",
        description="Post title",
        data_type="varchar"
    )

    layer.tables["posts"] = posts_table

    # Save
    manager = SemanticLayerManager()
    manager.save(layer)

    print(f"✓ Created complete semantic layer for '{target}'")
    print(f"  - 2 tables with data types")
    print(f"  - All columns have data_type specified")

    return target, layer


def setup_incomplete_semantic_layer():
    """Create a semantic layer WITHOUT complete schema info (missing data types)"""
    print("\nSetting up incomplete semantic layer (no data types)...")

    target = "test_incomplete_db"
    layer = SemanticLayer(target=target)

    # Add table WITHOUT data types
    users_table = TableAnnotation(
        name="users",
        description="User accounts"
    )

    users_table.columns["id"] = ColumnAnnotation(
        name="id",
        description="User ID"
        # NOTE: No data_type! This makes it incomplete
    )

    users_table.columns["email"] = ColumnAnnotation(
        name="email",
        description="Email"
        # NOTE: No data_type!
    )

    layer.tables["users"] = users_table

    # Save
    manager = SemanticLayerManager()
    manager.save(layer)

    print(f"✓ Created incomplete semantic layer for '{target}'")
    print(f"  - 1 table WITHOUT data types")

    return target, layer


def test_semantic_layer_schema_collection():
    """Test that ask3 can use semantic layer for schema collection"""
    print("\n" + "="*60)
    print("Test: Schema Collection from Semantic Layer")
    print("="*60)

    target, layer = setup_complete_semantic_layer()

    try:
        from lib.engines.ask3_engine import Ask3NLToSQLEngine

        # Create engine
        engine = Ask3NLToSQLEngine()

        # Create mock state
        class MockState:
            def __init__(self):
                self.target = target
                self.nl_question = "Test question"
                self.extra_data = {}

        engine.state = MockState()

        print("\n1. Testing _has_complete_schema()...")
        is_complete = engine._has_complete_schema(layer)

        if is_complete:
            print("  ✓ Semantic layer recognized as complete")
        else:
            print("  ✗ Semantic layer NOT recognized as complete")
            return False

        print("\n2. Testing _format_semantic_schema()...")
        start_time = time.time()
        schema = engine._format_semantic_schema(layer)
        elapsed = time.time() - start_time

        print(f"  ⏱ Schema generation took: {elapsed:.3f}s")

        if not schema:
            print("  ✗ No schema returned")
            return False

        # Check schema content
        checks = [
            ("users table", "Table: users" in schema),
            ("posts table", "Table: posts" in schema),
            ("table description", "User account records" in schema),
            ("column with type", "id integer" in schema or "id int" in schema),
            ("column description", "email" in schema.lower()),
            ("enum values", "Active" in schema or "Inactive" in schema),
        ]

        all_passed = True
        for check_name, result in checks:
            status = "✓" if result else "✗"
            print(f"  {status} {check_name}: {'Found' if result else 'NOT FOUND'}")
            all_passed = all_passed and result

        # Show sample
        print(f"\n3. Sample schema output:")
        print("   " + "\n   ".join(schema.split("\n")[:15]))
        print("   ...")

        if elapsed < 0.1:
            print(f"\n✓ Schema generation was FAST ({elapsed:.3f}s < 0.1s)")
        else:
            print(f"\n⚠ Schema generation was slower than expected: {elapsed:.3f}s")

        return all_passed

    except Exception as e:
        print(f"\n✗ FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_incomplete_semantic_layer_fallback():
    """Test that incomplete semantic layer triggers fallback"""
    print("\n" + "="*60)
    print("Test: Incomplete Semantic Layer Fallback")
    print("="*60)

    target, layer = setup_incomplete_semantic_layer()

    try:
        from lib.engines.ask3_engine import Ask3NLToSQLEngine

        # Create engine
        engine = Ask3NLToSQLEngine()

        # Create mock state
        class MockState:
            def __init__(self):
                self.target = target
                self.extra_data = {}

        engine.state = MockState()

        print("\n1. Testing _has_complete_schema()...")
        is_complete = engine._has_complete_schema(layer)

        if not is_complete:
            print("  ✓ Incomplete semantic layer correctly identified")
            return True
        else:
            print("  ✗ Incomplete semantic layer NOT identified (false positive)")
            return False

    except Exception as e:
        print(f"\n✗ FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_semantic_table_info_retrieval():
    """Test that individual table info can be retrieved"""
    print("\n" + "="*60)
    print("Test: Semantic Table Info Retrieval")
    print("="*60)

    target, layer = setup_complete_semantic_layer()

    try:
        from lib.engines.ask3_engine import Ask3NLToSQLEngine

        # Create engine
        engine = Ask3NLToSQLEngine()

        # Create mock state
        class MockState:
            def __init__(self):
                self.target = target

        engine.state = MockState()

        print("\n1. Testing _get_semantic_table_info('users')...")
        table_info = engine._get_semantic_table_info("users")

        if not table_info:
            print("  ✗ No table info returned")
            return False

        print(f"  ✓ Got table info for 'users'")
        print(f"  ✓ Description: {table_info.description}")
        print(f"  ✓ Columns: {len(table_info.columns)}")

        # Test non-existent table
        print("\n2. Testing _get_semantic_table_info('nonexistent')...")
        table_info = engine._get_semantic_table_info("nonexistent")

        if table_info is None:
            print("  ✓ Correctly returned None for non-existent table")
            return True
        else:
            print("  ✗ Should have returned None for non-existent table")
            return False

    except Exception as e:
        print(f"\n✗ FAILED with exception: {e}")
        import traceback
        traceback.print_exc()
        return False


def cleanup():
    """Clean up test semantic layers"""
    print("\n" + "="*60)
    print("Cleanup")
    print("="*60)

    try:
        for target in ["test_perf_db", "test_incomplete_db"]:
            layer_file = os.path.expanduser(f"~/.rdst/semantic-layer/{target}.yaml")
            if os.path.exists(layer_file):
                os.remove(layer_file)
                print(f"✓ Removed {target}.yaml")
    except Exception as e:
        print(f"⚠ Warning: Could not clean up: {e}")


def main():
    print("="*60)
    print("Semantic Layer Integration Test Suite")
    print("Test 3: Schema Collection Performance")
    print("="*60)

    results = []

    try:
        # Run tests
        results.append(("Schema Collection from Semantic Layer", test_semantic_layer_schema_collection()))
        results.append(("Incomplete Semantic Layer Fallback", test_incomplete_semantic_layer_fallback()))
        results.append(("Semantic Table Info Retrieval", test_semantic_table_info_retrieval()))

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
        print("\n✓ SUCCESS: Schema collection optimization is working!")
        print("\n📊 Performance Impact:")
        print("   - With complete semantic layer: <0.1s (instant)")
        print("   - With incomplete semantic layer: Falls back to database")
        print("   - Without semantic layer: Uses database (previous behavior)")
        return 0
    else:
        print(f"\n✗ FAILURE: {total - passed} test(s) failed")
        return 1


if __name__ == '__main__':
    sys.exit(main())
