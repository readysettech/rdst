"""What a monitored statement is: its kind, its display name, and whether RDST analyzes it.

One place for these rules so the query list, the load breakdown, the analyze route, the
Analyze database run and alerts agree. Analyzability follows the analyzer's own safety rules
(`shared.query_safety`): RDST plans only read-only SELECTs, never runs writes, DDL or
functions that reach outside the queried tables.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from typing import Any

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError

from shared.query_safety import SIDE_EFFECT_FUNCTIONS, validate_query_safety

# Per-text results are cached for a few databases' worth of tracked queries (10,000 each).
# Naming reads every member of a query's family, so a cache smaller than the tracked set is
# evicted by each pass before any entry is reused.
TEXT_CACHE_ENTRIES = 32_768

WRITE_VERBS = {"INSERT", "UPDATE", "DELETE", "MERGE", "REPLACE", "UPSERT", "COPY"}
DDL_VERBS = {"CREATE", "DROP", "ALTER", "TRUNCATE", "RENAME", "COMMENT", "GRANT", "REVOKE"}
MAINTENANCE_VERBS = {"ANALYZE", "VACUUM", "REINDEX", "CLUSTER", "OPTIMIZE", "CHECKPOINT",
                     "REFRESH", "LOCK", "CHECK", "CHECKSUM"}
READ_VERBS = {"SELECT", "WITH", "VALUES", "TABLE"}
PERMISSION_VERBS = {"GRANT", "REVOKE"}
# Mean latency above which EXPLAIN ANALYZE isn't run; the analyzer reads the plan instead.
EXECUTE_LIMIT_MS = 3000

_COMMENT = re.compile(r"^\s*(?:/\*.*?\*/\s*|--[^\n]*\n\s*)*", re.S)
_WORD = re.compile(r"[A-Za-z_]+")
_AGGREGATE = re.compile(r"\b(count|sum|avg|min|max|string_agg|array_agg|json_agg)\s*\(", re.I)
_IDENT = r"(?:\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[\w$]+)(?:\s*\.\s*(?:\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[\w$]+))*"
_TABLES = re.compile(r"\b(?:from|join|into|update|table)\s+(?:only\s+)?(" + _IDENT + r")", re.I)
_ON_TABLE = re.compile(r"\bon\s+(?:only\s+)?(" + _IDENT + r")", re.I)
_WHERE_COLUMN = re.compile(r"\bwhere\s+(?:not\s+)?\(?\s*(" + _IDENT + r")", re.I)
_DDL_OBJECT = re.compile(
    r"^\s*(create|drop|alter|truncate)\s+(?:or\s+replace\s+)?(?:unique\s+)?"
    r"(?:materialized\s+)?(index|table|view|sequence|schema|function|trigger|type|extension)?"
    r"\s*(?:concurrently\s+)?(?:if\s+(?:not\s+)?exists\s+)?(" + _IDENT + r")?", re.I)
_SIDE_EFFECT = re.compile(r"\b(" + "|".join(sorted(SIDE_EFFECT_FUNCTIONS, key=len, reverse=True))
                          + r")\s*\(", re.I)
_NOT_NAMES = {"select", "lateral", "unnest", "generate_series", "only", "if", "exists"}


def _strip(text: str) -> str:
    return _COMMENT.sub("", text or "", count=1).strip()


def _name(identifier: str) -> str:
    last = re.split(r"\s*\.\s*", identifier.strip())[-1]
    return last.strip('"`[]')


def verb(text: str) -> str:
    match = _WORD.match(_strip(text))
    return match.group(0).upper() if match else ""


def statement_kind(text: str) -> str:
    """select, write, ddl, maintenance, utility (SET, BEGIN, SHOW, ...) or unknown."""
    first = verb(text)
    if not first:
        return "unknown"
    if first in READ_VERBS:
        # A data-modifying CTE or SELECT INTO still writes.
        body = _strip(text).upper()
        if re.search(r"\b(INSERT|UPDATE|DELETE|MERGE)\b", body) and first == "WITH":
            return "write"
        return "select"
    if first in WRITE_VERBS:
        return "write"
    if first in DDL_VERBS:
        return "ddl"
    if first in MAINTENANCE_VERBS:
        return "maintenance"
    return "utility"


_CTE = re.compile(r"(?:\bwith\s+(?:recursive\s+)?|,\s*)(\w+)\s+as\s*\(", re.I)


def _tables(text: str) -> list[str]:
    names: list[str] = []
    ctes = {name.lower() for name in _CTE.findall(text)}
    for match in _TABLES.finditer(text):
        name = _name(match.group(1))
        if name and name.lower() not in _NOT_NAMES | ctes and not name.startswith("$") \
                and name not in names:
            names.append(name)
    return names


@lru_cache(maxsize=TEXT_CACHE_ENTRIES)
def display_name(text: str) -> str:
    """One scheme for every statement: `<OP> on <table>[ and <table>][ by <column>]`.

    OP is the first aggregate of a read (COUNT, SUM, ...) or the statement verb (SELECT,
    INSERT, DROP INDEX, ...); the column is the first one the WHERE clause filters on.
    """
    body = " ".join(_strip(text).split())
    if not body:
        return "Untitled query"
    kind = statement_kind(body)
    first = verb(body)
    if kind == "ddl":
        match = _DDL_OBJECT.match(body)
        if match:
            op = " ".join(part.upper() for part in match.group(1, 2) if part)
            on = _ON_TABLE.search(body)
            if on and match.group(2) and match.group(2).lower() in ("index", "trigger"):
                return f"{op} on {_name(on.group(1))}"
            if match.group(3):
                return f"{op} {_name(match.group(3))}"
            return op
        return first
    if kind in ("maintenance", "utility"):
        tables = _tables(body) or [_name(word) for word in body.split()[1:2]
                                   if re.match(r"^[\w\".]+$", word)]
        return f"{first} {tables[0]}" if tables else first
    aggregate = _AGGREGATE.search(body) if kind == "select" else None
    op = aggregate.group(1).upper() if aggregate else ("SELECT" if first in READ_VERBS else first)
    # A call that reaches outside the tables (pg_sleep, dblink, ...) is what the statement
    # does, so it is named rather than hidden behind the table.
    reaching = _SIDE_EFFECT.search(body)
    if reaching:
        op = f"{op} {reaching.group(1).lower()}"
    tables = _tables(body)
    if not tables:
        return op
    on = tables[0] if len(tables) == 1 else (
        f"{tables[0]} and {tables[1]}" if len(tables) == 2 else
        f"{tables[0]} and {len(tables) - 1} more")
    name = f"{op} on {on}"
    where = _WHERE_COLUMN.search(body)
    if where:
        column = _name(where.group(1))
        if column and column.lower() not in _NOT_NAMES and not column.startswith("$") \
                and not column[0].isdigit():
            name += f" by {column}"
    return name


def _unsafe_function(issues: list[str]) -> str | None:
    for issue in issues:
        match = re.match(r"(\w+)\(\) reaches outside", issue)
        if match:
            return match.group(1).lower()
        if issue.startswith("SLEEP function"):
            return "sleep"
    return None


@lru_cache(maxsize=TEXT_CACHE_ENTRIES)
def _classify(text: str) -> tuple[bool, str, str | None]:
    if not (text or "").strip():
        return False, "no_text", ("RDST doesn't have this query's text yet, so there's "
                                  "nothing to analyze.")
    kind = statement_kind(text)
    first = verb(text)
    if kind == "write":
        from shared.monitor.write_lookup import lookup_for

        if lookup_for(text) is not None:
            return True, "ok", None
        return False, "write", f"{first} changes data, so RDST doesn't run it."
    if kind == "ddl":
        what = "permissions" if first in PERMISSION_VERBS else "the schema"
        return False, "ddl", f"{first} changes {what}, so RDST doesn't run it."
    if kind == "maintenance":
        return False, "maintenance", (f"{first} is a maintenance command, so there's no query "
                                      "plan to analyze.")
    if kind != "select":
        label = first or "This statement"
        return False, "utility", f"{label} isn't a query, so there's no plan to analyze."
    safety = validate_query_safety(text)
    if not safety.get("safe"):
        issues = list(safety.get("issues") or [])
        function = _unsafe_function(issues)
        if function:
            return False, "unsafe_function", f"This query calls {function}, which RDST won't run."
        if any("INTO" in issue for issue in issues):
            return False, "write", "SELECT INTO writes a table, so RDST doesn't run it."
        dangerous = safety.get("dangerous_keywords") or []
        if dangerous:
            return False, "write", f"{dangerous[0]} changes data, so RDST doesn't run it."
        return False, "unsafe_function", "RDST only analyzes plain read-only queries."
    return True, "ok", None


def analyzability(text: str, *, mean_ms: float | None = None,
                  execute: bool = False) -> dict[str, Any]:
    """`analyzable`, `analyzable_code`, `not_analyzable_reason` and `analysis_note`."""
    ok, code, reason = _classify(text or "")
    note = None
    if ok and statement_kind(text or "") == "write":
        note = "RDST analyzes how it finds its rows, without running the write."
    if ok and execute and mean_ms is not None and mean_ms > EXECUTE_LIMIT_MS:
        note = (f"Runs longer than {EXECUTE_LIMIT_MS // 1000} s, so RDST reads its plan "
                "without running it.")
    return {"analyzable": ok, "analyzable_code": code, "not_analyzable_reason": reason,
            "analysis_note": note}


@lru_cache(maxsize=TEXT_CACHE_ENTRIES)
def is_workload(text: str) -> bool:
    """False for RDST's own statements and statements that only touch system catalogs."""
    if not (text or "").strip():
        return True
    from shared.query_registry.sql_normalizer import references_user_relations

    try:
        return references_user_relations(text, dialect="postgres") or \
            statement_kind(text) in ("write", "ddl", "maintenance")
    except Exception:
        return True


def alert_member(text: str) -> bool:
    """Only reads and writes of the application count toward alerts; DDL never does."""
    return statement_kind(text) in ("select", "write", "unknown") and is_workload(text)


@lru_cache(maxsize=TEXT_CACHE_ENTRIES)
def _name_details(text: str) -> tuple[tuple[str, ...], ...]:
    """SQL differences in display priority order; literals only appear for LIMIT."""
    empty = ((),) * 6
    for dialect in ("postgres", "mysql"):
        try:
            tree = parse_one(text, read=dialect)
            if tree is None:
                return empty

            def columns(nodes):
                return tuple(dict.fromkeys(column.name for node in nodes
                                           for column in node.find_all(exp.Column, bfs=False)))

            filters = columns(tree.find_all(exp.Where))
            joins = tuple(dict.fromkeys(table.name for join in tree.find_all(exp.Join)
                                        for table in join.this.find_all(exp.Table)))
            orders = tuple(dict.fromkeys(
                column.name + (" descending" if ordered.args.get("desc") else "")
                for order in tree.find_all(exp.Order) for ordered in order.expressions
                for column in ordered.find_all(exp.Column)))
            groups = tuple(dict.fromkeys(
                value for group in tree.find_all(exp.Group) for expression in group.expressions
                for value in ((expression.args["unit"].name.lower(),)
                              if isinstance(expression, (exp.TimestampTrunc, exp.DateTrunc))
                              and expression.args.get("unit") else columns([expression]))))
            limits = tuple(dict.fromkeys(
                limit.expression.this for limit in tree.find_all(exp.Limit)
                if isinstance(limit.expression, exp.Literal)
                and limit.expression.is_int and int(limit.expression.this) >= 0))
            selected = columns(tree.expressions) if isinstance(tree, exp.Select) else ()
            return filters, joins, orders, groups, limits, selected
        except (SqlglotError, RecursionError):
            continue
    return empty


def distinct_names(texts: dict[str, str], existing: dict[str, str] | None = None) -> dict[str, str]:
    """Allocate names in first-seen order, preserving unambiguous assignments."""
    bases = {key: display_name(text) if text else f"Query {key}" for key, text in texts.items()}
    names = dict(existing or {})
    # Duplicate saved labels are ambiguous; retain reservations for expired queries.
    duplicates = {name for name, count in Counter(names.values()).items() if count > 1}
    names = {key: name for key, name in names.items()
             if key not in texts or name not in duplicates}
    used = set(names.values())
    groups: dict[str, list[str]] = {}
    for key, name in bases.items():
        groups.setdefault(name, []).append(key)
    for name, keys in groups.items():
        if len(keys) == 1 and keys[0] not in names and name not in used:
            names[keys[0]] = name
            used.add(name)
    prefixes = ("filtered on ", "with ", "sorted by ", "per ", "top ", "returning ")
    for name, keys in groups.items():
        pending = [key for key in keys if key not in names]
        if not pending:
            continue
        details = {key: _name_details(texts[key]) for key in keys}
        def common_values(peers: list[str], category: int) -> set[str]:
            common = set(details[peers[0]][category])
            for peer in peers[1:]:
                if not common:
                    break
                common.intersection_update(details[peer][category])
            return common

        # Full-family traits are shared by every pending name.
        full_common = [common_values(keys, category) for category in range(len(prefixes))]
        for key in pending:
            peers = keys
            traits = []
            for category, prefix in enumerate(prefixes):
                common = (full_common[category] if peers is keys
                          else common_values(peers, category))
                differing = tuple(value for value in details[key][category] if value not in common)
                if not differing or (category == 5 and len(differing) > 3):
                    continue
                traits.append(prefix + " and ".join(differing))
                peers = [peer for peer in peers if details[peer][category] == details[key][category]]
                if len(peers) == 1:
                    break
            chosen = ", ".join([name, *traits])
            if chosen in used:
                index = 2
                while f"{chosen}, variant {index}" in used:
                    index += 1
                chosen = f"{chosen}, variant {index}"
            names[key] = chosen
            used.add(chosen)
    return names
