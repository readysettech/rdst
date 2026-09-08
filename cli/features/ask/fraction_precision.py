"""Recompute a uniquely proven fraction without changing its units."""

from dataclasses import dataclass
import math
import hashlib, json
from time import perf_counter

import sqlglot
from sqlglot import exp

from features.ask.percentage_threshold import (
    PercentageThresholdPlan,
    plan_percentage_threshold,
    proven_fraction_witness,
)
from features.ask.sql_validation import check_read_only
from features.ask.encoded_identifier_storage import _schema_table, _schema_column

VERSION = "proven-fraction-projection-precision-v2-joined"


@dataclass(frozen=True)
class FractionPrecisionPlan:
    original_sql: str
    fraction_proof: PercentageThresholdPlan
    ordered: bool

    @property
    def proof_sql(self):
        return self.fraction_proof.proof_sql


def plan_fraction_precision(sql, dialect, schema_info):
    if dialect != "mysql" or not check_read_only(sql)["is_read_only"]:
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
            "group",
            "having",
            "distinct",
            "with_",
            "qualify",
            "locks",
            "into",
        )
    ):
        return None
    source = tree.args.get("from_")
    if source is None or not isinstance(source.this, exp.Table):
        return None
    table = source.this
    projection = tree.expressions[0]
    column = projection.this if isinstance(projection, exp.Alias) else projection
    if not isinstance(column, exp.Column) or column.is_star:
        return None
    if tree.args.get("joins"):
        table = _joined_projection_source(tree, column, schema_info)
        if table is None:
            return None
    if table.db or table.catalog or column.db or column.catalog:
        return None
    if column.table and column.table.casefold() != table.alias_or_name.casefold():
        return None
    # Preserve only simple row filters and ordering. Volatile or opaque
    # functions, window expressions and implicit aggregation abstain.
    if any(
        isinstance(n, exp.Func) and not isinstance(n, (exp.And, exp.Or))
        for n in tree.walk()
    ):
        return None
    probe_shape = (
        exp.select(exp.Count(this=exp.Star()))
        .from_(table.copy())
        .where(exp.LT(this=column.copy(), expression=exp.Literal.number(1)))
    )
    proof = plan_percentage_threshold(
        probe_shape.sql(dialect="mysql"), dialect, schema_info
    )
    if proof is None:
        return None
    return FractionPrecisionPlan(sql, proof, bool(tree.args.get("order")))


def _joined_projection_source(tree, column, schema_info):
    """Resolve one qualified source without changing any joined row semantics."""
    joins = tree.args.get("joins") or []
    if len(joins) != 1 or not column.table:
        return None
    join = joins[0]
    if (
        join.args.get("side")
        or join.args.get("method")
        or join.args.get("using")
        or join.args.get("kind") not in (None, "", "INNER")
        or not isinstance(join.this, exp.Table)
    ):
        return None
    sources = [tree.args["from_"].this, join.this]
    aliases = {t.alias_or_name.casefold(): t for t in sources}
    if len(aliases) != 2 or any(t.db or t.catalog for t in sources):
        return None
    tables = {a: _schema_table(schema_info, t.name) for a, t in aliases.items()}
    if any(t is None for t in tables.values()):
        return None
    on = join.args.get("on")
    if (
        not isinstance(on, exp.EQ)
        or not all(
            isinstance(c, exp.Column) and c.table for c in (on.this, on.expression)
        )
        or {on.this.table.casefold(), on.expression.table.casefold()} != set(aliases)
    ):
        return None
    # Every joined reference must resolve independently. Outer projection aliases
    # and ordinal ORDER BY can silently change ranking when a fraction is refined.
    for c in tree.find_all(exp.Column):
        t = tables.get(c.table.casefold())
        if (
            c.is_star
            or c.db
            or c.catalog
            or t is None
            or _schema_column(t, c.name) is None
        ):
            return None
    order = tree.args.get("order")
    if order and any(isinstance(o.this, exp.Literal) for o in order.expressions):
        return None
    return aliases.get(column.table.casefold())


def fraction_candidate(plan, proof_rows):
    witness = proven_fraction_witness(plan.fraction_proof, proof_rows)
    if witness is None:
        return None
    tree = sqlglot.parse_one(plan.original_sql, read="mysql")
    projection = tree.expressions[0]
    column = projection.this if isinstance(projection, exp.Alias) else projection
    qualifier = column.table or tree.args["from_"].this.alias_or_name
    numerator, denominator = (
        exp.column(name, table=qualifier, quoted=True).sql(dialect="mysql")
        for name in witness
    )
    stored = column.sql(dialect="mysql")
    replacement = sqlglot.parse_one(
        f"SELECT CASE WHEN {stored} IS NULL THEN NULL ELSE CAST({numerator} AS DOUBLE) / NULLIF({denominator}, 0) END",
        read="mysql",
    ).expressions[0]
    if isinstance(projection, exp.Alias):
        projection.set("this", replacement)
    else:
        tree.set("expressions", [replacement])
    return tree.sql(dialect="mysql")


def accepts_fraction_precision(plan, before, after):
    if not 1 <= len(before) <= 10_000 or len(before) != len(after):
        return False
    if any(len(row) != 1 for row in (*before, *after)):
        return False

    def values(rows):
        result = []
        for (value,) in rows:
            if value is None:
                result.append(None)
                continue
            if isinstance(value, (bool, str, bytes)):
                raise ValueError("Expected native numeric values")
            number = float(value)
            if not math.isfinite(number) or not 0 <= number <= 1:
                raise ValueError("Expected finite fractions")
            result.append(number)
        return result

    try:
        old, new = values(before), values(after)
    except (ValueError, TypeError, OverflowError):
        return False
    if not plan.ordered:
        old.sort(key=lambda v: (v is not None, v or 0))
        new.sort(key=lambda v: (v is not None, v or 0))
    return all(
        a is None
        and b is None
        or a is not None
        and b is not None
        and abs(a - b) <= 1e-12
        for a, b in zip(old, new)
    )


SYSTEM = "Classify the quantitative content and explicit precision or storage requirements in effective_question. Understand any language and ordinary typos. Do not decide whether SQL is correct or choose a formula. The host independently proves a unique stored fraction relationship. Return only the requested fields."


CATALOG = [
    {
        "intent": "unrounded_rate_precision",
        "claim": "Classify three properties independently. requested_rate is true only when the question asks for a quantitative rate, fraction, ratio or proportion represented by the single projected numeric column. Generic values, raw measurements, identifiers and undefined scores are insufficient. stored_values_requested is true only when the question explicitly requires the stored or reported values as saved, without recomputing them. rounding_or_rendering_requested is true only for requested decimal-place rounding, a formatted string, a percent-sign display, or another specific rendered output. Asking for an unrounded, precise or full-precision numeric rate requests no rounding or rendering and does not request preservation of a stored approximation. If the meaning is uncertain, requested_rate must be false. The host separately proves a unique numerator/denominator relationship over all non-null stored rows within1e-12 and preserves nulls, units and all SQL clauses; it cannot choose a different metric. Cite an exact supporting question excerpt.",
    }
]


SCHEMA = {
    "type": "object",
    "properties": {
        "requested_rate": {"type": "boolean"},
        "stored_values_requested": {"type": "boolean"},
        "rounding_or_rendering_requested": {"type": "boolean"},
        "source_excerpt": {"type": "string"},
    },
    "required": [
        "requested_rate",
        "stored_values_requested",
        "rounding_or_rendering_requested",
        "source_excerpt",
    ],
    "additionalProperties": False,
}


def route_fraction_precision(q, sql, d, a, callback=None):
    tree = sqlglot.parse_one(sql, read=d)
    from sqlglot import exp

    composite = tree.expressions[0]
    prompt = json.dumps(
        {
            "effective_question": q,
            "generated_sql": sql,
            "dialect": d,
            "trigger_catalog": CATALOG,
            "parsed_sql_facts": {
                "projected_expression": composite.sql(dialect=d),
                "shape": "single direct numeric projection; population, ordering, nulls and units must remain unchanged",
            },
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    response = a.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="fraction_precision_routing",
        temperature=0.0,
        max_tokens=700,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "fraction_precision",
                    "strict": True,
                    "schema": SCHEMA,
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
        and v.get("requested_rate") is True
        and v.get("stored_values_requested") is False
        and v.get("rounding_or_rendering_requested") is False
    )
    return {
        "activate": activate,
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }
