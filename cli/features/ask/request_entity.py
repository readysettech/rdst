"""Resolve an entity reference without seeing the proposed stored label."""

import hashlib, json
from time import perf_counter

VERSION = "independent-request-entity-expansion-v1"
SYSTEM = "Read the entire question and the supplied short reference. You are not given a database label or candidate answer. Resolve only what the user positively requests. Return the required JSON instance, never a schema or SQL."
CATALOG = [
    {
        "contract": """Identify the referent of short_reference in the complete question. polarity is positive only if that named entity is included in the requested population; not from, excluding and equivalents in any language are excluded. scope is single_named_entity only if that entity itself is the whole target for this reference. Its subsidiaries, affiliates, related companies, family, descendants, subgroup, teams, divisions and versions must not be silently merged with or substituted for that one entity. Comparing two independently named entities can contain a single_named_entity reference to each; this does not imply subsidiaries. Exact spelling/string/label equality requests are literal_spelling. Categories, status codes and prefix searches are category_or_prefix. A name merely mentioned in an example or in somebody else's request is mentioned_only. Read negation and every later qualifier before classifying.
expanded_name is the full proper name actually requested. If the full name or an explicit alias definition appears in the question, copy the full name exactly and choose explicit_in_question. Otherwise, give one conventional expansion only when the question resolves a clearly recognized named entity unambiguously. Familiarity or a plausible guess does not establish a unique referent: an ambiguous ordinary name without disambiguating context is unknown. Do not invent corporate suffixes. If no established longer name is known, leave expanded_name empty and choose unknown. A first word inside a full proper name is not automatically an abbreviated entity reference. reference_excerpt must quote verbatim the clause that establishes the reference and its scope, including relevant exclusions or associated-entity qualifiers."""
    }
]
P = {
    "polarity": {
        "type": "string",
        "enum": ["positive", "excluded", "mentioned_only", "unknown"],
    },
    "scope": {
        "type": "string",
        "enum": [
            "single_named_entity",
            "entity_with_related_members",
            "literal_spelling",
            "category_or_prefix",
            "unknown",
        ],
    },
    "name_source": {
        "type": "string",
        "enum": ["explicit_in_question", "conventional_unambiguous_name", "unknown"],
    },
    "expanded_name": {"type": "string"},
    "reference_excerpt": {"type": "string"},
}
SCHEMA = {
    "type": "object",
    "properties": P,
    "required": list(P),
    "additionalProperties": False,
}


def accepts(q, short, full, v):
    if not isinstance(v, dict) or set(v) != set(P):
        return False
    for k, s in P.items():
        if not isinstance(v[k], str) or "enum" in s and v[k] not in s["enum"]:
            return False
    ex = v["reference_excerpt"]
    name = v["expanded_name"]
    return (
        v["polarity"] == "positive"
        and v["scope"] == "single_named_entity"
        and v["name_source"] != "unknown"
        and bool(ex.strip())
        and ex in q
        and short.casefold() in ex.casefold()
        and bool(name.strip())
        and name.casefold() == full.casefold()
        and (v["name_source"] != "explicit_in_question" or name in q)
    )


def route(q, short, adapter, callback=None):
    prompt = json.dumps(
        dict(
            effective_question=q,
            short_reference=short,
            complete_request_contract=CATALOG,
        ),
        ensure_ascii=False,
    )
    started = perf_counter()
    r = adapter.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="request_entity_expansion",
        temperature=0.0,
        max_tokens=1000,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "request_entity_expansion",
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
    return dict(
        classification=json.loads(raw),
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        response_sha256=hashlib.sha256(raw.encode()).hexdigest(),
        model=r.get("model"),
    )
