"""Utility for parsing JSON from LLM responses that may include markdown fences."""

import json
from typing import Any, Dict


def parse_llm_json(raw_text: str) -> Dict[str, Any]:
    """Parse JSON from an LLM response, stripping markdown code fences if present.

    Args:
        raw_text: Raw LLM response text, possibly wrapped in ```json ... ``` fences.

    Returns:
        Parsed JSON as a dict.

    Raises:
        json.JSONDecodeError: If the text cannot be parsed as JSON.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        # Strip opening fence (might be ```json or just ```)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    return json.loads(text)
