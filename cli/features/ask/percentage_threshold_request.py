"""Extract a question threshold before host decimal comparison."""

from decimal import Decimal, InvalidOperation
import hashlib, json, re
from time import perf_counter
import sqlglot
from sqlglot import exp

NUMBER_VERSION = "question-threshold-number-v1"
SYSTEM = "Parse the effective question in its own language, including typos. Extract the complete numeric threshold condition on the supplied column concept. The column is a binding candidate, not evidence of the requested unit or number. Do not infer stored units or diagnose SQL. Copy excerpts exactly. Return only the requested JSON; do not write SQL."
CATALOG = [
    {
        "contract": "Report column_binding yes only if the column denotes the quantity whose threshold is requested, no for a different concept, unknown for ambiguity. Interpret the complete question. requested_unit is percentage only for an explicit percentage threshold on that quantity, fraction for an explicit fractional threshold, percentage_points for changes in percentage points, raw for explicitly stored units, other for a different quantity, unknown for an unqualified rate. A column name containing percent does not establish requested units. Copy number_text as the complete numeral written in the question BEFORE any unit conversion, excluding a percent sign; do not turn a percent into a fraction or calculate a new number. Leave number_text empty for a written-out number, ambiguous numeral or no single threshold. condition_excerpt is a verbatim consecutive excerpt covering that full threshold condition. comparison describes the relation of the requested quantity to its threshold, independent of word order. threshold_count counts all requested threshold bounds on this column concept; unrelated identifiers, dates, age ranges and thresholds on other concepts are not counted. condition_kind is threshold only for one direct threshold on the quantity itself, change for growth/decrease/differences, range for two or more bounds, other or unknown otherwise. Distinguish quantities mentioned inside a parent description from the quantity that actually has a threshold."
    }
]


def enum(*values):
    return {"type": "string", "enum": list(values)}


PROPERTIES = {
    "column_binding": enum("yes", "no", "unknown"),
    "requested_unit": enum(
        "percentage", "fraction", "percentage_points", "raw", "other", "unknown"
    ),
    "number_text": {"type": "string"},
    "condition_excerpt": {"type": "string"},
    "comparison": enum("lt", "lte", "gt", "gte", "eq", "between", "other", "unknown"),
    "threshold_count": {"type": "integer"},
    "condition_kind": enum("threshold", "change", "range", "other", "unknown"),
}
SCHEMA = {
    "type": "object",
    "properties": PROPERTIES,
    "required": list(PROPERTIES),
    "additionalProperties": False,
}


def numeric_text_value(text):
    if (
        not isinstance(text, str)
        or not re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?", text)
        or len(text) > 32
    ):
        return None
    # A single separator followed by three digits can be a grouping mark.
    if re.fullmatch(r"[0-9]+[.,][0-9]{3}", text):
        return None
    try:
        v = Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None
    return v if v.is_finite() and 0 < v <= 100 else None


def grounded_number(text, excerpt, question):
    if not text or not excerpt:
        return False
    for outer in re.finditer(re.escape(excerpt), question):
        for match in re.finditer(re.escape(text), excerpt):
            start, end = outer.start() + match.start(), outer.start() + match.end()
            before = question[start - 1 : start] if start else ""
            after = question[end : end + 1]
            if (not before or before not in "0123456789.,+-eE") and (
                not after or after not in "0123456789.,eE"
            ):
                return True
    return False


def threshold_facts(sql, dialect):
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.ParseError:
        return None
    matches = []
    for n in tree.find_all(exp.LT, exp.LTE, exp.GT, exp.GTE):
        col, lit = n.this, n.expression
        op = {exp.LT: "lt", exp.LTE: "lte", exp.GT: "gt", exp.GTE: "gte"}[type(n)]
        if isinstance(lit, exp.Column) and isinstance(col, exp.Literal):
            col, lit = lit, col
            op = {"lt": "gt", "lte": "gte", "gt": "lt", "gte": "lte"}[op]
        if (
            not isinstance(col, exp.Column)
            or not isinstance(lit, exp.Literal)
            or lit.is_string
        ):
            return None
        try:
            number = Decimal(lit.this)
        except InvalidOperation:
            return None
        matches.append((col.sql(dialect=dialect), number, op))
    return matches[0] if len(matches) == 1 else None


def accepts(question, facts, v):
    if facts is None or not isinstance(v, dict) or set(v) != set(PROPERTIES):
        return False
    for name, spec in PROPERTIES.items():
        if spec["type"] == "string" and not isinstance(v[name], str):
            return False
        if spec["type"] == "integer" and type(v[name]) is not int:
            return False
        if "enum" in spec and v[name] not in spec["enum"]:
            return False
    excerpt = v["condition_excerpt"]
    text = v["number_text"]
    number = numeric_text_value(text)
    return (
        v["column_binding"] == "yes"
        and v["requested_unit"] == "percentage"
        and v["condition_kind"] == "threshold"
        and v["threshold_count"] == 1
        and v["comparison"] == facts[2]
        and bool(excerpt.strip())
        and excerpt in question
        and grounded_number(text, excerpt, question)
        and number is not None
        and number == facts[1]
    )


def route_threshold_number(question, sql, dialect, adapter, callback=None):
    facts = threshold_facts(sql, dialect)
    if facts is None:
        return {
            "activate": False,
            "version": NUMBER_VERSION,
            "reason": "unsupported-threshold-facts",
        }
    prompt = json.dumps(
        {
            "effective_question": question,
            "dialect": dialect,
            "trigger_catalog": CATALOG,
            "binding_candidate": {"column": facts[0]},
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    r = adapter.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="percentage_threshold_request",
        temperature=0.0,
        max_tokens=1200,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "percentage_threshold_request",
                    "strict": True,
                    "schema": SCHEMA,
                },
            }
        },
    )
    raw = r.get("response", "")
    if callback:
        callback(
            prompt=prompt,
            response=raw,
            tokens=r.get("tokens_used", 0),
            latency_ms=(perf_counter() - started) * 1000,
            model=r.get("model", "unknown"),
        )
    v = json.loads(raw)
    return {
        "activate": bool(accepts(question, facts, v)),
        "version": NUMBER_VERSION,
        "decision": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": r.get("model"),
    }


from .percentage_threshold_unit import route_threshold_unit

SPLIT_VERSION = "split-numeric-threshold-unit-v1"


def route_percentage_threshold(question, sql, dialect, adapter, callback=None):
    first = route_threshold_number(question, sql, dialect, adapter, callback)
    result = {"activate": False, "version": SPLIT_VERSION, "number": first}
    if not first["activate"]:
        result["reason"] = "numeric-threshold-contract-abstained"
        return result
    v = first["decision"]
    second = route_threshold_unit(
        question, v["number_text"], v["condition_excerpt"], adapter, callback
    )
    result.update(
        unit=second,
        activate=second["activate"],
        reason="complete-numeric-and-unit-contract"
        if second["activate"]
        else "independent-unit-contract-abstained",
    )
    return result
