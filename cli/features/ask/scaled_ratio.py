"""Improve a scaled floating ratio only after proving exact integral operands."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from time import perf_counter
import sqlglot
from sqlglot import exp
from features.ask.sql_validation import check_read_only

VERSION = "proved-exact-scaled-ratio-v1"


@dataclass(frozen=True)
class ScaledRatioPlan:
    original_sql: str
    candidate_sql: str
    proof_sql: str


def plan_scaled_ratio(sql, dialect):
    if dialect != "mysql" or not check_read_only(sql)["is_read_only"]:
        return None
    try:
        statements = sqlglot.parse(sql, read="mysql")
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    tree = statements[0]
    if len(tree.expressions) != 1 or len(list(tree.find_all(exp.Select))) > 3:
        return None
    if any(
        tree.args.get(k)
        for k in (
            "group",
            "having",
            "order",
            "limit",
            "offset",
            "distinct",
            "with_",
            "qualify",
            "into",
            "locks",
        )
    ):
        return None
    projection = tree.expressions[0]
    value = projection.this if isinstance(projection, exp.Alias) else projection
    if not isinstance(value, exp.Mul):
        return None
    # SQL supplies the arithmetic tree; this is never an English intent rule.
    division, scale = value.this, value.expression
    if isinstance(division, exp.Literal):
        division, scale = scale, division
    if not isinstance(scale, exp.Literal) or scale.is_string:
        return None
    try:
        if Decimal(scale.this) != 100:
            return None
    except InvalidOperation:
        return None
    while isinstance(division, exp.Paren):
        division = division.this
    if not isinstance(division, exp.Div) or not isinstance(division.this, exp.Cast):
        return None
    cast = division.this
    if cast.args["to"].this != exp.DataType.Type.DOUBLE:
        return None
    # Require a scalar outer aggregate. Nested aggregates must belong to their
    # own scalar subquery; no free row columns or changing rank membership.
    outer_aggregates = [
        a for a in tree.find_all(exp.AggFunc) if a.find_ancestor(exp.Select) is tree
    ]
    if not outer_aggregates or any(
        not isinstance(a, (exp.Sum, exp.Count)) for a in outer_aggregates
    ):
        return None
    allowed = (
        exp.Sum,
        exp.Count,
        exp.Cast,
        exp.Case,
        exp.If,
        exp.Coalesce,
        exp.Nullif,
        exp.And,
        exp.Or,
        exp.Year,
        exp.TsOrDsToDate,
    )
    if any(isinstance(n, exp.Func) and not isinstance(n, allowed) for n in tree.walk()):
        return None
    for select in tree.find_all(exp.Select):
        if select is not tree and (
            len(select.expressions) != 1
            or select.args.get("group")
            or select.args.get("with_")
            or select.args.get("locks")
            or select.args.get("into")
        ):
            return None
    for col in value.find_all(exp.Column):
        if (
            col.find_ancestor(exp.Select) is tree
            and col.find_ancestor(exp.AggFunc) is None
        ):
            return None
    replacement = exp.Div(
        this=exp.Mul(this=cast.copy(), expression=scale.copy()),
        expression=division.expression.copy(),
    )
    proof = tree.copy()
    proof.set("expressions", [cast.this.copy(), division.expression.copy()])
    value.replace(replacement)
    return ScaledRatioPlan(sql, tree.sql(dialect="mysql"), proof.sql(dialect="mysql"))


from fractions import Fraction
import struct


def _exact_integer(value):
    if type(value) is int:
        return value
    if (
        isinstance(value, Decimal)
        and value.is_finite()
        and value == value.to_integral_value()
    ):
        return int(value)
    return None


def prove_scaled_ratio(before, operands):
    if (
        len(before) != 1
        or len(before[0]) != 1
        or type(before[0][0]) is not float
        or not math.isfinite(before[0][0])
    ):
        return None
    if len(operands) != 1 or len(operands[0]) != 2:
        return None
    n, d = map(_exact_integer, operands[0])
    if n is None or d is None or d == 0 or abs(n * 100) > 2**53 or abs(d) > 2**53:
        return None
    original = before[0][0]
    old_expected = float(n) / float(d) * 100
    if original != old_expected:
        return None
    exact = Fraction(n * 100, d)
    candidate = float(exact)
    # Independently verify the conversion against both adjacent binary64 values.
    distance = abs(exact - Fraction.from_float(candidate))
    distances = [
        abs(exact - Fraction.from_float(math.nextafter(candidate, direction)))
        for direction in (-math.inf, math.inf)
    ]
    if any(distance > x for x in distances):
        return None
    if (
        any(distance == x for x in distances)
        and int.from_bytes(struct.pack(">d", candidate), "big") & 1
    ):
        return None
    old_distance = abs(exact - Fraction.from_float(original))
    if distance >= old_distance:
        return None
    return {
        "numerator": n,
        "denominator": d,
        "scaled_numerator": n * 100,
        "original_expected": old_expected,
        "expected": candidate,
        "exact_fraction": str(exact),
        "original_error": str(old_distance),
        "candidate_error": str(distance),
        "nearest_binary64_verified": True,
    }


def accepts_scaled_ratio(proof, rows):
    return (
        bool(proof)
        and len(rows) == 1
        and len(rows[0]) == 1
        and type(rows[0][0]) is float
        and math.isfinite(rows[0][0])
        and rows[0][0] == proof["expected"]
    )


SYSTEM = "Independently classify the requested numeric presentation using only the effective question. Understand ordinary language, typos and other languages. Do not generate SQL, choose a formula, infer percentage units or change any population. Return one populated JSON data instance, never a schema."
CATALOG = """The host has an existing scalar floating ratio multiplied by100. It will preserve every existing operand, the existing scale, all rows and all SQL clauses. It can move the existing multiplication before division only after exact integer operand proofs establish that this gives a strictly closer floating representation of the same exact rational value. This step does not correct or validate the existing formula or units. Identify whether the question asks for a numeric scalar result, and whether it constrains its representation. Generic rates, growth, fractions, shares, ratios, differences and percentages can be numeric scalar requests without specifying arithmetic evaluation order. Do not infer a scale from those words. Set representation to constrained if the user requires rounding, fixed decimal places, a formatted string or percent sign, exact decimal/rational arithmetic, an exact fraction output, stored values unchanged, a specific intermediate precision, or a particular multiplication/division order. Unrounded or full-precision numeric output alone is free; exact arithmetic or a prescribed operation order is constrained even when unrounded. Missing, conflicting or ambiguous presentation requirements use uncertain. Names, lists, grouped outputs, summaries or unspecified reports are not a scalar numeric request. Copy an exact contiguous question excerpt supporting the presentation classification."""
SCHEMA = {
    "type": "object",
    "properties": {
        "output_kind": {
            "type": "string",
            "enum": ["scalar_numeric", "other", "uncertain"],
        },
        "representation": {
            "type": "string",
            "enum": ["free", "constrained", "uncertain"],
        },
        "source_excerpt": {"type": "string"},
    },
    "required": ["output_kind", "representation", "source_excerpt"],
    "additionalProperties": False,
}


def route_scaled_ratio(question, adapter, callback=None):
    prompt = json.dumps(
        {"effective_question": question, "trigger_catalog": CATALOG}, ensure_ascii=False
    )
    started = perf_counter()
    response = adapter.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="scaled_ratio_routing",
        temperature=0.0,
        max_tokens=900,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "scaled_ratio_presentation",
                    "strict": True,
                    "schema": SCHEMA,
                },
            }
        },
    )
    raw = response.get("response", "")
    if callback:
        callback(
            phase="scaled_ratio_routing",
            prompt=prompt,
            response=raw,
            tokens=response.get("tokens_used", 0),
            latency_ms=(perf_counter() - started) * 1000,
            model=response.get("model", "unknown"),
        )
    decision = json.loads(raw)
    excerpt = decision.get("source_excerpt")
    return {
        "activate": decision.get("output_kind") == "scalar_numeric"
        and decision.get("representation") == "free"
        and isinstance(excerpt, str)
        and bool(excerpt.strip())
        and excerpt in question,
        "classification": decision,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }
