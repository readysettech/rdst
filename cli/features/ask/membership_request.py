"""Parse a complete collection condition before a bounded membership proof."""

import hashlib
import json
from time import perf_counter

VERSION = "collection-request-v1-count-and-qualifiers"
SYSTEM = (
    "Parse the effective question in its own language, including typos. "
    "Extract the complete condition on collection members, separately from "
    "conditions on the parent object. Do not generate SQL. The supplied column "
    "and member are binding candidates, not evidence of what the user requested. "
    "Copy source excerpts verbatim, preserving spelling and punctuation. "
    "Return only the required JSON object."
)
CATALOG = [
    {
        "parse": "complete_membership_request",
        "contract": (
            "output is parent_count only for how many parent objects satisfy a "
            "condition, member_count for number of members, identities for a list "
            "of objects, other otherwise. collection_binding is yes only when the "
            "predicate column denotes the collection requested, no for a different "
            "concept and unknown for ambiguous correspondence. value_kind is "
            "collection for sets such as skills or tags; scalar for a description, "
            "title, or single-valued category. "
            "named_member must be copied from the question, never filled from the "
            "candidate. member_relation is different_from_named only when the "
            "qualifying members differ from that named member. Use absence_of_named "
            "for a prohibition on the named member, whole_value_comparison for "
            "comparison of the complete stored value, other or unknown otherwise. "
            "lower_bound and upper_bound describe how many qualifying members each "
            "parent must carry. Use -1 for an unknown lower bound or an unbounded "
            "upper bound. A plain existential request has lower_bound 1 and "
            "upper_bound -1; an exact count has equal bounds; all-members "
            "requirements cannot be represented by a fixed existential bound. "
            "named_presence states whether the named member is additionally "
            "required, forbidden, optional or unknown. "
            "member_qualifiers contains a separate verbatim excerpt for EVERY "
            "restriction on a qualifying alternative member beyond being different "
            "from named_member: its status, temporal properties, identity, "
            "relationships, or additional excluded members. Include restrictions "
            "in relative clauses and later sentences. Conditions only on the "
            "parent object do not belong in member_qualifiers. Negated restrictions "
            "and explicit statements of indifference must be interpreted by "
            "meaning; do not invent a member restriction from them. "
            "allows_missing is true when a parent with no known members may "
            "qualify. combination is single_requirement only when the member "
            "condition is this one counted set; use compound for disjunctions with "
            "other ways of qualifying, universal conditions, or further collection "
            "requirements that these fields do not express. "
            "source_excerpt is a verbatim consecutive excerpt covering the member "
            "condition. coverage is complete only if all member requirements in "
            "the whole question are accounted for, ambiguous otherwise."
        ),
    }
]


def enum(*values):
    return {"type": "string", "enum": list(values)}


PROPERTIES = {
    "output": enum("parent_count", "member_count", "identities", "other"),
    "collection_binding": enum("yes", "no", "unknown"),
    "value_kind": enum("collection", "scalar", "unknown"),
    "named_member": {"type": "string"},
    "member_relation": enum(
        "different_from_named",
        "absence_of_named",
        "whole_value_comparison",
        "other",
        "unknown",
    ),
    "lower_bound": {"type": "integer"},
    "upper_bound": {"type": "integer"},
    "named_presence": enum("optional", "required", "forbidden", "unknown"),
    "member_qualifiers": {"type": "array", "items": {"type": "string"}},
    "allows_missing": {"type": "boolean"},
    "combination": enum("single_requirement", "compound", "unknown"),
    "source_excerpt": {"type": "string"},
    "coverage": enum("complete", "ambiguous"),
}
SCHEMA = {
    "type": "object",
    "properties": PROPERTIES,
    "required": list(PROPERTIES),
    "additionalProperties": False,
}


def accepts_request(question, plan, value):
    if not isinstance(value, dict) or set(value) != set(PROPERTIES) or plan is None:
        return False
    excerpt = value["source_excerpt"]
    member = value["named_member"]
    return (
        value["output"] == "parent_count"
        and value["collection_binding"] == "yes"
        and value["value_kind"] == "collection"
        and value["member_relation"] == "different_from_named"
        and type(value["lower_bound"]) is int
        and value["lower_bound"] == 1
        and type(value["upper_bound"]) is int
        and value["upper_bound"] == -1
        and value["named_presence"] == "optional"
        and type(value["member_qualifiers"]) is list
        and not value["member_qualifiers"]
        and value["allows_missing"] is False
        and value["combination"] == "single_requirement"
        and value["coverage"] == "complete"
        and isinstance(excerpt, str)
        and bool(excerpt.strip())
        and excerpt in question
        and isinstance(member, str)
        and bool(member.strip())
        and member in question
        and member.lower() == plan.target.lower()
    )


def route_membership_request(question, plan, dialect, adapter, callback=None):
    prompt = json.dumps(
        {
            "effective_question": question,
            "dialect": dialect,
            "trigger_catalog": CATALOG,
            "structure_facts": {
                "predicate_column": plan.column,
                "predicate_member": plan.target,
            },
        },
        ensure_ascii=False,
    )
    started = perf_counter()
    response = adapter.generate_response(
        prompt=prompt,
        system_message=SYSTEM,
        purpose="collection_request_routing",
        temperature=0.0,
        max_tokens=1500,
        extra={
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "collection_request",
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
    value = json.loads(raw)
    return {
        "activate": bool(accepts_request(question, plan, value)),
        "classification": value,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "response_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "model": response.get("model"),
    }


from .list_membership import SCHEMA as MEMBERSHIP_SCHEMA

QUALIFIER_SCHEMA = SCHEMA

VERSION = "split-membership-contract-v1"


def typed_instance(value, schema):
    if not isinstance(value, dict) or set(value) != set(schema["properties"]):
        return False
    for name, spec in schema["properties"].items():
        field = value[name]
        kind = spec["type"]
        if kind == "string" and not isinstance(field, str):
            return False
        if kind == "integer" and type(field) is not int:
            return False
        if kind == "boolean" and type(field) is not bool:
            return False
        if kind == "array" and (
            type(field) is not list or any(not isinstance(x, str) for x in field)
        ):
            return False
        if "enum" in spec and field not in spec["enum"]:
            return False
    return True


def qualifier_instance(value):
    if typed_instance(value, QUALIFIER_SCHEMA):
        return value, "none"
    if not isinstance(value, dict) or set(value) not in (
        {"properties", "additionalProperties"},
        {"properties", "additionalProperties", "title"},
    ):
        return None, "invalid"
    if value["additionalProperties"] is not False or (
        "title" in value and value["title"] != "collection_request"
    ):
        return None, "invalid"
    if not typed_instance(value["properties"], QUALIFIER_SCHEMA):
        return None, "invalid"
    return value["properties"], "single-schema-envelope"


def qualifier_accepts(question, plan, value):
    if not typed_instance(value, QUALIFIER_SCHEMA) or plan is None:
        return False
    # Presence of the named member is owned by the independent membership
    # classifier. This parser owns qualifying-member bounds and restrictions.
    return (
        value["output"] == "parent_count"
        and value["collection_binding"] == "yes"
        and value["value_kind"] == "collection"
        and value["member_relation"] == "different_from_named"
        and value["lower_bound"] == 1
        and value["upper_bound"] == -1
        and value["member_qualifiers"] == []
        and value["allows_missing"] is False
        and value["combination"] == "single_requirement"
        and value["coverage"] == "complete"
        and bool(value["source_excerpt"].strip())
        and value["source_excerpt"] in question
        and bool(value["named_member"].strip())
        and value["named_member"] in question
        and value["named_member"].lower() == plan.target.lower()
    )


def membership_accepts(question, plan, value):
    if not typed_instance(value, MEMBERSHIP_SCHEMA) or plan is None:
        return False
    # The independent qualifying-member parser owns the verbatim condition
    # quote. This classifier owns existential and named-presence semantics.
    return (
        value["mixed_members_match"] == "yes"
        and value["quantifier"] == "any_other_member"
        and value["requires_named_member"] is False
        and value["requests_missing_values"] is False
        and value["output_request"] == "count_parent_records"
        and value["value_role"] == "collection_member"
        and value["generated_column_matches_question"] == "yes"
        and bool(value["named_member"].strip())
        and value["named_member"] in question
        and value["named_member"].lower() == plan.target.lower()
    )


def route_qualified_membership(
    question, plan, dialect, adapter, membership_route, callback=None
):
    first = route_membership_request(question, plan, dialect, adapter, callback)
    value, normalization = qualifier_instance(first["classification"])
    result = {
        "activate": False,
        "version": VERSION,
        "qualifier": first,
        "normalization": normalization,
    }
    if not qualifier_accepts(question, plan, value):
        result["reason"] = "qualifying-member-contract-abstained"
        return result
    second = membership_route()
    result["membership"] = second
    result["activate"] = bool(
        membership_accepts(question, plan, second["classification"])
    )
    result["reason"] = (
        "complete-split-contract"
        if result["activate"]
        else "membership-contract-abstained"
    )
    return result
