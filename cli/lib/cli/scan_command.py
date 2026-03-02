"""
RDST Scan - Scan codebase for ORM queries and analyze them.

This command scans a codebase for database queries in ORM code (SQLAlchemy, Django, etc.),
extracts the SQL using an LLM, and optionally analyzes them for performance issues.

Commands:
    rdst scan [directory]       - Scan codebase and build query corpus
    rdst scan --list [--issues] - List queries from corpus
    rdst scan --check [--diff]  - Validate queries (for CI)

Key features:
- DETERMINISTIC extraction using AST (Abstract Syntax Tree)
- AST finds terminal methods (.all(), .first()) and extracts ORM snippets
- Small snippets are sent to LLM for ORM->SQL conversion (not whole files)
- LLM responses are cached by snippet hash for full determinism
- Same code -> Same AST -> Same snippets -> Same hashes -> Same SQL
"""

import os
import re
import sys
import json
from pathlib import Path
from typing import List, Dict, Any, Optional

from lib.ui import (
    get_console,
    StyledPanel,
    StyledTable,
    Progress,
    SpinnerColumn,
    TextColumn,
    StyleTokens,
    Icons,
)

from contextlib import contextmanager

from .rdst_cli import RdstResult


@contextmanager
def _terminal_guard():
    """Save and restore terminal settings around code that uses Rich Live/Progress.

    Rich's Progress/Live display modifies terminal state (cursor visibility,
    escape sequences for line movement). On WSL2, these modifications can
    corrupt bash's readline — making typed text and command history invisible.

    This context manager saves the exact termios settings before entry and
    restores them on exit, plus writes a hard ANSI reset as a safety net.
    """
    saved = None
    fd = None
    try:
        import termios
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
    except Exception:
        pass  # Not a terminal, termios unavailable, or piped stdin

    try:
        yield
    finally:
        # Restore termios settings if we saved them
        if saved is not None:
            try:
                import termios
                termios.tcsetattr(fd, termios.TCSADRAIN, saved)
            except Exception:
                pass
        # Hard ANSI reset as safety net: reset attributes, show cursor, re-enable line wrap
        # Only write if stdout is a real terminal (avoid leaking escapes into piped output)
        try:
            if sys.stdout.isatty():
                sys.stdout.write('\033[0m\033[?25h\033[?7h')
                sys.stdout.flush()
        except Exception:
            pass
from .scan_context import ContextGatherer
from .ast_extractor import CrossFileResolver, extract_queries_from_file, ExtractedQuery
from .js_extractor import extract_queries_from_js_file
from .snippet_cache import get_cache
from lib.query_registry.query_registry import QueryRegistry, hash_sql

# File extensions for JS/TS files (use JS extractor)
_JS_EXTENSIONS = {'.js', '.ts', '.tsx', '.jsx'}


# ORM detection patterns - comprehensive coverage for SQLAlchemy and Django
ORM_PATTERNS = {
    "sqlalchemy": [
        # Imports
        r"from sqlalchemy",
        r"import sqlalchemy",
        # Session/Query patterns (1.x style)
        r"\.query\(",
        r"\.filter\(",
        r"\.filter_by\(",
        r"\.join\(",
        r"\.outerjoin\(",
        r"\.group_by\(",
        r"\.order_by\(",
        r"\.having\(",
        r"\.distinct\(",
        r"\.limit\(",
        r"\.offset\(",
        r"\.subquery\(",
        r"\.with_entities\(",
        r"\.options\(",
        r"\.correlate\(",
        r"\.union\(",
        r"\.union_all\(",
        r"\.intersect\(",
        r"\.except_\(",
        # Terminal methods
        r"\.all\(\)",
        r"\.first\(\)",
        r"\.one\(\)",
        r"\.one_or_none\(\)",
        r"\.scalar\(",
        r"\.scalars\(",
        r"\.count\(\)",
        r"\.exists\(\)",
        r"\.fetchall\(",
        r"\.fetchone\(",
        r"\.fetchmany\(",
        # SQLAlchemy 2.0 style
        r"\bselect\(",
        r"\binsert\(",
        r"\bupdate\(",
        r"\bdelete\(",
        r"\.execute\(",
        # Session operations
        r"session\.(query|execute|add|delete|commit|flush|merge|refresh)",
        r"db\.(query|execute|session|add|commit)",
        # Relationship loading
        r"joinedload\(",
        r"subqueryload\(",
        r"selectinload\(",
        r"lazyload\(",
        r"immediateload\(",
        # Raw SQL
        r"text\(['\"]",
        # Functions
        r"func\.\w+\(",
        r"and_\(",
        r"or_\(",
        r"not_\(",
        r"case\(",
        r"cast\(",
        r"coalesce\(",
        r"nullif\(",
        r"literal\(",
        r"desc\(",
        r"asc\(",
        r"nullsfirst\(",
        r"nullslast\(",
    ],
    "django": [
        # QuerySet creation
        r"\.objects\.",
        # Filtering
        r"\.filter\(",
        r"\.exclude\(",
        r"\.get\(",
        # Terminal methods
        r"\.all\(\)",
        r"\.first\(\)",
        r"\.last\(\)",
        r"\.latest\(",
        r"\.earliest\(",
        r"\.count\(\)",
        r"\.exists\(\)",
        r"\.iterator\(",
        # Aggregation
        r"\.annotate\(",
        r"\.aggregate\(",
        # Related objects
        r"\.select_related\(",
        r"\.prefetch_related\(",
        # Output transformation
        r"\.values\(",
        r"\.values_list\(",
        r"\.only\(",
        r"\.defer\(",
        # Ordering/Distinct
        r"\.order_by\(",
        r"\.reverse\(\)",
        r"\.distinct\(",
        # Slicing is handled differently (Python slice syntax)
        # Bulk operations
        r"\.update\(",
        r"\.delete\(",
        r"\.create\(",
        r"\.bulk_create\(",
        r"\.bulk_update\(",
        r"\.get_or_create\(",
        r"\.update_or_create\(",
        r"\.in_bulk\(",
        # Raw SQL
        r"\.raw\(",
        r"\.extra\(",
        r"RawSQL\(",
        # Expressions
        r"\bF\(['\"]",
        r"\bQ\(",
        r"\bValue\(",
        r"\bCase\(",
        r"\bWhen\(",
        r"\bSubquery\(",
        r"\bExists\(",
        r"\bOuterRef\(",
        # Aggregate functions
        r"\bSum\(",
        r"\bCount\(",
        r"\bAvg\(",
        r"\bMin\(",
        r"\bMax\(",
        r"\bStdDev\(",
        r"\bVariance\(",
        # Window functions
        r"\.window\(",
        r"\bWindow\(",
        r"\bRowNumber\(",
        r"\bRank\(",
        r"\bDenseRank\(",
        # Lookups (used in filter kwargs)
        r"__exact=",
        r"__iexact=",
        r"__contains=",
        r"__icontains=",
        r"__in=",
        r"__gt=",
        r"__gte=",
        r"__lt=",
        r"__lte=",
        r"__startswith=",
        r"__istartswith=",
        r"__endswith=",
        r"__iendswith=",
        r"__range=",
        r"__isnull=",
        r"__regex=",
        r"__iregex=",
    ],
    "raw_sql": [
        r"execute\(['\"]SELECT",
        r"execute\(['\"]INSERT",
        r"execute\(['\"]UPDATE",
        r"execute\(['\"]DELETE",
        r"cursor\.execute\(",
        r"text\(['\"]SELECT",
        r"\.executemany\(",
        r"connection\.cursor\(",
    ],
    "prisma": [
        # Imports / client
        r"@prisma/client",
        r"PrismaClient",
        r"prisma\.\w+\.",
        # Query methods
        r"\.findMany\(",
        r"\.findUnique\(",
        r"\.findFirst\(",
        r"\.findFirstOrThrow\(",
        r"\.findUniqueOrThrow\(",
        # Mutations
        r"\.createMany\(",
        r"\.createManyAndReturn\(",
        r"\.updateMany\(",
        r"\.updateManyAndReturn\(",
        r"\.upsert\(",
        r"\.deleteMany\(",
        # Aggregation
        r"\.aggregate\(",
        r"\.groupBy\(",
        # Raw SQL
        r"\.\$queryRaw",
        r"\.\$queryRawUnsafe\(",
        r"\.\$executeRaw",
        r"\.\$executeRawUnsafe\(",
        # Transaction
        r"\.\$transaction\(",
        # Prisma-specific args
        r"\binclude\s*:",
        r"\bwhere\s*:",
        r"\borderBy\s*:",
        r"\btake\s*:",
        r"\bskip\s*:",
        r"\bdistinct\s*:",
    ],
    "drizzle": [
        # Imports
        r"drizzle-orm",
        r"from ['\"]drizzle-",
        # Builder starters
        r"\bdb\.select\(",
        r"\bdb\.selectDistinct\(",
        r"\bdb\.selectDistinctOn\(",
        r"\bdb\.insert\(",
        r"\bdb\.update\(",
        r"\bdb\.delete\(",
        r"\bdb\.execute\(",
        r"\bdb\.\$count\(",
        # Relational API
        r"\bdb\.query\.\w+\.",
        # Chain methods
        r"\.from\(",
        r"\.innerJoin\(",
        r"\.leftJoin\(",
        r"\.rightJoin\(",
        r"\.fullJoin\(",
        r"\.groupBy\(",
        r"\.having\(",
        r"\.orderBy\(",
        r"\.limit\(",
        r"\.offset\(",
        r"\.returning\(",
        r"\.onConflictDoNothing\(",
        r"\.onConflictDoUpdate\(",
        r"\.onDuplicateKeyUpdate\(",
        r"\.values\(",
        r"\.set\(",
        # Drizzle operators
        r"\beq\(",
        r"\bne\(",
        r"\bgt\(",
        r"\bgte\(",
        r"\blt\(",
        r"\blte\(",
        r"\blike\(",
        r"\bilike\(",
        r"\binArray\(",
        r"\bnotInArray\(",
        r"\bisNull\(",
        r"\bisNotNull\(",
        r"\bbetween\(",
        r"\band\(",
        r"\bor\(",
        r"\bnot\(",
        # Drizzle aggregate/functions
        r"\bcount\(",
        r"\bsum\(",
        r"\bavg\(",
        r"\bmin\(",
        r"\bmax\(",
        r"\bcountDistinct\(",
        # Raw SQL tag
        r"\bsql`",
        r"\bsql\.raw\(",
        # Transaction / batch
        r"\bdb\.transaction\(",
        r"\bdb\.batch\(",
        # Set operations
        r"\bunion\(",
        r"\bunionAll\(",
        r"\bintersect\(",
        r"\bexcept\(",
    ],
}


class ScanCommand:
    """Command handler for rdst scan."""

    def __init__(self, console: Optional[Any] = None):
        self.console = console or get_console()

    def execute(
        self,
        subcommand: str = "scan",
        directory: str = ".",
        dry_run: bool = False,
        analyze: bool = False,
        target: Optional[str] = None,
        output_json: bool = False,
        with_issues: bool = False,
        file_pattern: Optional[str] = None,
        diff: Optional[str] = None,
        shallow: bool = False,
        warn_threshold: int = 60,
        fail_threshold: int = 40,
        nosave: bool = False,
        sequential: bool = False,
        **kwargs
    ) -> RdstResult:
        """
        Execute a scan subcommand.

        Subcommands:
            scan  - Scan codebase and save queries to registry
            list  - List queries from registry (source=scan)
            check - Validate queries (for CI)

        Analysis modes (when --analyze is set):
            Default (deep): Requires DB connection, runs EXPLAIN ANALYZE
            --shallow: Uses schema YAML only, no DB connection needed
        """
        if subcommand == "list":
            return self._list_queries(with_issues, file_pattern, output_json)
        elif subcommand == "check":
            return self._check_queries(directory, diff, target, output_json)
        else:  # Default: scan
            return self._scan_directory(
                directory, dry_run, analyze, target, output_json,
                shallow=shallow, warn_threshold=warn_threshold, fail_threshold=fail_threshold,
                diff=diff, nosave=nosave, file_pattern=file_pattern, sequential=sequential
            )

    def _scan_directory(
        self,
        directory: str,
        dry_run: bool,
        analyze: bool,
        target: Optional[str],
        output_json: bool,
        shallow: bool = False,
        warn_threshold: int = 60,
        fail_threshold: int = 40,
        diff: Optional[str] = None,
        nosave: bool = False,
        file_pattern: Optional[str] = None,
        sequential: bool = False,
    ) -> RdstResult:
        """
        Scan a codebase for ORM queries and save to registry.

        Deterministic AST-based extraction:
        1. AST finds all terminal methods (.all(), .first(), etc.)
        2. AST extracts just the ORM snippet for each query
        3. Snippets are hashed deterministically
        4. LLM converts small snippets to SQL (cached by hash)
        5. Queries saved to registry with source="scan"

        Args:
            directory: Path to scan
            dry_run: Just show what would be scanned, don't convert or save
            analyze: Whether to run rdst analyze on extracted queries
            target: Database target for analysis
            output_json: Output results as JSON
            diff: Git ref to diff against (e.g., HEAD, main) - only scan changed files
        """
        directory = os.path.abspath(directory)

        # Accept both files and directories
        single_file = None
        if os.path.isfile(directory):
            single_file = directory
            directory = os.path.dirname(directory)
        elif not os.path.isdir(directory):
            return RdstResult(False, f"Path not found: {directory}")

        # Require target for scan
        if not target:
            return RdstResult(
                False,
                "Target required for scan.\n\n"
                "Usage: rdst scan ./path --target <target>\n\n"
                "The target database is needed to:\n"
                "  1. Load schema context for ORM→SQL conversion\n"
                "  2. Associate queries with the correct database\n"
                "  3. Run analysis (if --analyze is specified)"
            )

        # Check that schema exists for target
        schema_file = Path.home() / ".rdst" / "semantic-layer" / f"{target}.yaml"
        if not schema_file.exists():
            return RdstResult(
                False,
                f"No schema found for target '{target}'.\n\n"
                f"Scan requires a schema to understand your database structure.\n"
                f"Please run:\n\n"
                f"  rdst schema init --target {target}\n\n"
                f"This will introspect your database and create the schema."
            )

        # Step 1: Find files with ORM patterns
        if self.console and not output_json:
            if diff:
                self.console.print(f"\n[bold]Scanning[/bold] {directory} [dim](diff: {diff})[/dim]...")
            else:
                self.console.print(f"\n[bold]Scanning[/bold] {directory}...")

        if single_file:
            orm_files = self._find_orm_files_single(single_file, directory)
        else:
            orm_files = self._find_orm_files(directory)

        # If --file-pattern is provided, filter to matching files
        if file_pattern and orm_files:
            import fnmatch
            orm_files = [
                f for f in orm_files
                if fnmatch.fnmatch(f["file"], file_pattern)
                or fnmatch.fnmatch(os.path.basename(f["file"]), file_pattern)
            ]

        # If --diff is provided, filter to only changed files
        if diff and orm_files:
            import subprocess
            try:
                # First, find the git repo root
                git_root_result = subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    capture_output=True,
                    text=True,
                    cwd=directory,
                )
                if git_root_result.returncode != 0:
                    error_msg = git_root_result.stderr.strip() or "not a git repository"
                    return RdstResult(
                        False,
                        f"Not a git repository: {directory}\n\n"
                        "The --diff flag requires a git repository.\n"
                        "Either remove --diff to scan all files, or run from a git repo."
                    )

                git_root = git_root_result.stdout.strip()

                # Get changed files relative to git root
                result = subprocess.run(
                    ["git", "diff", "--name-only", diff],
                    capture_output=True,
                    text=True,
                    cwd=git_root,
                )
                if result.returncode != 0:
                    error_msg = result.stderr.strip() or "git diff failed"
                    return RdstResult(False, f"Git error: {error_msg}")

                changed_files_from_root = set(f.strip() for f in result.stdout.strip().split("\n") if f.strip())

                if not changed_files_from_root:
                    return RdstResult(
                        True,
                        "No files changed in diff.",
                        data={"files": [], "queries": [], "diff": diff, "status": "pass"}
                    )

                # Convert changed file paths to be relative to the scan directory
                # git_root: /path/to/repo
                # directory: /path/to/repo/backend
                # changed file from git: backend/app/services/file.py
                # orm_file["file"]: app/services/file.py
                scan_dir_rel_to_root = os.path.relpath(directory, git_root)
                if scan_dir_rel_to_root == ".":
                    # Scanning from git root - paths match directly
                    changed_files = changed_files_from_root
                else:
                    # Scanning from subdirectory - need to strip the prefix
                    prefix = scan_dir_rel_to_root + os.sep
                    changed_files = set()
                    for f in changed_files_from_root:
                        if f.startswith(prefix):
                            changed_files.add(f[len(prefix):])
                        elif f.startswith(scan_dir_rel_to_root + "/"):
                            # Handle both / and os.sep
                            changed_files.add(f[len(scan_dir_rel_to_root) + 1:])

                # Filter orm_files to only include changed files
                original_count = len(orm_files)
                orm_files = [f for f in orm_files if f["file"] in changed_files]

                if self.console and not output_json:
                    self.console.print(f"[dim]Git diff: {len(changed_files_from_root)} files changed, {len(orm_files)} with ORM patterns[/dim]")

            except FileNotFoundError:
                return RdstResult(
                    False,
                    "Git not found. The --diff flag requires git to be installed."
                )
            except Exception as e:
                return RdstResult(False, f"Failed to get git diff: {e}")

        if not orm_files:
            if diff:
                return RdstResult(
                    True,
                    "No ORM files in diff.",
                    data={"files": [], "queries": [], "diff": diff, "status": "pass"}
                )
            return RdstResult(True, "No files with ORM patterns found.", data={"files": [], "queries": []})

        if self.console and not output_json:
            self.console.print(f"Found [cyan]{len(orm_files)}[/cyan] files with ORM patterns\n")

        # Initialize cache (global) and cross-file resolver
        snippet_cache = get_cache("scan")  # Global snippet cache for all scan operations
        cross_file_resolver = CrossFileResolver(directory)

        # Load schema context once (for LLM conversion) - use target if provided
        schema_context = self._load_schema_context(target)
        if schema_context and self.console and not output_json:
            self.console.print(f"[dim]Loaded schema context ({len(schema_context)} chars)[/dim]")

        # Check for API key or trial token early - fail fast if not configured
        if not dry_run:
            _has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("RDST_TRIAL_TOKEN"))
            if not _has_key:
                try:
                    from ..llm_manager.key_resolution import resolve_api_key
                    resolve_api_key()
                    _has_key = True
                except Exception:
                    pass
            if not _has_key:
                return RdstResult(
                    False,
                    "No LLM API key configured.\n\n"
                    "rdst scan requires an Anthropic API key to convert ORM code to SQL.\n\n"
                    "Options:\n"
                    "  1. Run 'rdst init' to sign up for a free trial (up to 925K tokens)\n"
                    "  2. Set your own key: export ANTHROPIC_API_KEY=\"sk-ant-...\"\n"
                    "     Get one at: https://console.anthropic.com/"
                )

        # Step 2: AST-based extraction (DETERMINISTIC)
        all_queries = []
        cache_hits = 0
        cache_misses = 0

        if self.console and not output_json:
            with _terminal_guard():
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=self.console
                ) as progress:
                    task = progress.add_task("Extracting queries (AST)...", total=len(orm_files))

                    for file_info in orm_files:
                        filepath = file_info["file"]
                        full_path = os.path.join(directory, filepath)
                        progress.update(task, description=f"Parsing {filepath}...")

                        # Route to appropriate extractor based on file type
                        _, ext = os.path.splitext(full_path)
                        if ext in _JS_EXTENSIONS:
                            extracted = extract_queries_from_js_file(full_path)
                        else:
                            extracted = extract_queries_from_file(full_path)

                        for eq in extracted:
                            # Handle cross-file query builders
                            if eq.imports_query_builder and eq.imported_builder_name:
                                builder_query = cross_file_resolver.resolve_query_builder(
                                    full_path,
                                    eq.imported_builder_name,
                                    eq.imported_builder_module or ""
                                )
                                if builder_query:
                                    # Merge the builder's ORM code
                                    eq.orm_snippet = f"# From {eq.imported_builder_module}.{eq.imported_builder_name}:\n{builder_query.orm_snippet}\n# Called as:\n{eq.orm_snippet}"
                                    eq.snippet_hash = self._hash_snippet(eq.orm_snippet)

                            # Convert to dict format
                            query_dict = self._ast_query_to_dict(eq, filepath)

                            # Check cache first
                            if not dry_run:
                                cached_result = snippet_cache.get(eq.snippet_hash)
                                if cached_result:
                                    query_dict["sql"] = cached_result["sql"]
                                    query_dict["issues"] = cached_result["issues"]
                                    cache_hits += 1
                                # Mark for batch processing if not cached
                                elif not cached_result:
                                    query_dict["_needs_llm"] = True
                                    cache_misses += 1

                            all_queries.append(query_dict)

                        progress.advance(task)

                    # Batch process uncached queries (5 at a time for Haiku efficiency)
                    if not dry_run:
                        uncached = [q for q in all_queries if q.get("_needs_llm")]
                        if uncached:
                            progress.update(task, description="Converting ORM to SQL (batch)...")
                            self._batch_convert_snippets(uncached, snippet_cache, schema_context, batch_size=5, target=target)
                            for q in uncached:
                                q.pop("_needs_llm", None)
        else:
            # Non-progress-bar path (JSON output)
            for file_info in orm_files:
                filepath = file_info["file"]
                full_path = os.path.join(directory, filepath)

                # Route to appropriate extractor based on file type
                _, ext = os.path.splitext(full_path)
                if ext in _JS_EXTENSIONS:
                    extracted = extract_queries_from_js_file(full_path)
                else:
                    extracted = extract_queries_from_file(full_path)

                for eq in extracted:
                    if eq.imports_query_builder and eq.imported_builder_name:
                        builder_query = cross_file_resolver.resolve_query_builder(
                            full_path,
                            eq.imported_builder_name,
                            eq.imported_builder_module or ""
                        )
                        if builder_query:
                            eq.orm_snippet = f"# From {eq.imported_builder_module}.{eq.imported_builder_name}:\n{builder_query.orm_snippet}\n# Called as:\n{eq.orm_snippet}"
                            eq.snippet_hash = self._hash_snippet(eq.orm_snippet)

                    query_dict = self._ast_query_to_dict(eq, filepath)

                    if not dry_run:
                        cached_result = snippet_cache.get(eq.snippet_hash)
                        if cached_result:
                            query_dict["sql"] = cached_result["sql"]
                            query_dict["issues"] = cached_result["issues"]
                            cache_hits += 1
                        else:
                            query_dict["_needs_llm"] = True
                            cache_misses += 1

                    all_queries.append(query_dict)

            # Batch process uncached queries
            if not dry_run:
                uncached = [q for q in all_queries if q.get("_needs_llm")]
                if uncached:
                    self._batch_convert_snippets(uncached, snippet_cache, schema_context, batch_size=5, target=target)
                    for q in uncached:
                        q.pop("_needs_llm", None)

        # Tag every query with a status and skip_reason.
        # Infer specific, human-readable reasons from the ORM snippet.
        # Only tag after LLM conversion (not in dry-run mode).
        if not dry_run:
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

        # Save to query registry (unless --nosave)
        new_query_count = 0
        updated_query_count = 0
        registry_path = ""

        if not nosave:
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
                        target=target or "",
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

            registry_path = str(registry.registry_path)
        else:
            for q in all_queries:
                sql = q.get("sql", "")
                if q["status"] == "sql":
                    q["hash"] = hash_sql(sql)

        results = {
            "files": orm_files,
            "queries": all_queries,
            "issues_found": [],
            "extraction": {
                "method": "ast",
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "deterministic": True,
            },
            "registry": {
                "new_queries": new_query_count,
                "updated_queries": updated_query_count,
                "total_queries": 0 if nosave else len(registry.list_queries()),
                "path": registry_path,
                "skipped": nosave,
            }
        }

        for q in all_queries:
            if q.get("issues"):
                for issue in q["issues"]:
                    results["issues_found"].append({
                        "file": q["file"],
                        "function": q.get("function", "unknown"),
                        "issue": issue,
                        "sql": q.get("sql", "")
                    })

        # Run full analysis on each query if --analyze flag is set
        if analyze and target:
            # Fail immediately if no API key or trial token — don't silently produce empty results
            import os as _os
            _has_key = bool(_os.environ.get("ANTHROPIC_API_KEY") or _os.environ.get("RDST_TRIAL_TOKEN"))
            if not _has_key:
                try:
                    from ..llm_manager.key_resolution import resolve_api_key
                    resolve_api_key()
                    _has_key = True
                except Exception:
                    pass
            if not _has_key:
                error_msg = (
                    "No LLM API key configured. Cannot run analysis.\n\n"
                    "Options:\n"
                    "  1. Run 'rdst init' to sign up for a free trial (up to 925K tokens)\n"
                    "  2. Set your own key: export ANTHROPIC_API_KEY=\"sk-ant-...\"\n"
                    "     Get one at: https://console.anthropic.com/"
                )
                if output_json:
                    results["analysis"] = {"error": error_msg, "ci_status": "fail", "ci_exit_code": 1}
                    return RdstResult(False, json.dumps(results, indent=2), data=results)
                return RdstResult(False, error_msg)

            batch_size = 1 if sequential else 3

            if shallow:
                results["analysis"] = self._analyze_shallow_all_queries(
                    all_queries, target, output_json, warn_threshold, fail_threshold, batch_size=batch_size
                )
            else:
                # Deep analysis needs DB — validate connection early
                from .rdst_cli import TargetsConfig
                tc = TargetsConfig()
                tc.load()
                tgt = tc.get(target)
                if tgt:
                    pw_env = tgt.get("password_env", "")
                    if pw_env and not _os.environ.get(pw_env):
                        error_msg = (
                            f"Database password not set. Deep analysis requires a database connection.\n"
                            f"Export the password: export {pw_env}=<password>\n"
                            f"(Configured in ~/.rdst/config.toml for target '{target}')"
                        )
                        if output_json:
                            results["analysis"] = {"error": error_msg, "ci_status": "fail", "ci_exit_code": 1}
                            return RdstResult(False, json.dumps(results, indent=2), data=results)
                        return RdstResult(False, error_msg)

                results["analysis"] = self._analyze_all_queries(
                    all_queries, target, output_json, warn_threshold, fail_threshold, batch_size=batch_size
                )

        if target:
            results["target"] = target

        if output_json:
            return RdstResult(True, json.dumps(results, indent=2), data=results)

        self._print_report(results, show_analysis=(analyze and target))

        return RdstResult(True, "")

    def _ast_query_to_dict(self, eq: ExtractedQuery, filepath: str) -> Dict:
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
            "sql": "",  # Filled by LLM conversion
            "issues": [],  # Filled by LLM conversion
        }

    def _hash_snippet(self, snippet: str) -> str:
        """Generate deterministic hash for ORM snippet."""
        import hashlib
        normalized = ' '.join(snippet.split())
        return hashlib.md5(normalized.encode()).hexdigest()[:12]

    def _detect_sql_dialect(self, target: Optional[str] = None) -> str:
        """Detect SQL dialect from target's semantic layer YAML."""
        if not target:
            return "PostgreSQL"
        schema_file = Path.home() / ".rdst" / "semantic-layer" / f"{target}.yaml"
        if schema_file.exists():
            try:
                content = schema_file.read_text()
                if 'mysql' in content.lower():
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
            'sqlalchemy': 'SQLAlchemy',
            'django': 'Django',
            'prisma': 'Prisma',
            'drizzle': 'Drizzle',
            'raw_sql': 'Raw SQL',
        }
        return '/'.join(name_map.get(t, t) for t in sorted(orm_types))

    def _batch_convert_snippets(
        self,
        queries: List[Dict],
        snippet_cache,
        schema_context: str,
        batch_size: int = 5,
        target: Optional[str] = None,
    ):
        """
        Convert multiple ORM snippets to SQL in batches.
        Uses Haiku for efficiency, processes batch_size at a time.
        Returns JSON for clean parsing.
        """
        from lib.llm_manager.llm_manager import LLMManager
        llm = LLMManager()

        schema_section = f"\n\nDatabase Schema:\n{schema_context}" if schema_context else ""
        sql_dialect = self._detect_sql_dialect(target)

        for i in range(0, len(queries), batch_size):
            batch = queries[i:i + batch_size]

            # Detect ORM types in this batch for prompt context
            orm_desc = self._describe_orm_types(batch)

            # Build batch prompt with numbered snippets
            snippets_list = []
            for j, q in enumerate(batch):
                snippets_list.append(f'{j+1}. {q.get("orm_code", "")}')
            snippets_text = "\n\n".join(snippets_list)

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

            user_query = f"""Convert these {len(batch)} ORM snippets to SQL.

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

                # Clean up markdown code blocks if present
                if result_text.startswith("```"):
                    lines = result_text.split("\n")
                    # Remove first line (```json) and last line (```)
                    result_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

                # Parse JSON response
                parsed = json.loads(result_text)
                sql_list = parsed.get("queries", [])

                # Assign SQL to each query in batch
                for j, q in enumerate(batch):
                    if j < len(sql_list):
                        sql = sql_list[j].strip()
                        # Clean any remaining backticks
                        sql = sql.strip("`").strip()
                        if sql.lower().startswith("sql\n"):
                            sql = sql[4:].strip()
                        q["sql"] = sql
                        q["issues"] = self._detect_issues(sql)
                        # Cache it
                        snippet_cache.set(
                            q.get("snippet_hash", ""),
                            sql,
                            q["issues"],
                            q.get("orm_code", "")
                        )
                    else:
                        q["sql"] = "-- Conversion failed"
                        q["issues"] = ["LLM did not return SQL for this query"]

            except json.JSONDecodeError as e:
                # JSON parsing failed - try line-by-line fallback
                for q in batch:
                    if not q.get("sql"):
                        q["sql"] = f"-- JSON parse error: {e}"
                        q["issues"] = ["LLM response was not valid JSON"]

            except Exception as e:
                # Fallback: mark batch as failed
                for q in batch:
                    if not q.get("sql"):
                        q["sql"] = f"-- Batch conversion error: {e}"
                        q["issues"] = ["LLM conversion failed"]

    def _infer_skip_reason(self, sql: str, orm_code: str, q: Dict) -> str:
        """Infer a specific, human-readable skip reason from the ORM snippet and LLM output."""
        sql_lower = (sql or "").lower()
        orm_lower = orm_code.lower()

        # Cross-file: query built in another file, executed here
        if q.get("imports_builder"):
            return "Cross-file query — built in another module, can't trace statically"
        if "-- cross-file" in sql_lower:
            return "Cross-file query — built in another module, can't trace statically"

        # Dynamic arguments: **kwargs, spread, variable dicts
        if "-- dynamic" in sql_lower:
            return "Dynamic arguments — variable contents only known at runtime"
        if "**" in orm_code:
            return "Dynamic arguments — **kwargs expanded at runtime"
        if "...item" in orm_code or "...data" in orm_code or "...user" in orm_code:
            return "Dynamic arguments — spread operator expanded at runtime"

        # Result-only fetches: cursor.fetchall() / fetchone() without the execute()
        if re.search(r'cursor\.(fetchall|fetchone|fetchmany)\b', orm_code):
            return "Result fetch only — the SQL is in the preceding execute() call"
        if re.search(r'\.(fetchall|fetchone|fetchmany)\(\)', orm_code) and 'execute' not in orm_lower:
            return "Result fetch only — the SQL is in a separate execute() call"

        # Bulk operations with variable lists
        if 'bulk_create' in orm_lower or 'bulk_update' in orm_lower:
            return "Bulk operation — list of objects built at runtime"
        if 'createMany' in orm_code or 'updateMany' in orm_code:
            if any(v in orm_code for v in ['...', 'data:', 'items']):
                return "Bulk operation — data array built at runtime"

        # Generic "not a query" from LLM — try to explain why
        if "-- not a query" in sql_lower or not sql:
            # Check for common non-query patterns
            if re.search(r'\.(save|commit|flush|close|rollback)\(', orm_code):
                return "Session operation, not a query"
            if re.search(r'(get_or_create|update_or_create)\(', orm_code) and 'defaults=' in orm_code:
                return "Upsert with dynamic defaults — default values only known at runtime"
            return "Could not convert to SQL — ORM snippet is ambiguous or incomplete"

        # Fallback: use whatever the LLM said
        reason = sql.lstrip("- ").strip()
        return reason if reason else "Could not convert to SQL"

    def _detect_issues(self, sql: str) -> List[str]:
        """Detect common SQL issues."""
        issues = []
        sql_upper = sql.upper()
        if "SELECT *" in sql_upper:
            issues.append("Uses SELECT * - consider selecting specific columns")
        if "WHERE" in sql_upper and "LIMIT" not in sql_upper:
            issues.append("No LIMIT clause - could return many rows")
        if "LIKE" in sql_upper and "'%" in sql:
            issues.append("Leading wildcard in LIKE - may prevent index usage")
        return issues

    def _analyze_single_query(self, q: Dict, target: str) -> Dict:
        """
        Analyze a single query using subprocess to fully isolate output.
        Returns a dict with success status and extracted metrics.
        """
        import subprocess
        import json as json_module
        import time as _t

        sql = q.get("sql", "")
        query_hash = q.get("hash", "")

        try:
            # Run rdst analyze as subprocess with JSON output (suppresses all Rich output)
            cmd = [
                "python3", "rdst.py", "analyze",
                "-q", sql,
                "--target", target,
                "--json",
                "--skip-warning",
            ]

            _start = _t.time()
            # Run from rdst's own directory (where rdst.py lives)
            rdst_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,  # Isolate subprocess from terminal
                timeout=60,  # 60s timeout — if EXPLAIN ANALYZE is slow, fall back to --fast
                cwd=rdst_dir,
            )
            _elapsed = _t.time() - _start

            if proc.returncode == 0 and proc.stdout.strip():
                # Parse JSON from subprocess output.
                # The output may contain spinner/progress text before the JSON
                # when Rich console writes to stdout. Extract the JSON portion.
                raw_output = proc.stdout.strip()
                data = None
                try:
                    data = json_module.loads(raw_output)
                except json_module.JSONDecodeError:
                    # Extract JSON object from mixed output
                    start = raw_output.find('{')
                    end = raw_output.rfind('}')
                    if start >= 0 and end > start:
                        try:
                            data = json_module.loads(raw_output[start:end + 1])
                        except json_module.JSONDecodeError:
                            pass

                if data is None:
                    return {
                        "success": False,
                        "hash": query_hash,
                        "error": f"Invalid JSON output from analyze (rc={proc.returncode}, {len(raw_output)} bytes)",
                        "_subprocess_seconds": round(_elapsed, 1),
                        "_stderr": (proc.stderr or "")[:200],
                    }

                explain_results = data.get("explain_results") or {}
                llm_analysis = data.get("llm_analysis") or {}

                exec_time_ms = explain_results.get("execution_time_ms")

                rating = None
                risk_score = None
                issues = []
                recommendations = []

                if llm_analysis and llm_analysis.get("success"):
                    analysis_res = llm_analysis.get("analysis_results") or {}
                    performance = analysis_res.get("performance_assessment") or {}
                    rating = performance.get("overall_rating")
                    risk_score = performance.get("efficiency_score")

                    for concern in (performance.get("primary_concerns") or []):
                        issues.append(concern if isinstance(concern, str) else str(concern))

                    for opp in (analysis_res.get("optimization_opportunities") or []):
                        issue = opp if isinstance(opp, str) else opp.get("description", str(opp))
                        issues.append(issue)

                    for rec in (analysis_res.get("index_recommendations") or []):
                        if isinstance(rec, str):
                            recommendations.append(rec)
                        else:
                            idx_stmt = rec.get("index_statement") or rec.get("sql") or str(rec)
                            rationale = rec.get("rationale") or rec.get("reason", "")
                            if rationale:
                                recommendations.append(f"Index: {idx_stmt} — {rationale}")
                            else:
                                recommendations.append(f"Index: {idx_stmt}")

                    for sug in (analysis_res.get("rewrite_suggestions") or []):
                        if isinstance(sug, str):
                            recommendations.append(f"Rewrite: {sug}")
                        else:
                            sug_sql = sug.get("rewritten_sql") or sug.get("rewritten_query") or ""
                            explanation = sug.get("explanation", "")
                            improvement = sug.get("expected_improvement", "")
                            parts = [f"Rewrite: {sug_sql}"] if sug_sql else []
                            if explanation:
                                parts.append(f"  Why: {explanation}")
                            if improvement:
                                parts.append(f"  Expected: {improvement}")
                            recommendations.append("\n".join(parts) if parts else str(sug))

                # Extract rewrite benchmark results (actual EXPLAIN ANALYZE comparison)
                rewrite_test = data.get("rewrite_test_results") or {}
                rewrite_benchmarks = []
                if rewrite_test and rewrite_test.get("success"):
                    orig_perf = rewrite_test.get("original_performance") or {}
                    orig_ms = orig_perf.get("execution_time_ms")
                    for rr in (rewrite_test.get("rewrite_results") or []):
                        if not rr.get("success"):
                            continue
                        rr_perf = rr.get("performance") or {}
                        rr_ms = rr_perf.get("execution_time_ms")
                        rr_sql = (rr.get("rewritten_sql") or "")[:200]
                        imp = rr.get("improvement") or {}
                        speedup = imp.get("speedup_factor")
                        pct = imp.get("percentage_improvement")
                        parts = []
                        if rr_sql:
                            parts.append(f"Tested rewrite: {rr_sql}")
                        if orig_ms is not None and rr_ms is not None:
                            parts.append(f"  {orig_ms:.1f}ms → {rr_ms:.1f}ms")
                        if speedup and speedup > 1:
                            parts.append(f"  {speedup:.1f}x faster")
                        elif pct:
                            parts.append(f"  {pct:.0f}% improvement")
                        if parts:
                            rewrite_benchmarks.append("\n".join(parts))

                return {
                    "success": True,
                    "hash": query_hash,
                    "file": q.get("file", ""),
                    "function": q.get("function", ""),
                    "line": q.get("start_line", 0),
                    "sql": sql,
                    "execution_time_ms": exec_time_ms,
                    "risk_score": risk_score,
                    "rating": rating,
                    "issues": issues,
                    "recommendations": recommendations,
                    "rewrite_benchmarks": rewrite_benchmarks,
                    "_subprocess_seconds": round(_elapsed, 1),
                    "_llm_ran": bool(llm_analysis and llm_analysis.get("success")),
                    "_llm_error": (
                        llm_analysis.get("error", "") or llm_analysis.get("message", "")
                        or explain_results.get("error", "")
                    ) if llm_analysis and not llm_analysis.get("success") else "",
                }
            else:
                # Get error details — prefer JSON from stdout (has structured error),
                # fall back to stderr (rdst.py prints "Error: <msg>" there)
                error = ""

                # Try stdout JSON first — analyze --json writes errors here
                if proc.stdout:
                    raw = proc.stdout.strip()
                    # Try parsing the last JSON object (stdout may have Rich panels before it)
                    last_brace = raw.rfind('}')
                    if last_brace >= 0:
                        # Find the matching opening brace
                        start = raw.rfind('{', 0, last_brace)
                        if start >= 0:
                            try:
                                data = json_module.loads(raw[start:last_brace + 1])
                                error = data.get("error", "")
                            except (json_module.JSONDecodeError, ValueError):
                                pass

                # Fall back to stderr if no JSON error found
                if not error:
                    stderr_text = (proc.stderr or "").strip()
                    if stderr_text:
                        error = stderr_text.replace("Error: ", "", 1).strip()

                if not error:
                    error = f"Analysis failed (rc={proc.returncode})"
                return {
                    "success": False,
                    "hash": query_hash,
                    "error": error,
                    "_subprocess_seconds": round(_elapsed, 1),
                }

        except subprocess.TimeoutExpired:
            # Retry with --fast (skips EXPLAIN ANALYZE, uses EXPLAIN only)
            try:
                fast_cmd = [
                    "python3", "rdst.py", "analyze",
                    "-q", sql,
                    "--target", target,
                    "--json",
                    "--fast",
                    "--skip-warning",
                ]
                _start = _t.time()
                proc = subprocess.run(
                    fast_cmd,
                    capture_output=True,
                    text=True,
                    stdin=subprocess.DEVNULL,
                    timeout=60,  # --fast skips EXPLAIN ANALYZE, should be quick
                    cwd=rdst_dir,
                )
                _elapsed = _t.time() - _start

                if proc.returncode == 0 and proc.stdout.strip():
                    raw_output = proc.stdout.strip()
                    data = None
                    try:
                        data = json_module.loads(raw_output)
                    except json_module.JSONDecodeError:
                        start = raw_output.find('{')
                        end = raw_output.rfind('}')
                        if start >= 0 and end > start:
                            try:
                                data = json_module.loads(raw_output[start:end + 1])
                            except json_module.JSONDecodeError:
                                pass

                    if data is not None:
                        explain_results = data.get("explain_results") or {}
                        llm_analysis = data.get("llm_analysis") or {}
                        exec_time_ms = explain_results.get("execution_time_ms")

                        rating = None
                        risk_score = None
                        issues = ["EXPLAIN ANALYZE timed out — analyzed with EXPLAIN only"]
                        recommendations = []

                        if llm_analysis and llm_analysis.get("success"):
                            analysis_res = llm_analysis.get("analysis_results") or {}
                            performance = analysis_res.get("performance_assessment") or {}
                            rating = performance.get("overall_rating")
                            risk_score = performance.get("efficiency_score")

                            for concern in (performance.get("primary_concerns") or []):
                                issues.append(concern if isinstance(concern, str) else str(concern))
                            for opp in (analysis_res.get("optimization_opportunities") or []):
                                issue = opp if isinstance(opp, str) else opp.get("description", str(opp))
                                issues.append(issue)
                            for rec in (analysis_res.get("index_recommendations") or []):
                                if isinstance(rec, str):
                                    recommendations.append(rec)
                                else:
                                    idx_stmt = rec.get("index_statement") or rec.get("sql") or str(rec)
                                    rationale = rec.get("rationale") or rec.get("reason", "")
                                    if rationale:
                                        recommendations.append(f"Index: {idx_stmt} — {rationale}")
                                    else:
                                        recommendations.append(f"Index: {idx_stmt}")

                        return {
                            "success": True,
                            "hash": query_hash,
                            "file": q.get("file", ""),
                            "function": q.get("function", ""),
                            "line": q.get("start_line", 0),
                            "sql": sql,
                            "rating": rating,
                            "risk_score": risk_score,
                            "execution_time_ms": exec_time_ms,
                            "issues": issues,
                            "recommendations": recommendations,
                            "rewrite_benchmarks": [],
                            "fast_mode": True,
                            "_subprocess_seconds": round(_elapsed, 1),
                            "_llm_ran": bool(llm_analysis and llm_analysis.get("success")),
                            "_llm_error": "",
                        }

            except Exception:
                pass

            return {
                "success": False,
                "hash": query_hash,
                "error": "Timeout (EXPLAIN ANALYZE and fast mode both failed)",
            }
        except Exception as e:
            return {
                "success": False,
                "hash": query_hash,
                "error": str(e)[:40],
            }

    def _analyze_all_queries(
        self,
        queries: List[Dict],
        target: str,
        output_json: bool,
        warn_threshold: int = 60,
        fail_threshold: int = 40,
        batch_size: int = 5,
    ) -> Dict:
        """
        Run rdst analyze on all discovered queries in parallel batches.

        Args:
            queries: List of query dicts from scan
            target: Database target name
            output_json: Whether to output JSON
            warn_threshold: Risk score below this triggers warning
            fail_threshold: Risk score below this triggers failure
            batch_size: Number of queries to analyze in parallel (default 10)

        Returns aggregated analysis results with performance issues and recommendations.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        analysis_results = {
            "total_analyzed": 0,
            "successful": 0,
            "failed": 0,
            "performance_issues": [],
            "recommendations": [],
            "by_query": [],
            "failed_queries": [],
            "mode": "deep",
            "ci_status": "pass",
            "ci_exit_code": 0,
            "warn_threshold": warn_threshold,
            "fail_threshold": fail_threshold,
        }

        # Filter to valid SQL queries (skip comments, not-a-query, malformed, etc.)
        # Auto-substitute sample parameter values so EXPLAIN ANALYZE can run
        from .parameter_prompt import detect_placeholders, substitute_placeholders

        # Build column type map from semantic layer YAML for accurate parameter inference
        _col_type_map = {}  # "column_name" → "int"|"string"|"decimal"|"date"|"enum"|...
        _enum_values = {}   # "column_name" → first enum value (for realistic samples)
        if target:
            try:
                import yaml
                schema_path = os.path.expanduser(f"~/.rdst/semantic-layer/{target}.yaml")
                if os.path.exists(schema_path):
                    with open(schema_path) as f:
                        schema = yaml.safe_load(f)
                    for table_name, table_info in (schema.get("tables") or {}).items():
                        for col_name, col_info in (table_info.get("columns") or {}).items():
                            col_type = col_info.get("type", "string") if isinstance(col_info, dict) else "string"
                            _col_type_map[col_name.lower()] = col_type
                            if col_type == "enum" and isinstance(col_info, dict):
                                enum_vals = col_info.get("enum_values", {})
                                if enum_vals:
                                    first_val = next(iter(enum_vals)).strip()
                                    _enum_values[col_name.lower()] = first_val
            except Exception:
                pass  # Schema unavailable — fall back to heuristics

        # Sample values by type — used when we know the column type from schema
        _type_samples = {
            "int": 1,
            "integer": 1,
            "bigint": 1,
            "smallint": 1,
            "serial": 1,
            "decimal": 1000.00,
            "numeric": 1000.00,
            "float": 1000.00,
            "double": 1000.00,
            "real": 1000.00,
            "money": 1000.00,
            "string": "sample",
            "text": "sample",
            "varchar": "sample",
            "char": "sample",
            "date": "1995-06-15",
            "timestamp": "1995-06-15 00:00:00",
            "datetime": "1995-06-15 00:00:00",
            "boolean": True,
            "bool": True,
            "enum": "sample",
        }

        def _infer_sample_value(sql, placeholder, position):
            """Infer a sample parameter value using the schema YAML column types.

            Priority:
            1. LIMIT/OFFSET keywords → integer
            2. Column name from SQL context → look up type in schema YAML
            3. LIKE keyword → string with wildcards
            4. VALUES clause → look up column from INSERT column list
            5. Fall back to integer (1) — safer than string for most WHERE clauses
            """
            sql_upper = sql.upper()

            # LIMIT/OFFSET → small integer
            if re.search(r'\b(LIMIT|OFFSET)\s+' + re.escape(placeholder), sql_upper):
                return 10

            # LIKE → string with wildcards
            if re.search(r'\bLIKE\s+' + re.escape(placeholder), sql_upper):
                return '%sample%'

            # Find column name from context: "column = $1", "column > $1", "column IN ($1)"
            col_match = re.search(
                r'(\w+)\s*(?:[=<>!]+|(?:NOT\s+)?IN\s*\()\s*' + re.escape(placeholder),
                sql, re.IGNORECASE
            )
            if col_match:
                col = col_match.group(1).lower()
                # Look up in schema
                if col in _col_type_map:
                    col_type = _col_type_map[col]
                    if col_type == "enum" and col in _enum_values:
                        return _enum_values[col]
                    return _type_samples.get(col_type, 1)

            # VALUES clause — try to match positional columns from INSERT column list
            insert_match = re.search(r'INSERT\s+INTO\s+\w+\s*\(([^)]+)\)', sql, re.IGNORECASE)
            if insert_match and 'VALUES' in sql_upper:
                columns = [c.strip().lower() for c in insert_match.group(1).split(',')]
                if position < len(columns):
                    col = columns[position]
                    if col in _col_type_map:
                        col_type = _col_type_map[col]
                        if col_type == "enum" and col in _enum_values:
                            return _enum_values[col]
                        return _type_samples.get(col_type, 1)

            # Default to integer — works for most WHERE parameters
            return 1

        valid_queries = []
        skipped_queries = [q for q in queries if q.get("status") == "skipped"]
        params_substituted = 0
        for q in queries:
            if q.get("status") != "sql":
                continue
            sql = q.get("sql", "")
            # Auto-generate sample values for parameterized queries
            placeholders = detect_placeholders(sql)
            if placeholders:
                sample_values = {}
                for placeholder, position in placeholders:
                    sample_values[position] = _infer_sample_value(sql, placeholder, position)
                q["_original_sql"] = sql
                q["sql"] = substitute_placeholders(sql, sample_values)
                params_substituted += 1
            valid_queries.append(q)

        # Store skipped queries in results so display can show them
        analysis_results["skipped_queries"] = skipped_queries

        if not valid_queries:
            return analysis_results

        total_queries = len(valid_queries)


        # Map hash → query info for display
        query_info = {}
        for q in valid_queries:
            h = q.get("hash", "")
            func = q.get("function", "?")
            fname = q.get("file", "?")
            query_info[h] = {"function": func, "file": fname, "status": "pending"}

        completed_count = 0

        def _fmt_query_result(func_name: str, result: dict) -> str:
            """Format a single-line diagnostic for a completed query using Rich markup."""
            secs = result.get("_subprocess_seconds", "?")
            if result.get("success"):
                score = result.get("risk_score")
                rating = result.get("rating", "?")
                exec_ms = result.get("execution_time_ms")
                llm_ran = result.get("_llm_ran", False)
                llm_error = result.get("_llm_error", "")
                parts = [f"{secs}s"]
                if exec_ms is not None:
                    parts.append(f"exec:{exec_ms:.0f}ms")
                if score is not None:
                    parts.append(f"score:{score}")
                if rating:
                    parts.append(rating)
                if llm_ran and score is not None:
                    if result.get("fast_mode"):
                        return f"  [{StyleTokens.WARNING}]{Icons.WARNING}[/{StyleTokens.WARNING}] {func_name}() {Icons.ARROW} {', '.join(parts)} (EXPLAIN only, timed out)"
                    return f"  [{StyleTokens.SUCCESS}]{Icons.CHECK}[/{StyleTokens.SUCCESS}] {func_name}() {Icons.ARROW} {', '.join(parts)}"
                else:
                    err_hint = llm_error if llm_error else "no LLM score"
                    return f"  [{StyleTokens.WARNING}]{Icons.WARNING}[/{StyleTokens.WARNING}] {func_name}() {Icons.ARROW} {secs}s, {err_hint}"
            else:
                error = result.get("error", "unknown error")
                return f"  [{StyleTokens.ERROR}]{Icons.CROSS}[/{StyleTokens.ERROR}] {func_name}() {Icons.ARROW} {secs}s, FAILED: {error}"

        use_progress = self.console and not output_json

        if use_progress:
            self.console.print(f"\n[bold]Deep analysis[/bold] (EXPLAIN ANALYZE + LLM) of [{StyleTokens.HIGHLIGHT}]{total_queries}[/{StyleTokens.HIGHLIGHT}] queries...")

            # Collect per-query result lines to print AFTER Progress exits.
            # Printing inside a Live context corrupts WSL2 terminal state.
            completed_lines = []

            with _terminal_guard():
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=self.console,
                ) as progress:
                    ptask = progress.add_task(f"Analyzing 0/{total_queries}...", total=total_queries)

                    for batch_start in range(0, total_queries, batch_size):
                        batch = valid_queries[batch_start:batch_start + batch_size]

                        for q in batch:
                            query_info[q.get("hash", "")]["status"] = "running"

                        running_names = [v["function"] for v in query_info.values() if v["status"] == "running"]
                        progress.update(ptask, description=f"Analyzing {', '.join(running_names[:3])}...")

                        with ThreadPoolExecutor(max_workers=batch_size) as executor:
                            future_to_query = {
                                executor.submit(self._analyze_single_query, q, target): q
                                for q in batch
                            }
                            for future in as_completed(future_to_query):
                                q = future_to_query[future]
                                qhash = q.get("hash", "")
                                func_name = q.get("function", "?")
                                try:
                                    result = future.result()
                                except Exception as e:
                                    result = {
                                        "success": False,
                                        "hash": qhash,
                                        "error": str(e)[:40],
                                        "_subprocess_seconds": "?",
                                    }

                                analysis_results["total_analyzed"] += 1

                                if result.get("success"):
                                    analysis_results["successful"] += 1
                                    query_info[qhash]["status"] = "ok"

                                    query_result = {
                                        "hash": result["hash"],
                                        "file": result["file"],
                                        "function": result["function"],
                                        "line": result.get("line", 0),
                                        "sql": result["sql"],
                                        "execution_time_ms": result["execution_time_ms"],
                                        "risk_score": result.get("risk_score"),
                                        "rating": result["rating"],
                                        "issues": result["issues"],
                                        "recommendations": result["recommendations"],
                                        "rewrite_benchmarks": result.get("rewrite_benchmarks", []),
                                    }

                                    for issue in result["issues"]:
                                        analysis_results["performance_issues"].append({
                                            "hash": result["hash"],
                                            "file": result["file"],
                                            "issue": issue,
                                        })

                                    for rec in result["recommendations"]:
                                        analysis_results["recommendations"].append({
                                            "hash": result["hash"],
                                            "recommendation": rec,
                                        })

                                    analysis_results["by_query"].append(query_result)
                                else:
                                    analysis_results["failed"] += 1
                                    query_info[qhash]["status"] = "failed"
                                    analysis_results["failed_queries"].append({
                                        "hash": qhash,
                                        "function": q.get("function", ""),
                                        "sql": q.get("sql", ""),
                                        "error": result.get("error", "Unknown error"),
                                    })

                                # Collect result line — printed after Progress exits
                                completed_lines.append(_fmt_query_result(func_name, result))
                                done = analysis_results["total_analyzed"]
                                progress.update(ptask, completed=done, description=f"Analyzed {done}/{total_queries}...")
            # Print per-query results now that Progress/Live is fully gone
            for line in completed_lines:
                self.console.print(line)

        else:
            # JSON output mode — no progress display
            for batch_start in range(0, total_queries, batch_size):
                batch = valid_queries[batch_start:batch_start + batch_size]

                with ThreadPoolExecutor(max_workers=batch_size) as executor:
                    future_to_query = {
                        executor.submit(self._analyze_single_query, q, target): q
                        for q in batch
                    }
                    for future in as_completed(future_to_query):
                        q = future_to_query[future]
                        qhash = q.get("hash", "")
                        try:
                            result = future.result()
                        except Exception as e:
                            result = {
                                "success": False,
                                "hash": qhash,
                                "error": str(e)[:40],
                            }

                        analysis_results["total_analyzed"] += 1

                        if result.get("success"):
                            analysis_results["successful"] += 1

                            query_result = {
                                "hash": result["hash"],
                                "file": result["file"],
                                "function": result["function"],
                                "line": result.get("line", 0),
                                "sql": result["sql"],
                                "execution_time_ms": result["execution_time_ms"],
                                "risk_score": result.get("risk_score"),
                                "rating": result["rating"],
                                "issues": result["issues"],
                                "recommendations": result["recommendations"],
                                "rewrite_benchmarks": result.get("rewrite_benchmarks", []),
                            }

                            for issue in result["issues"]:
                                analysis_results["performance_issues"].append({
                                    "hash": result["hash"],
                                    "file": result["file"],
                                    "issue": issue,
                                })

                            for rec in result["recommendations"]:
                                analysis_results["recommendations"].append({
                                    "hash": result["hash"],
                                    "recommendation": rec,
                                })

                            analysis_results["by_query"].append(query_result)
                        else:
                            analysis_results["failed"] += 1
                            analysis_results["failed_queries"].append({
                                "hash": qhash,
                                "function": q.get("function", ""),
                                "sql": q.get("sql", ""),
                                "error": result.get("error", "Unknown error"),
                            })

        # Compute CI status from risk scores (same logic as shallow)
        scored_queries = [q for q in analysis_results["by_query"] if q.get("risk_score") is not None]
        if scored_queries:
            worst_score = min(q["risk_score"] for q in scored_queries)
        else:
            # No numeric scores — fall back to rating text
            worst_score = None
            rating_scores = {"excellent": 90, "good": 70, "fair": 50, "poor": 30, "critical": 10}
            for q in analysis_results["by_query"]:
                r = q.get("rating")
                if r and r in rating_scores:
                    if worst_score is None:
                        worst_score = rating_scores[r]
                    else:
                        worst_score = min(worst_score, rating_scores[r])

        if worst_score is None:
            # No scores at all (LLM failed) — treat as fail, exit code 1
            analysis_results["ci_status"] = "fail"
            analysis_results["ci_exit_code"] = 1
            # Build a useful error message
            if analysis_results["failed"] > 0 and analysis_results["successful"] == 0:
                first_error = analysis_results["failed_queries"][0]["error"] if analysis_results["failed_queries"] else "unknown"
                analysis_results["worst_score"] = f"ERROR — all {analysis_results['failed']} queries failed: {first_error[:80]}"
            else:
                analysis_results["worst_score"] = "ERROR — no LLM scores returned"
        else:
            if worst_score < fail_threshold:
                analysis_results["ci_status"] = "fail"
                analysis_results["ci_exit_code"] = 1
            elif worst_score < warn_threshold:
                analysis_results["ci_status"] = "warn"
                analysis_results["ci_exit_code"] = 0
            else:
                analysis_results["ci_status"] = "pass"
                analysis_results["ci_exit_code"] = 0
            analysis_results["worst_score"] = worst_score

        return analysis_results

    def _analyze_single_query_shallow(
        self, q: Dict, schema_info: str, db_engine: str
    ) -> Dict:
        """
        Analyze a single query using shallow LLM analysis (no DB connection).
        Returns a dict with success status and extracted metrics.
        """
        import time as _t
        from lib.functions.shallow_analysis import analyze_shallow_with_llm
        from lib.functions.query_parameterization import parameterize_for_llm

        sql = q.get("sql", "")
        query_hash = q.get("hash", "")

        _start = _t.time()
        try:
            param_result = parameterize_for_llm(sql=sql)
            parameterized_sql = param_result.get("parameterized_sql", sql)

            result = analyze_shallow_with_llm(
                parameterized_sql=parameterized_sql,
                original_sql=sql,
                schema_info=schema_info,
                database_engine=db_engine,
            )
            _elapsed = _t.time() - _start

            if result.get("success"):
                analysis_data = result.get("analysis_results") or {}
                performance = analysis_data.get("performance_assessment") or {}
                risk_score = performance.get("risk_score", 50)

                query_result = {
                    "success": True,
                    "hash": query_hash,
                    "file": q.get("file", ""),
                    "function": q.get("function", ""),
                    "line": q.get("start_line", 0),
                    "sql": sql,
                    "risk_score": risk_score,
                    "rating": performance.get("overall_rating", "unknown"),
                    "issues": performance.get("primary_concerns") or [],
                    "recommendations": [],
                    "_subprocess_seconds": round(_elapsed, 1),
                }

                for idx in (result.get("index_recommendations") or []):
                    idx_sql = idx.get("sql", "")
                    rationale = idx.get("rationale", "")
                    if idx_sql:
                        if rationale:
                            query_result["recommendations"].append(f"Index: {idx_sql} — {rationale}")
                        else:
                            query_result["recommendations"].append(f"Index: {idx_sql}")

                # Shallow mode: skip rewrite suggestions (can't benchmark without DB)
                # Users can run 'rdst analyze --hash <id>' for tested rewrites

                return query_result
            else:
                return {
                    "success": False,
                    "hash": query_hash,
                    "sql": sql,
                    "error": result.get("error", "Unknown error"),
                    "_subprocess_seconds": round(_elapsed, 1),
                }

        except Exception as e:
            import traceback as _tb
            _elapsed = _t.time() - _start
            return {
                "success": False,
                "hash": query_hash,
                "sql": sql,
                "error": f"{str(e)[:80]} at {_tb.format_exc().splitlines()[-2].strip()[:80]}",
                "_subprocess_seconds": round(_elapsed, 1),
            }

    def _analyze_shallow_all_queries(
        self,
        queries: List[Dict],
        target: str,
        output_json: bool,
        warn_threshold: int = 60,
        fail_threshold: int = 40,
        batch_size: int = 5,
    ) -> Dict:
        """
        Run shallow analysis (schema-only, no DB connection) on all queries in parallel.

        Uses the semantic layer YAML schema to analyze queries without
        executing EXPLAIN ANALYZE. Suitable for CI pipelines where DB
        access is not available at scan time.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from lib.functions.schema_from_yaml import collect_schema_from_yaml

        analysis_results = {
            "total_analyzed": 0,
            "successful": 0,
            "failed": 0,
            "performance_issues": [],
            "recommendations": [],
            "by_query": [],
            "failed_queries": [],
            "mode": "shallow",
            "ci_status": "pass",
            "ci_exit_code": 0,
            "warn_threshold": warn_threshold,
            "fail_threshold": fail_threshold,
        }

        # Filter to valid SQL queries
        valid_queries = [
            q for q in queries
            if q.get("sql") and not q["sql"].startswith("--")
        ]

        if not valid_queries:
            return analysis_results

        # Load schema from YAML once (no DB connection needed)
        schema_result = collect_schema_from_yaml(target=target)
        if not schema_result.get("success"):
            error_msg = schema_result.get("error", "Failed to load schema")
            if not output_json and self.console:
                self.console.print(f"[red]Schema error:[/red] {error_msg}")
            analysis_results["failed"] = len(valid_queries)
            analysis_results["failed_queries"] = [
                {"sql": q.get("sql", ""), "error": error_msg}
                for q in valid_queries
            ]
            analysis_results["ci_status"] = "fail"
            analysis_results["ci_exit_code"] = 1
            return analysis_results

        schema_info = schema_result.get("schema_info", "")
        db_engine = self._detect_sql_dialect(target)

        total_queries = len(valid_queries)
        worst_score = 100

        def _fmt_shallow_result(func_name: str, result: dict) -> str:
            secs = result.get("_subprocess_seconds", "?")
            if result.get("success"):
                score = result.get("risk_score")
                rating = result.get("rating", "?")
                parts = [f"{secs}s"]
                if score is not None:
                    parts.append(f"score:{score}")
                if rating:
                    parts.append(rating)
                return f"  [{StyleTokens.SUCCESS}]{Icons.CHECK}[/{StyleTokens.SUCCESS}] {func_name}() {Icons.ARROW} {', '.join(parts)}"
            else:
                error = result.get("error", "unknown error")
                return f"  [{StyleTokens.ERROR}]{Icons.CROSS}[/{StyleTokens.ERROR}] {func_name}() {Icons.ARROW} {secs}s, FAILED: {error}"

        def _process_result(result, q):
            nonlocal worst_score
            analysis_results["total_analyzed"] += 1
            query_hash = q.get("hash", "")

            if result.get("success"):
                analysis_results["successful"] += 1
                risk_score = result.get("risk_score", 50)
                if risk_score < worst_score:
                    worst_score = risk_score

                query_result = {
                    "hash": result["hash"],
                    "file": result["file"],
                    "function": result["function"],
                    "line": result.get("line", 0),
                    "sql": result["sql"],
                    "risk_score": risk_score,
                    "rating": result.get("rating", "unknown"),
                    "issues": result.get("issues", []),
                    "recommendations": result.get("recommendations", []),
                }

                for issue in result.get("issues", []):
                    analysis_results["performance_issues"].append({
                        "hash": result["hash"],
                        "file": result.get("file", ""),
                        "issue": issue,
                    })

                for rec in result.get("recommendations", []):
                    analysis_results["recommendations"].append({
                        "hash": result["hash"],
                        "recommendation": rec,
                    })

                analysis_results["by_query"].append(query_result)
            else:
                analysis_results["failed"] += 1
                analysis_results["failed_queries"].append({
                    "hash": query_hash,
                    "function": q.get("function", ""),
                    "sql": q.get("sql", ""),
                    "error": result.get("error", "Unknown error"),
                })

        use_progress = self.console and not output_json

        if use_progress:
            self.console.print(f"\n[bold]Shallow analysis[/bold] (schema-only, no DB) of [{StyleTokens.HIGHLIGHT}]{total_queries}[/{StyleTokens.HIGHLIGHT}] queries...")
            completed_lines = []

            with _terminal_guard():
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=self.console,
                ) as progress:
                    ptask = progress.add_task(f"Analyzing 0/{total_queries}...", total=total_queries)

                    for batch_start in range(0, total_queries, batch_size):
                        batch = valid_queries[batch_start:batch_start + batch_size]

                        with ThreadPoolExecutor(max_workers=batch_size) as executor:
                            future_to_query = {
                                executor.submit(
                                    self._analyze_single_query_shallow, q, schema_info, db_engine
                                ): q
                                for q in batch
                            }
                            for future in as_completed(future_to_query):
                                q = future_to_query[future]
                                func_name = q.get("function", "?")
                                try:
                                    result = future.result()
                                except Exception as e:
                                    result = {
                                        "success": False,
                                        "hash": q.get("hash", ""),
                                        "error": str(e)[:40],
                                        "_subprocess_seconds": "?",
                                    }

                                _process_result(result, q)
                                completed_lines.append(_fmt_shallow_result(func_name, result))
                                done = analysis_results["total_analyzed"]
                                progress.update(ptask, completed=done, description=f"Analyzed {done}/{total_queries}...")

            for line in completed_lines:
                self.console.print(line)

        else:
            # JSON output mode — no progress display
            for batch_start in range(0, total_queries, batch_size):
                batch = valid_queries[batch_start:batch_start + batch_size]

                with ThreadPoolExecutor(max_workers=batch_size) as executor:
                    future_to_query = {
                        executor.submit(
                            self._analyze_single_query_shallow, q, schema_info, db_engine
                        ): q
                        for q in batch
                    }
                    for future in as_completed(future_to_query):
                        q = future_to_query[future]
                        try:
                            result = future.result()
                        except Exception as e:
                            result = {
                                "success": False,
                                "hash": q.get("hash", ""),
                                "error": str(e)[:40],
                            }
                        _process_result(result, q)

        # Determine CI status based on worst score
        if worst_score < fail_threshold:
            analysis_results["ci_status"] = "fail"
            analysis_results["ci_exit_code"] = 1
        elif worst_score < warn_threshold:
            analysis_results["ci_status"] = "warn"
            analysis_results["ci_exit_code"] = 0
        else:
            analysis_results["ci_status"] = "pass"
            analysis_results["ci_exit_code"] = 0

        analysis_results["worst_score"] = worst_score

        return analysis_results

    def _load_schema_context(self, target: Optional[str]) -> str:
        """
        Load schema from rdst semantic-layer if available.
        Returns a compact schema summary for LLM context.
        """
        schema_dir = Path.home() / ".rdst" / "semantic-layer"
        schema_file = None

        if target:
            # Try target-specific schema file
            schema_file = schema_dir / f"{target}.yaml"

        if not schema_file or not schema_file.exists():
            # Try to find any schema file
            if schema_dir.exists():
                schemas = list(schema_dir.glob("*.yaml"))
                if schemas:
                    schema_file = schemas[0]

        if not schema_file or not schema_file.exists():
            return ""

        try:
            import yaml
            data = yaml.safe_load(schema_file.read_text())
            tables = data.get("tables", {})

            # Build compact schema summary
            lines = ["Database Schema:"]
            for table_name, table_info in tables.items():
                columns = table_info.get("columns", {})
                col_list = ", ".join(columns.keys())
                lines.append(f"  {table_name}: {col_list}")

            return "\n".join(lines)
        except Exception:
            return ""

    def _convert_snippet_to_sql(self, orm_snippet: str, schema_context: str = "", target: Optional[str] = None) -> tuple:
        """
        Use LLM to convert a small ORM snippet to parameterized SQL.

        This is called only for snippets not in the cache.
        The input is small (typically 50-200 chars), not whole files.
        Uses Haiku for speed and cost efficiency on single queries.

        Returns:
            (sql: str, issues: list)
        """
        schema_section = f"\n\n{schema_context}" if schema_context else ""
        sql_dialect = self._detect_sql_dialect(target)

        system_message = f"""Convert this ORM code to parameterized {sql_dialect} SQL.
{schema_section}

RULES:
1. Use $1, $2, $3 for ALL parameter values (never literal values)
2. Use uppercase SQL keywords: SELECT, FROM, WHERE, JOIN, etc.
3. Use lowercase for table/column names
4. Output ONLY the SQL query, no explanations
5. If you see func.count(), func.sum(), etc. - use the SQL equivalents
6. For .desc() use DESC, for .asc() use ASC
7. Match table names from the schema above when possible
8. For Prisma: translate include/select/where/orderBy/take/skip to SQL
9. For Drizzle: translate builder chains to SQL"""

        user_query = f"""Convert this ORM code to SQL:

{orm_snippet}

Output only the SQL query."""

        try:
            from lib.llm_manager.llm_manager import LLMManager
            llm = LLMManager()

            response = llm.query(
                system_message=system_message,
                user_query=user_query,
                max_tokens=500,
                temperature=0.0,
                model="claude-haiku-4-5-20251001",  # Use Haiku for single queries - fast & cheap
            )

            result_text = response.get("text", "").strip()

            # Clean up markdown code blocks if present
            if result_text.startswith("```"):
                lines = result_text.split("\n")
                result_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            # Basic issue detection
            issues = []
            sql_upper = result_text.upper()
            if "SELECT *" in sql_upper:
                issues.append("Uses SELECT * - consider selecting specific columns")
            if "WHERE" in sql_upper and "LIMIT" not in sql_upper:
                issues.append("No LIMIT clause - could return many rows")
            if "LIKE" in sql_upper and "%'" in result_text:
                issues.append("Leading wildcard in LIKE - may prevent index usage")

            return result_text, issues

        except Exception as e:
            return f"-- Error: {e}", ["LLM conversion failed"]

    def _find_orm_files(self, directory: str) -> List[Dict]:
        """Find all files that contain ORM patterns."""
        results = []
        directory_path = Path(directory)

        # File extensions to check
        extensions = {".py", ".js", ".ts", ".tsx", ".jsx"}

        # Directories to skip
        skip_dirs = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".tox", "eggs"}

        for root, dirs, files in os.walk(directory_path):
            # Skip certain directories
            dirs[:] = [d for d in dirs if d not in skip_dirs]

            for file in files:
                filepath = Path(root) / file
                if filepath.suffix not in extensions:
                    continue

                try:
                    content = filepath.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue

                # Check for ORM patterns (filtered by file type)
                detected_orms = []
                is_python = filepath.suffix == ".py"
                is_js_ts = filepath.suffix in {".js", ".ts", ".tsx", ".jsx"}
                for orm_name, patterns in ORM_PATTERNS.items():
                    # Only check relevant ORMs for the file type
                    if is_python and orm_name in ("prisma", "drizzle"):
                        continue
                    if is_js_ts and orm_name in ("sqlalchemy", "django"):
                        continue
                    for pattern in patterns:
                        if re.search(pattern, content, re.IGNORECASE):
                            detected_orms.append(orm_name)
                            break

                if detected_orms:
                    results.append({
                        "file": str(filepath.relative_to(directory_path)),
                        "orms": list(set(detected_orms)),
                        "lines": len(content.splitlines()),
                    })

        return results

    def _find_orm_files_single(self, file_path: str, base_dir: str) -> List[Dict]:
        """Check a single file for ORM patterns."""
        filepath = Path(file_path)
        extensions = {".py", ".js", ".ts", ".tsx", ".jsx"}
        if filepath.suffix not in extensions:
            return []

        try:
            content = filepath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []

        detected_orms = []
        is_python = filepath.suffix == ".py"
        is_js_ts = filepath.suffix in {".js", ".ts", ".tsx", ".jsx"}
        for orm_name, patterns in ORM_PATTERNS.items():
            if is_python and orm_name in ("prisma", "drizzle"):
                continue
            if is_js_ts and orm_name in ("sqlalchemy", "django"):
                continue
            for pattern in patterns:
                if re.search(pattern, content, re.IGNORECASE):
                    detected_orms.append(orm_name)
                    break

        if not detected_orms:
            return []

        return [{
            "file": os.path.basename(file_path),
            "orms": list(set(detected_orms)),
            "lines": len(content.splitlines()),
        }]

    def _extract_query_code(self, filepath: str, base_dir: str) -> str:
        """Extract relevant code sections that contain queries."""
        full_path = Path(base_dir) / filepath
        content = full_path.read_text(encoding="utf-8", errors="ignore")

        # For small files, return everything
        lines = content.splitlines()
        if len(lines) <= 200:
            return content

        # For larger files, extract functions containing query patterns
        relevant_sections = []
        in_function = False
        current_function = []
        has_query = False

        for line in lines:
            # Detect function start
            if re.match(r"^\s*(def |async def |class )", line):
                if in_function and has_query:
                    relevant_sections.append("\n".join(current_function))
                in_function = True
                current_function = [line]
                has_query = False
            elif in_function:
                current_function.append(line)
                # Check if this line has query patterns
                for patterns in ORM_PATTERNS.values():
                    for pattern in patterns:
                        if re.search(pattern, line):
                            has_query = True
                            break

        # Don't forget the last function
        if in_function and has_query:
            relevant_sections.append("\n".join(current_function))

        if relevant_sections:
            return "\n\n# ---\n\n".join(relevant_sections)
        return content[:5000]  # Fallback: first 5000 chars

    def _extract_queries_with_llm(self, code: str, filepath: str) -> List[Dict]:
        """
        Use LLM to extract SQL queries from ORM code.

        CRITICAL: The LLM is instructed to:
        1. Generate PARAMETERIZED SQL (use $1, $2, etc. - never literal values)
        2. Be deterministic (same input -> same output)
        3. Extract only actual queries, not model definitions
        """
        # System message emphasizes determinism and parameterization
        system_message = """You are a deterministic database query analyzer. Your task is to extract SQL queries from ORM code.

CRITICAL RULES:
1. ALWAYS generate PARAMETERIZED SQL using positional placeholders ($1, $2, $3, etc.)
   - NEVER include literal values like strings, numbers, or dates
   - Variables, function arguments, user inputs -> $1, $2, etc.
   - Example: `.filter(User.name == name)` -> `WHERE name = $1`
   - Example: `.filter(User.age > 18)` -> `WHERE age > $1` (18 is a literal but treat as param)

2. Be DETERMINISTIC - given the same code, always produce the exact same output
   - Process functions in order they appear in the file
   - Use consistent SQL formatting (uppercase keywords, lowercase identifiers)
   - Same ORM pattern -> Same SQL output

3. Output ONLY valid JSON, no explanations or markdown

4. Only extract actual database queries (SELECT, INSERT, UPDATE, DELETE)
   - Skip model definitions, imports, configuration"""

        user_query = f"""Extract all database queries from this code. For each query:
- Convert ORM code to PARAMETERIZED SQL (use $1, $2, $3 for all values)
- Identify the function containing the query
- List potential performance issues

Code from {filepath}:
```
{code}
```

Respond in this exact JSON format:
{{
  "queries": [
    {{
      "function": "function_name",
      "orm_code": "exact ORM code snippet",
      "sql": "SELECT ... WHERE col = $1",
      "issues": ["issue1", "issue2"]
    }}
  ]
}}

Rules for SQL generation:
- All variable values -> $1, $2, $3 (in order of appearance)
- Use uppercase for SQL keywords: SELECT, FROM, WHERE, JOIN, etc.
- Use lowercase for table/column names
- Include ORDER BY, LIMIT, GROUP BY when present in ORM code
- If query returns all columns, use "SELECT *" not individual columns
- For LIKE patterns, use: WHERE col LIKE $1 (the % is part of the parameter)"""

        try:
            # Use the LLM manager from RDST
            from lib.llm_manager.llm_manager import LLMManager
            llm = LLMManager()

            response = llm.query(
                system_message=system_message,
                user_query=user_query,
                max_tokens=4000,
                temperature=0.0,  # CRITICAL: temperature=0 for determinism
            )

            result_text = response.get("text", "")

            # Extract JSON from response
            json_match = re.search(r"\{[\s\S]*\}", result_text)
            if json_match:
                parsed = json.loads(json_match.group())
                queries = parsed.get("queries", [])

                # Post-process to ensure parameterization
                for q in queries:
                    sql = q.get("sql", "")
                    # Validate it has parameters (contains $N pattern or no WHERE clause)
                    if sql and "WHERE" in sql.upper() and not re.search(r'\$\d+', sql):
                        # LLM didn't parameterize - flag it
                        if "issues" not in q:
                            q["issues"] = []
                        q["issues"].append("WARNING: Query may not be properly parameterized")

                return queries

        except Exception as e:
            if self.console:
                self.console.print(f"  [dim]LLM error for {filepath}: {e}[/dim]")
            return []

        return []

    def _print_report(self, results: Dict, show_analysis: bool = False):
        """Print a formatted report."""
        if not self.console:
            # Plain text fallback
            print("\n" + "=" * 60)
            print("RDST SCAN REPORT")
            print("=" * 60)
            print(f"\nFiles with ORM code: {len(results['files'])}")
            for f in results["files"]:
                print(f"  - {f['file']} ({', '.join(f['orms'])})")
            print(f"\nQueries found: {len(results['queries'])}")
            for q in results["queries"]:
                print(f"\n[{q.get('file', '?')}] {q.get('function', '?')}")
                print(f"  SQL: {q.get('sql', '?')[:80]}...")
                if q.get("issues"):
                    print(f"  ISSUES: {', '.join(q['issues'])}")
            return

        # Rich output — clear funnel
        self.console.print()

        all_queries = results.get("queries", [])
        sql_queries = [q for q in all_queries if q.get("status") == "sql"]
        skipped = [q for q in all_queries if q.get("status") == "skipped"]

        funnel_lines = [
            f"[bold]Files with ORM code:[/bold] {len(results['files'])}",
            f"[bold]ORM snippets extracted:[/bold] {len(all_queries)}",
            f"[bold]Converted to SQL:[/bold] {len(sql_queries)}",
        ]
        if skipped:
            # Group skip reasons
            reasons = {}
            for q in skipped:
                r = q.get("skip_reason", "Unknown")
                reasons[r] = reasons.get(r, 0) + 1
            reason_parts = [f"{count} {reason.lower()}" for reason, count in reasons.items()]
            funnel_lines.append(f"[bold]Skipped:[/bold] {len(skipped)} ({', '.join(reason_parts)})")

        self.console.print(StyledPanel.create(
            "\n".join(funnel_lines),
            title="RDST Scan Report",
        ))

        # Files table
        if results["files"]:
            table = StyledTable.create(title="Files with ORM Code")
            table.add_column("File", style="cyan")
            table.add_column("ORMs", style="green")
            table.add_column("Lines", justify="right")

            for f in results["files"][:20]:  # Limit to 20 files
                table.add_row(f["file"], ", ".join(f["orms"]), str(f["lines"]))

            if len(results["files"]) > 20:
                table.add_row("...", f"({len(results['files']) - 20} more)", "")

            self.console.print(table)

        # Queries table
        if results["queries"]:
            self.console.print()
            table = StyledTable.create(title="Extracted Queries")
            table.add_column("File", style="cyan", max_width=30)
            table.add_column("Line", style="dim")
            table.add_column("Function", style="green")
            table.add_column("SQL Preview", max_width=50)

            for q in results["queries"][:20]:
                sql_preview = (q.get("sql", "")[:47] + "...") if len(q.get("sql", "")) > 50 else q.get("sql", "")
                line = str(q.get("start_line", "?"))
                table.add_row(
                    q.get("file", "?")[:30],
                    line,
                    q.get("function", "?"),
                    sql_preview,
                )

            self.console.print(table)

        # Skip static issues from ORM extraction - not useful, only show real LLM analysis issues

        # Extraction stats
        if "extraction" in results:
            self.console.print()
            ext = results["extraction"]
            self.console.print(f"[bold]Extraction method:[/bold] {ext['method'].upper()} (deterministic)")

        # Registry info
        if "registry" in results:
            self.console.print()
            if results["registry"].get("skipped"):
                self.console.print("[bold]Registry:[/bold] [dim]skipped (--nosave)[/dim]")
            else:
                self.console.print(f"[bold]Registry saved:[/bold] {results['registry']['path']}")
                self.console.print(f"  New queries: {results['registry']['new_queries']}")
                self.console.print(f"  Updated queries: {results['registry']['updated_queries']}")
                self.console.print(f"  Total in registry: {results['registry']['total_queries']}")

        # Analysis summary (if --analyze was used)
        if show_analysis and "analysis" in results:
            analysis = results["analysis"]
            is_shallow = analysis.get("mode") == "shallow"

            skipped_queries = analysis.get("skipped_queries", [])
            analyzed_ok = list(analysis.get("by_query", []))
            analyzed_fail = list(analysis.get("failed_queries", []))
            total_all = len(analyzed_ok) + len(analyzed_fail) + len(skipped_queries)

            summary_lines = [
                f"[bold]Mode:[/bold] {'Shallow (schema-only)' if is_shallow else 'Deep (EXPLAIN ANALYZE)'}",
                f"[bold]Total queries:[/bold] {total_all}",
                f"[bold]Analyzed:[/bold] {analysis.get('successful', 0)}",
            ]

            if skipped_queries:
                summary_lines.append(f"[bold]Skipped:[/bold] {len(skipped_queries)}")
            if analysis['failed'] > 0:
                summary_lines.append(f"[bold]Errors:[/bold] [red]{analysis['failed']}[/red]")

            # CI status
            ci_status = analysis.get("ci_status", "unknown")
            worst_score = analysis.get("worst_score", "N/A")
            fail_threshold = analysis.get("fail_threshold", 40)
            warn_threshold = analysis.get("warn_threshold", 60)
            status_color = {"pass": "green", "warn": "yellow", "fail": "red", "error": "red"}.get(ci_status, "red")
            summary_lines.append(f"[bold]Worst score:[/bold] [{status_color}]{worst_score}[/{status_color}]")

            if ci_status in ("fail", "warn", "pass"):
                summary_lines.append(f"[bold]Thresholds:[/bold] [dim]fail < {fail_threshold}, warn < {warn_threshold}[/dim]")

            by_query = analysis.get("by_query", [])
            below_fail = [q for q in by_query if q.get("risk_score") is not None and q["risk_score"] < fail_threshold]
            below_warn = [q for q in by_query if q.get("risk_score") is not None and fail_threshold <= q["risk_score"] < warn_threshold]
            if below_fail:
                summary_lines.append(f"[bold]Below fail threshold:[/bold] [red]{len(below_fail)} queries[/red]")
            if below_warn:
                summary_lines.append(f"[bold]Below warn threshold:[/bold] [yellow]{len(below_warn)} queries[/yellow]")

            summary_lines.append(f"[bold]CI Status:[/bold] [{status_color}]{ci_status.upper()}[/{status_color}]")

            self.console.print()
            border_color = {"pass": "green", "warn": "yellow", "fail": "red", "error": "red"}.get(ci_status, "red")
            self.console.print(StyledPanel.create(
                "\n".join(summary_lines),
                title="Analysis Summary",
                border_style=border_color,
            ))

            # Query-by-query details — EVERY query gets shown
            # Sort analyzed queries by score descending (best first)
            analyzed_ok.sort(key=lambda q: q.get("risk_score") if q.get("risk_score") is not None else -1, reverse=True)

            self.console.print()
            self.console.print(f"[bold]Query Details[/bold] ({total_all} queries)")

            # 1. Analyzed queries (successful) — sorted best score first
            for q in analyzed_ok:
                file_ref = f"{q['file']}:{q['line']}" if q.get('line') else q['file']
                rating = q.get('rating', '?')
                rating_color = "green" if rating in ("excellent", "good") else "yellow" if rating in ("fair", "moderate") else "red"
                short_hash = q.get('hash', '')[:8]

                risk_score = q.get('risk_score')
                score_color = "green" if risk_score and risk_score >= 71 else "yellow" if risk_score and risk_score >= 31 else "red" if risk_score is not None else "dim"
                parts = []
                if risk_score is not None:
                    parts.append(f"Score: [{score_color}]{risk_score}[/{score_color}]")
                exec_time = q.get('execution_time_ms')
                if exec_time is not None:
                    parts.append(f"Exec: {exec_time:.1f}ms")
                metric_str = " | ".join(parts) if parts else ""

                self.console.print(f"\n  [{rating_color}]●[/{rating_color}] [cyan]{file_ref}[/cyan] → [dim]{q['function']}()[/dim]  [dim]\\[{short_hash}][/dim]")
                self.console.print(f"    [dim]SQL:[/dim] {q['sql'][:250]}{'...' if len(q.get('sql', '')) > 250 else ''}")
                self.console.print(f"    [{rating_color}]{rating}[/{rating_color}] | {metric_str}")

                if q.get("issues"):
                    for issue in q["issues"][:6]:
                        issue_text = issue if isinstance(issue, str) else str(issue)
                        self.console.print(f"    [yellow]⚠[/yellow]  {issue_text}")

                if q.get("recommendations"):
                    for rec in q["recommendations"][:6]:
                        rec_text = rec if isinstance(rec, str) else str(rec)
                        for line in rec_text.split("\n"):
                            self.console.print(f"    [green]→[/green]  {line}")

                if q.get("rewrite_benchmarks"):
                    for bench in q["rewrite_benchmarks"][:3]:
                        bench_text = bench if isinstance(bench, str) else str(bench)
                        for line in bench_text.split("\n"):
                            self.console.print(f"    [magenta]⏱[/magenta]  {line}")

                if not q.get("issues") and not q.get("recommendations") and not q.get("rewrite_benchmarks"):
                    self.console.print("    [green]No issues found[/green]")

                # Per-query deep-dive hint
                if short_hash:
                    target_name = results.get("target", "")
                    target_arg = f" --target {target_name}" if target_name else ""
                    if is_shallow:
                        self.console.print(f"    [dim]Deep analysis: rdst analyze --hash {short_hash}{target_arg}[/dim]")
                    else:
                        self.console.print(f"    [dim]Re-analyze: rdst analyze --hash {short_hash}{target_arg}[/dim]")

            # 2. Failed queries (analysis error)
            for fq in analyzed_fail:
                func = fq.get("function", "")
                func_str = f"{func}()" if func else "?"
                err = fq.get("error", "Unknown error")
                sql_preview = fq.get("sql", "?")[:250]

                self.console.print(f"\n  [red]✗[/red] [dim]{func_str}[/dim]  [red]error[/red]")
                self.console.print(f"    [dim]SQL:[/dim] {sql_preview}")
                self.console.print(f"    [red]Error:[/red] {err}")

            # 3. Skipped queries — each one listed with its reason
            for sq in skipped_queries:
                func = sq.get("function", "")
                func_str = f"{func}()" if func else "?"
                file_ref = f"{sq.get('file', '?')}:{sq.get('start_line', '?')}"
                reason = sq.get("skip_reason", "Unknown")
                orm_preview = sq.get("orm_code", "")[:150]

                self.console.print(f"\n  [dim]○[/dim] [cyan]{file_ref}[/cyan] → [dim]{func_str}[/dim]  [dim]skipped[/dim]")
                self.console.print(f"    [dim]ORM:[/dim] {orm_preview}{'...' if len(sq.get('orm_code', '')) > 150 else ''}")
                self.console.print(f"    [yellow]⚠[/yellow]  {reason}")

        else:
            self.console.print()
            self.console.print("[dim]Run 'rdst query list' to see all queries, or 'rdst analyze --hash <hash> --target <target>' to analyze[/dim]")

    def _list_queries(
        self,
        with_issues: bool,
        file_pattern: Optional[str],
        output_json: bool,
    ) -> RdstResult:
        """List queries from the registry (filter by source=scan)."""
        registry = QueryRegistry()
        registry.load()

        # Get all queries and filter to scan-sourced ones
        all_queries = registry.list_queries()
        queries = [q for q in all_queries if q.source == "scan"]

        if not queries:
            return RdstResult(True, "No scan queries in registry. Run 'rdst scan <directory> --schema <target>' first.")

        # Convert to dict format for display and filtering
        query_dicts = []
        for q in queries:
            query_dicts.append({
                "hash": q.hash,
                "sql": q.sql,
                "source": q.source,
                "last_target": q.last_target,
                "last_analyzed": q.last_analyzed,
            })

        if output_json:
            return RdstResult(True, json.dumps({"queries": query_dicts}, indent=2), data={"queries": query_dicts})

        # Pretty print
        if not self.console:
            print("\nScan Queries in Registry")
            print(f"Total queries: {len(queries)}")
            for q in query_dicts:
                print(f"\n  [{q['hash'][:10]}]")
                print(f"    SQL: {q.get('sql', '?')[:60]}...")
            return RdstResult(True, "", data={"queries": query_dicts})

        # Rich output
        self.console.print()
        self.console.print(StyledPanel.create(
            f"[bold]Source:[/bold] scan\n"
            f"[bold]Total queries:[/bold] {len(queries)}",
            title="Query Registry (Scan)",
        ))

        table = StyledTable.create(title="Queries")
        table.add_column("Hash", style="dim", width=12)
        table.add_column("SQL", max_width=60)
        table.add_column("Last Target", style="green", max_width=15)

        for q in query_dicts[:50]:  # Limit to 50
            sql_preview = (q.get("sql", "")[:57] + "...") if len(q.get("sql", "")) > 60 else q.get("sql", "")
            table.add_row(
                q["hash"],
                sql_preview,
                q.get("last_target", "-") or "-"
            )

        if len(query_dicts) > 50:
            table.add_row("...", f"({len(query_dicts) - 50} more)", "")

        self.console.print(table)
        self.console.print()
        self.console.print("[dim]Run 'rdst analyze --hash <hash> --target <target>' to analyze a query[/dim]")

        return RdstResult(True, "", data={"queries": query_dicts})

    def _check_queries(
        self,
        directory: str,
        diff: Optional[str],
        target: Optional[str],
        output_json: bool,
    ) -> RdstResult:
        """
        Check queries for CI/pre-commit validation.

        Exit codes:
            0 = All queries pass
            1 = Warnings found (new queries)
            2 = Errors found (blocking issues)
        """
        registry = QueryRegistry()
        registry.load()

        # If --diff is provided, only check changed files
        if diff:
            # Get changed files from git
            import subprocess
            try:
                result = subprocess.run(
                    ["git", "diff", "--name-only", diff],
                    capture_output=True,
                    text=True,
                    cwd=directory,
                )
                changed_files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
            except Exception as e:
                return RdstResult(False, f"Failed to get git diff: {e}")

            if not changed_files:
                return RdstResult(True, "No changed files to check.", data={"status": "pass"})

            # Gather context and extract queries from changed files
            gatherer = ContextGatherer(directory)
            context = gatherer.gather_context(changed_files)

            if not context["query_files"]:
                return RdstResult(True, "No query files in diff.", data={"status": "pass"})

            # TODO: Extract queries from context and compare to corpus
            # For now, just report what we found
            return RdstResult(
                True,
                f"Found {len(context['query_files'])} files with queries in diff",
                data={
                    "status": "warning",
                    "query_files": list(context["query_files"].keys()),
                    "model_files": list(context["model_files"].keys()),
                }
            )

        # Without --diff, check entire registry
        all_queries = registry.list_queries()
        scan_queries = [q for q in all_queries if q.source == "scan"]

        if not scan_queries:
            return RdstResult(True, "No scan queries in registry. Run 'rdst scan <directory> --schema <target>' first.", data={"status": "pass"})

        results = {
            "status": "pass",
            "total_queries": len(scan_queries),
            "new_queries": 0,  # Registry doesn't track new vs approved status
            "queries_with_issues": 0,
            "issues": [],
        }

        if output_json:
            return RdstResult(True, json.dumps(results, indent=2), data=results)

        # Pretty print
        if not self.console:
            print(f"\nScan Check: {results['status'].upper()}")
            print(f"Total queries: {results['total_queries']}")
            print(f"New queries: {results['new_queries']}")
            print(f"With issues: {results['queries_with_issues']}")
            return RdstResult(True, "", data=results)

        status_color = {"pass": "green", "warning": "yellow", "error": "red"}.get(results["status"], "white")
        self.console.print()
        self.console.print(StyledPanel.create(
            f"[bold]Status:[/bold] [{status_color}]{results['status'].upper()}[/{status_color}]\n"
            f"[bold]Total queries:[/bold] {results['total_queries']}\n"
            f"[bold]New queries:[/bold] {results['new_queries']}\n"
            f"[bold]With issues:[/bold] {results['queries_with_issues']}",
            title="Scan Check",
            border_style=status_color,
        ))

        if results["issues"]:
            self.console.print()
            self.console.print("[bold yellow]Issues:[/bold yellow]")
            for issue in results["issues"][:10]:
                self.console.print(f"  [{issue['hash'][:8]}] {issue['file']}: {issue['issue']}")

        return RdstResult(True, "", data=results)
