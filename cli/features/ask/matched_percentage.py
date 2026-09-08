"""Count existing right-side entities in a proved binary percentage."""

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_UP
import math
import sqlglot
from sqlglot import exp
from features.ask.sql_validation import check_read_only
from features.ask.calendar_day import structural_schema
import hashlib, json
from time import perf_counter

VERSION = "proved-matched-entity-percentage-v2-typed-request"


@dataclass(frozen=True)
class MatchedPercentagePlan:
    candidate_sql: str
    proof_sql: str
    facts: dict


def _base_plan(sql, dialect, schema):
    if dialect != "mysql" or not check_read_only(sql)["is_read_only"]:
        return None
    try:
        statements = sqlglot.parse(sql, read="mysql")
    except sqlglot.errors.ParseError:
        return None
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        return None
    tree = statements[0]
    if (
        len(list(tree.find_all(exp.Select))) != 1
        or len(tree.expressions) != 1
        or any(
            tree.args.get(k)
            for k in (
                "distinct",
                "group",
                "having",
                "qualify",
                "order",
                "limit",
                "offset",
                "with_",
                "into",
                "locks",
            )
        )
    ):
        return None
    avg = tree.expressions[0]
    avg = avg.this if isinstance(avg, exp.Alias) else avg
    if not isinstance(avg, exp.Avg) or not isinstance(avg.this, exp.Case):
        return None
    case = avg.this
    ifs = case.args.get("ifs", [])
    if case.this is not None or len(ifs) != 1:
        return None
    positive = ifs[0].args.get("true")
    negative = case.args.get("default")
    if any(
        not isinstance(v, exp.Literal) or v.is_string or "e" in v.this.casefold()
        for v in (positive, negative)
    ):
        return None
    try:
        if Decimal(positive.this) != 100 or Decimal(negative.this) != 0:
            return None
    except InvalidOperation:
        return None
    condition = ifs[0].this
    if (
        not isinstance(condition, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE))
        or not isinstance(condition.this, exp.Column)
        or not isinstance(condition.expression, exp.Literal)
    ):
        return None
    from_ = tree.args.get("from_")
    joins = tree.args.get("joins") or []
    if from_ is None or not isinstance(from_.this, exp.Table) or len(joins) != 1:
        return None
    join = joins[0]
    left = from_.this
    right = join.this
    if (
        not isinstance(right, exp.Table)
        or join.side != "LEFT"
        or join.kind not in ("", "OUTER")
        or join.args.get("using")
    ):
        return None
    if (
        any(t.db or t.catalog for t in (left, right))
        or left.alias_or_name.casefold() == right.alias_or_name.casefold()
    ):
        return None
    tables = {k.casefold(): v for k, v in schema.tables.items()}
    if any(t.name.casefold() not in tables for t in (left, right)):
        return None
    left_info = tables[left.name.casefold()]
    right_info = tables[right.name.casefold()]
    keys = [name for name, c in right_info.columns.items() if c.is_primary_key]
    if len(keys) != 1:
        return None
    key = exp.column(keys[0], table=right.alias_or_name, quoted=True)
    on = join.args.get("on")
    if (
        not isinstance(on, exp.EQ)
        or not isinstance(on.this, exp.Column)
        or not isinstance(on.expression, exp.Column)
    ):
        return None

    def same(a, b):
        return (a.table.casefold(), a.name.casefold()) == (
            b.table.casefold(),
            b.name.casefold(),
        )

    if same(on.this, key):
        foreign = on.expression
    elif same(on.expression, key):
        foreign = on.this
    else:
        return None
    if (
        foreign.table.casefold() != left.alias_or_name.casefold()
        or condition.this.table.casefold() != right.alias_or_name.casefold()
    ):
        return None
    for col in tree.find_all(exp.Column):
        info = (
            left_info
            if col.table.casefold() == left.alias_or_name.casefold()
            else right_info
            if col.table.casefold() == right.alias_or_name.casefold()
            else None
        )
        if (
            info is None
            or col.is_star
            or col.name.casefold() not in {n.casefold() for n in info.columns}
        ):
            return None
    where = tree.args.get("where")
    if where and any(
        c.table.casefold() != left.alias_or_name.casefold()
        for c in where.find_all(exp.Column)
    ):
        return None
    for node in tree.find_all(exp.Func):
        if (
            node is avg
            or node is case
            or node is ifs[0]
            or isinstance(node, (exp.And, exp.Or, exp.Not))
        ):
            continue
        return None
    indicator = case.copy()
    indicator.args["ifs"][0].set("true", exp.Literal.number(1))
    indicator.set("default", exp.Literal.number(0))
    proof = tree.copy()
    proof.set(
        "expressions",
        [
            exp.Count(this=exp.Star()),
            exp.Count(this=key.copy()),
            exp.Sum(this=indicator.copy()),
        ],
    )
    numerator = exp.Mul(
        this=exp.Cast(
            this=exp.Sum(this=indicator.copy()), to=exp.DataType.build("DOUBLE")
        ),
        expression=exp.Literal.number(100),
    )
    candidate = exp.Div(
        this=numerator,
        expression=exp.Nullif(
            this=exp.Count(this=key.copy()), expression=exp.Literal.number(0)
        ),
    )
    avg.replace(candidate)
    facts = {
        "left_source": left.name,
        "left_alias": left.alias_or_name,
        "right_source": right.name,
        "right_alias": right.alias_or_name,
        "right_primary_key": keys[0],
        "membership_predicate": condition.sql(dialect="mysql"),
        "existing_scale": 100,
        "existing_multiplicity": "One occurrence per joined row; no DISTINCT. Missing right entities count as zero in the original average.",
        "complete_structural_schema": structural_schema(schema),
    }
    return MatchedPercentagePlan(
        tree.sql(dialect="mysql"), proof.sql(dialect="mysql"), facts
    )


def prove_matched_percentage(before, rows):
    if (
        len(before) != 1
        or len(before[0]) != 1
        or not isinstance(before[0][0], Decimal)
        or not before[0][0].is_finite()
        or len(rows) != 1
        or len(rows[0]) != 3
    ):
        return None
    total, matched, hits = rows[0]
    if type(total) is not int or type(matched) is not int:
        return None
    try:
        hits = Decimal(hits)
        if (
            not hits.is_finite()
            or hits != hits.to_integral_value()
            or not 0 <= hits <= matched < total < 2**53
            or matched == 0
        ):
            return None
        hits = int(hits)
        with localcontext() as ctx:
            ctx.prec = 80
            exact = Decimal(hits) * 100 / total
            quantum = Decimal(1).scaleb(before[0][0].as_tuple().exponent)
            if exact.quantize(quantum, rounding=ROUND_HALF_UP) != before[0][0]:
                return None
        return {
            "total_rows": total,
            "matched_rows": matched,
            "matching_rows": hits,
            "unmatched_rows": total - matched,
            "expected_percentage": float(hits) * 100 / matched,
        }
    except (TypeError, ValueError, InvalidOperation, ZeroDivisionError):
        return None


def accepts_matched_percentage(proof, rows):
    if (
        proof is None
        or len(rows) != 1
        or len(rows[0]) != 1
        or not isinstance(rows[0][0], float)
        or not math.isfinite(rows[0][0])
    ):
        return False
    expected = proof["expected_percentage"]
    return abs(rows[0][0] - expected) <= 2 * math.ulp(expected)


def _sql_atom(node, aliases):
    operators = {
        exp.EQ: "eq",
        exp.NEQ: "neq",
        exp.GT: "gt",
        exp.GTE: "gte",
        exp.LT: "lt",
        exp.LTE: "lte",
    }
    values = []
    if type(node) in operators:
        column = node.this
        operator = operators[type(node)]
        values = [node.expression]
    elif isinstance(node, exp.Between):
        column = node.this
        operator = "between"
        values = [node.args["low"], node.args["high"]]
    elif isinstance(node, exp.Is) and isinstance(node.expression, exp.Null):
        column = node.this
        operator = "is_null"
    elif (
        isinstance(node, exp.Not)
        and isinstance(node.this, exp.Is)
        and isinstance(node.this.expression, exp.Null)
    ):
        column = node.this.this
        operator = "is_not_null"
    else:
        return None
    if (
        not isinstance(column, exp.Column)
        or column.table.casefold() not in aliases
        or any(not isinstance(v, exp.Literal) for v in values)
    ):
        return None
    return {
        "table": aliases[column.table.casefold()],
        "column": column.name,
        "operator": operator,
        "values": [
            {"kind": "string" if v.is_string else "number", "value": v.this}
            for v in values
        ],
    }


def _conjunction(node):
    if isinstance(node, exp.And):
        return _conjunction(node.this) + _conjunction(node.expression)
    return [node]


def plan_matched_percentage(sql, dialect, schema):
    plan = _base_plan(sql, dialect, schema)
    if plan is None:
        return None
    tree = sqlglot.parse_one(sql, read="mysql")
    left = tree.args["from_"].this
    right = tree.args["joins"][0].this
    if left.name.casefold() == right.name.casefold():
        return None
    aliases = {t.alias_or_name.casefold(): t.name for t in (left, right)}
    avg = tree.find(exp.Avg)
    membership = _sql_atom(avg.this.args["ifs"][0].this, aliases)
    where = tree.args.get("where")
    context = [_sql_atom(n, aliases) for n in _conjunction(where.this)] if where else []
    if membership is None or any(n is None for n in context):
        return None
    return replace(
        plan,
        facts=plan.facts | {"membership_atom": membership, "context_atoms": context},
    )


OPERATORS = [
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "is_null",
    "is_not_null",
    "unknown",
]
VALUE_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["number", "string"]},
        "value": {"type": "string"},
    },
    "required": ["kind", "value"],
    "additionalProperties": False,
}
ATOM_SCHEMA = {
    "type": "object",
    "properties": {
        "table": {"type": "string"},
        "column": {"type": "string"},
        "operator": {"type": "string", "enum": OPERATORS},
        "values": {"type": "array", "items": VALUE_SCHEMA},
    },
    "required": ["table", "column", "operator", "values"],
    "additionalProperties": False,
}
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["clear", "ambiguous", "unsupported"]},
        "units": {"type": "string", "enum": ["percentage", "fraction", "other"]},
        "denominator_table": {"type": "string"},
        "numerator_condition": ATOM_SCHEMA,
        "context_conditions": {"type": "array", "items": ATOM_SCHEMA},
        "denominator_conditions": {"type": "array", "items": ATOM_SCHEMA},
        "multiplicity": {
            "type": "string",
            "enum": ["joined_occurrences", "distinct_entities", "unknown"],
        },
        "include_unmatched_events": {"type": "boolean"},
        "include_unjoined_entities": {"type": "boolean"},
        "rounding_requested": {"type": "boolean"},
    },
    "required": [
        "status",
        "units",
        "denominator_table",
        "numerator_condition",
        "context_conditions",
        "denominator_conditions",
        "multiplicity",
        "include_unmatched_events",
        "include_unjoined_entities",
        "rounding_requested",
    ],
    "additionalProperties": False,
}
SYSTEM = "Translate the question into a small logical percentage request using only the complete structural schema. No SQL or candidate is supplied. Return one populated JSON instance, not a schema definition. Do not invent missing definitions, fields, values, or conditions."
CATALOG = """Describe a scalar percentage request. denominator_table is the physical table whose entity occurrences the question counts. numerator_condition is the requested membership condition, expressed as one physical column/operator/literal atom. context_conditions contains EVERY explicit surrounding event/population filter, as physical atoms, with no inferred conditions. denominator_conditions contains ANY additional qualification of denominator entities beyond membership in the surrounding event population, such as having known status or exceeding a points threshold. Do not omit a qualification merely because it cannot be expressed by the supplied schema. Use unsupported for missing roles, compound/unknown definitions, arithmetic outputs, or unrepresentable conditions. Use exact physical table/column names from the schema. Translate numbers and ordinary synonyms, but do not substitute zero for missing/unknown or swap active and inactive. NULL conditions use is_null/is_not_null with empty values; numeric literals use kind number. BETWEEN uses its two inclusive endpoints. Preserve inequality direction and inclusivity. Ordinary entity occurrences linked to specified events retain one occurrence per joined row, including repeated entities. Explicit unique/distinct entities require distinct_entities. include_unmatched_events is true when events without a matched denominator entity must remain in the denominator; include_unjoined_entities is true when entities outside the event population must be included. Set rounding_requested for explicit rounding/fixed precision. Distinguish a percentage of events satisfying an entity condition from a percentage of the associated entity occurrences. The first noun or leading For/Among context need not be the denominator. Never infer filters, population, units or multiplicity from an imagined SQL query.
Counting policy: preserve ordinary joined occurrences unless the question explicitly requires each entity only once, unique/distinct entities, or deduplication. Do not infer distinct_entities merely from an entity noun such as people. If the question explicitly counts an entity again for each event, denominator_table remains the entity table; this does not request counting events with no entity. For example, the percentage of zero-certification clinicians associated with visits has clinicians as denominator, even when each visit repeats a clinician. A percentage of visits attended by zero-certification clinicians instead has visits as denominator. Leading event filters do not determine the denominator.
Unquoted natural-language state words describe a semantic concept. Interpret them in the naming language of the schema rather than copying a foreign-language adjective blindly. Preserve an explicitly quoted or exact literal value character for character; never translate that literal. Do not invent an opaque code or assume an undocumented business mapping."""


def _canonical_atom(atom):
    if (
        not isinstance(atom, dict)
        or not all(
            isinstance(atom.get(k), str) and atom[k]
            for k in ("table", "column", "operator")
        )
        or not isinstance(atom.get("values"), list)
    ):
        return None
    op = atom["operator"]
    size = (
        0
        if op in ("is_null", "is_not_null")
        else 2
        if op == "between"
        else 1
        if op in OPERATORS[:-1]
        else -1
    )
    if len(atom["values"]) != size:
        return None
    values = []
    try:
        for v in atom["values"]:
            if (
                not isinstance(v, dict)
                or not isinstance(v.get("value"), str)
                or v.get("kind") not in ("string", "number")
            ):
                return None
            value = v["value"]
            if v["kind"] == "number":
                number = Decimal(value)
                if not number.is_finite():
                    return None
                value = number
            values.append((v["kind"], value))
        return (atom["table"].casefold(), atom["column"].casefold(), op, tuple(values))
    except (InvalidOperation, ValueError):
        return None


def accepts_request(plan, request):
    if (
        not isinstance(request, dict)
        or request.get("status") != "clear"
        or request.get("units") != "percentage"
        or request.get("multiplicity") != "joined_occurrences"
    ):
        return False
    if any(
        request.get(k) is not False
        for k in (
            "include_unmatched_events",
            "include_unjoined_entities",
            "rounding_requested",
        )
    ):
        return False
    table = request.get("denominator_table")
    if (
        not isinstance(table, str)
        or table.casefold() != plan.facts["right_source"].casefold()
    ):
        return False
    if request.get("denominator_conditions") != []:
        return False
    expected_membership = _canonical_atom(plan.facts["membership_atom"])
    if (
        expected_membership is None
        or _canonical_atom(request.get("numerator_condition")) != expected_membership
    ):
        return False
    context = request.get("context_conditions")
    if not isinstance(context, list) or len(context) > 8:
        return False
    actual = [_canonical_atom(a) for a in context]
    expected = [_canonical_atom(a) for a in plan.facts["context_atoms"]]
    if any(a is None for a in actual):
        return False
    from collections import Counter

    return Counter(actual) == Counter(expected)


def route_matched_percentage(question, plan, adapter, callback=None):
    prompt = json.dumps(
        {
            "effective_question": question,
            "complete_structural_schema": plan.facts["complete_structural_schema"],
            "trigger_catalog": CATALOG,
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    response = adapter.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="matched_percentage_request_routing",
        temperature=0.0,
        max_tokens=1600,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "matched_percentage_request",
                    "strict": True,
                    "schema": RESPONSE_SCHEMA,
                },
            }
        },
    )
    raw = response.get("response", "")
    if callback:
        callback(
            phase="matched_percentage_request_routing",
            prompt=prompt,
            response=raw,
            tokens=response.get("tokens_used", 0),
            latency_ms=(perf_counter() - started) * 1000,
            model=response.get("model", "unknown"),
        )
    request = json.loads(raw)
    return {
        "request": request,
        "request_receipt": {
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
            "model": response.get("model"),
        },
        "apply": accepts_request(plan, request),
    }
