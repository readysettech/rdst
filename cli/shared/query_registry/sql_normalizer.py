"""
SQL Normalization with SQLGlot

Provides robust SQL parameterization using SQLGlot's AST parser.
Replaces literals with named placeholders (:p1, :p2, etc.) while
correctly handling comments, nested queries, and edge cases.
"""

import re
import logging
from functools import lru_cache
from itertools import count
from typing import Tuple, Dict, Any, Optional, Set

from sqlglot import parse_one, exp
from sqlglot.dialects.dialect import Dialect

logger = logging.getLogger(__name__)

# Suppress sqlglot parser warnings about unsupported syntax (e.g. EXPLAIN
# statements).  These are noisy and harmless -- sqlglot falls back to parsing
# the statement as a Command, which is fine for our normalisation purposes.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


@lru_cache(maxsize=None)
def _compat_generator_class(dialect: str):
    """Build (once per dialect) a generator class that omits AS for table aliases.

    sqlglot's generators produce 'FROM table AS alias' but Readyset's query ID
    hashing treats 'FROM table alias' (no AS) as a different query. Since the
    wire protocol sends queries without AS, we generate without AS to keep cache
    IDs consistent. Generating with the parse dialect keeps engine-specific
    syntax such as MySQL's `->>` JSON operator intact.
    """
    dialect_obj = Dialect.get_or_raise(dialect) if dialect else Dialect()
    base_cls = type(dialect_obj.generator())

    class _ReadysetCompatGenerator(base_cls):
        def table_sql(self, expression: exp.Table, sep: str = " ") -> str:
            return super().table_sql(expression, sep=sep)

        def placeholder_sql(self, expression: exp.Placeholder) -> str:
            # Registry placeholders are always :pN, whatever the dialect's native style.
            return f":{expression.name}" if expression.this else "?"

    return _ReadysetCompatGenerator, dialect_obj


def _compat_generator(dialect: str = None):
    generator_cls, dialect_obj = _compat_generator_class(dialect or "")
    return generator_cls(dialect=dialect_obj)


_JSON_PATH_PARENTS = tuple(
    getattr(exp, name)
    for name in ("JSONExtract", "JSONExtractScalar", "JSONBExtract", "JSONBExtractScalar")
    if hasattr(exp, name)
)


def _is_json_path_literal(literal: exp.Literal) -> bool:
    """JSON paths (`col->>'$.a'`, `col #>> '{a,b}'`, `JSON_VALUE(col, '$.a')`) name a
    key inside the document. They are structure, not data, so they stay in the
    normalized text instead of becoming parameters."""
    if not literal.is_string:
        return False
    parent = literal.parent
    if isinstance(parent, _JSON_PATH_PARENTS) and parent.expression is literal:
        return True
    return literal.this.startswith("$")


def _is_placeholder_index(literal: exp.Literal) -> bool:
    """Dialects with positional placeholders parse `$1` as a Parameter node
    wrapping the digit literal. The digit names the slot, not a value, so the
    parameter survives normalization verbatim."""
    return isinstance(literal.parent, exp.Parameter)


def _is_ordinal_literal(literal: exp.Literal) -> bool:
    """GROUP BY 1 / ORDER BY 2 ordinals reference select-list positions. They
    are structure, not data, so they stay in the normalized text. An ORDER BY
    inside a window (`OVER (ORDER BY 1)`) or an aggregate function
    (`ARRAY_AGG(x ORDER BY 1)`) instead orders rows by a constant, so a bare
    number there is a value, not a select-list ordinal."""
    if literal.is_string:
        return False
    parent = literal.parent
    if isinstance(parent, exp.Group):
        return True
    return (
        isinstance(parent, exp.Ordered)
        and parent.this is literal
        and isinstance(parent.parent, exp.Order)
        and isinstance(parent.parent.parent, (exp.Select, exp.Union))
    )


def _is_structural_literal(literal: exp.Literal) -> bool:
    """Literals that describe query structure rather than data values; these
    are never extracted as parameters."""
    return (
        _is_json_path_literal(literal)
        or _is_placeholder_index(literal)
        or _is_ordinal_literal(literal)
    )


_SYSTEM_SCHEMAS = {
    "pg_catalog",
    "information_schema",
    "performance_schema",
    "mysql",
    "sys",
}


# RDST prefixes its own observation statements with this marker; PostgreSQL
# stores the first-seen text verbatim while the queryid jumble ignores
# comments, so the marker identifies self-traffic without changing identity.
RDST_SELF_MARKER = "/*rdst"

# Session/transaction control and other utility statements are never user
# workload candidates; they also commonly defeat the AST parser.
_UTILITY_PREFIXES = (
    "begin",
    "commit",
    "rollback",
    "start transaction",
    "set ",
    "set\n",
    "show ",
    "reset ",
    "deallocate",
    "discard",
    "fetch",
    "close ",
    "checkpoint",
    "listen",
    "notify",
    "unlisten",
)

# Parse-failure fallback: capture relation tokens after FROM/JOIN/INTO/
# UPDATE/DELETE FROM so catalog-only statements the parser cannot handle
# (array casts, tooling SQL) are still classified instead of failing open.
_RELATION_TOKEN = re.compile(
    r"\b(?:from|join|into|update|delete\s+from)\s+"
    r'("?[A-Za-z_][\w$]*"?(?:\."?[A-Za-z_][\w$]*"?)?)',
    re.IGNORECASE,
)


def _is_system_relation(token: str) -> bool:
    parts = [part.strip('"').lower() for part in token.split(".")]
    schema = parts[0] if len(parts) == 2 else ""
    name = parts[-1]
    if schema in _SYSTEM_SCHEMAS:
        return True
    return not schema and name.startswith("pg_")


def references_user_relations(sql: str, dialect: str = None) -> bool:
    """Return True when the statement touches at least one user relation.

    System relations are catalog/statistics tables: schema-qualified
    pg_catalog/information_schema/performance_schema/mysql/sys names, and
    unqualified pg_-prefixed names, which resolve to pg_catalog through the
    default search_path. RDST-marked self-traffic, utility statements, and
    statements with no relations at all report False. When the AST parser
    fails, a regex fallback classifies by the captured relation tokens; only
    a statement whose tokens include a non-system relation (or that yields
    no signal at all while resembling a query) reports True, so a parser gap
    cannot flood the library with catalog traffic yet never drops a user
    query that names a user relation.
    """
    stripped = sql.lstrip()
    if stripped.lower().startswith(RDST_SELF_MARKER):
        return False
    # Classification looks through a leading comment the way the engine's
    # jumble does.
    while stripped.startswith("/*"):
        end = stripped.find("*/")
        if end < 0:
            break
        stripped = stripped[end + 2 :].lstrip()
    lowered = stripped.lower()
    if any(lowered.startswith(prefix) for prefix in _UTILITY_PREFIXES):
        return False
    try:
        parsed = parse_one(stripped, dialect=dialect)
    except Exception:
        tokens = _RELATION_TOKEN.findall(stripped)
        if tokens:
            return any(not _is_system_relation(token) for token in tokens)
        return True
    cte_names = {
        cte.alias_or_name.lower()
        for cte in parsed.find_all(exp.CTE)
        if cte.alias_or_name
    }
    for table in parsed.find_all(exp.Table):
        name = (table.name or "").lower()
        schema = (table.db or "").lower()
        if not name or (not schema and name in cte_names):
            continue
        if schema in _SYSTEM_SCHEMAS:
            continue
        if not schema and name.startswith("pg_"):
            continue
        return True
    return False


def normalize_and_extract(sql: str, dialect: str = None) -> Tuple[str, Dict[str, dict]]:
    """
    Parse SQL, extract literals, replace with named :p1, :p2 placeholders.

    Args:
        sql: Original SQL with actual values
        dialect: Optional dialect ('postgres', 'mysql', etc.)

    Returns:
        (normalized_sql, params) where params = {'p1': {'value': 20, 'type': 'number'}, ...}
    """
    if not sql or not sql.strip():
        return sql, {}

    try:
        tree = parse_one(sql, dialect=dialect)
    except Exception as e:
        logger.debug(f"SQLGlot parsing failed, falling back to regex: {e}")
        return _fallback_normalize(sql)

    params = {}
    literals = [lit for lit in tree.find_all(exp.Literal) if not _is_structural_literal(lit)]
    for i, literal in enumerate(literals, 1):
        param_name = f"p{i}"
        # Store value with type info
        if literal.is_string:
            params[param_name] = {'value': literal.this, 'type': 'string'}
        else:
            params[param_name] = {'value': literal.this, 'type': 'number'}
        # Replace with named :p1, :p2 placeholder
        literal.replace(exp.Placeholder(this=param_name))

    return _compat_generator(dialect).generate(tree), params


# Positional `$N` slots (pg_stat_statements texts) name the same parameter as
# RDST's own `:pN`: slot k is `pk`, the mapping query_registry's
# _identity_slot_count already assumes. A `$` that opens a dollar-quoted string
# ($$...$$, $tag$...$tag$) starts literal text instead, and a tag never starts
# with a digit, so masking the quoted regions keeps `$$100$$` out of the scan.
_DOLLAR_SLOT = re.compile(r"\$(\d+)")
_DOLLAR_QUOTE_OPEN = re.compile(r"\$(?:[A-Za-z_]\w*)?\$")
_SINGLE_QUOTED = re.compile(r"'(?:[^']|'')*'")


def mask_string_literals(sql: str) -> str:
    """Blank the body of every string literal, preserving offsets."""
    masked = []
    position = 0
    while position < len(sql):
        char = sql[position]
        if char == "'":
            match = _SINGLE_QUOTED.match(sql, position)
            end = match.end() if match else len(sql)
        elif char == "$":
            opening = _DOLLAR_QUOTE_OPEN.match(sql, position)
            if opening is None:
                masked.append(char)
                position += 1
                continue
            closing = sql.find(opening.group(0), opening.end())
            end = closing + len(opening.group(0)) if closing >= 0 else len(sql)
        else:
            masked.append(char)
            position += 1
            continue
        masked.append(" " * (end - position))
        position = end
    return "".join(masked)


def _dollar_slot_names(sql: str) -> Set[str]:
    """Parameter names for the `$N` slots that sit outside string literals."""
    return {f"p{index}" for index in _DOLLAR_SLOT.findall(mask_string_literals(sql))}


def _param_literal(param_info: Dict[str, Any]) -> str:
    """Render one stored parameter as a SQL literal."""
    value = param_info.get("value")
    if param_info.get("type") == "string":
        # Double embedded quotes so a value cannot terminate its own literal
        return "'" + str(value).replace("'", "''") + "'"
    return str(value)


def _substitute_dollar_slots(sql: str, params: Dict[str, dict]) -> str:
    """Replace each `$k` slot with the value stored under `pk`.

    sqlglot only reads `$N` as a parameter under a dialect that spells
    placeholders that way, and regenerates it verbatim otherwise, so the
    positional style is substituted textually before the AST pass.
    """
    masked = mask_string_literals(sql)
    pieces = []
    last = 0
    for match in _DOLLAR_SLOT.finditer(masked):
        param_info = params.get(f"p{match.group(1)}")
        if not isinstance(param_info, dict):
            continue
        pieces.append(sql[last : match.start()])
        pieces.append(_param_literal(param_info))
        last = match.end()
    pieces.append(sql[last:])
    return "".join(pieces)


def reconstruct_sql(normalized_sql: str, params: Dict[str, dict], dialect: str = None) -> str:
    """
    Reconstruct executable SQL by replacing :p1, :p2 and $1, $2 placeholders
    with values.

    Args:
        normalized_sql: SQL with :p1, :p2 or $1, $2 placeholders
        params: {'p1': {'value': ..., 'type': ...}, ...}
        dialect: Optional dialect

    Returns:
        Executable SQL with actual values
    """
    if not normalized_sql or not normalized_sql.strip():
        return normalized_sql

    if not params:
        return normalized_sql

    normalized_sql = _substitute_dollar_slots(normalized_sql, params)

    try:
        tree = parse_one(normalized_sql, dialect=dialect)
    except Exception as e:
        logger.debug(f"SQLGlot parsing failed during reconstruction, falling back to regex: {e}")
        return _fallback_reconstruct(normalized_sql, params)

    for placeholder in tree.find_all(exp.Placeholder):
        param_name = placeholder.this
        if param_name and param_name in params:
            param_info = params[param_name]
            if param_info['type'] == 'string':
                replacement = exp.Literal.string(param_info['value'])
            else:
                replacement = exp.Literal.number(param_info['value'])
            placeholder.replace(replacement)

    return _compat_generator(dialect).generate(tree)


# Placeholder spellings that must hash alike: RDST's own `:pN`, positional
# `$N` (pg_stat_statements texts; the digits never touch other tokens, so
# `$1::bigint` still maps), and a bare `?` (MySQL digest texts). Postgres
# spells JSON/jsonpath operators with `?` too (`??`, `?|`, `?&`, `@?`); a
# `?` adjacent to `?`, `|`, or `&`, or following `@`, is one of those
# operators in every dialect that produces it, never a placeholder.
# Stored texts from builds whose dialect-aware normalization lifted the
# digit out of a `$N` parameter carry the fused artifact `$:pN`; the
# optional `$` absorbs it so those texts converge with their clean forms.
_PLACEHOLDER_TOKEN = re.compile(r"\$?:p\d+\b|\$\d+\b|(?<![?@])\?(?![?|&])")


def canonicalize_placeholder_style(normalized_sql: str) -> str:
    """Renumber every placeholder by textual position, in one `:pN` style.

    Engine-normalized statement texts keep their engine's placeholder style
    through normalization: pg_stat_statements texts carry `$N` (the
    dialect-less parser reads them as identifiers and regenerates them
    verbatim) and MySQL digest texts carry anonymous `?`, while RDST's own
    literal extraction emits `:pN` numbered in AST-traversal order, which
    need not match textual order. Hashing the normalized text directly
    therefore gives one logical query a different identity per source; hash
    derivation routes through this function, which rewrites the k-th
    placeholder in text order to `:pk` regardless of its spelling, so every
    style converges on one canonical form. Distinct slots keep distinct
    indices; pg_stat_statements numbers `$N` textually, so its indices are
    preserved as `:pN`.

    Expects normalize()d SQL: string literals are already extracted to
    `:pN` and comments are stripped, so a remaining `$N` or bare `?` is a
    real parameter slot rather than text inside a literal. Regex is the
    right tool here because the engine styles survive the AST as plain
    text; only the hash input goes through this mapping, displayed and
    stored SQL keep their original style.
    """
    if not normalized_sql:
        return normalized_sql
    position = count(1)
    return _PLACEHOLDER_TOKEN.sub(
        lambda _: f":p{next(position)}", normalized_sql
    )


def denormalize_for_readyset(sql: str, engine: str = "postgresql") -> str:
    """Convert RDST's `:pN`-style placeholders to ReadySet-compatible form.

    ReadySet uses Postgres/MySQL parsers under the hood, so the placeholder
    syntax must match the upstream engine:
      - PostgreSQL: `$1, $2, ...` (positional, indexed)
      - MySQL:      `?` (anonymous)

    Verified empirically (2026-04-28) against `readysettech/readyset:latest`
    on Postgres mode:
      - `EXPLAIN CREATE CACHE FROM ... WHERE x IN ($1, $2, $3)` → q_<hash>, yes
      - `EXPLAIN CREATE CACHE FROM ... WHERE x IN ($1)`         → same q_<hash>, yes
      - `EXPLAIN CREATE CACHE FROM ... WHERE x IN ('a','b','c')`→ same q_<hash>, yes
      - `EXPLAIN CREATE CACHE FROM ... WHERE x IN (?)`          → SYNTAX ERROR (Postgres rejects `?`)

    So for IN lists we don't need to collapse — ReadySet does it. We just need
    to use the right placeholder syntax for the engine.

    Fixes CLD-1748: previously we sent `IN (?)` to Postgres-mode ReadySet which
    failed parsing.
    """
    if not sql:
        return sql
    eng = (engine or "postgresql").lower()
    if eng in ("postgresql", "postgres", "pg"):
        # `:pN` → `$N` (preserves index, so multiple placeholders stay distinct)
        return re.sub(r":p(\d+)", r"$\1", sql)
    if eng == "mysql":
        # MySQL uses anonymous `?`. Collapse multi-placeholder IN lists since
        # MySQL doesn't accept `IN (?)` for variable-length lists either way —
        # users typically send literals there. For our :pN form, single `?` is
        # the safest equivalent.
        in_placeholder_list = re.compile(
            r'(\bIN\s*\(\s*)((?::p\d+)(?:\s*,\s*:p\d+)+)(\s*\))',
            re.IGNORECASE,
        )
        sql = in_placeholder_list.sub(r"\1?\3", sql)
        return re.sub(r":p\d+", "?", sql)
    # Unknown engine — pass through unchanged
    return sql


_READYSET_QUERY_ID_RE = re.compile(r"q_[0-9a-f]{8,}", re.IGNORECASE)


def parse_query_id_from_explain(output: str) -> Optional[str]:
    """Extract `q_<hash>` from `EXPLAIN CREATE CACHE FROM <sql>` output.

    ReadySet returns rows like:
        query id    | q_13b0714e3f57aa57
        query       | <normalized SQL>
        readyset supported | yes

    We scan the output for the first `q_<hex>` token. Returns None if absent.
    """
    if not output:
        return None
    m = _READYSET_QUERY_ID_RE.search(output)
    return m.group(0) if m else None


def parse_supported_from_explain(output: str) -> str:
    """Extract the `readyset supported` value from EXPLAIN output.

    Returns one of "yes" | "pending" | "unsupported: <reason>" | "" (if not parseable).
    """
    if not output:
        return ""
    for line in output.splitlines():
        if "readyset supported" in line.lower() or "supported" in line.lower():
            # Line like: "readyset supported | yes"  or  "supported     | unsupported: ..."
            parts = re.split(r"[|\t]", line, maxsplit=1)
            if len(parts) == 2:
                value = parts[1].strip().lower()
                if value.startswith("yes"):
                    return "yes"
                if value.startswith("pending"):
                    return "pending"
                if value.startswith("unsupported"):
                    return value
    return ""


def get_placeholder_names(normalized_sql: str, dialect: str = None) -> Set[str]:
    """
    Get all placeholder names in normalized SQL.

    Both spellings name the same parameter set: `:pk` names itself and the
    positional `$k` of an engine-normalized text names `pk`.

    Args:
        normalized_sql: SQL with :p1, :p2 or $1, $2 placeholders
        dialect: Optional dialect

    Returns:
        Set of placeholder names (e.g., {'p1', 'p2', 'p3'})
    """
    if not normalized_sql or not normalized_sql.strip():
        return set()

    names = _dollar_slot_names(normalized_sql)
    try:
        tree = parse_one(normalized_sql, dialect=dialect)
        return names | {p.this for p in tree.find_all(exp.Placeholder) if p.this}
    except Exception as e:
        logger.debug(f"SQLGlot parsing failed, falling back to regex: {e}")
        # Fallback: find :pN patterns with regex
        matches = re.findall(r':p(\d+)', normalized_sql)
        return names | {f"p{m}" for m in matches}


def _fallback_normalize(sql: str) -> Tuple[str, Dict[str, dict]]:
    """
    Fallback regex-based normalization when SQLGlot fails.

    This is the legacy approach - less accurate but works for edge cases
    where SQLGlot can't parse the SQL.
    """
    normalized = sql

    # Collapse whitespace
    normalized = re.sub(r'\s+', ' ', normalized)

    # Track extracted values
    params = {}
    param_counter = [0]

    def replace_with_placeholder(match, is_string=False):
        param_counter[0] += 1
        param_name = f"p{param_counter[0]}"
        value = match.group(0)

        if is_string:
            # Remove quotes from value
            params[param_name] = {'value': value[1:-1], 'type': 'string'}
        else:
            params[param_name] = {'value': value, 'type': 'number'}

        return f":{param_name}"

    # Replace string literals with placeholders
    normalized = re.sub(
        r"'[^']*'",
        lambda m: replace_with_placeholder(m, is_string=True),
        normalized
    )

    # Replace numeric literals with placeholders. A digit run preceded by a
    # single `$` is a positional placeholder's slot index (`$1`, `$12`), not
    # a literal; it stays verbatim, matching the parse path's Parameter
    # guard. A digit run preceded by `$$` is inside a dollar-quoted string
    # (`$$100$$`), so it stays eligible for extraction.
    normalized = re.sub(
        r'(?:(?<!\$)|(?<=\$\$))\b\d+(?:\.\d+)?\b',
        lambda m: replace_with_placeholder(m, is_string=False),
        normalized
    )

    return normalized.strip(), params


def _fallback_reconstruct(normalized_sql: str, params: Dict[str, dict]) -> str:
    """
    Fallback regex-based reconstruction when SQLGlot fails.
    """
    result = normalized_sql

    for param_name, param_info in params.items():
        result = result.replace(f":{param_name}", _param_literal(param_info), 1)

    return result
