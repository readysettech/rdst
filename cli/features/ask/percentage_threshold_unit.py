"""Independently resolve the unit attached to a question's threshold numeral."""

import hashlib, json
from time import perf_counter

VERSION = "independent-numeric-rate-unit-v1"
SYSTEM = "Interpret the unit of the supplied numeral in the complete question, in any language including typos. The quantity may have one descriptive name while the numeral is explicitly expressed in a different unit. An explicit unit on the numeral controls its scale. Do not inspect storage or generate SQL. Copy the unit expression exactly from the question. Return only the required JSON."
CATALOG = [
    {
        "contract": "Identify the rate_unit in which the threshold numeral is expressed, not merely a unit word in the quantity name. percent means one hundredth of a dimensionless proportion per numeric unit; basis_points means one ten-thousandth; per_mille means one thousandth; fraction means proportion units. Distinguish a change in percentage points from an absolute percentage threshold. Respect instructions to compare a raw stored value without conversion. percentage language can establish a percentage threshold when no conflicting numeric unit is supplied, but cannot override an explicit basis-point, per-mille, fractional, or raw unit on the numeral. proportion_multiplier is the factor multiplying the numeral to express a dimensionless proportion; use unknown for non-rate, raw, change or ambiguous requests. unit_expression copies the exact unit wording or symbol that establishes the numeral scale, preserving typos. interpretation is absolute_threshold only for a direct threshold on the quantity itself, change for a growth/decrease/difference, raw for stored-unit comparison, other or unknown otherwise."
    }
]


def enum(*v):
    return {"type": "string", "enum": list(v)}


PROPERTIES = {
    "rate_unit": enum(
        "percent",
        "basis_points",
        "per_mille",
        "fraction",
        "percentage_points",
        "raw",
        "other",
        "unknown",
    ),
    "proportion_multiplier": enum("0.01", "0.0001", "0.001", "1", "unknown"),
    "unit_expression": {"type": "string"},
    "interpretation": enum("absolute_threshold", "change", "raw", "other", "unknown"),
}
SCHEMA = {
    "type": "object",
    "properties": PROPERTIES,
    "required": list(PROPERTIES),
    "additionalProperties": False,
}


def accepts_unit(question, value):
    if not isinstance(value, dict) or set(value) != set(PROPERTIES):
        return False
    for name, spec in PROPERTIES.items():
        if (
            not isinstance(value[name], str)
            or "enum" in spec
            and value[name] not in spec["enum"]
        ):
            return False
    return (
        value["rate_unit"] == "percent"
        and value["proportion_multiplier"] == "0.01"
        and value["interpretation"] == "absolute_threshold"
        and bool(value["unit_expression"].strip())
        and value["unit_expression"] in question
    )


def route_threshold_unit(
    question, number_text, condition_excerpt, adapter, callback=None
):
    prompt = json.dumps(
        {
            "effective_question": question,
            "threshold_numeral": number_text,
            "condition_excerpt": condition_excerpt,
            "unit_contract": CATALOG,
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    r = adapter.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="percentage_threshold_unit",
        temperature=0.0,
        max_tokens=1000,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "percentage_threshold_unit",
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
        "activate": bool(accepts_unit(question, v)),
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": r.get("model"),
    }
