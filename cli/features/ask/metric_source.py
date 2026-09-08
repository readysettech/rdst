"""Prototype constrained measure-source selection, not imported by Ask."""

import hashlib, json
from time import perf_counter

VERSION = "annual-metric-source-v6-independent-request"
SYSTEM = "Return exactly one JSON object conforming to the required schema, with no prose, markdown or explanation before or after it. Resolve the requested quantitative measure against the complete structural schema. Interpret any language and typos. Select only a supplied structurally valid binding or abstain. Do not generate SQL or invent metric definitions. Both requested_metric_excerpt and question_scope_quote must be exact substrings copied from effective_question. The host rejects excerpts copied from SQL, schema or structure facts. For question_scope_quote copy the shortest question phrase containing its dimension filter values, without adding labels or syntax."
CATALOG = [
    {
        "intent": "annual_total_metric_source",
        "claim": "A query ranks calendar years by SUM of one fact measure and keeps a dimension filter. The host can bind its measure, calendar axis and existing dimension relationship to one different fact table. Classify the question independently: output_request is year_only only if it asks solely for a year; aggregate_request is sum only for a total measure, other or unknown otherwise. Identify a verbatim requested_metric_excerpt. Judge current_metric_match as direct, proxy or unknown. A direct stored measure matching the requested concept is preferred over a different related quantity, such as measured usage versus billed price. Do not infer undocumented business equivalences, table completeness, conversion factors or units from names. Choose an option only if its measure directly matches the requested concept and its time column is that measure's observation/reporting period. A column named Cost, Revenue or Usage is not interchangeable merely because they are numeric. If the original already measures the requested concept, keep it. If source choice or metric meaning is ambiguous, abstain. Confirm that all original dimension filters express the requested scope and that this scope applies to the selected fact. Do not change filters, output attributes, grouping grain, aggregation or rank direction. Independently classify existing_filters_match_question as match, mismatch or unknown by comparing each original dimension condition with the effective question. A syntactically preserved filter may still conflict with the question. Copy a verbatim question_scope_quote supporting those original conditions; never copy it from SQL.",
    }
]
SCHEMA = {
    "type": "object",
    "properties": {
        "existing_filters_match_question": {
            "type": "string",
            "enum": ["match", "mismatch", "unknown"],
        },
        "question_scope_quote": {
            "type": "string",
            "description": "Exact verbatim substring of effective_question supporting dimension conditions. Never SQL, column labels or a paraphrase.",
        },
        "decision": {"type": "string", "enum": ["replace", "keep", "unknown"]},
        "option_id": {"type": "string"},
        "output_request": {"type": "string", "enum": ["year_only", "other", "unknown"]},
        "aggregate_request": {"type": "string", "enum": ["sum", "other", "unknown"]},
        "requested_metric_excerpt": {"type": "string"},
        "current_metric_match": {
            "type": "string",
            "enum": ["direct", "proxy", "unknown"],
        },
        "selected_metric_match": {
            "type": "string",
            "enum": ["direct", "proxy", "unknown"],
        },
        "selected_period_matches_measure": {"type": "boolean"},
        "dimension_scope_preserved": {"type": "boolean"},
        "missing_definition": {"type": "boolean"},
    },
    "required": [
        "existing_filters_match_question",
        "question_scope_quote",
        "decision",
        "option_id",
        "output_request",
        "aggregate_request",
        "requested_metric_excerpt",
        "current_metric_match",
        "selected_metric_match",
        "selected_period_matches_measure",
        "dimension_scope_preserved",
        "missing_definition",
    ],
    "additionalProperties": False,
}


def route_metric_source(question, sql, dialect, schema, facts, adapter, callback=None):
    prompt = json.dumps(
        {
            "effective_question": question,
            "generated_sql": sql,
            "dialect": dialect,
            "complete_schema": schema,
            "trigger_catalog": CATALOG,
            "structure_facts": facts,
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    r = adapter.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="metric_source_routing",
        temperature=0.0,
        max_tokens=1200,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "metric_source",
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
    ex = v.get("requested_metric_excerpt")
    scope = v.get("question_scope_quote")
    filters = facts.get("dimension_filters", [])
    options = {x["option_id"] for x in facts["binding_options"]}
    active = (
        v.get("existing_filters_match_question") == "match"
        and isinstance(scope, str)
        and bool(scope.strip())
        and scope in question
        and bool(filters)
        and all(str(f["value"]).casefold() in scope.casefold() for f in filters)
        and v.get("decision") == "replace"
        and v.get("option_id") in options
        and v.get("output_request") == "year_only"
        and v.get("aggregate_request") == "sum"
        and v.get("current_metric_match") == "proxy"
        and v.get("selected_metric_match") == "direct"
        and v.get("selected_period_matches_measure") is True
        and v.get("dimension_scope_preserved") is True
        and v.get("missing_definition") is False
        and isinstance(ex, str)
        and bool(ex.strip())
        and ex in question
    )
    return {
        "activate": active,
        "option_id": v.get("option_id"),
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": r.get("model"),
    }
