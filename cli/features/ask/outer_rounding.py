"""Remove one unrequested outer rounding after a simultaneous value witness."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json, hashlib
from time import perf_counter
import sqlglot
from sqlglot import exp
from features.ask.sql_validation import check_read_only

VERSION = "explicit-outer-rounding-policy-v2"


@dataclass(frozen=True)
class RoundingPlan:
    candidate_sql: str
    proof_sql: str
    decimals: int


def plan_outer_rounding(sql, dialect):
    if dialect != "mysql" or not check_read_only(sql)["is_read_only"]:
        return None
    try:
        statements = sqlglot.parse(sql, read="mysql")
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    t = statements[0]
    if len(list(t.find_all(exp.Select))) != 1 or len(t.expressions) != 1:
        return None
    if any(
        t.args.get(k)
        for k in [
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
        ]
    ):
        return None
    projected = t.expressions[0]
    rounded = projected.this if isinstance(projected, exp.Alias) else projected
    if not isinstance(rounded, exp.Round):
        return None
    digits = rounded.args.get("decimals")
    if digits is not None and (
        not isinstance(digits, exp.Literal)
        or digits.is_string
        or not digits.this.isdigit()
        or not 0 <= int(digits.this) <= 12
    ):
        return None
    if not any(
        isinstance(n, (exp.Sum, exp.Avg, exp.Count)) for n in rounded.this.walk()
    ):
        return None
    for n in t.walk():
        if (
            isinstance(n, exp.Func)
            and n is not rounded
            and not isinstance(
                n,
                (
                    exp.Sum,
                    exp.Avg,
                    exp.Count,
                    exp.Cast,
                    exp.Case,
                    exp.If,
                    exp.And,
                    exp.Or,
                    exp.Not,
                    exp.Nullif,
                ),
            )
        ):
            return None
        if isinstance(n, exp.Window):
            return None
    tables = list(t.find_all(exp.Table))
    if not 1 <= len(tables) <= 6 or any(t.db or t.catalog for t in tables):
        return None
    proof = t.copy()
    proof.set("expressions", [rounded.copy(), rounded.this.copy()])
    decimals = int(digits.this) if digits is not None else 0
    rounded.replace(rounded.this.copy())
    return RoundingPlan(t.sql(dialect="mysql"), proof.sql(dialect="mysql"), decimals)


def number(value):
    if value is None or isinstance(value, (str, bytes, bool)):
        return None
    try:
        n = Decimal(str(value))
        return n if n.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def proved_unrounded_value(original, proof):
    if (
        len(original) != 1
        or len(original[0]) != 1
        or len(proof) != 1
        or len(proof[0]) != 2
    ):
        return None
    old, rounded, unrounded = map(number, [original[0][0], proof[0][0], proof[0][1]])
    if old is None or rounded != old or unrounded is None or old == unrounded:
        return None
    return unrounded


def accepts_unrounded(expected, candidate):
    return (
        expected is not None
        and len(candidate) == 1
        and len(candidate[0]) == 1
        and number(candidate[0][0]) == expected
    )


SYSTEM = "Classify only the requested output kind and explicit precision requirement in effective_question. Interpret any language and typos. Do not use generated SQL to invent a user requirement. Never generate SQL or change a metric, population, units or null behavior. Return the required JSON object."
CATALOG = [
    {
        "intent": "unrequested_outer_rounding",
        "claim": "The host can remove one outer ROUND while preserving its entire inner expression and population. First classify output_request from the question alone: numeric_value when the user requests a numeric result itself, entity when they request identities, names or objects, and unknown otherwise. A numeric criterion used to select or rank entities does not request that numeric value as an output. If both identities and numbers are requested, use unknown because this scalar-only correction cannot fulfill that output. Independently classify output_kind from the requested numeric result, not from a selection criterion: percentage, fraction, quantity, money, or unknown. Classify precision_requirement as fixed when the user requests rounding, decimal places, significant figures, cents, fixed-decimal rendering or an approximate result; full when the user explicitly requests full precision; unspecified when no precision requirement is present; unknown when unclear. An ordinary percentage or quantitative request does not itself require rounding. For monetary output, preserve conventional rounding unless full precision is explicit. The host will permit only percentage, fraction or quantity with full or unspecified precision, or money with full precision. Cite a verbatim excerpt identifying the requested numeric output. Do not correct wrong units or calculation logic.",
    }
]
SCHEMA = {
    "type": "object",
    "properties": {
        "output_request": {
            "type": "string",
            "enum": ["numeric_value", "entity", "unknown"],
        },
        "output_kind": {
            "type": "string",
            "enum": ["percentage", "fraction", "quantity", "money", "unknown"],
        },
        "precision_requirement": {
            "type": "string",
            "enum": ["fixed", "full", "unspecified", "unknown"],
        },
        "source_excerpt": {"type": "string"},
    },
    "required": [
        "output_request",
        "output_kind",
        "precision_requirement",
        "source_excerpt",
    ],
    "additionalProperties": False,
}


def route_outer_rounding(q, sql, d, a, callback=None):
    prompt = json.dumps(
        {
            "effective_question": q,
            "generated_sql": sql,
            "dialect": d,
            "trigger_catalog": CATALOG,
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    response = a.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="outer_rounding_routing",
        temperature=0.0,
        max_tokens=600,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "outer_rounding",
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
    kind = v.get("output_kind")
    precision = v.get("precision_requirement")
    active = (
        v.get("output_request") == "numeric_value"
        and isinstance(ex, str)
        and bool(ex.strip())
        and ex in q
        and (
            kind in {"percentage", "fraction", "quantity"}
            and precision in {"full", "unspecified"}
            or kind == "money"
            and precision == "full"
        )
    )
    return {
        "activate": active,
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }
