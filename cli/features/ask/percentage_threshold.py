"""Bounded fractional-storage proof for an explicit percentage threshold."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import permutations
import math
import sqlglot
from sqlglot import exp
from features.ask.sql_validation import check_read_only
from features.ask.encoded_identifier_storage import _schema_table, _schema_column

VERSION = "proven-fraction-percentage-threshold-v3-split-unit"
MAX_SOURCE_COLUMNS = 12
_NUMERIC = {
    "tinyint",
    "smallint",
    "mediumint",
    "int",
    "integer",
    "bigint",
    "float",
    "double",
    "real",
    "decimal",
    "numeric",
}
_APPROXIMATE = {"float", "double", "real", "decimal", "numeric"}


@dataclass(frozen=True)
class PercentageThresholdPlan:
    candidate_sql: str
    proof_sql: str
    witness_columns: tuple[tuple[str, str], ...]
    narrows: bool
    source_columns: tuple[str, ...]


def plan_percentage_threshold(sql, dialect, schema_info):
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
    tree = statements[0]
    if len(tree.expressions) != 1 or len(list(tree.find_all(exp.Select))) != 1:
        return None
    if any(
        tree.args.get(k)
        for k in (
            "joins",
            "group",
            "having",
            "limit",
            "offset",
            "order",
            "distinct",
            "with_",
            "qualify",
        )
    ):
        return None
    projection = tree.expressions[0]
    count = projection.this if isinstance(projection, exp.Alias) else projection
    if not isinstance(count, exp.Count) or not isinstance(count.this, exp.Star):
        return None
    if any(
        isinstance(n, exp.Func) and n is not count and not isinstance(n, exp.And)
        for n in tree.walk()
    ):
        return None
    where = tree.args.get("where")
    source = tree.args.get("from_")
    if where is None or source is None or not isinstance(source.this, exp.Table):
        return None
    ref = source.this
    if ref.db or ref.catalog:
        return None
    table = _schema_table(schema_info, ref.name)
    if table is None:
        return None
    matches = []
    for node in where.walk():
        if isinstance(node, (exp.Or, exp.Not)):
            return None
        if not isinstance(node, (exp.LT, exp.LTE, exp.GT, exp.GTE)):
            continue
        if isinstance(node.this, exp.Column) and isinstance(
            node.expression, exp.Literal
        ):
            col, lit = node.this, node.expression
            narrows = isinstance(node, (exp.LT, exp.LTE))
        elif isinstance(node.expression, exp.Column) and isinstance(
            node.this, exp.Literal
        ):
            col, lit = node.expression, node.this
            narrows = isinstance(node, (exp.GT, exp.GTE))
        else:
            return None
        if (
            lit.is_string
            or col.db
            or col.catalog
            or col.table
            and col.table.lower() != ref.alias_or_name.lower()
        ):
            return None
        item = _schema_column(table, col.name)
        kind = str(getattr(item, "data_type", "")).lower().split("(", 1)[0]
        if kind not in _APPROXIMATE:
            # The model classifies one range threshold. Additional range
            # predicates would make that semantic reference ambiguous.
            return None
        try:
            threshold = Decimal(str(lit.this))
        except InvalidOperation:
            return None
        if not threshold.is_finite() or not 0 < threshold <= 100:
            return None
        matches.append((col, lit, threshold, narrows))
    if len(matches) != 1:
        return None
    col, lit, threshold, narrows = matches[0]
    if sum(c.name.lower() == col.name.lower() for c in tree.find_all(exp.Column)) != 1:
        return None
    numeric = [
        name
        for name, item in table.columns.items()
        if name.lower() != col.name.lower()
        and str(getattr(item, "data_type", "")).lower().split("(", 1)[0] in _NUMERIC
    ]
    if not 2 <= len(numeric) <= MAX_SOURCE_COLUMNS:
        return None
    pairs = tuple(permutations(numeric, 2))

    def column(name):
        return exp.column(name, table=ref.alias_or_name, quoted=True).sql(
            dialect="mysql"
        )

    stored = column(col.name)
    inner = tree.copy()
    inner.set(
        "expressions",
        [sqlglot.parse_one(f"SELECT {stored} AS _s", read="mysql").expressions[0]]
        + [
            sqlglot.parse_one(
                f"SELECT {column(name)} AS _c{i}", read="mysql"
            ).expressions[0]
            for i, name in enumerate(numeric)
        ],
    )
    inner.set(
        "where",
        sqlglot.parse_one(f"SELECT 1 WHERE {stored} IS NOT NULL", read="mysql").args[
            "where"
        ],
    )
    expressions = ["COUNT(*)", "COUNT(DISTINCT _s)", "MIN(_s)", "MAX(_s)"]
    indexes = {name: i for i, name in enumerate(numeric)}
    for i in range(len(numeric)):
        c = f"_c{i}"
        expressions.extend(
            [
                f"SUM(CASE WHEN {c} IS NULL OR {c}<0 OR {c}>9007199254740992 OR {c}<>FLOOR({c}) THEN 1 ELSE 0 END)",
                f"MIN({c})",
            ]
        )
    for numerator, denominator in pairs:
        n, d = f"_c{indexes[numerator]}", f"_c{indexes[denominator]}"
        expressions.extend(
            [f"MAX(ABS(_s-CAST({n} AS DOUBLE)/NULLIF({d},0)))", f"MAX({n}-{d})"]
        )
    proof = sqlglot.parse_one(
        "SELECT "
        + ",".join(expressions)
        + " FROM ("
        + inner.sql(dialect="mysql")
        + ") AS _fraction_proof",
        read="mysql",
    )
    lit.replace(exp.Literal.number(format(threshold / Decimal(100), "f")))
    return PercentageThresholdPlan(
        tree.sql(dialect="mysql"),
        proof.sql(dialect="mysql"),
        pairs,
        narrows,
        tuple(numeric),
    )


def proven_fraction_witness(plan, rows):
    if len(rows) != 1 or len(rows[0]) != 4 + 2 * len(plan.source_columns) + 2 * len(
        plan.witness_columns
    ):
        return None
    count, distinct, low, high, *errors = rows[0]
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or not 10 <= count <= 1_000_000
    ):
        return None
    if not isinstance(distinct, int) or isinstance(distinct, bool) or distinct < 4:
        return None
    try:
        low, high = float(low), float(high)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(low) or not math.isfinite(high) or not 0 <= low < high <= 1:
        return None
    source_errors = errors[: 2 * len(plan.source_columns)]
    pair_errors = errors[2 * len(plan.source_columns) :]
    facts = {
        name: source_errors[2 * i : 2 * i + 2]
        for i, name in enumerate(plan.source_columns)
    }
    proven = []
    for i, pair in enumerate(plan.witness_columns):
        numerator, denominator = pair
        n_invalid, n_min = facts[numerator]
        d_invalid, d_min = facts[denominator]
        error, delta = pair_errors[2 * i : 2 * i + 2]
        try:
            values = [
                float(x) for x in (n_invalid, n_min, d_invalid, d_min, error, delta)
            ]
        except (TypeError, ValueError, OverflowError):
            continue
        if not all(math.isfinite(x) for x in values):
            continue
        if (
            n_invalid == d_invalid == 0
            and n_min >= 0
            and d_min > 0
            and delta <= 0
            and 0 <= error <= 1e-12
        ):
            proven.append(pair)
    return proven[0] if len(proven) == 1 else None


def accepts_percentage_threshold(plan, before, after):
    if len(before) != 1 or len(after) != 1 or len(before[0]) != 1 or len(after[0]) != 1:
        return False
    old, new = before[0][0], after[0][0]
    if any(not isinstance(x, int) or isinstance(x, bool) or x < 0 for x in (old, new)):
        return False
    return new <= old if plan.narrows else new >= old


from .percentage_threshold_request import route_percentage_threshold
