"""Utility for parsing JSON from LLM responses that may include markdown fences."""

from __future__ import annotations

import json
from typing import Any, Dict


def parse_llm_json(raw_text: str) -> Dict[str, Any]:
    """Parse JSON from an LLM response, stripping markdown code fences if present."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    return json.loads(text)


__all__ = ["parse_llm_json"]
