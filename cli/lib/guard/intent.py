"""
Intent-based guard derivation.

Uses LLM to analyze natural language policy intent and derive concrete,
enforceable guard rules. The LLM is only used at creation time - runtime
enforcement is fully deterministic.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .config import (
    GuardConfig,
    MaskingConfig,
    RestrictionsConfig,
    GuardsConfig,
    LimitsConfig,
)

logger = logging.getLogger(__name__)

# JSON schema for LLM output
DERIVATION_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {
            "type": "string",
            "description": "Brief description of what this guard does"
        },
        "allowed_tables": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "Tables the user can access (null = all tables allowed)"
        },
        "denied_columns": {
            "type": ["array", "null"],
            "items": {"type": "string"},
            "description": "Column patterns to block (e.g., '*password*', '*.ssn')"
        },
        "required_filters": {
            "type": ["object", "null"],
            "additionalProperties": {
                "type": "array",
                "items": {"type": "string"}
            },
            "description": "Tables requiring specific column filters: {table: [columns]}"
        },
        "masking": {
            "type": ["object", "null"],
            "additionalProperties": {"type": "string"},
            "description": "Column patterns to mask: {'*.email': 'email', '*.phone': 'partial:4'}"
        },
        "max_estimated_rows": {
            "type": ["integer", "null"],
            "description": "Maximum rows a query should return (prevents bulk exports)"
        },
        "max_rows": {
            "type": "integer",
            "description": "Hard limit on returned rows",
            "default": 1000
        },
        "require_where": {
            "type": "boolean",
            "description": "Require WHERE clause on all queries",
            "default": False
        },
        "require_limit": {
            "type": "boolean",
            "description": "Require LIMIT clause on all queries",
            "default": False
        }
    },
    "required": ["description"]
}

DERIVATION_PROMPT = '''You are a database security expert. Analyze the following guard policy intent and derive concrete, enforceable rules.

## Policy Intent
{intent}

## Database Schema (if available)
{schema_context}

## Instructions

Based on the intent, derive specific rules. Be conservative - when in doubt, be more restrictive.

For column patterns, use fnmatch-style wildcards:
- `*password*` matches any column containing "password"
- `*.email` matches columns ending in "email"
- `ssn` matches exact column name "ssn"

For masking types:
- `email` - masks email preserving structure: `u***@d***.com`
- `redact` - replaces with `[REDACTED]`
- `partial:N` - shows last N characters: `****1234`
- `hash` - shows hash prefix: `a1b2c3d4`

For required_filters, specify columns that MUST have value filters (not just IS NOT NULL):
- Example: {{"users": ["id", "email"]}} means queries on users must filter on id OR email

Think about:
1. What data should be completely blocked (denied_columns)?
2. What data should be masked but visible (masking)?
3. What tables should be inaccessible (allowed_tables)?
4. Should bulk exports be prevented (max_estimated_rows, required_filters)?
5. Are there specific tables that need row-level filtering (required_filters)?

## Output

Return a JSON object with derived rules:

```json
{{
  "description": "Brief description of guard purpose",
  "allowed_tables": ["table1", "table2"] or null,
  "denied_columns": ["*password*", "*secret*", "*.ssn"] or null,
  "required_filters": {{"users": ["id", "email"]}} or null,
  "masking": {{"*.email": "email", "*.phone": "partial:4"}} or null,
  "max_estimated_rows": 100 or null,
  "max_rows": 1000,
  "require_where": true or false,
  "require_limit": true or false
}}
```

Only include fields that are relevant to the intent. Return ONLY the JSON, no other text.'''


def derive_rules_from_intent(
    intent: str,
    name: str = "derived-guard",
    schema_context: str | None = None,
    llm_manager: Any | None = None,
) -> GuardConfig:
    """
    Use LLM to analyze intent and derive concrete guard rules.

    Args:
        intent: Natural language policy description.
        name: Name for the guard.
        schema_context: Optional database schema for context.
        llm_manager: LLM client. If None, creates default.

    Returns:
        GuardConfig with derived rules and intent stored.

    Raises:
        ValueError: If LLM returns invalid JSON or rules.
    """
    # Get or create LLM manager
    if llm_manager is None:
        from ..llm_manager import LLMManager
        llm_manager = LLMManager()

    # Build prompt
    schema_str = schema_context if schema_context else "No schema provided - derive general rules."
    prompt = DERIVATION_PROMPT.format(
        intent=intent,
        schema_context=schema_str,
    )

    # Call LLM
    logger.info("Deriving guard rules from intent...")
    result = llm_manager.generate_response(
        prompt=prompt,
        system_message="You are a database security expert. Output only valid JSON.",
        temperature=0.0,  # Deterministic output
        max_tokens=2000,
    )
    response = result["response"]

    # Parse response
    try:
        # Extract JSON from response (handle markdown code blocks)
        json_str = _extract_json(response)
        derived = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.error(f"LLM returned invalid JSON: {response[:500]}")
        raise ValueError(f"Could not parse LLM response as JSON: {e}")

    # Validate and build config
    return _build_config_from_derived(name, intent, derived)


def _extract_json(text: str) -> str:
    """Extract JSON from text, handling markdown code blocks."""
    text = text.strip()

    # Handle markdown code blocks
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end > start:
            return text[start:end].strip()

    if "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end > start:
            return text[start:end].strip()

    # Try to find JSON object directly
    if "{" in text:
        start = text.find("{")
        # Find matching closing brace
        depth = 0
        for i, c in enumerate(text[start:], start):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i+1]

    return text


def _build_config_from_derived(
    name: str,
    intent: str,
    derived: dict[str, Any],
) -> GuardConfig:
    """Build GuardConfig from LLM-derived rules."""

    # Build restrictions
    restrictions = RestrictionsConfig(
        denied_columns=derived.get("denied_columns"),
        allowed_tables=derived.get("allowed_tables"),
        required_filters=derived.get("required_filters"),
    )

    # Build guards
    guards = GuardsConfig(
        require_where=derived.get("require_where", False),
        require_limit=derived.get("require_limit", False),
        max_estimated_rows=derived.get("max_estimated_rows"),
    )

    # Build masking
    masking_patterns = derived.get("masking") or {}
    masking = MaskingConfig(patterns=masking_patterns)

    # Build limits
    limits = LimitsConfig(
        max_rows=derived.get("max_rows", 1000),
        timeout_seconds=derived.get("timeout_seconds", 30),
    )

    # Build full config
    return GuardConfig(
        name=name,
        description=derived.get("description", ""),
        intent=intent,
        derived=True,
        masking=masking,
        restrictions=restrictions,
        guards=guards,
        limits=limits,
    )


def format_derived_rules(config: GuardConfig) -> str:
    """Format derived rules for display to user.

    Args:
        config: GuardConfig to format.

    Returns:
        Human-readable string showing derived rules.
    """
    lines = []

    if config.description:
        lines.append(f"Description: {config.description}")
        lines.append("")

    # Restrictions
    if config.restrictions.allowed_tables:
        lines.append(f"Allowed tables: {', '.join(config.restrictions.allowed_tables)}")

    if config.restrictions.denied_columns:
        lines.append(f"Denied columns: {', '.join(config.restrictions.denied_columns)}")

    if config.restrictions.required_filters:
        lines.append("Required filters:")
        for table, cols in config.restrictions.required_filters.items():
            lines.append(f"  {table}: {', '.join(cols)}")

    # Masking
    if config.masking.patterns:
        lines.append("Masking:")
        for pattern, mask_type in config.masking.patterns.items():
            lines.append(f"  {pattern}: {mask_type}")

    # Guards
    if config.guards.require_where:
        lines.append("Require WHERE: yes")
    if config.guards.require_limit:
        lines.append("Require LIMIT: yes")
    if config.guards.max_estimated_rows:
        lines.append(f"Max estimated rows: {config.guards.max_estimated_rows:,}")

    # Limits
    lines.append(f"Max rows: {config.limits.max_rows:,}")
    lines.append(f"Timeout: {config.limits.timeout_seconds}s")

    return "\n".join(lines)
