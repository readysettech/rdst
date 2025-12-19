"""
RDST Scan AST Extractor - Deterministic query extraction using Python AST.

This module extracts ORM queries from Python code WITHOUT using an LLM for detection.
It uses Python's Abstract Syntax Tree (AST) to:

1. Find all terminal methods that execute queries (.all(), .first(), .one(), etc.)
2. Walk backwards to find the full query chain (.query()...filter()...all())
3. Extract only the relevant ORM snippet (not the whole file)
4. Hash snippets for deterministic identification

The LLM is only used for the final step: converting the small ORM snippet to SQL.
This provides determinism because:
- Same code -> Same AST -> Same snippets -> Same hashes
- LLM with temperature=0 on same input -> Same output
- Hash-based caching makes subsequent runs instant

Cross-file query detection:
- Tracks imports of query builder functions
- When a terminal method is called on an imported function result,
  it traces back to find the actual query definition
"""

import ast
import hashlib
import re
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple, Any
from dataclasses import dataclass, field


# Terminal methods that execute a query and return results
# NOTE: 'count' is NOT included because func.count() is an aggregate function,
# not a terminal method. The actual terminal is .scalar() or .all() after func.count().
TERMINAL_METHODS = {
    # SQLAlchemy query termination
    'all', 'first', 'one', 'one_or_none', 'scalar', 'scalar_one', 'scalar_one_or_none',
    'exists', 'delete', 'update',
    # Django QuerySet termination
    'get', 'create', 'update_or_create', 'get_or_create', 'bulk_create', 'bulk_update',
    'aggregate', 'latest', 'earliest', 'last',
    # Raw SQL execution
    'execute', 'fetchone', 'fetchall', 'fetchmany',
}

# Query building methods (indicate we're in a query chain)
QUERY_CHAIN_METHODS = {
    # SQLAlchemy
    'query', 'filter', 'filter_by', 'join', 'outerjoin', 'group_by', 'order_by',
    'having', 'limit', 'offset', 'distinct', 'subquery', 'with_entities',
    'options', 'load_only', 'joinedload', 'selectinload', 'contains_eager',
    # Django
    'objects', 'filter', 'exclude', 'annotate', 'values', 'values_list',
    'select_related', 'prefetch_related', 'defer', 'only', 'using',
    # Common
    'where', 'select', 'from_', 'text',
}


@dataclass
class ExtractedQuery:
    """Represents an extracted ORM query."""
    function_name: str
    class_name: Optional[str]
    orm_snippet: str
    snippet_hash: str
    start_line: int
    end_line: int
    terminal_method: str
    file_path: Optional[str] = None
    # For cross-file queries
    imports_query_builder: bool = False
    imported_builder_name: Optional[str] = None
    imported_builder_module: Optional[str] = None
    # ORM type: sqlalchemy, django, prisma, drizzle, raw_sql
    orm_type: Optional[str] = None


@dataclass
class ImportInfo:
    """Tracks an imported name that might be a query builder."""
    name: str  # The local name after import
    module: str  # The module it's from
    original_name: str  # The original name in the module (for 'from x import y as z')


class ASTQueryExtractor(ast.NodeVisitor):
    """
    Extract ORM queries from Python source using AST analysis.

    This is 100% deterministic - same source code always produces
    the same extracted queries in the same order.
    """

    def __init__(self, source: str, file_path: Optional[str] = None):
        self.source = source
        self.lines = source.splitlines()
        self.file_path = file_path

        # Results
        self.queries: List[ExtractedQuery] = []

        # Tracking state during traversal
        self.current_class: Optional[str] = None
        self.current_function: Optional[str] = None
        self.current_function_start: int = 0
        self.current_function_end: int = 0

        # Track function bodies for snippet extraction
        self.function_bodies: Dict[str, Tuple[int, int, str]] = {}  # name -> (start, end, body)

        # Track imports for cross-file detection
        self.imports: Dict[str, ImportInfo] = {}  # local_name -> ImportInfo

        # Track functions that return query objects (for cross-file)
        self.query_builder_functions: Set[str] = set()

    def _detect_orm_type(self) -> Optional[str]:
        """Detect ORM type from file imports."""
        for info in self.imports.values():
            mod = info.module or ''
            if 'django.db' in mod or 'django.db.models' in mod:
                return 'django'
            if 'sqlalchemy' in mod:
                return 'sqlalchemy'
        return None

    def extract(self) -> List[ExtractedQuery]:
        """
        Main entry point. Parse and extract all queries.

        Returns:
            List of ExtractedQuery objects, sorted by line number.
        """
        try:
            tree = ast.parse(self.source)
        except SyntaxError as e:
            # Can't parse, return empty
            return []

        # First pass: collect imports and function bodies
        self._collect_metadata(tree)

        # Second pass: find terminal methods and extract queries
        self.visit(tree)

        # Set ORM type from imports
        orm_type = self._detect_orm_type()
        for q in self.queries:
            if q.orm_type is None:
                q.orm_type = orm_type

        # Sort by line number for deterministic ordering
        self.queries.sort(key=lambda q: q.start_line)

        return self.queries

    def _collect_metadata(self, tree: ast.AST):
        """First pass: collect imports and map function bodies."""
        for node in ast.walk(tree):
            # Collect imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    self.imports[local_name] = ImportInfo(
                        name=local_name,
                        module=alias.name,
                        original_name=alias.name.split('.')[-1]
                    )

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                for alias in node.names:
                    local_name = alias.asname or alias.name
                    self.imports[local_name] = ImportInfo(
                        name=local_name,
                        module=module,
                        original_name=alias.name
                    )

            # Map function bodies
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_name = node.name
                start_line = node.lineno
                end_line = node.end_lineno or node.lineno
                body_lines = self.lines[start_line - 1:end_line]
                body = '\n'.join(body_lines)
                self.function_bodies[func_name] = (start_line, end_line, body)

                # Check if this function is a query builder (returns a query)
                if self._is_query_builder_function(node):
                    self.query_builder_functions.add(func_name)

    def _is_query_builder_function(self, node: ast.FunctionDef) -> bool:
        """
        Check if a function returns a query object (is a query builder).

        Query builders typically:
        - Don't call terminal methods like .all(), .first()
        - Return the result of .query(), .filter(), etc.
        """
        has_query_patterns = False
        has_terminal = False

        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if isinstance(child.func, ast.Attribute):
                    method_name = child.func.attr
                    if method_name in QUERY_CHAIN_METHODS:
                        has_query_patterns = True
                    if method_name in TERMINAL_METHODS:
                        has_terminal = True

        # It's a builder if it has query patterns but no terminal methods
        return has_query_patterns and not has_terminal

    def visit_ClassDef(self, node: ast.ClassDef):
        """Track current class."""
        old_class = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = old_class

    def visit_FunctionDef(self, node: ast.FunctionDef):
        """Process a function definition."""
        self._process_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        """Process an async function definition."""
        self._process_function(node)

    def _process_function(self, node):
        """Common processing for sync and async functions."""
        old_function = self.current_function
        old_start = self.current_function_start
        old_end = self.current_function_end

        self.current_function = node.name
        self.current_function_start = node.lineno
        self.current_function_end = node.end_lineno or node.lineno

        # Visit children to find terminal methods
        self.generic_visit(node)

        self.current_function = old_function
        self.current_function_start = old_start
        self.current_function_end = old_end

    def visit_Call(self, node: ast.Call):
        """
        Check if this is a terminal method call that executes a query.

        We look for patterns like:
        - db.query(...).filter(...).all()
        - User.objects.filter(...).first()
        - query_builder_func(db, ...).limit(N).all()
        """
        if isinstance(node.func, ast.Attribute):
            method_name = node.func.attr

            if method_name in TERMINAL_METHODS:
                # Found a terminal method - extract the query
                self._extract_query_at_node(node, method_name)

        # Continue visiting children
        self.generic_visit(node)

    def _extract_query_at_node(self, node: ast.Call, terminal_method: str):
        """Extract the ORM query snippet for a terminal method call."""
        if not self.current_function:
            return  # Skip queries not in a function

        # Extract the complete expression directly from source using AST line numbers.
        # The Call node for .all() in db.query(X).filter(Y).all() spans the entire chain,
        # so node.lineno..node.end_lineno gives us the full ORM expression with all arguments.
        start = node.lineno
        end = node.end_lineno or node.lineno
        if start < 1 or end > len(self.lines):
            return

        snippet_lines = self.lines[start - 1:end]
        # Strip leading indentation uniformly (dedent)
        if snippet_lines:
            min_indent = min(
                (len(line) - len(line.lstrip()) for line in snippet_lines if line.strip()),
                default=0,
            )
            snippet_lines = [line[min_indent:] for line in snippet_lines]
        orm_snippet = '\n'.join(snippet_lines).strip()

        if not orm_snippet:
            return

        # Generate deterministic hash
        snippet_hash = self._hash_snippet(orm_snippet)

        # Check for cross-file query builder usage
        imports_builder = False
        builder_name = None
        builder_module = None

        # Check if this calls an imported query builder
        call_chain = self._get_call_chain(node)
        for name in call_chain:
            if name in self.imports:
                imp = self.imports[name]
                # Check if the import is from a local file (relative import or local module)
                if imp.module.startswith('.') or '.' not in imp.module:
                    imports_builder = True
                    builder_name = imp.original_name
                    builder_module = imp.module
                    break

        query = ExtractedQuery(
            function_name=self.current_function,
            class_name=self.current_class,
            orm_snippet=orm_snippet,
            snippet_hash=snippet_hash,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            terminal_method=terminal_method,
            file_path=self.file_path,
            imports_query_builder=imports_builder,
            imported_builder_name=builder_name,
            imported_builder_module=builder_module,
        )

        self.queries.append(query)


    def _get_call_chain(self, node: ast.Call) -> List[str]:
        """
        Get the names in a call chain.

        For: build_query(db, x).filter(...).all()
        Returns: ['build_query']

        For: self._build_query(x).all()
        Returns: ['_build_query']
        """
        names = []

        current = node
        while isinstance(current, ast.Call):
            if isinstance(current.func, ast.Attribute):
                # It's a method call like .filter()
                current = current.func.value
            elif isinstance(current.func, ast.Name):
                # It's a function call like build_query()
                names.append(current.func.id)
                break
            else:
                break

        return names

    def _hash_snippet(self, snippet: str) -> str:
        """
        Generate a deterministic hash for an ORM snippet.

        We normalize whitespace to handle formatting differences,
        but keep the actual code intact.
        """
        # Normalize: collapse whitespace, strip
        normalized = ' '.join(snippet.split())
        return hashlib.md5(normalized.encode()).hexdigest()[:12]


class CrossFileResolver:
    """
    Resolves query builders across files.

    When a file imports a query builder function from another file,
    this resolves the import and extracts the actual query from the builder.
    """

    def __init__(self, repo_root: str):
        self.repo_root = Path(repo_root)
        self.cache: Dict[str, List[ExtractedQuery]] = {}  # file_path -> queries

    def resolve_query_builder(
        self,
        importing_file: str,
        builder_name: str,
        builder_module: str
    ) -> Optional[ExtractedQuery]:
        """
        Find the actual query definition in the builder file.

        Args:
            importing_file: Path to file that imports the builder
            builder_name: Name of the builder function
            builder_module: Module path (e.g., '.query_builders', '..utils.queries')

        Returns:
            The ExtractedQuery from the builder, or None if not found
        """
        # Resolve the module path to a file
        builder_file = self._resolve_module_path(importing_file, builder_module)
        if not builder_file or not builder_file.exists():
            return None

        # Extract queries from the builder file (with caching)
        builder_path = str(builder_file)
        if builder_path not in self.cache:
            try:
                source = builder_file.read_text(encoding='utf-8')
                extractor = ASTQueryExtractor(source, builder_path)
                self.cache[builder_path] = extractor.extract()
            except Exception:
                return None

        # Find the specific builder function
        for query in self.cache[builder_path]:
            if query.function_name == builder_name:
                return query

        return None

    def _resolve_module_path(self, from_file: str, module: str) -> Optional[Path]:
        """
        Resolve a module path to an actual file.

        Handles:
        - Relative imports: .query_builders -> same directory
        - Parent imports: ..utils.queries -> parent directory
        - Absolute imports: app.services.queries -> from repo root
        """
        from_path = Path(from_file)

        if module.startswith('.'):
            # Relative import
            # Count leading dots
            dots = len(module) - len(module.lstrip('.'))
            module_part = module.lstrip('.')

            # Go up (dots - 1) directories from the file's directory
            base_dir = from_path.parent
            for _ in range(dots - 1):
                base_dir = base_dir.parent

            if module_part:
                # Try as file first, then as package
                candidates = [
                    base_dir / (module_part.replace('.', '/') + '.py'),
                    base_dir / module_part.replace('.', '/') / '__init__.py',
                ]
            else:
                candidates = [base_dir / '__init__.py']
        else:
            # Absolute import - try from repo root and from file's directory
            candidates = [
                self.repo_root / (module.replace('.', '/') + '.py'),
                self.repo_root / module.replace('.', '/') / '__init__.py',
                from_path.parent / (module.replace('.', '/') + '.py'),
            ]

        for candidate in candidates:
            if candidate.exists():
                return candidate

        return None


def extract_queries_from_file(file_path: str) -> List[ExtractedQuery]:
    """
    Extract all ORM queries from a Python file.

    This is the main entry point for single-file extraction.

    Args:
        file_path: Path to the Python file

    Returns:
        List of ExtractedQuery objects
    """
    path = Path(file_path)
    if not path.exists():
        return []

    try:
        source = path.read_text(encoding='utf-8')
    except Exception:
        return []

    extractor = ASTQueryExtractor(source, file_path)
    return extractor.extract()


def extract_queries_from_source(source: str, file_path: Optional[str] = None) -> List[ExtractedQuery]:
    """
    Extract all ORM queries from Python source code.

    Args:
        source: Python source code
        file_path: Optional path for reference

    Returns:
        List of ExtractedQuery objects
    """
    extractor = ASTQueryExtractor(source, file_path)
    return extractor.extract()


# Test the extractor
if __name__ == '__main__':
    test_code = '''
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from ..models import Customer, Order
from .query_builders import build_top_customers_query

class CustomerService:
    def __init__(self, db: Session):
        self.db = db

    def get_customer_by_id(self, customer_id: int):
        return self.db.query(Customer).filter(
            Customer.c_custkey == customer_id
        ).first()

    def get_all_customers(self, limit: int = 100):
        return self.db.query(
            Customer.c_custkey,
            Customer.c_name,
            Customer.c_acctbal
        ).order_by(desc(Customer.c_acctbal)).limit(limit).all()

    def get_top_customers(self, min_balance: float):
        # Uses imported query builder
        query = build_top_customers_query(self.db, min_balance)
        return query.limit(100).all()

    def count_customers(self):
        return self.db.query(func.count(Customer.c_custkey)).scalar()
'''

    extractor = ASTQueryExtractor(test_code, 'test_service.py')
    queries = extractor.extract()

    print(f"Found {len(queries)} queries:\n")
    for q in queries:
        print(f"Function: {q.function_name}")
        print(f"Hash: {q.snippet_hash}")
        print(f"Terminal: .{q.terminal_method}()")
        if q.imports_query_builder:
            print(f"Uses imported builder: {q.imported_builder_name} from {q.imported_builder_module}")
        print(f"Snippet:\n{q.orm_snippet}")
        print("-" * 40)
