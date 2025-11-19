from __future__ import annotations

import os
from typing import Dict, Any

try:
    from rich.console import Console
    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False
    Console = None


class CacheCommand:
    """Handles all functionality for the rdst cache command."""

    def __init__(self, client=None):
        """Initialize the CacheCommand with an optional CloudAgentClient."""
        self.client = client
        self._console = Console() if _RICH_AVAILABLE else None

    def execute_cache(
        self,
        query: str,
        target: str,
        target_config: Dict[str, Any],
        strategy: str = "explicit",
        tag: str = None,
        json_output: bool = False
    ):
        """
        Execute the cache command workflow.

        Args:
            query: SQL query string OR hash_id from registry
            target: Target database name
            target_config: Target database configuration
            strategy: Caching strategy (explicit, always, etc.)
            tag: Optional tag to assign when saving to registry
            json_output: Output results in JSON format

        Returns:
            RdstResult with performance comparison and caching instructions
        """
        from .rdst_cli import RdstResult
        import json as json_module
        from ..query_registry.query_registry import QueryRegistry
        from ..functions.readyset_cacheability import check_readyset_cacheability
        from ..functions.readyset_explain_cache import explain_create_cache_readyset, create_cache_readyset
        from ..functions.performance_comparison import compare_query_performance, format_performance_comparison

        if not query:
            return RdstResult(False, "cache requires a SQL query or hash_id")

        try:
            output_lines = []
            output_lines.append("=" * 70)
            output_lines.append("ReadySet Cache Performance Analysis")
            output_lines.append("=" * 70)
            output_lines.append("")

            # Step 1: Resolve query (hash_id or direct SQL)
            output_lines.append("Step 1: Resolving Query")
            output_lines.append("-" * 70)

            registry = QueryRegistry()
            registry.load()

            # Check if input is a hash_id
            resolved_query = None
            is_from_registry = False
            query_hash = None

            # Check if query looks like a hash ID (12 hex characters)
            import re
            if re.match(r'^[0-9a-f]{12}$', query.lower()):
                # Looks like a hash_id
                entry = registry.get_query(query)
                if entry:
                    resolved_query = registry.get_executable_query(query, interactive=False)
                    if resolved_query:
                        output_lines.append(f"✓ Loaded query from registry (hash: {query})")
                        output_lines.append(f"  SQL: {resolved_query[:80]}...")
                        is_from_registry = True
                        query_hash = query
                    else:
                        output_lines.append(f"✗ Hash {query} found but no parameters available")
                        return RdstResult(False, "\n".join(output_lines))
                else:
                    # Not in registry, treat as SQL
                    resolved_query = query
                    output_lines.append(f"✓ Using direct SQL query")
            else:
                # Direct SQL query
                resolved_query = query
                output_lines.append(f"✓ Using direct SQL query")

            output_lines.append("")

            # Step 2: Static cacheability check
            output_lines.append("Step 2: Static Cacheability Analysis")
            output_lines.append("-" * 70)
            static_result = check_readyset_cacheability(query=resolved_query)

            if static_result['cacheable']:
                output_lines.append(f"✓ Query appears cacheable (confidence: {static_result['confidence']})")
                if static_result.get('warnings'):
                    for warning in static_result['warnings']:
                        output_lines.append(f"  ⚠ {warning}")
            else:
                output_lines.append("✗ Query is NOT cacheable by ReadySet")
                if static_result.get('issues'):
                    for issue in static_result['issues']:
                        output_lines.append(f"  • {issue}")
                output_lines.append("")
                output_lines.append("This query cannot be cached. Consider rewriting to avoid non-deterministic functions.")
                return RdstResult(True, "\n".join(output_lines), data={"static_analysis": static_result})

            output_lines.append("")

            # Get password from environment
            password = target_config.get('password', '')
            password_env = target_config.get('password_env')
            if password_env:
                password = os.environ.get(password_env, '')

            # Step 3: Setup test database and ReadySet containers
            output_lines.append("Step 3: Setting up Test Database and ReadySet")
            output_lines.append("-" * 70)

            from .readyset_setup import setup_readyset_containers

            setup_result = setup_readyset_containers(
                target_name=target,
                target_config=target_config,
                test_data_rows=100,
                llm_model=None  # Use provider's default model
            )

            if not setup_result.get("success"):
                output_lines.append(f"✗ {setup_result.get('error', 'Setup failed')}")
                return RdstResult(False, "\n".join(output_lines))

            # Extract configuration from setup result
            readyset_port = setup_result["readyset_port"]
            readyset_host = setup_result["readyset_host"]

            output_lines.append("✓ Test database and ReadySet containers ready")
            output_lines.append("")

            # Step 4: Cache the query in ReadySet
            output_lines.append("Step 4: Creating Cache in ReadySet")
            output_lines.append("-" * 70)

            # Get test_db_config from setup result
            test_db_config = setup_result["target_config"]

            # Ensure password is set from our target_config
            if not test_db_config.get('password'):
                test_db_config['password'] = password

            # First verify it's cacheable with EXPLAIN
            explain_result = explain_create_cache_readyset(
                query=resolved_query,
                readyset_port=readyset_port,
                test_db_config=test_db_config
            )

            if not explain_result.get('success') or not explain_result.get('cacheable'):
                output_lines.append(f"✗ ReadySet cannot cache this query")
                if explain_result.get('explanation'):
                    output_lines.append(f"  {explain_result['explanation']}")
                return RdstResult(False, "\n".join(output_lines))

            # Create the cache
            create_result = create_cache_readyset(
                query=resolved_query,
                readyset_port=readyset_port,
                test_db_config=test_db_config
            )

            if not create_result.get('success'):
                output_lines.append(f"✗ Failed to create cache: {create_result.get('error')}")
                return RdstResult(False, "\n".join(output_lines))

            output_lines.append("✓ Cache created successfully in ReadySet")
            output_lines.append("")

            # Step 5: Performance comparison
            output_lines.append("Step 5: Performance Comparison (Target DB vs ReadySet)")
            output_lines.append("-" * 70)
            output_lines.append("")

            # Use the original target DB configuration (production database)
            # NOT the test database - we want to compare prod DB vs ReadySet with cache
            original_db_config = {
                'engine': target_config.get('engine', 'postgresql'),
                'host': target_config.get('host', 'localhost'),
                'port': target_config.get('port', 5432),
                'database': target_config.get('database', 'postgres'),
                'user': target_config.get('user', 'postgres'),
                'password': password
            }

            perf_result = compare_query_performance(
                query=resolved_query,
                original_db_config=original_db_config,
                readyset_port=readyset_port,  # Use the port from workflow setup
                readyset_host='localhost',
                iterations=10,
                warmup_iterations=2
            )

            if perf_result.get('success'):
                perf_output = format_performance_comparison(perf_result)
                output_lines.append(perf_output)
            else:
                output_lines.append(f"✗ Performance comparison failed: {perf_result.get('error')}")

            output_lines.append("")

            # Step 6: Output CREATE CACHE command for deployment
            output_lines.append("Step 6: Deployment Instructions")
            output_lines.append("-" * 70)
            output_lines.append("")
            output_lines.append("To cache this query in your ReadySet instance:")
            output_lines.append("")

            # Generate the CREATE CACHE command
            cache_command = static_result.get('create_cache_command')
            if cache_command:
                output_lines.append(cache_command)
            else:
                output_lines.append(f"CREATE CACHE FROM {resolved_query};")

            output_lines.append("")
            output_lines.append("Connect to your ReadySet and run this command:")
            if target_config.get('engine') == 'mysql':
                output_lines.append(f"  mysql -h YOUR_READYSET_HOST -P YOUR_READYSET_PORT -u {target_config['user']} -D {target_config['database']}")
            else:
                output_lines.append(f"  psql -h YOUR_READYSET_HOST -p YOUR_READYSET_PORT -U {target_config['user']} -d {target_config['database']}")

            output_lines.append("")

            # Step 7: Save to registry if new
            if not is_from_registry:
                output_lines.append("Step 7: Saving to Query Registry")
                output_lines.append("-" * 70)

                saved_hash, is_new = registry.add_query(
                    sql=resolved_query,
                    tag=tag or "",
                    source="cache",
                    target=target
                )

                if is_new:
                    output_lines.append(f"✓ Query saved to registry (hash: {saved_hash})")
                    if tag:
                        output_lines.append(f"  Tagged as: {tag}")
                else:
                    output_lines.append(f"✓ Query updated in registry (hash: {saved_hash})")
                    if tag:
                        output_lines.append(f"  Tagged as: {tag}")

                query_hash = saved_hash
            else:
                output_lines.append(f"✓ Query already in registry (hash: {query_hash})")

            output_lines.append("")
            output_lines.append("=" * 70)

            result_data = {
                "query": resolved_query,
                "query_hash": query_hash,
                "target": target,
                "static_analysis": static_result,
                "explain_result": explain_result,
                "create_result": create_result,
                "performance_comparison": perf_result if perf_result.get('success') else None,
                "strategy": strategy,
                "tag": tag
            }

            if json_output:
                json_result = {
                    "success": True,
                    "data": result_data
                }
                return RdstResult(True, json_module.dumps(json_result, indent=2), data=result_data)

            return RdstResult(True, "\n".join(output_lines), data=result_data)

        except Exception as e:
            import traceback
            error_msg = f"cache command failed: {str(e)}\n{traceback.format_exc()}"

            if json_output:
                json_result = {
                    "success": False,
                    "error": str(e),
                    "traceback": traceback.format_exc()
                }
                return RdstResult(False, json_module.dumps(json_result, indent=2))

            return RdstResult(False, error_msg)
