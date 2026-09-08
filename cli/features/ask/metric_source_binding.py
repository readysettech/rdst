"""Host-owned annual measure bindings and bounded result proof."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import sqlglot
from sqlglot import exp
from features.ask.sql_validation import (
    check_read_only,
    validate_resolved_columns_against_schema,
)
from features.ask.encoded_identifier_storage import _schema_table, _schema_column

NUMERIC = {
    "tinyint",
    "smallint",
    "mediumint",
    "int",
    "integer",
    "bigint",
    "decimal",
    "numeric",
    "double",
    "float",
    "real",
}
INTEGER = {"tinyint", "smallint", "mediumint", "int", "integer", "bigint"}
TEXT = {"char", "varchar", "text"}
DATES = {"date", "datetime", "timestamp"}
MAX_PERIODS = 1000


def kind(column):
    return str(getattr(column, "data_type", "")).lower().split("(", 1)[0].strip()


def conjuncts(node):
    return (
        conjuncts(node.this) + conjuncts(node.expression)
        if isinstance(node, exp.And)
        else [node]
    )


def relationship_parts(rel):
    if isinstance(rel, dict):
        return rel.get("target"), rel.get("join"), rel.get("type")
    return (
        getattr(rel, "target_table", None),
        getattr(rel, "join_pattern", None),
        getattr(rel, "relationship_type", None),
    )


@dataclass(frozen=True)
class AnnualBindingPlan:
    original_sql: str
    fact_table: str
    fact_alias: str
    dimension_table: str
    dimension_alias: str
    dimension_key: str
    old_measure: str
    old_period: str
    options: tuple

    def facts(self):
        t = sqlglot.parse_one(self.original_sql, read="mysql")
        return {
            "current_binding": {
                "table": self.fact_table,
                "measure": self.old_measure,
                "period": self.old_period,
            },
            "dimension_table": self.dimension_table,
            "dimension_filters": [
                {
                    "column": term.this.sql(dialect="mysql"),
                    "value": term.expression.this,
                }
                for term in conjuncts(t.args["where"].this)
            ],
            "dimension_scope": t.args["where"].this.sql(dialect="mysql"),
            "binding_options": list(self.options),
        }

    def dimension_proof_sql(self):
        original = sqlglot.parse_one(self.original_sql, read="mysql")
        key = exp.column(self.dimension_key, table=self.dimension_alias, quoted=True)
        return (
            exp.select(
                exp.Count(this=exp.Star()),
                exp.Count(this=key.copy()),
                exp.Count(this=exp.Distinct(expressions=[key.copy()])),
            )
            .from_(
                exp.Table(
                    this=exp.to_identifier(self.dimension_table, quoted=True),
                    alias=exp.TableAlias(
                        this=exp.to_identifier(self.dimension_alias, quoted=True)
                    ),
                )
            )
            .where(original.args["where"].this.copy())
            .sql(dialect="mysql")
        )

    def candidate_sql(self, option_id):
        options = [x for x in self.options if x["option_id"] == option_id]
        if len(options) != 1:
            return None
        option = options[0]
        tree = sqlglot.parse_one(self.original_sql, read="mysql")
        for table in tree.find_all(exp.Table):
            if table.alias_or_name == self.fact_alias:
                if not table.args.get("alias"):
                    table.set(
                        "alias",
                        exp.TableAlias(
                            this=exp.to_identifier(self.fact_alias, quoted=True)
                        ),
                    )
                table.set("this", exp.to_identifier(option["table"], quoted=True))
        for year in list(tree.find_all(exp.Year)):
            year.replace(
                exp.Substring(
                    this=exp.column(
                        option["period"], table=self.fact_alias, quoted=True
                    ),
                    start=exp.Literal.number(1),
                    length=exp.Literal.number(4),
                )
            )
        next(tree.find_all(exp.Sum)).set(
            "this", exp.column(option["measure"], table=self.fact_alias, quoted=True)
        )
        on = tree.args["joins"][0].args["on"]
        for col in on.find_all(exp.Column):
            if col.table == self.fact_alias and col.name != option["fact_key"]:
                col.set("this", exp.to_identifier(option["fact_key"], quoted=True))
        return tree.sql(dialect="mysql")

    def period_proof_sql(self, option_id):
        candidate = self.candidate_sql(option_id)
        if candidate is None:
            return None
        option = next(x for x in self.options if x["option_id"] == option_id)
        tree = sqlglot.parse_one(candidate, read="mysql")
        period = exp.column(option["period"], table=self.fact_alias, quoted=True)
        measure = exp.column(option["measure"], table=self.fact_alias, quoted=True)
        tree.set(
            "expressions",
            [
                period.copy(),
                exp.Sum(this=measure.copy()),
                exp.Sum(this=exp.Abs(this=measure.copy())),
                exp.Count(this=measure.copy()),
            ],
        )
        tree.set("group", exp.Group(expressions=[period.copy()]))
        tree.set("order", None)
        tree.set("limit", exp.Limit(expression=exp.Literal.number(MAX_PERIODS + 1)))
        return tree.sql(dialect="mysql")


def _year_only_input(tree):
    """Plan a year-only answer while retaining a separately classified contract.

    The semantic binder must independently require year_only output before
    executing the plan. A second projection can only repeat the exact SUM
    already used to rank years, so no additional requested measure is guessed.
    """
    if len(tree.expressions) == 1:
        return tree
    if len(tree.expressions) != 2:
        return None
    first, second = tree.expressions
    year = first.this if isinstance(first, exp.Alias) else first
    measure = second.this if isinstance(second, exp.Alias) else second
    if not isinstance(year, exp.Year) or not isinstance(measure, exp.Sum):
        return None
    if not isinstance(measure.this, exp.Column) or measure.this.is_star:
        return None
    aliases = [e.alias.casefold() for e in tree.expressions if e.alias]
    if len(aliases) != len(set(aliases)):
        return None
    order = tree.args.get("order")
    if order is None or len(order.expressions) != 1:
        return None
    ranked = order.expressions[0].this
    if ranked != measure:
        if not (
            second.alias
            and isinstance(ranked, exp.Column)
            and not ranked.table
            and ranked.name.casefold() == second.alias.casefold()
        ):
            return None
        ranked.replace(measure.copy())
    tree.set("expressions", [first.copy()])
    return tree


def plan_annual_binding(sql, dialect, schema_info):
    if (
        dialect != "mysql"
        or schema_info is None
        or not check_read_only(sql)["is_read_only"]
    ):
        return None
    try:
        statements = sqlglot.parse(sql, read="mysql")
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    tree = _year_only_input(statements[0])
    if tree is None:
        return None
    sql = tree.sql(dialect="mysql")
    if len(list(tree.find_all(exp.Select))) != 1 or len(tree.expressions) != 1:
        return None
    if any(
        tree.args.get(k)
        for k in ["with_", "having", "distinct", "offset", "qualify", "locks", "into"]
    ):
        return None
    if any(
        isinstance(n, exp.Func)
        and not isinstance(n, (exp.Year, exp.Sum, exp.And, exp.TsOrDsToDate))
        for n in tree.walk()
    ):
        return None
    projected = tree.expressions[0]
    year = projected.this if isinstance(projected, exp.Alias) else projected
    period = year.this if isinstance(year, exp.Year) else None
    if isinstance(period, exp.TsOrDsToDate):
        period = period.this
    if not isinstance(period, exp.Column) or not period.table:
        return None
    group, order, limit = (
        tree.args.get("group"),
        tree.args.get("order"),
        tree.args.get("limit"),
    )
    if (
        not group
        or group.expressions != [year]
        or not order
        or len(order.expressions) != 1
        or not limit
    ):
        return None
    ranked = order.expressions[0]
    if (
        not isinstance(ranked.this, exp.Sum)
        or not isinstance(ranked.this.this, exp.Column)
        or not ranked.args.get("desc")
    ):
        return None
    if (
        not isinstance(limit.expression, exp.Literal)
        or limit.expression.is_string
        or limit.expression.this != "1"
    ):
        return None
    sums = list(tree.find_all(exp.Sum))
    years = list(tree.find_all(exp.Year))
    if len(sums) != 1 or len(years) != 2:
        return None
    metric = ranked.this.this
    if metric.table != period.table:
        return None
    tables = list(tree.find_all(exp.Table))
    joins = tree.args.get("joins", [])
    if len(tables) != 2 or len(joins) != 1 or any(t.db or t.catalog for t in tables):
        return None
    if any(joins[0].args.get(k) for k in ["side", "method", "using"]) or joins[
        0
    ].args.get("kind") not in (None, "", "INNER"):
        return None
    on = joins[0].args.get("on")
    if not isinstance(on, exp.EQ) or not all(
        isinstance(c, exp.Column) and c.table and not c.db and not c.catalog
        for c in (on.this, on.expression)
    ):
        return None
    by_alias = {t.alias_or_name: t for t in tables}
    if (
        len(by_alias) != 2
        or metric.table not in by_alias
        or set(c.table for c in (on.this, on.expression)) != set(by_alias)
    ):
        return None
    fact = by_alias[metric.table]
    dimension = next(t for t in tables if t is not fact)
    dim_key = next(
        c.name for c in (on.this, on.expression) if c.table == dimension.alias_or_name
    )
    fact_key = next(
        c.name for c in (on.this, on.expression) if c.table == fact.alias_or_name
    )
    fact_schema = _schema_table(schema_info, fact.name)
    dim_schema = _schema_table(schema_info, dimension.name)
    if fact_schema is None or dim_schema is None:
        return None
    if (
        kind(_schema_column(fact_schema, metric.name)) not in NUMERIC
        or kind(_schema_column(fact_schema, period.name)) not in DATES
    ):
        return None
    if (
        kind(_schema_column(fact_schema, fact_key)) not in INTEGER
        or kind(_schema_column(dim_schema, dim_key)) not in INTEGER
    ):
        return None
    where = tree.args.get("where")
    if not where:
        return None
    clauses = conjuncts(where.this)
    if not 1 <= len(clauses) <= 6:
        return None
    for term in clauses:
        if (
            not isinstance(term, exp.EQ)
            or not isinstance(term.this, exp.Column)
            or term.this.table != dimension.alias_or_name
            or not isinstance(term.expression, exp.Literal)
        ):
            return None
    # Complete resolution protects qualifiers and projection aliases before enumeration.
    if not validate_resolved_columns_against_schema(
        sql,
        {name: list(table.columns) for name, table in schema_info.tables.items()},
        "mysql",
    )["is_valid"]:
        return None
    options = []
    for name, table in sorted(schema_info.tables.items()):
        if name.casefold() in {fact.name.casefold(), dimension.name.casefold()}:
            continue
        for rel in getattr(table, "relationships", []):
            target, pattern, relation_type = relationship_parts(rel)
            if (
                relation_type != "many_to_one"
                or not isinstance(target, str)
                or target.casefold() != dimension.name.casefold()
                or not isinstance(pattern, str)
            ):
                continue
            try:
                eq = sqlglot.parse_one(pattern, read="mysql")
            except sqlglot.errors.ParseError:
                continue
            if not isinstance(eq, exp.EQ) or not all(
                isinstance(c, exp.Column) and not c.db and not c.catalog
                for c in (eq.this, eq.expression)
            ):
                continue
            left, right = eq.this, eq.expression
            if left.table.casefold() == target.casefold():
                left, right = right, left
            if (
                left.table.casefold() != name.casefold()
                or right.table.casefold() != target.casefold()
                or right.name.casefold() != dim_key.casefold()
            ):
                continue
            if kind(_schema_column(table, left.name)) not in INTEGER:
                continue
            measures = [
                n
                for n, c in sorted(table.columns.items())
                if kind(c) in NUMERIC and n.casefold() != left.name.casefold()
            ]
            periods = [n for n, c in sorted(table.columns.items()) if kind(c) in TEXT]
            for measure in measures:
                for period_name in periods:
                    option = {
                        "table": name,
                        "measure": measure,
                        "period": period_name,
                        "fact_key": left.name,
                        "join": eq.sql(dialect="mysql"),
                    }
                    if option not in options:
                        options.append(option)
    if not 1 <= len(options) <= 12:
        return None
    options = tuple(dict(option_id=f"b{i}", **v) for i, v in enumerate(options))
    return AnnualBindingPlan(
        sql,
        fact.name,
        fact.alias_or_name,
        dimension.name,
        dimension.alias_or_name,
        dim_key,
        metric.name,
        period.name,
        options,
    )


def number(value):
    if isinstance(value, (str, bytes, bool)) or value is None:
        return None
    try:
        v = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return v if v.is_finite() else None


def proves_dimension_key(rows):
    if len(rows) != 1 or len(rows[0]) != 3:
        return False
    a, b, c = map(number, rows[0])
    return a is not None and a == b == c and a == int(a) and 0 < a < 2**53


def proved_annual_winner(rows):
    if not 1 <= len(rows) <= MAX_PERIODS:
        return None
    years = {}
    seen = set()
    for row in rows:
        if len(row) != 4:
            return None
        period, total, absolute, n = row
        if (
            not isinstance(period, str)
            or len(period) != 6
            or not period.isascii()
            or not period.isdigit()
            or not 1000 <= int(period[:4]) <= 9999
            or not 1 <= int(period[4:]) <= 12
            or period in seen
        ):
            return None
        seen.add(period)
        total, absolute, n = map(number, [total, absolute, n])
        if (
            total is None
            or absolute is None
            or n is None
            or n != int(n)
            or not 0 < n <= 1000000
            or not 0 <= absolute < 2**53
        ):
            return None
        # Conservative guard against floating SUM order changes. Reject close winners.
        error = absolute * n * (Decimal(2) ** -50) + Decimal("0.000000001")
        if abs(total) > absolute + error:
            return None
        y = years.setdefault(
            period[:4], [Decimal(0), Decimal(0), Decimal(0), Decimal(0)]
        )
        y[0] += total
        y[1] += error
        y[2] += n
        y[3] += absolute
        if y[2] > 1000000 or y[3] >= 2**53:
            return None
    if len(years) < 2:
        return None
    for values in years.values():
        # The candidate sums all annual rows directly, so also bound that
        # accumulation independently of the per-period grouping order.
        values[1] += values[2] * values[3] * (Decimal(2) ** -50)
    winner = max(years, key=lambda y: years[y][0])
    if any(
        years[winner][0] - years[winner][1] <= v[0] + v[1]
        for y, v in years.items()
        if y != winner
    ):
        return None
    return winner


def accepts_annual_winner(expected, rows):
    return (
        expected is not None
        and len(rows) == 1
        and len(rows[0]) == 1
        and rows[0][0] == expected
    )
