"""Prove one missing label in two otherwise identical scalar counts."""

from dataclasses import dataclass
from decimal import Decimal
import sqlglot
from sqlglot import exp
from features.ask.sql_validation import check_read_only

VERSION = "proved-count-comparison-name-v1"


@dataclass(frozen=True)
class CountNamePlan:
    sql: str
    operation: str
    labels: tuple[str, str]
    spans: tuple[tuple[int, int], tuple[int, int]]
    table: str
    column: str
    domain_sql: str
    counts: tuple[str, str]


@dataclass(frozen=True)
class NameWitness:
    index: int
    short: str
    full: str


def plan_count_name(sql, dialect, schema):
    if (
        dialect != "mysql"
        or not isinstance(sql, str)
        or not 1 <= len(sql) <= 20000
        or not getattr(schema, "tables", None)
        or not check_read_only(sql)["is_read_only"]
    ):
        return None
    try:
        trees = sqlglot.parse(sql, read="mysql")
    except sqlglot.errors.ParseError:
        return None
    if len(trees) != 1 or not isinstance(trees[0], exp.Select):
        return None
    tree = trees[0]
    if (
        any(v for k, v in tree.args.items() if k != "expressions")
        or len(tree.expressions) != 1
    ):
        return None
    calc = tree.expressions[0]
    calc = calc.this if isinstance(calc, exp.Alias) else calc
    if not isinstance(calc, (exp.Sub, exp.Add)):
        return None
    selects = []
    labels = []
    spans = []
    bindings = []
    normalized = []
    for sub in (calc.this, calc.expression):
        if (
            not isinstance(sub, exp.Subquery)
            or any(v for k, v in sub.args.items() if k != "this")
            or not isinstance(sub.this, exp.Select)
        ):
            return None
        s = sub.this
        if (
            any(
                v
                for k, v in s.args.items()
                if k not in {"expressions", "from_", "joins", "where"}
            )
            or len(s.expressions) != 1
        ):
            return None
        count = s.expressions[0]
        if (
            not isinstance(count, exp.Count)
            or not isinstance(count.this, (exp.Star, exp.Column))
            or any(v for k, v in count.args.items() if k not in {"this", "big_int"})
        ):
            return None
        source = s.args.get("from_")
        where = s.args.get("where")
        if (
            not source
            or not isinstance(source.this, exp.Table)
            or not where
            or not isinstance(where.this, exp.EQ)
        ):
            return None
        joins = s.args.get("joins", [])
        if len(joins) > 3:
            return None
        for j in joins:
            if (
                not isinstance(j.this, exp.Table)
                or j.args.get("side")
                or j.args.get("method")
                or j.args.get("using")
                or j.args.get("kind") not in (None, "INNER")
            ):
                return None
            if any(
                not isinstance(n, exp.EQ)
                or not isinstance(n.this, exp.Column)
                or not isinstance(n.expression, exp.Column)
                for n in (
                    j.args.get("on").flatten()
                    if isinstance(j.args.get("on"), exp.And)
                    else [j.args.get("on")]
                )
            ):
                return None
        tables = [source.this] + [j.this for j in joins]
        aliases = {}
        for t in tables:
            if (
                any(v for k, v in t.args.items() if k not in {"this", "alias"})
                or not isinstance(t.this, exp.Identifier)
                or t.name not in schema.tables
                or t.alias_or_name in aliases
            ):
                return None
            aliases[t.alias_or_name] = t.name
        eq = where.this
        col, lit = (
            (eq.this, eq.expression)
            if isinstance(eq.this, exp.Column)
            else (eq.expression, eq.this)
        )
        if (
            not isinstance(col, exp.Column)
            or col.args.get("db")
            or col.args.get("catalog")
            or not isinstance(lit, exp.Literal)
            or not lit.is_string
        ):
            return None
        names = (
            [aliases[col.table]]
            if col.table in aliases
            else []
            if col.table
            else [
                name
                for name in aliases.values()
                if col.name in schema.tables[name].columns
            ]
        )
        if len(names) != 1:
            return None
        info = schema.tables[names[0]].columns.get(col.name)
        if info is None or not str(info.data_type).lower().startswith(
            ("varchar", "char", "text", "tinytext", "mediumtext", "longtext")
        ):
            return None
        value = lit.this
        if (
            not 2 <= len(value) <= 80
            or value.strip() != value
            or any(c in value for c in "'\\\x00\n\r")
        ):
            return None
        start, end = lit.meta.get("start"), lit.meta.get("end")
        if (
            type(start) is not int
            or type(end) is not int
            or sql[start : end + 1] != "'" + value + "'"
        ):
            return None
        labels.append(value)
        spans.append((start, end + 1))
        bindings.append((names[0], col.name))
        selects.append(s.sql(dialect="mysql", identify=True))
        copy = s.copy()
        copy.args["where"].set("this", exp.true())
        normalized.append(copy)
    if (
        bindings[0] != bindings[1]
        or labels[0].casefold() == labels[1].casefold()
        or normalized[0] != normalized[1]
    ):
        return None
    table, column = bindings[0]
    name = exp.to_identifier(column, quoted=True).sql(dialect="mysql")
    source = exp.to_identifier(table, quoted=True).sql(dialect="mysql")
    domain = f"SELECT CAST({name} AS BINARY), MAX({name} = '{labels[0]}'), MAX({name} = '{labels[1]}') FROM {source} GROUP BY CAST({name} AS BINARY) LIMIT 101"
    return CountNamePlan(
        sql,
        "sub" if isinstance(calc, exp.Sub) else "add",
        tuple(labels),
        tuple(spans),
        table,
        column,
        domain,
        tuple(selects),
    )


def name_from_domain(plan, rows):
    if (
        not isinstance(plan, CountNamePlan)
        or not isinstance(rows, (tuple, list))
        or not 1 <= len(rows) <= 100
    ):
        return None
    seen = set()
    names = []
    matches = [0, 0]
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            return None
        raw, *flags = row
        if raw is None:
            if flags != [None, None] or None in seen:
                return None
            seen.add(None)
            continue
        try:
            name = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        except UnicodeDecodeError:
            return None
        if (
            not isinstance(name, str)
            or len(name) > 1024
            or name in seen
            or any(type(f) is not int or f not in (0, 1) for f in flags)
        ):
            return None
        seen.add(name)
        names.append(name)
        for i, f in enumerate(flags):
            matches[i] += f
    if sorted(matches) != [0, 1]:
        return None
    i = matches.index(0)
    short = plan.labels[i]
    candidates = [n for n in names if n.casefold().startswith(short.casefold() + " ")]
    if (
        len(candidates) != 1
        or not candidates[0].strip()
        or any(c in candidates[0] for c in "\\\x00\n\r")
    ):
        return None
    return NameWitness(i, short, candidates[0])


def candidate_sql(plan, witness):
    if witness.index not in (0, 1) or witness.short != plan.labels[witness.index]:
        return None
    start, end = plan.spans[witness.index]
    literal = "'" + witness.full.replace("'", "''") + "'"
    candidate = plan.sql[:start] + literal + plan.sql[end:]
    try:
        old = sqlglot.parse_one(plan.sql, read="mysql")
        new = sqlglot.parse_one(candidate, read="mysql")
    except sqlglot.errors.ParseError:
        return None
    tokens = list(old.find_all(exp.Literal))
    target = next((t for t in tokens if t.meta.get("start") == start), None)
    if target is None:
        return None
    target.set("this", witness.full)
    return (
        candidate if old == new and check_read_only(candidate)["is_read_only"] else None
    )


def count_proof_sql(plan, witness):
    candidate = candidate_sql(plan, witness)
    if candidate is None:
        return None
    tree = sqlglot.parse_one(candidate, read="mysql")
    calc = tree.expressions[0]
    calc = calc.this if isinstance(calc, exp.Alias) else calc
    sub = (calc.this, calc.expression)[witness.index]
    return f"SELECT ({plan.counts[0]}), ({plan.counts[1]}), {sub.sql(dialect='mysql', identify=True)}"


def scalar(rows):
    if (
        not isinstance(rows, (list, tuple))
        or len(rows) != 1
        or not isinstance(rows[0], (list, tuple))
        or len(rows[0]) != 1
    ):
        return None
    n = rows[0][0]
    if (
        isinstance(n, bool)
        or not isinstance(n, (int, Decimal))
        or not Decimal(n).is_finite()
        or Decimal(n) != int(n)
    ):
        return None
    return int(n)


def proved_result(plan, witness, before, rows):
    if (
        scalar(before) is None
        or not isinstance(rows, (list, tuple))
        or len(rows) != 1
        or not isinstance(rows[0], (list, tuple))
        or len(rows[0]) != 3
    ):
        return None
    values = [scalar([[n]]) for n in rows[0]]
    if any(n is None or n < 0 for n in values):
        return None
    left, right, new = values
    if values[witness.index] != 0 or values[1 - witness.index] <= 0 or new <= 0:
        return None
    apply = lambda a, b: a - b if plan.operation == "sub" else a + b
    if apply(left, right) != scalar(before):
        return None
    values[witness.index] = new
    return apply(*values[:2])


def accepts_result(expected, rows):
    return expected is not None and scalar(rows) == expected
