from __future__ import annotations

import os
from typing import Any, Dict, Optional

from rich.console import Group
from rich.text import Text

from lib.ui import (
    get_console,
    InlineSQL,
    MessagePanel,
    NextSteps,
    NoticePanel,
    QueryPanel,
    SectionBox,
    SectionHeader,
    StatusLine,
)


class CacheCommand:
    """Handles all functionality for the rdst cache command."""

    def __init__(self, client=None):
        """Initialize the CacheCommand with an optional CloudAgentClient."""
        self.client = client
        self._console = get_console()

    def execute_cache(
        self,
        query: str,
        target: str,
        target_config: Dict[str, Any],
        strategy: str = "explicit",
        tag: Optional[str] = None,
        json_output: bool = False,
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
        from ..functions.readyset_explain_cache import (
            explain_create_cache_readyset,
            create_cache_readyset,
        )
        from ..functions.performance_comparison import (
            compare_query_performance,
            format_performance_comparison,
        )

        if not query:
            return RdstResult(False, "cache requires a SQL query or hash_id")

        try:
            content_items: list[Any] = []

            def add_spacer() -> None:
                content_items.append(Text(""))

            def render_output(items: list[Any]) -> str:
                with self._console.capture() as capture:
                    self._console.print(
                        SectionBox(
                            "Readyset Cache Performance Analysis",
                            content=Group(*items) if items else "",
                        )
                    )
                return capture.get().rstrip()

            # Step 1: Resolve query (hash_id or direct SQL)
            content_items.append(SectionHeader("Step 1: Resolving Query"))
            add_spacer()

            registry = QueryRegistry()
            registry.load()

            # Check if input is a hash_id
            resolved_query = None
            is_from_registry = False
            query_hash = None

            # Check if query looks like a hash ID (12 hex characters)
            import re

            if re.match(r"^[0-9a-f]{12}$", query.lower()):
                # Looks like a hash_id
                entry = registry.get_query(query)
                if entry:
                    resolved_query = registry.get_executable_query(
                        query, interactive=False
                    )
                    if resolved_query:
                        content_items.append(
                            MessagePanel(
                                f"Loaded query from registry (hash: {query})",
                                variant="success",
                            )
                        )
                        content_items.append(
                            StatusLine(
                                "SQL", str(InlineSQL(resolved_query, max_length=80))
                            )
                        )
                        is_from_registry = True
                        query_hash = query
                    else:
                        content_items.append(
                            MessagePanel(
                                f"Hash {query} found but no parameters available",
                                variant="error",
                            )
                        )
                        return RdstResult(False, render_output(content_items))
                else:
                    # Not in registry, treat as SQL
                    resolved_query = query
                    content_items.append(
                        MessagePanel("Using direct SQL query", variant="info")
                    )
            else:
                # Direct SQL query
                resolved_query = query
                content_items.append(
                    MessagePanel("Using direct SQL query", variant="info")
                )

            add_spacer()

            # Step 2: Static cacheability check
            content_items.append(SectionHeader("Step 2: Static Cacheability Analysis"))
            add_spacer()
            static_result = check_readyset_cacheability(query=resolved_query)

            if static_result["cacheable"]:
                content_items.append(
                    MessagePanel(
                        f"Query appears cacheable (confidence: {static_result['confidence']})",
                        variant="success",
                    )
                )
                warnings = static_result.get("warnings") or []
                if warnings:
                    content_items.append(
                        NoticePanel(
                            title="CACHEABILITY WARNINGS",
                            description="Readyset detected potential issues:",
                            variant="warning",
                            bullets=warnings,
                        )
                    )
            else:
                issues = static_result.get("issues") or []
                content_items.append(
                    NoticePanel(
                        title="CACHEABILITY FAILED",
                        description="This query cannot be cached by Readyset.",
                        variant="error",
                        bullets=issues,
                        action_hint="Consider rewriting to avoid non-deterministic functions.",
                    )
                )
                return RdstResult(
                    True,
                    render_output(content_items),
                    data={"static_analysis": static_result},
                )

            add_spacer()

            # Get password from environment
            password = target_config.get("password", "")
            password_env = target_config.get("password_env")
            if password_env:
                password = os.environ.get(password_env, "")

            # Step 3: Setup test database and Readyset containers
            content_items.append(
                SectionHeader("Step 3: Setting up Test Database and Readyset")
            )
            add_spacer()

            from .readyset_setup import setup_readyset_containers

            setup_result = setup_readyset_containers(
                target_name=target,
                target_config=target_config,
                test_data_rows=100,
                llm_model="",  # Use provider's default model
            )

            if not setup_result.get("success"):
                content_items.append(
                    MessagePanel(
                        setup_result.get("error", "Setup failed"),
                        variant="error",
                    )
                )
                return RdstResult(False, render_output(content_items))

            # Extract configuration from setup result
            readyset_port = setup_result["readyset_port"]
            readyset_host = setup_result["readyset_host"]

            content_items.append(
                MessagePanel(
                    "Test database and Readyset containers ready",
                    variant="success",
                )
            )
            add_spacer()

            # Step 4: Cache the query in Readyset
            content_items.append(SectionHeader("Step 4: Creating Cache in Readyset"))
            add_spacer()

            # Get test_db_config from setup result
            test_db_config = setup_result["target_config"]

            # Ensure password is set from our target_config
            if not test_db_config.get("password"):
                test_db_config["password"] = password

            # First verify it's cacheable with EXPLAIN
            explain_result = explain_create_cache_readyset(
                query=resolved_query,
                readyset_port=readyset_port,
                test_db_config=test_db_config,
            )

            if not explain_result.get("success") or not explain_result.get("cacheable"):
                content_items.append(
                    MessagePanel("Readyset cannot cache this query", variant="error")
                )
                explanation = explain_result.get("explanation")
                if explanation:
                    content_items.append(
                        NoticePanel(
                            title="CACHEABILITY DETAILS",
                            description=explanation,
                            variant="warning",
                        )
                    )
                return RdstResult(False, render_output(content_items))

            # Create the cache
            create_result = create_cache_readyset(
                query=resolved_query,
                readyset_port=readyset_port,
                test_db_config=test_db_config,
            )

            if not create_result.get("success"):
                content_items.append(
                    MessagePanel(
                        f"Failed to create cache: {create_result.get('error')}",
                        variant="error",
                    )
                )
                return RdstResult(False, render_output(content_items))

            content_items.append(
                MessagePanel(
                    "Cache created successfully in Readyset", variant="success"
                )
            )
            add_spacer()

            # Step 5: Performance comparison
            content_items.append(
                SectionHeader("Step 5: Performance Comparison (Target DB vs Readyset)")
            )
            add_spacer()

            # Use the original target DB configuration (production database)
            # NOT the test database - we want to compare prod DB vs Readyset with cache
            original_db_config = {
                "engine": target_config.get("engine", "postgresql"),
                "host": target_config.get("host", "localhost"),
                "port": target_config.get("port", 5432),
                "database": target_config.get("database", "postgres"),
                "user": target_config.get("user", "postgres"),
                "password": password,
            }

            perf_result = compare_query_performance(
                query=resolved_query,
                original_db_config=original_db_config,
                readyset_port=readyset_port,  # Use the port from workflow setup
                readyset_host="localhost",
                iterations=10,
                warmup_iterations=2,
            )

            if perf_result.get("success"):
                perf_output = format_performance_comparison(perf_result)
                content_items.append(Text(perf_output))
            else:
                content_items.append(
                    MessagePanel(
                        f"Performance comparison failed: {perf_result.get('error')}",
                        variant="error",
                    )
                )

            add_spacer()

            # Step 6: Output CREATE CACHE command for deployment
            content_items.append(SectionHeader("Step 6: Deployment Instructions"))
            add_spacer()
            content_items.append(
                MessagePanel(
                    "To cache this query in your Readyset instance:", variant="info"
                )
            )
            add_spacer()

            # Generate the CREATE CACHE command
            cache_command = static_result.get("create_cache_command")
            cache_command = cache_command or f"CREATE CACHE FROM {resolved_query};"
            content_items.append(
                QueryPanel(cache_command, title="CREATE CACHE Command")
            )

            connect_steps = []
            if target_config.get("engine") == "mysql":
                connect_steps.append(
                    (
                        f"mysql -h YOUR_READYSET_HOST -P YOUR_READYSET_PORT -u {target_config['user']} -D {target_config['database']}",
                        "Connect to Readyset",
                    )
                )
            else:
                connect_steps.append(
                    (
                        f"psql -h YOUR_READYSET_HOST -p YOUR_READYSET_PORT -U {target_config['user']} -d {target_config['database']}",
                        "Connect to Readyset",
                    )
                )
            content_items.append(NextSteps(connect_steps, title="Connect to Readyset"))

            add_spacer()

            # Step 7: Save to registry if new
            if not is_from_registry:
                content_items.append(SectionHeader("Step 7: Saving to Query Registry"))
                add_spacer()

                saved_hash, is_new = registry.add_query(
                    sql=resolved_query, tag=tag or "", source="cache", target=target
                )

                if is_new:
                    content_items.append(
                        MessagePanel(
                            f"Query saved to registry (hash: {saved_hash})",
                            variant="success",
                        )
                    )
                else:
                    content_items.append(
                        MessagePanel(
                            f"Query updated in registry (hash: {saved_hash})",
                            variant="success",
                        )
                    )
                if tag:
                    content_items.append(StatusLine("Tagged as", tag))

                query_hash = saved_hash
            else:
                content_items.append(
                    MessagePanel(
                        f"Query already in registry (hash: {query_hash})",
                        variant="info",
                    )
                )

            add_spacer()

            result_data = {
                "query": resolved_query,
                "query_hash": query_hash,
                "target": target,
                "static_analysis": static_result,
                "explain_result": explain_result,
                "create_result": create_result,
                "performance_comparison": perf_result
                if perf_result.get("success")
                else None,
                "strategy": strategy,
                "tag": tag,
            }

            if json_output:
                json_result = {"success": True, "data": result_data}
                return RdstResult(
                    True, json_module.dumps(json_result, indent=2), data=result_data
                )

            return RdstResult(True, render_output(content_items), data=result_data)

        except Exception as e:
            import traceback

            error_msg = f"cache command failed: {str(e)}\n{traceback.format_exc()}"

            if json_output:
                json_result = {
                    "success": False,
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                }
                return RdstResult(False, json_module.dumps(json_result, indent=2))

            return RdstResult(False, error_msg)
