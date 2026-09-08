"""Extract count units and complete scope before seeing any generated SQL."""

import hashlib, json

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["clear", "unknown"]},
        "count_unit": {
            "type": "string",
            "enum": [
                "undirected_relationships",
                "directed_records",
                "vertices",
                "graph_namespaces",
                "other",
                "unknown",
            ],
        },
        "result_shape": {
            "type": "string",
            "enum": ["scalar_count", "grouped_counts", "other", "unknown"],
        },
        "endpoint_number": {"type": ["string", "null"]},
        "count_phrase": {"type": "string"},
        "conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": [
                            "endpoint_number",
                            "all_graphs",
                            "graph_subset",
                            "edge_property",
                            "neighbor_property",
                            "time",
                            "other",
                        ],
                    },
                    "phrase": {"type": "string"},
                },
                "required": ["role", "phrase"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "status",
        "count_unit",
        "result_shape",
        "endpoint_number",
        "count_phrase",
        "conditions",
    ],
    "additionalProperties": False,
}
CATALOG = "Extract the count unit and EVERY condition from the question alone. Return the shortest complete verbatim count phrase and a verbatim phrase for each condition. Include adjectives, restrictions, negations, exceptions, date ranges, status words, neighbor restrictions, and graph restrictions even if no schema field is available for them. Do not drop a modifier just because it looks incidental. Undirected describes the count unit; it is not an extra property. A local endpoint number and an explicit all-graphs population have their dedicated roles. A named graph is a subset. Active, approved, valid, colored, weighted, typed, or other edge restrictions are edge_property. Conditions on the opposite endpoint are neighbor_property. A count of graph namespaces or vertices differs from a count of relationships. Counting both directed storage rows differs from counting each undirected relationship once. A per-graph answer is grouped_counts. If the count or endpoint number is absent, preserve that absence. No SQL or candidate is available and none should be assumed. Interpret the original language and copy phrases without translation."


def accepts_endpoint_request(question, plan, r):
    if not isinstance(r, dict) or set(r) != set(SCHEMA["required"]):
        return False
    if (
        r["status"] != "clear"
        or r["count_unit"] != "undirected_relationships"
        or r["result_shape"] != "scalar_count"
        or r["endpoint_number"] != plan.number
    ):
        return False
    conditions = r["conditions"]
    if not isinstance(conditions, list) or not conditions:
        return False
    for c in conditions:
        if (
            not isinstance(c, dict)
            or set(c) != {"role", "phrase"}
            or c["role"] not in ["endpoint_number", "all_graphs"]
        ):
            return False
    if sum(c["role"] == "endpoint_number" for c in conditions) != 1:
        return False
    phrases = [r["count_phrase"]] + [c["phrase"] for c in conditions]
    return all(
        isinstance(s, str) and bool(s.strip()) and s in question for s in phrases
    )


def route_endpoint_request(question, plan, adapter):
    prompt = json.dumps(
        {"effective_question": question, "trigger_catalog": CATALOG}, ensure_ascii=False
    )
    r = adapter.generate_response(
        prompt=prompt,
        system_message="Extract the complete requested count and condition inventory without SQL, schema, database results or candidate. Return only the populated JSON contract.",
        purpose="endpoint_request_routing",
        temperature=0.0,
        max_tokens=1600,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "endpoint_request",
                    "strict": True,
                    "schema": SCHEMA,
                },
            }
        },
    )
    raw = r.get("response", "")
    v = json.loads(raw)
    return {
        "activate": accepts_endpoint_request(question, plan, v),
        "classification": v,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": r.get("model"),
    }
