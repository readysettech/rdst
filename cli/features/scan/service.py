"""ScanService - Async generator-based codebase scanning service.

Scans codebases for ORM queries, extracts SQL via AST + LLM,
and optionally analyzes performance. Exposes an async generator
interface that yields events during execution for both CLI and Web API.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from shared.constants import rdst_semantic_layer_dir

from .events import (
    ScanCompleteEvent,
    ScanErrorEvent,
    ScanEvent,
    ScanFilesFoundEvent,
    ScanProgressEvent,
    ScanQueryResultEvent,
    ScanRegistryEvent,
    ScanStatusEvent,
)
from .extractors.orm_patterns import ORM_PATTERNS_COMPILED, _JS_EXTENSIONS, _SCAN_EXTENSIONS
from .models import ScanInput, ScanOptions

logger = logging.getLogger(__name__)


class ScanService:
    """Service for codebase scanning with async event streaming.

    Usage:
        service = ScanService()
        async for event in service.scan_directory(input_data, options):
            handle_event(event)
    """

    def __init__(self) -> None:
        pass

    async def scan_directory(
        self,
        input_data: ScanInput,
        options: ScanOptions,
    ) -> AsyncGenerator[ScanEvent, None]:
        """Scan a directory for ORM queries. Yields events during execution.

        Phases:
        1. Config validation
        2. File discovery (find ORM files)
        3. AST extraction (deterministic)
        4. LLM conversion (cached, batched)
        5. Registry save
        6. Optional analysis
        7. Complete
        """
        directory = os.path.abspath(os.path.expanduser(input_data.directory))
        target = input_data.target

        # Phase 1: Config validation
        yield ScanStatusEvent(type="status", phase="config", message="Validating configuration...")

        # Validate directory
        single_file = None
        if os.path.isfile(directory):
            single_file = directory
            directory = os.path.dirname(directory)
        elif not os.path.isdir(directory):
            yield ScanErrorEvent(type="error", message=f"Path not found: {directory}", phase="config")
            return

        # Validate target
        if not target:
            yield ScanErrorEvent(
                type="error",
                message="Target required for scan. Select a target database.",
                phase="config",
            )
            return

        # Check schema exists
        schema_file = rdst_semantic_layer_dir() / f"{target}.yaml"
        if not schema_file.exists():
            yield ScanErrorEvent(
                type="error",
                message=f"No schema found for target '{target}'. Run 'rdst schema init --target {target}' first.",
                phase="config",
            )
            return

        # Check API key (unless dry-run)
        if not options.dry_run:
            has_key = bool(
                os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("RDST_TRIAL_TOKEN")
            )
            if not has_key:
                try:
                    from shared.llm.key_resolution import resolve_api_key

                    resolve_api_key()
                    has_key = True
                except Exception:
                    pass
            if not has_key:
                yield ScanErrorEvent(
                    type="error",
                    message="No LLM API key configured. Run 'rdst init' for a free trial or set ANTHROPIC_API_KEY.",
                    phase="config",
                )
                return

        yield ScanStatusEvent(type="status", phase="config", message="Configuration valid")
        await asyncio.sleep(0)  # Yield control

        # Phase 2: File discovery
        scan_label = single_file if single_file else directory
        yield ScanStatusEvent(
            type="status",
            phase="discovery",
            message=f"Scanning {scan_label} for ORM patterns..."
            + (f" (diff: {options.diff})" if options.diff else ""),
        )

        orm_files = await asyncio.to_thread(
            self._find_orm_files, directory, single_file
        )

        # Apply file pattern filter
        if options.file_pattern and orm_files:
            import fnmatch

            orm_files = [
                f
                for f in orm_files
                if fnmatch.fnmatch(f["file"], options.file_pattern)
                or fnmatch.fnmatch(os.path.basename(f["file"]), options.file_pattern)
            ]

        # Apply git diff filter
        if options.diff and orm_files:
            orm_files = await asyncio.to_thread(
                self._filter_by_diff, orm_files, directory, options.diff
            )
            if orm_files is None:
                yield ScanErrorEvent(
                    type="error",
                    message="Git diff failed. Ensure the directory is a git repository.",
                    phase="discovery",
                )
                return

        if not orm_files:
            msg = "No ORM files in diff." if options.diff else "No files with ORM patterns found."
            yield ScanFilesFoundEvent(type="files_found", files=[], total=0)
            yield ScanCompleteEvent(
                type="complete",
                success=True,
                summary={
                    "files_count": 0,
                    "queries_total": 0,
                    "queries_sql": 0,
                    "queries_skipped": 0,
                    "cache_hits": 0,
                    "cache_misses": 0,
                    "registry_new": 0,
                    "registry_updated": 0,
                    "registry_total": 0,
                    "message": msg,
                },
            )
            return

        yield ScanFilesFoundEvent(
            type="files_found", files=orm_files, total=len(orm_files)
        )

        # Phase 3: AST extraction
        yield ScanStatusEvent(
            type="status",
            phase="extraction",
            message=f"Extracting queries from {len(orm_files)} files...",
        )

        from features.scan.cli.snippet_cache import get_cache
        from features.scan.extractors.ast_extractor import (
            CrossFileResolver,
            extract_queries_from_file,
        )
        from features.scan.extractors.js_extractor import extract_queries_from_js_file

        snippet_cache = get_cache("scan")
        cross_file_resolver = CrossFileResolver(directory)

        all_queries: List[Dict[str, Any]] = []
        cache_hits = 0
        cache_misses = 0

        for idx, file_info in enumerate(orm_files):
            filepath = file_info["file"]
            full_path = os.path.join(directory, filepath)

            yield ScanProgressEvent(
                type="progress",
                phase="extraction",
                current=idx + 1,
                total=len(orm_files),
                message=f"Parsing {filepath}",
            )

            # Run extraction in thread to avoid blocking
            _, ext = os.path.splitext(full_path)
            if ext in _JS_EXTENSIONS:
                extracted = await asyncio.to_thread(extract_queries_from_js_file, full_path)
            else:
                extracted = await asyncio.to_thread(extract_queries_from_file, full_path)

            for eq in extracted:
                # Handle cross-file query builders
                if eq.imports_query_builder and eq.imported_builder_name:
                    builder_query = cross_file_resolver.resolve_query_builder(
                        full_path,
                        eq.imported_builder_name,
                        eq.imported_builder_module or "",
                    )
                    if builder_query:
                        eq.orm_snippet = (
                            f"# From {eq.imported_builder_module}.{eq.imported_builder_name}:\n"
                            f"{builder_query.orm_snippet}\n# Called as:\n{eq.orm_snippet}"
                        )
                        eq.snippet_hash = self._hash_snippet(eq.orm_snippet)

                query_dict = self._ast_query_to_dict(eq, filepath)

                if not options.dry_run:
                    cached_result = snippet_cache.get(eq.snippet_hash)
                    if cached_result:
                        query_dict["sql"] = cached_result["sql"]
                        query_dict["issues"] = cached_result["issues"]
                        cache_hits += 1
                    else:
                        query_dict["_needs_llm"] = True
                        cache_misses += 1

                all_queries.append(query_dict)

            await asyncio.sleep(0)  # Yield control between files

        # Phase 4: LLM conversion
        if not options.dry_run:
            uncached = [q for q in all_queries if q.get("_needs_llm")]
            if uncached:
                yield ScanStatusEvent(
                    type="status",
                    phase="conversion",
                    message=f"Converting {len(uncached)} ORM snippets to SQL...",
                )

                schema_context = self._load_schema_context(target)
                sql_dialect = self._detect_sql_dialect(target)
                batch_size = 1 if options.sequential else 5
                total_batches = (len(uncached) + batch_size - 1) // batch_size

                for batch_idx in range(0, len(uncached), batch_size):
                    batch = uncached[batch_idx : batch_idx + batch_size]
                    batch_num = batch_idx // batch_size + 1

                    yield ScanProgressEvent(
                        type="progress",
                        phase="conversion",
                        current=batch_num,
                        total=total_batches,
                        message=f"Converting batch {batch_num}/{total_batches} ({len(batch)} queries)",
                    )

                    await asyncio.to_thread(
                        self._batch_convert_snippets,
                        batch,
                        snippet_cache,
                        schema_context,
                        sql_dialect,
                    )

                    for q in batch:
                        q.pop("_needs_llm", None)

                    await asyncio.sleep(0)

        # Tag statuses
        if not options.dry_run:
            for q in all_queries:
                sql = q.get("sql", "").strip()
                orm_code = q.get("orm_code", "")
                if not sql or sql.startswith("--"):
                    q["status"] = "skipped"
                    q["skip_reason"] = self._infer_skip_reason(sql, orm_code, q)
                else:
                    q["status"] = "sql"
        else:
            for q in all_queries:
                q["status"] = "pending"

        # Emit query results in batches to reduce SSE/render overhead
        batch_size_emit = 50
        for i in range(0, len(all_queries), batch_size_emit):
            batch = all_queries[i : i + batch_size_emit]
            for q in batch:
                yield ScanQueryResultEvent(type="query_result", query=q)
            await asyncio.sleep(0)

        # Phase 5: Registry save
        new_query_count = 0
        updated_query_count = 0
        total_in_registry = 0

        if not options.nosave and not options.dry_run:
            yield ScanStatusEvent(
                type="status",
                phase="registry",
                message="Saving queries to registry...",
            )

            from shared.query_registry import QueryRegistry, hash_sql

            registry = QueryRegistry()
            registry.load()

            for q in all_queries:
                if q["status"] != "sql":
                    continue
                sql = q.get("sql", "")
                try:
                    query_hash, is_new = registry.add_query(
                        sql=sql,
                        source="scan",
                        target=target,
                        skip_param_extraction=True,
                    )
                    q["hash"] = query_hash
                    if is_new:
                        new_query_count += 1
                    else:
                        updated_query_count += 1
                except ValueError as e:
                    q["status"] = "skipped"
                    q["skip_reason"] = str(e).split("\n")[0]

            total_in_registry = len(registry.list_queries())

            yield ScanRegistryEvent(
                type="registry",
                new_queries=new_query_count,
                updated_queries=updated_query_count,
                total_queries=total_in_registry,
                skipped=False,
                registry_path=str(registry.registry_path),
            )
        else:
            from shared.query_registry import QueryRegistry, hash_sql

            for q in all_queries:
                if q.get("status") == "sql":
                    q["hash"] = hash_sql(q.get("sql", ""))

            if options.nosave:
                yield ScanRegistryEvent(
                    type="registry",
                    new_queries=0,
                    updated_queries=0,
                    total_queries=0,
                    skipped=True,
                )
            else:
                # dry_run without nosave: load registry read-only for stats
                registry = QueryRegistry()
                registry.load()
                yield ScanRegistryEvent(
                    type="registry",
                    new_queries=0,
                    updated_queries=0,
                    total_queries=len(registry.list_queries()),
                    skipped=False,
                    registry_path=str(registry.registry_path),
                )

        # Phase 6: Optional analysis
        analysis_summary = None
        if options.analyze and target and not options.dry_run:
            yield ScanStatusEvent(
                type="status",
                phase="analysis",
                message=f"Analyzing queries ({'shallow' if options.shallow else 'deep'} mode)...",
            )
            analysis_summary = await asyncio.to_thread(
                self._run_analysis,
                all_queries,
                target,
                options,
            )

            # Emit analysis progress
            if analysis_summary:
                total_analyzed = analysis_summary.get("total_analyzed", 0)
                yield ScanProgressEvent(
                    type="progress",
                    phase="analysis",
                    current=total_analyzed,
                    total=total_analyzed,
                    message=f"Analyzed {total_analyzed} queries",
                )

        # Build summary
        sql_queries = [q for q in all_queries if q.get("status") == "sql"]
        skipped_queries = [q for q in all_queries if q.get("status") == "skipped"]

        summary: Dict[str, Any] = {
            "files_count": len(orm_files),
            "queries_total": len(all_queries),
            "queries_sql": len(sql_queries),
            "queries_skipped": len(skipped_queries),
            "cache_hits": cache_hits,
            "cache_misses": cache_misses,
            "registry_new": new_query_count,
            "registry_updated": updated_query_count,
            "registry_total": total_in_registry,
            "registry_skipped": options.nosave or options.dry_run,
        }

        if analysis_summary:
            summary["analysis"] = analysis_summary

        yield ScanCompleteEvent(type="complete", success=True, summary=summary)

    # ========================================================================
    # Helper methods (mostly ported from ScanCommand)
    # ========================================================================

    @staticmethod
    def _detect_orms(filepath: Path, content: str) -> List[str]:
        """Detect ORM patterns in file content, filtering by language.

        For Python files, import lines are checked first so that ambiguous
        method-name patterns (e.g. .filter(), .all(), .first() which appear in
        both SQLAlchemy and Django) don't cause false-positive ORM labels.

        Priority for Python:
          1. If the file imports from sqlalchemy → include sqlalchemy, skip django
             (unless it also imports from django)
          2. If the file imports from django → include django, skip sqlalchemy
             (unless it also imports from sqlalchemy)
          3. If neither explicit import is found → fall back to pattern matching
             for both (keeps backwards compatibility for files with no imports)
        """
        import re as _re
        detected_orms = []
        is_python = filepath.suffix == ".py"
        is_js_ts = filepath.suffix in _JS_EXTENSIONS

        # --- Import-aware disambiguation for Python SQLAlchemy vs Django ---
        python_orm_skip: set = set()
        if is_python:
            has_sqlalchemy_import = bool(
                _re.search(r'^\s*(from|import)\s+sqlalchemy', content, _re.MULTILINE)
            )
            has_django_import = bool(
                _re.search(r'^\s*(from|import)\s+django', content, _re.MULTILINE)
            )
            # If only one framework is imported, skip the other to avoid false positives
            if has_sqlalchemy_import and not has_django_import:
                python_orm_skip.add("django")
            elif has_django_import and not has_sqlalchemy_import:
                python_orm_skip.add("sqlalchemy")
            # If both are imported (rare but valid), allow both through

        for orm_name, patterns in ORM_PATTERNS_COMPILED.items():
            if is_python and orm_name in ("prisma", "drizzle"):
                continue
            if is_js_ts and orm_name in ("sqlalchemy", "django"):
                continue
            if is_python and orm_name in python_orm_skip:
                continue
            for pattern in patterns:
                if pattern.search(content):
                    detected_orms.append(orm_name)
                    break
        return detected_orms

    def _find_orm_files(
        self, directory: str, single_file: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Find files with ORM patterns."""
        if single_file:
            filepath = Path(single_file)
            if filepath.suffix not in _SCAN_EXTENSIONS:
                return []
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                return []
            detected_orms = self._detect_orms(filepath, content)
            if not detected_orms:
                return []
            return [{
                "file": os.path.basename(single_file),
                "orms": list(set(detected_orms)),
                "lines": len(content.splitlines()),
            }]

        results = []
        directory_path = Path(directory)
        skip_dirs = {
            "node_modules", ".git", "__pycache__", ".venv", "venv",
            "dist", "build", ".tox", "eggs",
        }

        for root, dirs, files in os.walk(directory_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for file in files:
                filepath = Path(root) / file
                if filepath.suffix not in _SCAN_EXTENSIONS:
                    continue
                try:
                    content = filepath.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                detected_orms = self._detect_orms(filepath, content)
                if detected_orms:
                    results.append({
                        "file": filepath.relative_to(directory_path).as_posix(),
                        "orms": list(set(detected_orms)),
                        "lines": len(content.splitlines()),
                    })

        return results

    def _filter_by_diff(
        self,
        orm_files: List[Dict[str, Any]],
        directory: str,
        diff: str,
    ) -> Optional[List[Dict[str, Any]]]:
        """Filter ORM files to only those changed since git ref. Returns None on error."""
        import subprocess

        try:
            git_root_result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, cwd=directory,
            )
            if git_root_result.returncode != 0:
                return None

            git_root = git_root_result.stdout.strip()

            result = subprocess.run(
                ["git", "diff", "--name-only", diff],
                capture_output=True, text=True, cwd=git_root,
            )
            if result.returncode != 0:
                return None

            changed_files_from_root = {
                f.strip().replace("\\", "/")
                for f in result.stdout.strip().split("\n")
                if f.strip()
            }

            if not changed_files_from_root:
                return []

            scan_dir_rel_to_root = os.path.relpath(directory, git_root).replace(
                "\\", "/"
            )
            if scan_dir_rel_to_root == ".":
                changed_files = changed_files_from_root
            else:
                prefix = f"{scan_dir_rel_to_root}/"
                changed_files = {
                    path[len(prefix):]
                    for path in changed_files_from_root
                    if path.startswith(prefix)
                }

            return [
                file
                for file in orm_files
                if file["file"].replace("\\", "/") in changed_files
            ]

        except (FileNotFoundError, Exception):
            return None

    def _ast_query_to_dict(self, eq: Any, filepath: str) -> Dict[str, Any]:
        """Convert an ExtractedQuery dataclass to dict format."""
        return {
            "file": filepath,
            "function": eq.function_name,
            "class": eq.class_name,
            "orm_code": eq.orm_snippet,
            "snippet_hash": eq.snippet_hash,
            "terminal_method": eq.terminal_method,
            "start_line": eq.start_line,
            "end_line": eq.end_line,
            "imports_builder": eq.imports_query_builder,
            "orm_type": eq.orm_type,
            "sql": "",
            "issues": [],
        }

    def _hash_snippet(self, snippet: str) -> str:
        """Generate deterministic hash for ORM snippet."""
        normalized = " ".join(snippet.split())
        return hashlib.md5(normalized.encode()).hexdigest()[:12]

    def _load_schema_context(self, target: Optional[str]) -> str:
        """Load schema from semantic-layer YAML."""
        if not target:
            return ""
        schema_file = rdst_semantic_layer_dir() / f"{target}.yaml"
        if not schema_file.exists():
            return ""

        try:
            import yaml

            data = yaml.safe_load(schema_file.read_text(encoding="utf-8"))
            tables = data.get("tables", {})
            lines = ["Database Schema:"]
            for table_name, table_info in tables.items():
                columns = table_info.get("columns", {})
                col_list = ", ".join(columns.keys())
                lines.append(f"  {table_name}: {col_list}")
            return "\n".join(lines)
        except Exception:
            return ""

    def _detect_sql_dialect(self, target: Optional[str] = None) -> str:
        """Detect SQL dialect from target's semantic layer YAML."""
        if not target:
            return "PostgreSQL"
        schema_file = rdst_semantic_layer_dir() / f"{target}.yaml"
        if schema_file.exists():
            try:
                content = schema_file.read_text(encoding="utf-8")
                if "mysql" in content.lower():
                    return "MySQL"
            except Exception:
                pass
        return "PostgreSQL"

    def _describe_orm_types(self, queries: List[Dict]) -> str:
        """Build ORM type description string from query batch."""
        orm_types = set()
        for q in queries:
            ot = q.get("orm_type")
            if ot:
                orm_types.add(ot)
        if not orm_types:
            return "SQLAlchemy/Django/Prisma/Drizzle"
        name_map = {
            "sqlalchemy": "SQLAlchemy",
            "django": "Django",
            "prisma": "Prisma",
            "drizzle": "Drizzle",
            "raw_sql": "Raw SQL",
        }
        return "/".join(name_map.get(t, t) for t in sorted(orm_types))

    @staticmethod
    def _extract_text_literal_sql(orm_code: str) -> Optional[str]:
        """Extract the literal SQL string from SQLAlchemy text("...") patterns.

        Handles patterns like:
          session.execute(text("SELECT * FROM users WHERE active = true"))
          db.execute(text('SELECT id FROM orders WHERE status = $1'))
          result = session.execute(text(\"\"\"SELECT ...\"\"\"))

        Returns the raw SQL string if found, otherwise None.
        This bypasses the LLM entirely — the snippet already *is* SQL.
        """
        # Match text("...") or text('...') or text(\"\"\"...\"\"\") or text('''...''')
        # We look for the innermost string argument to text(...)
        patterns = [
            # Triple-quoted strings first (more specific)
            r'text\s*\(\s*"""([\s\S]*?)"""\s*\)',
            r"text\s*\(\s*'''([\s\S]*?)'''\s*\)",
            # Single-quoted strings
            r'text\s*\(\s*"((?:[^"\\]|\\.)*)"\s*\)',
            r"text\s*\(\s*'((?:[^'\\]|\\.)*)'\s*\)",
        ]
        for pattern in patterns:
            m = re.search(pattern, orm_code, re.DOTALL)
            if m:
                sql = m.group(1).strip()
                if sql:
                    return sql
        return None

    def _batch_convert_snippets(
        self,
        queries: List[Dict],
        snippet_cache: Any,
        schema_context: str,
        sql_dialect: str = "PostgreSQL",
    ) -> None:
        """Convert ORM snippets to SQL in batches via LLM.

        Raw SQLAlchemy text("SQL...") snippets are extracted directly without
        calling the LLM — the literal SQL is already present in the snippet.
        """
        from shared.llm_manager import LLMManager

        # --- Pre-pass: resolve text("SQL...") patterns without LLM (Bug 3 fix) ---
        needs_llm: List[Dict] = []
        for q in queries:
            orm_code = q.get("orm_code", "")
            literal_sql = self._extract_text_literal_sql(orm_code)
            if literal_sql is not None:
                q["sql"] = literal_sql
                q["issues"] = self._detect_issues(literal_sql)
                snippet_cache.set(
                    q.get("snippet_hash", ""),
                    literal_sql,
                    q["issues"],
                    orm_code,
                )
            else:
                needs_llm.append(q)

        # If all snippets were resolved via text() extraction, nothing left to do
        if not needs_llm:
            return

        llm = LLMManager()
        schema_section = f"\n\nDatabase Schema:\n{schema_context}" if schema_context else ""
        orm_desc = self._describe_orm_types(needs_llm)

        snippets_list = []
        for j, q in enumerate(needs_llm):
            snippets_list.append(f'{j + 1}. {q.get("orm_code", "")}')
        snippets_text = "\n\n".join(snippets_list)

        # Remap: subsequent code uses `queries` variable; point it at needs_llm
        queries = needs_llm

        system_message = f"""Convert {orm_desc} ORM snippets to {sql_dialect} SQL.
{schema_section}

RULES:
1. Use $1, $2, $3 for parameter placeholders
2. Uppercase SQL keywords (SELECT, FROM, WHERE)
3. Lowercase table/column names
4. Output ONLY valid JSON, no markdown, no notes, no explanations
5. If a snippet is not a database query, output "-- Not a query" as the SQL
6. If a snippet uses dynamic kwargs (**data, ...item, spread operators) that prevent determining columns, output "-- Dynamic arguments" as the SQL
7. If a snippet calls a method on an unknown variable (e.g. "query.first()" without seeing the query definition), output "-- Cross-file query" as the SQL
8. NEVER use literal "..." or ellipsis in SQL output. Always list actual column names from the schema, or use the appropriate -- marker if columns cannot be determined
9. For Prisma: translate include/select/where/orderBy/take/skip to SQL equivalents
10. For Drizzle: translate builder chains (.from().where().limit()) to SQL"""

        user_query = f"""Convert these {len(queries)} ORM snippets to SQL.

{snippets_text}

Respond with ONLY this JSON (no markdown code blocks):
{{"queries": ["SQL for snippet 1", "SQL for snippet 2", ...]}}"""

        try:
            response = llm.query(
                system_message=system_message,
                user_query=user_query,
                max_tokens=2000,
                temperature=0.0,
                model="claude-haiku-4-5-20251001",
            )

            result_text = response.get("text", "").strip()
            if result_text.startswith("```"):
                lines = result_text.split("\n")
                result_text = "\n".join(
                    lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
                )

            parsed = json.loads(result_text)
            sql_list = parsed.get("queries", [])

            for j, q in enumerate(queries):
                if j < len(sql_list):
                    sql = sql_list[j].strip().strip("`").strip()
                    if sql.lower().startswith("sql\n"):
                        sql = sql[4:].strip()
                    q["sql"] = sql
                    q["issues"] = self._detect_issues(sql)
                    snippet_cache.set(
                        q.get("snippet_hash", ""),
                        sql,
                        q["issues"],
                        q.get("orm_code", ""),
                    )
                else:
                    q["sql"] = "-- Conversion failed"
                    q["issues"] = ["LLM did not return SQL for this query"]

        except json.JSONDecodeError as e:
            for q in queries:
                if not q.get("sql"):
                    q["sql"] = f"-- JSON parse error: {e}"
                    q["issues"] = ["LLM response was not valid JSON"]
        except Exception as e:
            for q in queries:
                if not q.get("sql"):
                    q["sql"] = f"-- Batch conversion error: {e}"
                    q["issues"] = ["LLM conversion failed"]

    def _detect_issues(self, sql: str) -> List[str]:
        """Detect common SQL anti-patterns."""
        issues = []
        sql_upper = sql.upper()
        if "SELECT *" in sql_upper:
            issues.append("Uses SELECT * - consider selecting specific columns")
        if "WHERE" in sql_upper and "LIMIT" not in sql_upper:
            issues.append("No LIMIT clause - could return many rows")
        if "LIKE" in sql_upper and "'%" in sql:
            issues.append("Leading wildcard in LIKE - may prevent index usage")
        return issues

    def _infer_skip_reason(self, sql: str, orm_code: str, q: Dict) -> str:
        """Infer a specific, human-readable skip reason."""
        sql_lower = (sql or "").lower()

        if q.get("imports_builder"):
            return "Cross-file query — built in another module, can't trace statically"
        if "-- cross-file" in sql_lower:
            return "Cross-file query — built in another module, can't trace statically"
        if "-- dynamic" in sql_lower:
            return "Dynamic arguments - variable contents only known at runtime"
        if "**" in orm_code:
            return "Dynamic arguments - **kwargs expanded at runtime"
        if any(s in orm_code for s in ("...item", "...data", "...user")):
            return "Dynamic arguments - spread operator expanded at runtime"
        if re.search(r"cursor\.(fetchall|fetchone|fetchmany)\b", orm_code):
            return "Result fetch only - the SQL is in the preceding execute() call"
        if re.search(r"\.(fetchall|fetchone|fetchmany)\(\)", orm_code) and "execute" not in orm_code.lower():
            return "Result fetch only - the SQL is in a separate execute() call"
        if "bulk_create" in orm_code.lower() or "bulk_update" in orm_code.lower():
            return "Bulk operation - list of objects built at runtime"
        if "-- not a query" in sql_lower or not sql:
            if re.search(r"\.(save|commit|flush|close|rollback)\(", orm_code):
                return "Session management, not a query"
            if re.search(r"\.(add|add_all|merge|delete)\(", orm_code):
                return "Write operation without SELECT"
            if re.search(r'\b(SELECT|INSERT|UPDATE|DELETE|WITH)\b', orm_code, re.IGNORECASE):
                return "Table not found in schema"
            return "Not a database query"
        return "Could not convert to SQL"

    def _run_analysis(
        self,
        queries: List[Dict],
        target: str,
        options: ScanOptions,
    ) -> Dict[str, Any]:
        """Run analysis on queries (blocking, meant for asyncio.to_thread)."""
        from features.scan.cli.command import ScanCommand

        # Create a headless ScanCommand (no console) for analysis
        cmd = ScanCommand.__new__(ScanCommand)
        cmd.console = None

        batch_size = 1 if options.sequential else 3

        if options.shallow:
            return cmd._analyze_shallow_all_queries(
                queries,
                target,
                output_json=True,
                warn_threshold=options.warn_threshold,
                fail_threshold=options.fail_threshold,
                batch_size=batch_size,
            )
        else:
            return cmd._analyze_all_queries(
                queries,
                target,
                output_json=True,
                warn_threshold=options.warn_threshold,
                fail_threshold=options.fail_threshold,
                batch_size=batch_size,
            )
