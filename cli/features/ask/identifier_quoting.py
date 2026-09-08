"""Prove reserved table words and quote only their original source tokens."""

from collections.abc import Mapping
from dataclasses import dataclass
import ast
import sqlglot
from sqlglot import exp
from features.ask.sql_validation import check_read_only

VERSION = "proved-mysql-table-identifier-quoting-v1"


@dataclass(frozen=True)
class TableToken:
    name: str
    start: int
    end: int


@dataclass(frozen=True)
class QuotePlan:
    original_sql: str
    tokens: tuple[TableToken, ...]
    proof_sql: str


def mysql_syntax_error(error):
    if not isinstance(error, str) or not 1 <= len(error) <= 20000:
        return False
    try:
        value = ast.literal_eval(error)
    except (ValueError, SyntaxError, RecursionError):
        return False
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and type(value[0]) is int
        and value[0] == 1064
        and isinstance(value[1], str)
        and bool(value[1])
    )


def plan_identifier_quoting(sql, dialect, schema, error):
    if (
        dialect != "mysql"
        or not mysql_syntax_error(error)
        or not isinstance(getattr(schema, "tables", None), Mapping)
        or not isinstance(sql, str)
        or not 1 <= len(sql) <= 20000
    ):
        return None
    if not check_read_only(sql)["is_read_only"]:
        return None
    try:
        statements = sqlglot.parse(sql, read="mysql")
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    tree = statements[0]
    if (
        tree.find(exp.With)
        or tree.find(exp.Into)
        or any(s.args.get("locks") for s in tree.find_all(exp.Select))
    ):
        return None
    tokens = []
    tables = list(tree.find_all(exp.Table))
    if not 1 <= len(tables) <= 16:
        return None
    for table in tables:
        if any(v for k, v in table.args.items() if k not in {"this", "alias"}):
            return None
        identifier = table.this
        if (
            not isinstance(identifier, exp.Identifier)
            or identifier.name not in schema.tables
        ):
            return None
        if identifier.args.get("quoted"):
            continue
        start, end = identifier.meta.get("start"), identifier.meta.get("end")
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start <= end < len(sql)
            or sql[start : end + 1] != identifier.name
        ):
            return None
        tokens.append(TableToken(identifier.name, start, end + 1))
    if not tokens:
        return None
    spans = sorted((t.start, t.end) for t in tokens)
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        return None
    words = sorted({t.name.upper() for t in tokens})
    literals = ", ".join(exp.Literal.string(w).sql(dialect="mysql") for w in words)
    proof = f"SELECT WORD, RESERVED FROM INFORMATION_SCHEMA.KEYWORDS WHERE WORD IN ({literals}) LIMIT {len(words) + 1}"
    return QuotePlan(sql, tuple(tokens), proof)


def candidate_from_keywords(plan, rows):
    if (
        not isinstance(plan, QuotePlan)
        or not isinstance(rows, (list, tuple))
        or not 1 <= len(rows) <= len({t.name.upper() for t in plan.tokens})
    ):
        return None
    words = {t.name.upper() for t in plan.tokens}
    seen = set()
    reserved = set()
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            return None
        word, flag = row
        if (
            not isinstance(word, str)
            or word not in words
            or word in seen
            or type(flag) is not int
            or flag not in (0, 1)
        ):
            return None
        seen.add(word)
        if flag:
            reserved.add(word)
    if not reserved:
        return None
    selected = [t for t in plan.tokens if t.name.upper() in reserved]
    candidate = plan.original_sql
    for t in sorted(selected, key=lambda t: t.start, reverse=True):
        if candidate[t.start : t.end] != t.name or "`" in t.name:
            return None
        candidate = candidate[: t.start] + "`" + t.name + "`" + candidate[t.end :]
    try:
        original = sqlglot.parse_one(plan.original_sql, read="mysql")
        parsed = sqlglot.parse_one(candidate, read="mysql")
    except sqlglot.errors.ParseError:
        return None
    spans = {(t.start, t.end) for t in selected}
    for table in original.find_all(exp.Table):
        identifier = table.this
        if (identifier.meta.get("start"), identifier.meta.get("end", -2) + 1) in spans:
            identifier.set("quoted", True)
    if original != parsed or not check_read_only(candidate)["is_read_only"]:
        return None
    return candidate
