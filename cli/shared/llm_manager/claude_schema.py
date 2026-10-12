"""Restate JSON Schemas in the subset Claude structured outputs enforce.

Keyservice applies the same restatement to hosted Claude requests.
"""

from __future__ import annotations

import json
from typing import Any

CLAUDE_STRING_FORMATS = frozenset({
    "date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid",
})


def _type_branch(schema: dict[str, Any], member: Any) -> Any:
    """One anyOf branch of a type union, keeping only what applies to that type.

    None when the union's const or enum admits no value of that type.
    """
    allowed = [schema["const"]] if "const" in schema else schema.get("enum")
    if not isinstance(allowed, list):
        allowed = None
    if member == "null":
        return {"type": "null"} if allowed is None or None in allowed else None
    if allowed is not None and all(value is None for value in allowed):
        return None
    branch = {**schema, "type": member}
    if isinstance(branch.get("enum"), list):
        branch["enum"] = [value for value in branch["enum"] if value is not None]
    return claude_json_schema(branch)


def claude_json_schema(schema: Any) -> Any:
    """Restate a JSON Schema in the subset Claude structured outputs accept.

    Unsupported keywords move into the description, as Anthropic's SDK does, so the model
    still sees them; callers validate the reply against their full schema. Type unions
    become anyOf and every object closes with additionalProperties false. A map, whose
    values additionalProperties describes, and an allOf of several schemas, whose closed
    branches would reject each other's properties, have no equivalent and raise ValueError.
    """
    if not isinstance(schema, dict):
        return schema
    if schema.get("additionalProperties", False) is not False:
        raise ValueError("Claude structured outputs cannot express open objects")
    rest = dict(schema)
    out: dict[str, Any] = {}
    for definitions in ("$defs", "definitions"):
        if isinstance(rest.get(definitions), dict):
            out[definitions] = {
                name: claude_json_schema(sub) for name, sub in rest.pop(definitions).items()
            }
    if isinstance(rest.get("allOf"), list) and len(rest["allOf"]) > 1:
        # Closing each branch would forbid the properties the others require.
        raise ValueError("Claude structured outputs cannot express composed schemas")
    if "$ref" in rest:
        out["$ref"] = rest.pop("$ref")
        return out
    type_ = rest.pop("type", None)
    if isinstance(type_, list):
        description = rest.pop("description", None)
        branches = (_type_branch(rest, member) for member in type_)
        out["anyOf"] = [branch for branch in branches if branch is not None]
        if description is not None:
            out["description"] = description
        return out
    for keyword in ("anyOf", "oneOf", "allOf"):
        variants = rest.pop(keyword, None)
        if isinstance(variants, list):
            out["allOf" if keyword == "allOf" else "anyOf"] = [
                claude_json_schema(variant) for variant in variants
            ]
    if type_ is not None:
        out["type"] = type_
    for keyword in ("enum", "const", "description", "title"):
        if keyword in rest:
            out[keyword] = rest.pop(keyword)
    if type_ == "object":
        properties = rest.pop("properties", {})
        out["properties"] = {
            key: claude_json_schema(sub) for key, sub in properties.items()
        } if isinstance(properties, dict) else properties
        rest.pop("additionalProperties", None)
        out["additionalProperties"] = False
        if "required" in rest:
            out["required"] = rest.pop("required")
    elif type_ == "string" and rest.get("format") in CLAUDE_STRING_FORMATS:
        out["format"] = rest.pop("format")
    elif type_ == "array":
        if "items" in rest:
            out["items"] = claude_json_schema(rest.pop("items"))
        if rest.get("minItems") in (0, 1):
            out["minItems"] = rest.pop("minItems")
    if rest:
        unsupported = ", ".join(
            f"{key}: {json.dumps(value, separators=(',', ':'), ensure_ascii=False)}"
            for key, value in rest.items()
        )
        description = out.get("description")
        out["description"] = (f"{description}\n\n" if description else "") + f"{{{unsupported}}}"
    return out
