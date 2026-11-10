"""Find the definitions behind names that SQL validation rejected.

A question's provided context or the user's clarification answers can define metrics
("WPR = ..."), and a model sometimes writes
such a metric as if it were a column. The validation repair then quotes the definitions so
the model can expand each term into the columns it is computed from.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

# Definitions quoted in one repair prompt.
MAX_DEFINITIONS = 5

_UNKNOWN_NAME_MESSAGE = re.compile(
    r"not found|does not exist|could not be resolved|unknown column", re.IGNORECASE
)
_REJECTED_NAME_PATTERNS = (
    re.compile(r"Unknown column: ([\w.\"`]+)"),
    re.compile(r"Column '([^']+)'"),
)
_PARENTHESIZED = re.compile(r"\(([^()]*)\)")
_EQUATION_NAME = re.compile(r"^\W*(?:\\text\{)?([A-Za-z][\w]*)\}?\s*=(?!=)")


def defined_term_feedback(errors: Iterable[Any], provided_context: str) -> str:
    """Repair guidance quoting each definition a rejected name refers to, or "" if none."""
    if not provided_context:
        return ""
    names = []
    for error in errors:
        message = getattr(error, "message", "") or ""
        if not _UNKNOWN_NAME_MESSAGE.search(message):
            continue
        candidates = [getattr(error, "column", "") or ""]
        candidates += [m for p in _REJECTED_NAME_PATTERNS for m in p.findall(message)]
        for candidate in candidates:
            key = _key(candidate.split(".")[-1])
            if key and key not in names:
                names.append(key)
    definitions = _definitions(provided_context, names)
    if not definitions:
        return ""
    return (
        "\n\nThese names are terms the provided context or the user's answers define, not "
        "columns. Write each "
        "as the expression its definition gives, using the columns that definition "
        "names:\n" + "\n".join(f"- {text}" for text in definitions)
    )


def _definitions(context: str, names: list[str]) -> list[str]:
    found: list[str] = []
    entries = _knowledge_entries(context)
    for name in names:
        matches = [
            f"{entry.get('knowledge', '')}: {entry['definition']}".lstrip(": ")
            for entry in entries
            if name in _entry_names(entry)
        ]
        if not matches:
            words = r"[\W_]+".join(map(re.escape, name.split("_")))
            pattern = re.compile(rf"(?<![\w.]){words}\}}?\s*=(?!=)", re.IGNORECASE)
            matches = [
                line.strip().lstrip("-* ")
                for line in context.splitlines()
                if pattern.search(line)
            ]
        for text in matches:
            if text not in found and len(found) < MAX_DEFINITIONS:
                found.append(text)
    return found


def _knowledge_entries(context: str) -> list[dict[str, Any]]:
    """Objects with a string definition from the JSON arrays in the context."""
    decoder = json.JSONDecoder()
    entries = []
    index = context.find("[")
    while index != -1:
        try:
            value, end = decoder.raw_decode(context, index)
        except ValueError:
            index = context.find("[", index + 1)
            continue
        if isinstance(value, list):
            entries += [
                item
                for item in value
                if isinstance(item, dict) and isinstance(item.get("definition"), str)
            ]
        index = context.find("[", end)
    return entries


def _entry_names(entry: dict[str, Any]) -> set[str]:
    """A knowledge entry's name, its parts in and out of parentheses, and its formula name."""
    title = entry.get("knowledge")
    names = set()
    if isinstance(title, str):
        names |= {title, _PARENTHESIZED.sub("", title), *_PARENTHESIZED.findall(title)}
    equation = _EQUATION_NAME.match(entry["definition"])
    if equation:
        names.add(equation.group(1))
    return {_key(name) for name in names} - {""}


def _key(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", name.strip('"`').casefold()).strip("_")
