#!/usr/bin/env python3
"""
RDST Analyze Workflow Runner

Demonstrates how to execute the complete analyze workflow using the WorkflowManager.
This shows the integration between all the functions and the workflow engine.
"""

import sys
import json
from pathlib import Path
from typing import Dict, Any, Optional

# Add the project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from lib.workflow_manager.workflow_manager import WorkflowManager, DEFAULT_FUNCTIONS
from lib.functions import ANALYZE_WORKFLOW_FUNCTIONS, DATABASE_SETUP_FUNCTIONS, READYSET_FUNCTIONS
from lib.cli.rdst_cli import TargetsConfig


def run_analyze_workflow(query: str, target: str = None, test_rewrites: bool = True,
                        llm_model: str = None,
                        save_as: str = "", verbose: bool = False) -> Dict[str, Any]:
    """
    Execute the complete RDST analyze workflow.

    Args:
        query: SQL query to analyze
        target: Target database configuration name (None for default)
        test_rewrites: Whether to test suggested query rewrites
        llm_model: LLM model to use for analysis
        save_as: Optional tag to save the query as
        verbose: Enable verbose output

    Returns:
        Dict with analysis results or error information
    """
    try:
        # 1. Load target configuration
        cfg = TargetsConfig()
        cfg.load()

        # Resolve target
        target_name = target or cfg.get_default()
        if not target_name:
            return {
                "success": False,
                "error": "No target specified and no default configured. Run 'rdst configure' first."
            }

        target_config = cfg.get(target_name)
        if not target_config:
            available_targets = cfg.list_targets()
            targets_str = ', '.join(available_targets) if available_targets else 'none'
            return {
                "success": False,
                "error": f"Target '{target_name}' not found. Available targets: {targets_str}"
            }

        # 2. Set up workflow manager with combined function registry
        workflow_functions = {
            **DEFAULT_FUNCTIONS,  # Built-in workflow functions
            **ANALYZE_WORKFLOW_FUNCTIONS,  # Our analyze functions
            **DATABASE_SETUP_FUNCTIONS,  # Database setup functions
            **READYSET_FUNCTIONS,  # ReadySet container functions
        }

        # Load workflow definition - use simple workflow for analysis only
        # Note: ReadySet parallelization is handled at the CLI level via ThreadPoolExecutor
        workflow_path = Path(__file__).parent / "analyze_workflow_simple.json"
        if not workflow_path.exists():
            return {
                "success": False,
                "error": f"Workflow file not found: {workflow_path}"
            }
        mgr = WorkflowManager.from_file(str(workflow_path), resources=workflow_functions)

        # 3. Prepare initial workflow input
        initial_input = {
            "query": query,
            "target": target_name,
            "target_config": target_config,
            "test_rewrites": test_rewrites,
            "llm_model": llm_model,
            "save_as": save_as,
            "source": "analyze",
            "verbose": verbose
        }

        if verbose:
            print(f"Starting analyze workflow for query: {query[:100]}...")
            print(f"Target: {target_name}")
            print(f"Test rewrites: {test_rewrites}")
            print(f"LLM model: {llm_model}")

        # 4. Execute workflow
        if verbose:
            # Run synchronously for immediate results with progress
            result = mgr.run(initial_input)
            return {"success": True, "result": result}
        else:
            # Run asynchronously for production use
            workflow_id = mgr.run_async(initial_input=initial_input)

            # Wait for completion (in real implementation, this could be polled)
            import time
            max_wait = 300  # 5 minutes timeout
            wait_time = 0

            while wait_time < max_wait:
                execution = mgr.get_workflow_status(workflow_id)
                if not execution:
                    return {"success": False, "error": "Workflow execution not found"}

                if execution.status.value in ['completed', 'failed']:
                    if execution.status.value == 'completed':
                        return {"success": True, "result": execution.context}
                    else:
                        return {
                            "success": False,
                            "error": f"Workflow failed at step: {execution.current_step}",
                            "context": execution.context
                        }

                time.sleep(2)
                wait_time += 2

            return {"success": False, "error": "Workflow timed out"}

    except Exception as e:
        return {
            "success": False,
            "error": f"Workflow execution failed: {str(e)}",
            "exception_type": type(e).__name__
        }


def print_analysis_results(result: Dict[str, Any]) -> None:
    """Print formatted analysis results."""
    if not result.get("success", False):
        print(f"❌ Analysis failed: {result.get('error', 'Unknown error')}")
        return

    workflow_result = result.get("result", {})
    formatted_output = workflow_result

    # If the result is the raw workflow context, extract the formatted output
    if "analysis_summary" not in workflow_result and "FormatFinalResults" in workflow_result:
        formatted_output = workflow_result.get("FormatFinalResults", {})

    print("🔍 Query Analysis Results")
    print("=" * 50)

    # Analysis Summary
    summary = formatted_output.get("analysis_summary", {})
    if summary:
        print(f"Overall Rating: {summary.get('overall_rating', 'N/A')}")
        print(f"Execution Time: {summary.get('execution_time_ms', 0):.1f}ms")
        print(f"Efficiency Score: {summary.get('efficiency_score', 0)}/100")
        print(f"Rows Examined: {summary.get('rows_processed', {}).get('examined', 0):,}")
        print(f"Rows Returned: {summary.get('rows_processed', {}).get('returned', 0):,}")

        concerns = summary.get('primary_concerns', [])
        if concerns:
            print(f"Primary Concerns: {', '.join(concerns)}")
        print()

    # Recommendations
    recommendations = formatted_output.get("recommendations", {})
    if recommendations.get("available", False):
        rewrites = recommendations.get("query_rewrites", [])
        if rewrites:
            print(f"📝 Query Rewrites ({len(rewrites)} suggestions):")
            for i, rewrite in enumerate(rewrites[:3], 1):  # Show top 3
                print(f"  {i}. {rewrite.get('type', 'Unknown')} ({rewrite.get('priority', 'medium')} priority)")
                print(f"     {rewrite.get('explanation', 'No explanation')}")
            print()

        indexes = recommendations.get("index_suggestions", [])
        if indexes:
            print(f"📊 Index Suggestions ({len(indexes)} suggestions):")
            for i, index in enumerate(indexes[:3], 1):  # Show top 3
                print(f"  {i}. {index.get('table', 'unknown')}: {', '.join(index.get('columns', []))}")
                print(f"     {index.get('rationale', 'No rationale')}")
            print()

    # Rewrite Testing
    rewrite_testing = formatted_output.get("rewrite_testing", {})
    if rewrite_testing.get("tested", False):
        print("🧪 Rewrite Testing Results:")
        print(f"   {rewrite_testing.get('summary', 'No summary')}")
        best = rewrite_testing.get("best_rewrite")
        if best:
            improvement = best.get("improvement", {}).get("overall", {})
            improvement_pct = improvement.get("improvement_pct", 0)
            print(f"   Best rewrite: {improvement_pct:+.1f}% performance change")
        print()

    # ReadySet Cacheability
    readyset_cache = formatted_output.get("readyset_cacheability", {})
    if readyset_cache.get("checked", False):
        print("🚀 ReadySet Cacheability:")
        if readyset_cache.get("cacheable"):
            print(f"   ✓ Query is CACHEABLE (confidence: {readyset_cache.get('confidence', 'unknown')})")
            if readyset_cache.get("create_cache_command"):
                print(f"\n   Ready-to-use command:")
                for line in readyset_cache.get("create_cache_command", "").split('\n'):
                    print(f"   {line}")

            if readyset_cache.get("warnings"):
                print(f"\n   ⚠ Warnings:")
                for warning in readyset_cache.get("warnings", []):
                    print(f"     • {warning}")
        else:
            print(f"   ✗ Query is NOT cacheable")
            issues = readyset_cache.get("issues", [])
            if issues:
                print(f"\n   Blocking issues:")
                for issue in issues:
                    print(f"     • {issue}")

        if readyset_cache.get("explanation"):
            print(f"\n   {readyset_cache.get('explanation')}")
        print()

    # Metadata
    metadata = formatted_output.get("metadata", {})
    if metadata:
        print("📋 Analysis Metadata:")
        print(f"   Target: {metadata.get('target', 'N/A')}")
        print(f"   Database: {metadata.get('database_engine', 'N/A')}")
        print(f"   Analysis ID: {metadata.get('analysis_id', 'N/A')}")
        print(f"   LLM Model: {metadata.get('llm_model', 'N/A')}")


def main():
    """Main CLI entry point for testing the analyze workflow."""
    import argparse

    parser = argparse.ArgumentParser(
        description='Run RDST analyze workflow',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_analyze_workflow.py "SELECT * FROM users WHERE active = true"
  python run_analyze_workflow.py "SELECT u.name, COUNT(*) FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.name" --target mysql_test
  python run_analyze_workflow.py --query-file query.sql --no-test-rewrites --verbose
        """
    )

    # Query input options
    query_group = parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument('query', nargs='?', help='SQL query to analyze')
    query_group.add_argument('--query-file', help='Read query from file')

    # Analysis options
    parser.add_argument('--target', help='Target database configuration')
    parser.add_argument('--no-test-rewrites', action='store_true',
                       help='Skip testing of suggested query rewrites')
    parser.add_argument('--llm-model', default=None,
                       help='LLM model to use for analysis (default: provider default)')
    parser.add_argument('--save-as', help='Tag to save the query as')
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose output')

    args = parser.parse_args()

    # Get query text
    if args.query:
        query = args.query
    else:  # query_file
        try:
            with open(args.query_file, 'r') as f:
                query = f.read().strip()
        except Exception as e:
            print(f"Error reading query file: {e}")
            sys.exit(1)

    # Run analysis
    result = run_analyze_workflow(
        query=query,
        target=args.target,
        test_rewrites=not args.no_test_rewrites,
        llm_model=args.llm_model,
        save_as=args.save_as or "",
        verbose=args.verbose
    )

    # Print results
    print_analysis_results(result)

    # Exit with appropriate code
    if result.get("success", False):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
