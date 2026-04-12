"""
RDST Scan JS/TS Extractor - Deterministic query extraction from JavaScript/TypeScript.

Extracts ORM queries from Prisma and Drizzle code using regex + brace/paren counting.
Python's ast module can't parse JS/TS, so we use a regex-based approach that:

1. Finds terminal method calls (prisma.model.findMany, db.select, etc.)
2. Extracts the full expression including nested braces/parens
3. Hashes snippets deterministically for caching

The LLM handles the actual ORM→SQL conversion; this module just identifies and
extracts code snippets in a deterministic way.
"""

import re
import hashlib
from pathlib import Path
from typing import List, Optional, Tuple

from .ast_extractor import ExtractedQuery


# ---------------------------------------------------------------------------
# Prisma patterns
# ---------------------------------------------------------------------------

# All Prisma model-level query methods
PRISMA_TERMINAL_METHODS = {
    'findMany', 'findUnique', 'findFirst', 'findFirstOrThrow', 'findUniqueOrThrow',
    'create', 'createMany', 'createManyAndReturn',
    'update', 'updateMany', 'updateManyAndReturn',
    'upsert',
    'delete', 'deleteMany',
    'count', 'aggregate', 'groupBy',
}

# Raw SQL methods on prisma client
PRISMA_RAW_METHODS = {
    '$queryRaw', '$queryRawUnsafe', '$executeRaw', '$executeRawUnsafe',
}

# Prisma transaction
PRISMA_TRANSACTION = {'$transaction'}

# Combined regex: prisma.<model>.<method>( or prisma.<rawMethod>(
# Captures: full match start, model name, method name
_PRISMA_MODEL_RE = re.compile(
    r'(\bprisma\s*\.\s*(\w+)\s*\.\s*('
    + '|'.join(re.escape(m) for m in PRISMA_TERMINAL_METHODS)
    + r')\s*\()',
)

_PRISMA_RAW_RE = re.compile(
    r'(\bprisma\s*\.\s*('
    + '|'.join(re.escape(m).replace(r'\$', r'\$') for m in PRISMA_RAW_METHODS)
    + r')\s*[\(`])',  # May use ( or template literal `
)

_PRISMA_TRANSACTION_RE = re.compile(
    r'(\bprisma\s*\.\s*\$transaction\s*\()',
)

# ---------------------------------------------------------------------------
# Drizzle patterns
# ---------------------------------------------------------------------------

# Drizzle SQL-like builder starters: db.select(), db.insert(), etc.
DRIZZLE_STARTERS = {
    'select', 'selectDistinct', 'selectDistinctOn',
    'insert', 'update', 'delete', 'execute',
}

# Drizzle special methods
DRIZZLE_SPECIAL = {'$count'}

# db.select(...).from(...) or db.insert(table).values(...) etc.
_DRIZZLE_BUILDER_RE = re.compile(
    r'(\bdb\s*\.\s*('
    + '|'.join(re.escape(m).replace(r'\$', r'\$') for m in DRIZZLE_STARTERS | DRIZZLE_SPECIAL)
    + r')\s*\()',
)

# Drizzle relational API: db.query.<model>.findMany/findFirst
_DRIZZLE_RELATIONAL_RE = re.compile(
    r'(\bdb\s*\.\s*query\s*\.\s*(\w+)\s*\.\s*(findMany|findFirst)\s*\()',
)

# Drizzle transaction: db.transaction(
_DRIZZLE_TRANSACTION_RE = re.compile(
    r'(\bdb\s*\.\s*transaction\s*\()',
)

# Drizzle batch: db.batch(
_DRIZZLE_BATCH_RE = re.compile(
    r'(\bdb\s*\.\s*batch\s*\()',
)

# Drizzle raw SQL: sql`...` or sql.raw(...)
_DRIZZLE_SQL_TAG_RE = re.compile(
    r'(\bsql\s*`)',
)

# Drizzle set operations (standalone): union, unionAll, intersect, except_
_DRIZZLE_SET_OPS_RE = re.compile(
    r'(\b(union|unionAll|intersect|except_?)\s*\()',
)

# ---------------------------------------------------------------------------
# JS/TS function boundary detection
# ---------------------------------------------------------------------------

# Match function/method definitions in JS/TS
_FUNCTION_DEF_RE = re.compile(
    r'(?:'
    r'(?:async\s+)?function\s+(\w+)'       # function name() or async function name()
    r'|(\w+)\s*(?::\s*\w[\w<>,\s]*\s*)?\s*[=]\s*(?:async\s+)?(?:\([^)]*\)|[\w]+)\s*=>'  # const name = (...) =>
    r'|(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*\w[\w<>,\s|]*)?\s*\{'  # class method: name(...) {
    r')'
)

# ---------------------------------------------------------------------------
# Core extraction helpers
# ---------------------------------------------------------------------------

def _is_inside_comment_or_string(source: str, pos: int) -> bool:
    """
    Check if position is inside a comment or string literal.
    Simple heuristic — scans backwards from pos for comment/string context.
    """
    # Check if we're on a line that starts with // (single-line comment)
    line_start = source.rfind('\n', 0, pos) + 1
    line_before = source[line_start:pos].lstrip()
    if line_before.startswith('//'):
        return True

    # Check if inside a block comment: look for /* before pos without matching */
    last_block_open = source.rfind('/*', 0, pos)
    if last_block_open != -1:
        last_block_close = source.rfind('*/', 0, pos)
        if last_block_close < last_block_open:
            return True

    # Check if inside a string — count unescaped quotes before pos on same line
    line_content = source[line_start:pos]
    in_single = False
    in_double = False
    i = 0
    while i < len(line_content):
        ch = line_content[i]
        if ch == '\\':
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        i += 1

    return in_single or in_double


def _extract_balanced_expression(source: str, start: int) -> Tuple[str, int]:
    """
    Extract a balanced expression starting at `start`.
    Handles parens (), braces {}, brackets [], template literals ``,
    and skips string contents.

    Returns (expression_text, end_position).
    """
    depth_paren = 0
    depth_brace = 0
    depth_bracket = 0
    in_single_str = False
    in_double_str = False
    in_template = False
    i = start
    length = len(source)

    # We expect to start at an opening ( or { or `
    # Walk forward, tracking nesting
    started = False

    while i < length:
        ch = source[i]

        # Handle escape sequences inside strings
        if (in_single_str or in_double_str or in_template) and ch == '\\':
            i += 2
            continue

        # String state tracking
        if ch == "'" and not in_double_str and not in_template:
            in_single_str = not in_single_str
            i += 1
            continue
        if ch == '"' and not in_single_str and not in_template:
            in_double_str = not in_double_str
            i += 1
            continue
        if ch == '`' and not in_single_str and not in_double_str:
            in_template = not in_template
            if not started and not in_template:
                # We started with ` and just closed it
                return source[start:i + 1], i + 1
            i += 1
            continue

        # Skip content inside strings
        if in_single_str or in_double_str or in_template:
            i += 1
            continue

        # Skip single-line comments
        if ch == '/' and i + 1 < length:
            if source[i + 1] == '/':
                # Skip to end of line
                newline = source.find('\n', i)
                i = newline + 1 if newline != -1 else length
                continue
            if source[i + 1] == '*':
                # Skip to end of block comment
                end_comment = source.find('*/', i + 2)
                i = end_comment + 2 if end_comment != -1 else length
                continue

        # Track nesting
        if ch == '(':
            depth_paren += 1
            started = True
        elif ch == ')':
            depth_paren -= 1
        elif ch == '{':
            depth_brace += 1
            started = True
        elif ch == '}':
            depth_brace -= 1
        elif ch == '[':
            depth_bracket += 1
            started = True
        elif ch == ']':
            depth_bracket -= 1

        i += 1

        # Check if all brackets are balanced and we've started
        if started and depth_paren <= 0 and depth_brace <= 0 and depth_bracket <= 0:
            # Continue to capture chained method calls: .method(...)
            # Look ahead for dot-method continuation
            rest = source[i:].lstrip()
            if rest.startswith('.'):
                # There's a chained call — continue extracting
                dot_pos = source.index('.', i)
                # Find the method name after the dot
                method_match = re.match(r'\.\s*(\w+)\s*\(', source[dot_pos:])
                if method_match:
                    # Continue from after the method name's opening paren
                    i = dot_pos + method_match.end() - 1  # position at (
                    depth_paren = 1
                    started = True
                    i += 1  # move past (
                    continue
                else:
                    # Dot property access without call — still part of chain
                    prop_match = re.match(r'\.\s*\w+', source[dot_pos:])
                    if prop_match:
                        i = dot_pos + prop_match.end()
                        continue
            break

    return source[start:i], i


def _extract_drizzle_chain(source: str, start: int) -> Tuple[str, int]:
    """
    Extract a Drizzle builder chain starting from db.select(...).
    Drizzle uses chained calls: db.select().from(table).where(...).limit(10)

    This extracts the initial call and all chained .method() calls.
    """
    # First extract the initial balanced expression
    expr, end = _extract_balanced_expression(source, start)
    return expr, end


def _find_function_at(source: str, pos: int) -> Tuple[Optional[str], Optional[str]]:
    """
    Find the function/method name and optional class name containing position `pos`.
    Returns (function_name, class_name).
    """
    # Search backwards for the nearest function definition
    best_func = None
    best_class = None
    best_pos = -1

    for m in _FUNCTION_DEF_RE.finditer(source[:pos]):
        func_name = m.group(1) or m.group(2) or m.group(3)
        if func_name and m.start() > best_pos:
            best_func = func_name
            best_pos = m.start()

    # Try to find class name
    if best_func:
        # Look for 'class ClassName' before the function
        class_re = re.compile(r'\bclass\s+(\w+)')
        for m in class_re.finditer(source[:best_pos]):
            best_class = m.group(1)

    return best_func, best_class


def _line_number(source: str, pos: int) -> int:
    """Get 1-based line number for position in source."""
    return source[:pos].count('\n') + 1


def _hash_snippet(snippet: str) -> str:
    """Generate deterministic hash for an ORM snippet."""
    normalized = ' '.join(snippet.split())
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Main extractor class
# ---------------------------------------------------------------------------

class JSQueryExtractor:
    """
    Extract ORM queries from JavaScript/TypeScript source code.

    Supports:
    - Prisma: findMany, findUnique, findFirst, create, update, delete,
              count, aggregate, groupBy, $queryRaw, $executeRaw, $transaction, etc.
    - Drizzle: db.select().from().where(), db.insert().values(),
               db.query.model.findMany(), db.execute(sql``), db.transaction(), etc.
    """

    def __init__(self, source: str, file_path: Optional[str] = None):
        self.source = source
        self.file_path = file_path
        self.queries: List[ExtractedQuery] = []

    def extract(self) -> List[ExtractedQuery]:
        """Extract all ORM queries from the source. Returns sorted by line number."""
        self._extract_prisma_queries()
        self._extract_drizzle_queries()

        # Deduplicate by snippet_hash (same expression found by multiple patterns)
        seen_hashes = set()
        unique = []
        for q in self.queries:
            if q.snippet_hash not in seen_hashes:
                seen_hashes.add(q.snippet_hash)
                unique.append(q)

        unique.sort(key=lambda q: q.start_line)
        return unique

    def _extract_prisma_queries(self):
        """Extract Prisma ORM queries."""
        # Model-level methods: prisma.user.findMany({...})
        for m in _PRISMA_MODEL_RE.finditer(self.source):
            if _is_inside_comment_or_string(self.source, m.start()):
                continue

            model_name = m.group(2)
            method_name = m.group(3)

            # Find the opening paren position
            paren_pos = m.end() - 1
            expr, end_pos = _extract_balanced_expression(self.source, paren_pos)

            # Full snippet includes the prisma.model.method prefix
            full_snippet = self.source[m.start():end_pos].strip()

            func_name, class_name = _find_function_at(self.source, m.start())
            start_line = _line_number(self.source, m.start())
            end_line = _line_number(self.source, end_pos)

            self.queries.append(ExtractedQuery(
                function_name=func_name or '<module>',
                class_name=class_name,
                orm_snippet=full_snippet,
                snippet_hash=_hash_snippet(full_snippet),
                start_line=start_line,
                end_line=end_line,
                terminal_method=method_name,
                file_path=self.file_path,
                orm_type='prisma',
            ))

        # Raw SQL methods: prisma.$queryRaw`...` or prisma.$queryRaw(...)
        for m in _PRISMA_RAW_RE.finditer(self.source):
            if _is_inside_comment_or_string(self.source, m.start()):
                continue

            method_name = m.group(2)

            # Check if it's a template literal or paren call
            char_at_end = self.source[m.end() - 1] if m.end() > 0 else ''
            if char_at_end == '`':
                # Template literal: prisma.$queryRaw`SELECT ...`
                expr, end_pos = _extract_balanced_expression(self.source, m.end() - 1)
            else:
                # Paren call: prisma.$queryRawUnsafe("SELECT ...")
                expr, end_pos = _extract_balanced_expression(self.source, m.end() - 1)

            full_snippet = self.source[m.start():end_pos].strip()
            func_name, class_name = _find_function_at(self.source, m.start())
            start_line = _line_number(self.source, m.start())
            end_line = _line_number(self.source, end_pos)

            self.queries.append(ExtractedQuery(
                function_name=func_name or '<module>',
                class_name=class_name,
                orm_snippet=full_snippet,
                snippet_hash=_hash_snippet(full_snippet),
                start_line=start_line,
                end_line=end_line,
                terminal_method=method_name,
                file_path=self.file_path,
                orm_type='prisma',
            ))

        # Transaction: prisma.$transaction([...]) or prisma.$transaction(async (tx) => {...})
        for m in _PRISMA_TRANSACTION_RE.finditer(self.source):
            if _is_inside_comment_or_string(self.source, m.start()):
                continue

            paren_pos = m.end() - 1
            expr, end_pos = _extract_balanced_expression(self.source, paren_pos)
            full_snippet = self.source[m.start():end_pos].strip()

            func_name, class_name = _find_function_at(self.source, m.start())
            start_line = _line_number(self.source, m.start())
            end_line = _line_number(self.source, end_pos)

            self.queries.append(ExtractedQuery(
                function_name=func_name or '<module>',
                class_name=class_name,
                orm_snippet=full_snippet,
                snippet_hash=_hash_snippet(full_snippet),
                start_line=start_line,
                end_line=end_line,
                terminal_method='$transaction',
                file_path=self.file_path,
                orm_type='prisma',
            ))

    def _extract_drizzle_queries(self):
        """Extract Drizzle ORM queries."""
        # Builder chains: db.select().from().where()...
        for m in _DRIZZLE_BUILDER_RE.finditer(self.source):
            if _is_inside_comment_or_string(self.source, m.start()):
                continue

            method_name = m.group(2)
            paren_pos = m.end() - 1
            expr, end_pos = _extract_drizzle_chain(self.source, paren_pos)
            full_snippet = self.source[m.start():end_pos].strip()

            func_name, class_name = _find_function_at(self.source, m.start())
            start_line = _line_number(self.source, m.start())
            end_line = _line_number(self.source, end_pos)

            self.queries.append(ExtractedQuery(
                function_name=func_name or '<module>',
                class_name=class_name,
                orm_snippet=full_snippet,
                snippet_hash=_hash_snippet(full_snippet),
                start_line=start_line,
                end_line=end_line,
                terminal_method=method_name,
                file_path=self.file_path,
                orm_type='drizzle',
            ))

        # Relational API: db.query.user.findMany({...})
        for m in _DRIZZLE_RELATIONAL_RE.finditer(self.source):
            if _is_inside_comment_or_string(self.source, m.start()):
                continue

            model_name = m.group(2)
            method_name = m.group(3)
            paren_pos = m.end() - 1
            expr, end_pos = _extract_balanced_expression(self.source, paren_pos)
            full_snippet = self.source[m.start():end_pos].strip()

            func_name, class_name = _find_function_at(self.source, m.start())
            start_line = _line_number(self.source, m.start())
            end_line = _line_number(self.source, end_pos)

            self.queries.append(ExtractedQuery(
                function_name=func_name or '<module>',
                class_name=class_name,
                orm_snippet=full_snippet,
                snippet_hash=_hash_snippet(full_snippet),
                start_line=start_line,
                end_line=end_line,
                terminal_method=method_name,
                file_path=self.file_path,
                orm_type='drizzle',
            ))

        # Transaction: db.transaction(async (tx) => {...})
        for m in _DRIZZLE_TRANSACTION_RE.finditer(self.source):
            if _is_inside_comment_or_string(self.source, m.start()):
                continue

            paren_pos = m.end() - 1
            expr, end_pos = _extract_balanced_expression(self.source, paren_pos)
            full_snippet = self.source[m.start():end_pos].strip()

            func_name, class_name = _find_function_at(self.source, m.start())
            start_line = _line_number(self.source, m.start())
            end_line = _line_number(self.source, end_pos)

            self.queries.append(ExtractedQuery(
                function_name=func_name or '<module>',
                class_name=class_name,
                orm_snippet=full_snippet,
                snippet_hash=_hash_snippet(full_snippet),
                start_line=start_line,
                end_line=end_line,
                terminal_method='transaction',
                file_path=self.file_path,
                orm_type='drizzle',
            ))

        # Batch: db.batch([...])
        for m in _DRIZZLE_BATCH_RE.finditer(self.source):
            if _is_inside_comment_or_string(self.source, m.start()):
                continue

            paren_pos = m.end() - 1
            expr, end_pos = _extract_balanced_expression(self.source, paren_pos)
            full_snippet = self.source[m.start():end_pos].strip()

            func_name, class_name = _find_function_at(self.source, m.start())
            start_line = _line_number(self.source, m.start())
            end_line = _line_number(self.source, end_pos)

            self.queries.append(ExtractedQuery(
                function_name=func_name or '<module>',
                class_name=class_name,
                orm_snippet=full_snippet,
                snippet_hash=_hash_snippet(full_snippet),
                start_line=start_line,
                end_line=end_line,
                terminal_method='batch',
                file_path=self.file_path,
                orm_type='drizzle',
            ))


# ---------------------------------------------------------------------------
# Module-level entry points (same interface as ast_extractor)
# ---------------------------------------------------------------------------

def extract_queries_from_js_file(file_path: str) -> List[ExtractedQuery]:
    """
    Extract all ORM queries from a JS/TS file.

    Args:
        file_path: Path to the .js/.ts/.tsx/.jsx file

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

    extractor = JSQueryExtractor(source, file_path)
    return extractor.extract()


def extract_queries_from_js_source(source: str, file_path: Optional[str] = None) -> List[ExtractedQuery]:
    """
    Extract all ORM queries from JS/TS source code string.

    Args:
        source: JavaScript/TypeScript source code
        file_path: Optional path for reference

    Returns:
        List of ExtractedQuery objects
    """
    extractor = JSQueryExtractor(source, file_path)
    return extractor.extract()
