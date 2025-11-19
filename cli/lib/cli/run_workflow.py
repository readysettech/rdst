import argparse
import json
import os
import sys
import time


from lib.workflow_manager.workflow_manager import WorkflowManager, WorkflowStatus
from lib.workflows import resources

def print_separator(title, char="="):
    """Print a nice separator"""
    print(f"\n{char * 60}")
    print(f" {title}")
    print(f"{char * 60}")

def print_step_details(step_name, step_result):
    """Print detailed information about a step"""
    print(f"  📋 Step: {step_name}")
    print(f"    Status: {step_result.status.value}")
    if step_result.started_at:
        print(f"    Started: {step_result.started_at.strftime('%H:%M:%S.%f')[:-3]}")
    if step_result.completed_at:
        print(f"    Completed: {step_result.completed_at.strftime('%H:%M:%S.%f')[:-3]}")
        if step_result.started_at:
            duration = step_result.completed_at - step_result.started_at
            print(f"    Duration: {duration.total_seconds():.3f}s")
    if step_result.retry_count > 0:
        print(f"    Retries: {step_result.retry_count}")
    if step_result.error:
        print(f"    Error: {step_result.error}")
    if step_result.result:
        result_str = str(step_result.result)
        if len(result_str) > 100:
            result_str = result_str[:100] + "..."
        print(f"    Result: {result_str}")
    print()

def test_llm_integration():
    """Test all LLM integration features"""
    print_separator("LLM INTEGRATION TESTS", "-")

    # Test 1: Simple LLM call
    print("\n🧪 Test 1: Simple LLM Call")
    workflow = {
        "StartAt": "TestLLM",
        "States": {
            "TestLLM": {
                "Type": "Task",
                "Resource": "call_llm",
                "Parameters": {
                    "prompt": "Explain what a database is in one sentence.",
                    "model": None
                },
                "End": True
            }
        }
    }

    manager = WorkflowManager.from_dict(workflow)
    result = manager.run()
    print(f"LLM Response: {result['TestLLM']['response']}")
    print(f"   Model: {result['TestLLM']['model']}")

    # Test 2: LLM with database data
    print("\n🧪 Test 2: LLM with Database Analysis")
    db_workflow = {
        "StartAt": "GetDBInfo",
        "States": {
            "GetDBInfo": {
                "Type": "Task",
                "Resource": "get_db_size",
                "Next": "AnalyzeWithLLM"
            },
            "AnalyzeWithLLM": {
                "Type": "Task",
                "Resource": "call_llm",
                "Parameters": {
                    "prompt": "I have a database with {{States.GetDBInfo.size_mb}}MB. What are 2 optimization tips?",
                    "model": None
                },
                "End": True
            }
        }
    }

    manager = WorkflowManager.from_dict(db_workflow)
    result = manager.run()
    print(f"DB Size: {result['States']['GetDBInfo']['size_mb']}MB")
    print(f"   LLM Analysis: {result['States']['AnalyzeWithLLM']['response']}")

def test_database_functions():
    """Test all database functions"""
    print_separator("DATABASE FUNCTIONS TESTS", "-")

    manager = WorkflowManager()

    print("🧪 Testing Database Functions:")

    # Test get_db_size
    db_size = manager.resources["get_db_size"]()
    print(f"  DB Size: {db_size['size_mb']}MB ({db_size['size_gb']}GB)")

    # Test get_table_count
    table_count = manager.resources["get_table_count"]()
    print(f"  📋 Tables: {table_count['table_count']}, Views: {table_count['view_count']}")

    # Test get_query_stats
    query_stats = manager.resources["get_query_stats"]()
    print(f"  Queries: {query_stats['total_queries']}, Avg: {query_stats['avg_response_time_ms']}ms")

    # Test analyze_schema
    schema = manager.resources["analyze_schema"]()
    print(f"  Schema: {schema['tables']} tables, {schema['indexes']} indexes")
    print(f"      Recommendations: {', '.join(schema['recommendations'][:2])}...")

def test_async_execution():
    """Test async execution with status tracking"""
    print_separator("ASYNC EXECUTION & STATUS TRACKING", "-")

    workflow = {
        "StartAt": "Step1",
        "States": {
            "Step1": {
                "Type": "Task",
                "Resource": "get_db_size",
                "Next": "Step2"
            },
            "Step2": {
                "Type": "Task",
                "Resource": "get_table_count",
                "Next": "Step3"
            },
            "Step3": {
                "Type": "Task",
                "Resource": "call_llm",
                "Parameters": {
                    "prompt": "Analyze database: {{States.Step1.size_mb}}MB, {{States.Step2.table_count}} tables",
                    "model": None
                },
                "End": True
            }
        }
    }

    manager = WorkflowManager.from_dict(workflow)

    print("Starting async workflow...")
    workflow_id = manager.run_async()
    print(f"📍 Workflow ID: {workflow_id}")

    # Monitor in real-time
    print("\nReal-time Status Monitoring:")
    max_checks = 10
    for check in range(max_checks):
        status = manager.get_workflow_status(workflow_id)

        print(f"  ⏰ Check #{check + 1}: {status.status.value}")
        print(f"    Current Step: {status.current_step or 'None'}")
        print(f"    Completed: {len([s for s in status.steps.values() if s.status == WorkflowStatus.COMPLETED])}/{len(status.steps)}")

        if status.status in [WorkflowStatus.COMPLETED, WorkflowStatus.FAILED]:
            break

        time.sleep(0.5)

    final_status = manager.get_workflow_status(workflow_id)
    print(f"\nFinal Status: {final_status.status.value}")

    if final_status.started_at and final_status.completed_at:
        duration = final_status.completed_at - final_status.started_at
        print(f"🕐 Total Duration: {duration.total_seconds():.3f}s")

    print("\n📋 Step Details:")
    for step_name, step_result in final_status.steps.items():
        print_step_details(step_name, step_result)

def run_workflow_file(workflow_path, input_data=None, show_tracking=False):
    """Run a workflow from JSON file with optional status tracking"""
    print_separator(f"RUNNING WORKFLOW: {os.path.basename(workflow_path)}")

    registry = {
        "get_user_data": resources.get_user_data,
        "summarize_info": resources.summarize_info,
        "compose_prompt": resources.compose_prompt,
    }

    wf_path = os.path.abspath(workflow_path)
    manager = WorkflowManager.from_file(wf_path, resources=registry)
    initial = input_data or {}

    if show_tracking:
        # Run with async tracking
        workflow_id = manager.run_async()
        print(f"Started async execution: {workflow_id}")

        # Monitor progress
        while True:
            status = manager.get_workflow_status(workflow_id)
            print(f"Status: {status.status.value}, Current: {status.current_step}")

            if status.status in [WorkflowStatus.COMPLETED, WorkflowStatus.FAILED]:
                break
            time.sleep(0.1)

        # Get final result from context
        final_status = manager.get_workflow_status(workflow_id)
        ctx = final_status.context

        # Show tracking details
        print("\n📋 Execution Details:")
        for step_name, step_result in final_status.steps.items():
            print_step_details(step_name, step_result)
    else:
        # Run synchronously
        ctx = manager.run(initial_input=initial)

    print("\nWorkflow Result:")
    print(json.dumps(ctx, indent=2))

    # Show prompt if available (for fetch_user_data_workflow)
    try:
        if "BuildPrompt" in ctx and "prompt" in ctx["BuildPrompt"]:
            print("\n💬 Generated Prompt:")
            print("-" * 40)
            print(ctx["BuildPrompt"]["prompt"])
            print("-" * 40)
    except (KeyError, TypeError):
        pass

    return ctx

def main():
    ap = argparse.ArgumentParser(
        description="Enhanced Workflow Runner with LLM and Status Tracking",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run basic workflow
  python run_workflow.py --workflow ../workflows/fetch_user_data_workflow.json

  # Run with custom input
  python run_workflow.py --workflow ../workflows/fetch_user_data_workflow.json --input '{"user_id": "456"}'

  # Run with status tracking
  python run_workflow.py --workflow ../workflows/fetch_user_data_workflow.json --track

  # Test all features
  python run_workflow.py --test-all

  # Test specific features
  python run_workflow.py --test-llm
  python run_workflow.py --test-db
  python run_workflow.py --test-async
"""
    )

    ap.add_argument("--workflow", help="Path to workflow JSON file")
    ap.add_argument("--input", help="Initial input JSON string", default="{}")
    ap.add_argument("--track", action="store_true", help="Show detailed status tracking")

    # Test options
    ap.add_argument("--test-all", action="store_true", help="Run all tests (LLM, DB, Async)")
    ap.add_argument("--test-llm", action="store_true", help="Test LLM integration")
    ap.add_argument("--test-db", action="store_true", help="Test database functions")
    ap.add_argument("--test-async", action="store_true", help="Test async execution")

    args = ap.parse_args()

    try:
        if args.test_all:
            print("🧪 Running All Tests")
            test_database_functions()
            test_llm_integration()
            test_async_execution()
            print_separator("ALL TESTS COMPLETE", "=")
            return 0

        if args.test_llm:
            test_llm_integration()
            return 0

        if args.test_db:
            test_database_functions()
            return 0

        if args.test_async:
            test_async_execution()
            return 0

        # Handle workflow execution
        if not args.workflow:
            # Default to fetch_user_data_workflow if no workflow specified
            default_workflow = "../workflows/fetch_user_data_workflow.json"
            if os.path.exists(default_workflow):
                print(f"🔄 No workflow specified, using default: {default_workflow}")
                args.workflow = default_workflow
            else:
                print("No workflow specified. Use --workflow or --test-* options.")
                ap.print_help()
                return 1

        if not os.path.exists(args.workflow):
            print(f"Workflow file not found: {args.workflow}")
            return 1

        try:
            input_data = json.loads(args.input)
        except json.JSONDecodeError as e:
            print(f"Invalid input JSON: {e}")
            return 1

        run_workflow_file(args.workflow, input_data, args.track)
        return 0

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())