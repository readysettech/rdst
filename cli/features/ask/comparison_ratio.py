"""Correct a scalar occurrence count only for a proved single amount pair."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
import hashlib
import json
from time import perf_counter

import sqlglot
from sqlglot import exp
from .sql_validation import check_read_only
from .encoded_identifier_storage import _schema_table, _schema_column

VERSION = "proved-single-pair-ratio-v2-scalar-reference"
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


@dataclass(frozen=True)
class ComparisonRatioPlan:
    pair_sql: str
    scalar_reference: bool = False

    @property
    def proof_sql(self):
        return (
            sqlglot.parse_one(self.pair_sql, read="mysql").limit(2).sql(dialect="mysql")
        )

    def candidate_sql(self, numerator_operand):
        if numerator_operand not in {"left", "right"}:
            return None
        tree = sqlglot.parse_one(self.pair_sql, read="mysql")
        left, right = tree.expressions
        numerator, denominator = (
            (left, right) if numerator_operand == "left" else (right, left)
        )
        ratio = exp.Div(
            this=exp.Cast(this=numerator.copy(), to=exp.DataType.build("DOUBLE")),
            expression=exp.Nullif(
                this=denominator.copy(), expression=exp.Literal.number(0)
            ),
        )
        tree.set("expressions", [ratio])
        return tree.sql(dialect="mysql")


def _without_comparison(node, comparison):
    if node is comparison:
        return None
    if isinstance(node, exp.And):
        a = _without_comparison(node.this, comparison)
        b = _without_comparison(node.expression, comparison)
        if a is None:
            return b
        if b is None:
            return a
        return exp.And(this=a, expression=b)
    if isinstance(node, exp.Paren):
        value = _without_comparison(node.this, comparison)
        return exp.Paren(this=value) if value is not None else None
    return node.copy()


def _plan_scalar_reference(tree, schema_info):
    """Keep an uncorrelated scalar operand intact, including its row checks."""
    selects = list(tree.find_all(exp.Select))
    subqueries = list(tree.find_all(exp.Subquery))
    if len(selects) != 2 or len(subqueries) != 1:
        return None
    inner = subqueries[0].this
    if not isinstance(inner, exp.Select) or len(inner.expressions) != 1:
        return None
    reference = inner.expressions[0]
    if not isinstance(reference, exp.Column):
        return None
    comparison = subqueries[0].parent
    if (
        not isinstance(comparison, exp.GT)
        or comparison.expression is not subqueries[0]
        or not isinstance(comparison.this, exp.Column)
    ):
        return None
    where = tree.args.get("where")
    if where is None or comparison not in list(where.walk()):
        return None
    # The comparison must be one complete positive outer conjunct.
    parent = comparison.parent
    while parent is not where:
        if not isinstance(parent, (exp.And, exp.Paren)):
            return None
        parent = parent.parent
    projection = tree.expressions[0] if len(tree.expressions) == 1 else None
    count = projection.this if isinstance(projection, exp.Alias) else projection
    if not isinstance(count, exp.Count) or not isinstance(count.this, exp.Star):
        return None
    if any(
        isinstance(n, (exp.Or, exp.Not))
        or isinstance(n, exp.Func)
        and n is not count
        and not isinstance(n, exp.And)
        for n in tree.walk()
    ):
        return None
    for select, measure in [(tree, comparison.this), (inner, reference)]:
        if any(
            select.args.get(k)
            for k in (
                "group",
                "having",
                "distinct",
                "with_",
                "qualify",
                "locks",
                "into",
                "order",
                "limit",
                "offset",
            )
        ):
            return None
        tables = [
            t
            for t in select.find_all(exp.Table)
            if t.find_ancestor(exp.Select) is select
        ]
        if not 1 <= len(tables) <= 4 or any(t.db or t.catalog for t in tables):
            return None
        aliases = {t.alias_or_name.casefold(): t.name for t in tables}
        if len(aliases) != len(tables):
            return None
        # Resolve each scope separately. Unqualified or outer references abstain.
        for column in select.find_all(exp.Column):
            if column.find_ancestor(exp.Select) is not select:
                continue
            if column.db or column.catalog or column.table.casefold() not in aliases:
                return None
            table = _schema_table(schema_info, aliases[column.table.casefold()])
            item = _schema_column(table, column.name) if table is not None else None
            if item is None:
                return None
            if (
                column is measure
                and str(item.data_type).lower().split("(", 1)[0] not in NUMERIC
            ):
                return None
    remainder = _without_comparison(where.this, comparison)
    tree.set("where", exp.Where(this=remainder) if remainder is not None else None)
    tree.set("expressions", [comparison.this.copy(), subqueries[0].copy()])
    return ComparisonRatioPlan(tree.sql(dialect="mysql"), scalar_reference=True)


def plan_comparison_ratio(sql, dialect, schema_info):
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
    if len(list(tree.find_all(exp.Select))) == 2:
        return _plan_scalar_reference(tree, schema_info)
    if len(tree.expressions) != 1 or len(list(tree.find_all(exp.Select))) != 1:
        return None
    if any(
        tree.args.get(k)
        for k in (
            "group",
            "having",
            "distinct",
            "with_",
            "qualify",
            "locks",
            "into",
            "order",
            "limit",
            "offset",
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
    if where is None or any(isinstance(n, (exp.Or, exp.Not)) for n in where.walk()):
        return None
    comparisons = [
        n
        for n in where.walk()
        if isinstance(n, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ))
        and isinstance(n.this, exp.Column)
        and isinstance(n.expression, exp.Column)
    ]
    if len(comparisons) != 1 or not isinstance(comparisons[0], exp.GT):
        return None
    comparison = comparisons[0]
    tables = list(tree.find_all(exp.Table))
    if not 2 <= len(tables) <= 4 or any(t.db or t.catalog for t in tables):
        return None
    aliases = {t.alias_or_name.casefold(): t.name for t in tables}
    if len(aliases) != len(tables):
        return None
    left, right = comparison.this, comparison.expression
    if (
        not left.table
        or not right.table
        or left.table.casefold() == right.table.casefold()
    ):
        return None
    for column in (left, right):
        if column.db or column.catalog or column.table.casefold() not in aliases:
            return None
        table = _schema_table(schema_info, aliases[column.table.casefold()])
        if table is None:
            return None
        item = _schema_column(table, column.name)
        if str(getattr(item, "data_type", "")).lower().split("(", 1)[0] not in NUMERIC:
            return None

    remainder = _without_comparison(where.this, comparison)
    tree.set("where", exp.Where(this=remainder) if remainder is not None else None)
    tree.set("expressions", [left.copy(), right.copy()])
    return ComparisonRatioPlan(tree.sql(dialect="mysql"))


def proven_comparison_ratio(proof_rows, primary_rows, numerator_operand):
    if (
        numerator_operand not in {"left", "right"}
        or len(proof_rows) != 1
        or len(proof_rows[0]) != 2
    ):
        return None
    if len(primary_rows) != 1 or len(primary_rows[0]) != 1:
        return None
    count = primary_rows[0][0]
    if not isinstance(count, int) or isinstance(count, bool) or count not in {0, 1}:
        return None
    raw_left, raw_right = proof_rows[0]
    if any(
        isinstance(v, (str, bytes, bool)) or v is None for v in (raw_left, raw_right)
    ):
        return None
    try:
        left, right = Decimal(str(raw_left)), Decimal(str(raw_right))
        if not all(v.is_finite() and 0 <= v <= 2**53 for v in (left, right)):
            return None
        if count != int(left > right):
            return None
        numerator, denominator = (
            (left, right) if numerator_operand == "left" else (right, left)
        )
        if denominator <= 0:
            return None
        value = float(numerator) / float(denominator)
        return value if math.isfinite(value) else None
    except (ValueError, InvalidOperation, OverflowError, ZeroDivisionError):
        return None


def accepts_comparison_ratio(expected, rows):
    if expected is None or len(rows) != 1 or len(rows[0]) != 1:
        return False
    value = rows[0][0]
    if value is None or isinstance(value, (str, bytes, bool)):
        return False
    try:
        value = float(value)
        return math.isfinite(value) and math.isclose(
            value, expected, rel_tol=1e-12, abs_tol=1e-12
        )
    except (ValueError, TypeError, OverflowError):
        return False


SYSTEM = "Classify the mathematical output requested by effective_question and its relationship to the two numeric operands in generated_sql. Interpret any language and ordinary typos. Do not generate SQL, approve a correction or infer business definitions. The host separately proves there is one unambiguous pair of values. Return only the requested structured fields."


CATALOG = [
    {
        "intent": "scalar_multiplicative_comparison",
        "claim": "ratio_only is true only when the request asks for one multiplicative comparison, expressing how many times one stated amount is another amount. Ordinary how-many-times-more wording comparing two fixed amounts may denote that multiplicative factor. It is false for the number of occurrences or records satisfying a comparison, differences, percent changes, requests for both amounts alongside a ratio, entity lists, or unresolved meaning. compared_measure_matches is true only when both compared SQL operands are the metric and populations described by the request. numerator_operand identifies the operand for the subject amount being compared to the reference amount: left or right according to parsed_sql_facts; use unknown if orientation is uncertain. Do not use the SQL comparison direction to infer the requested numerator. A request for additional multiples above a reference or a percentage change is not a simple ratio. The host must prove exactly one non-null pair after removing this one comparison, a positive denominator and that the original COUNT agrees with this pair's comparison. It preserves every other filter and join. Cite an exact source excerpt supporting the mathematical request.",
    }
]


SCHEMA = {
    "type": "object",
    "properties": {
        "ratio_only": {"type": "boolean"},
        "compared_measure_matches": {"type": "boolean"},
        "numerator_operand": {"type": "string", "enum": ["left", "right", "unknown"]},
        "source_excerpt": {"type": "string"},
    },
    "required": [
        "ratio_only",
        "compared_measure_matches",
        "numerator_operand",
        "source_excerpt",
    ],
    "additionalProperties": False,
}


def route_comparison_ratio(q, sql, d, a, callback=None):
    tree = sqlglot.parse_one(sql, read=d)
    from sqlglot import exp

    composite = next(
        n
        for n in tree.find_all(exp.GT)
        if isinstance(n.this, exp.Column)
        and isinstance(n.expression, (exp.Column, exp.Subquery))
        and n.find_ancestor(exp.Select) is tree
    )
    scalar_reference = isinstance(composite.expression, exp.Subquery)
    catalog, schema = CATALOG, SCHEMA
    if scalar_reference:
        catalog = CATALOG + [
            {
                "intent": "independent_scalar_comparison_measures",
                "claim": "Independently classify question_measure from effective_question alone and operand_measure from the two SQL numeric operands alone. Use money for monetary cost, price, revenue or budget; quantity for nonmonetary measured amounts or counts; unknown for unresolved or mixed measures. Do not derive one field from the other or from your activation decision. A shared entity or category name does not establish a shared measure. compared_measure_matches still requires the same exact metric and units in both operands and the question, including when both kinds are quantity. Host code rejects different or unknown kinds. Preserve the scalar subquery's full scope; never infer missing business definitions.",
            }
        ]
        schema = json.loads(json.dumps(SCHEMA))
        for field in ("question_measure", "operand_measure"):
            schema["properties"][field] = {
                "type": "string",
                "enum": ["money", "quantity", "unknown"],
            }
            schema["required"].append(field)
    prompt = json.dumps(
        {
            "effective_question": q,
            "generated_sql": sql,
            "dialect": d,
            "trigger_catalog": catalog,
            "parsed_sql_facts": {
                "left_operand": composite.this.sql(dialect=d),
                "right_operand": composite.expression.sql(dialect=d),
                "comparison": composite.sql(dialect=d),
                "projection": "one scalar COUNT(*)",
            },
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    response = a.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="comparison_ratio_routing",
        temperature=0.0,
        max_tokens=700,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "comparison_ratio",
                    "strict": True,
                    "schema": schema,
                },
            }
        },
    )
    raw = response.get("response", "")
    if callback:
        callback(
            prompt=prompt,
            response=raw,
            tokens=response.get("tokens_used", 0),
            latency_ms=(perf_counter() - started) * 1000,
            model=response.get("model", "unknown"),
        )
    v = json.loads(raw)
    ex = v.get("source_excerpt")
    activate = (
        isinstance(ex, str)
        and bool(ex.strip())
        and ex in q
        and v.get("ratio_only") is True
        and v.get("compared_measure_matches") is True
        and v.get("numerator_operand") in {"left", "right"}
        and (
            not scalar_reference
            or v.get("question_measure") in {"money", "quantity"}
            and v.get("question_measure") == v.get("operand_measure")
        )
    )
    return {
        "activate": activate,
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }
