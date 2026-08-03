"""Generate example questions grounded in a target's actual schema.

Replaces the hardcoded generic e-commerce prompts (ports schema-grounded
examples from CL 14059). Prefers the semantic layer's business context via an
LLM; degrades to deterministic introspection-based questions when there is no
semantic layer or no AI key, so the feature never depends on a working key to
render *something* useful.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from shared.anthropic_env import has_anthropic_api_key
from shared.constants import rdst_data_dir

logger = logging.getLogger(__name__)

# Schema-neutral last resort — only when no tables are known at all.
_NEUTRAL = [
    "How many rows are in each table?",
    "Show me a sample of 10 rows from the largest table",
    "Which tables have the most columns?",
]


def _cache_path(target: str):
    d = rdst_data_dir() / "cache" / "ask_examples"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{target}.json"


def _fingerprint(table_names: list[str]) -> str:
    joined = "\n".join(sorted(table_names))
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _introspection_examples(table_names: list[str]) -> list[str]:
    """Deterministic, no-LLM questions from table names alone."""
    if not table_names:
        return list(_NEUTRAL)
    first = table_names[0]
    out = [f"Show me 10 rows from {first}", f"How many rows are in {first}?"]
    if len(table_names) > 1:
        out.append(f"Show the relationship between {first} and {table_names[1]}")
    else:
        out.append(f"What are the distinct values in {first}?")
    return out[:3]


def _semantic_examples(target: str, layer: Any) -> list[str] | None:
    """LLM-generated questions from table descriptions + business context."""
    from shared.llm_manager import LLMManager

    lines = []
    for name, table in list(layer.tables.items())[:12]:
        desc = getattr(table, "description", "") or ""
        ctx = getattr(table, "business_context", "") or ""
        lines.append(f"- {name}: {desc} {ctx}".strip())
    schema_blurb = "\n".join(lines)

    prompt = (
        f"A database named '{target}' has these tables:\n{schema_blurb}\n\n"
        "Write exactly 3 natural-language questions a user would realistically "
        "ask this specific database, answerable with a single SQL query. Make "
        "them concrete to this schema — reference real tables/concepts, not "
        "generic 'customers/orders'. Return JSON: "
        '{"questions": ["...", "...", "..."]}'
    )
    try:
        resp = LLMManager().query(
            system_message="You generate concise, schema-specific example questions.",
            user_query=prompt,
            max_tokens=300,
            temperature=0.4,
        )
        text = resp.get("text", "")
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
            questions = [q.strip() for q in data.get("questions", []) if q.strip()]
            if questions:
                return questions[:3]
    except Exception:
        logger.debug("Semantic example generation failed", exc_info=True)
    return None


def get_example_questions(
    target: str, table_names: list[str], semantic_layer: Any | None
) -> dict:
    """Return {examples: [str], source: 'semantic'|'introspection'}.

    Caches semantic (LLM) results per target, invalidated by a schema
    fingerprint. Introspection fallbacks are cheap and not cached.
    """
    fp = _fingerprint(table_names)
    cache = _cache_path(target)

    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if cached.get("fingerprint") == fp and cached.get("examples"):
                return {"examples": cached["examples"], "source": "semantic"}
        except Exception:
            logger.debug("Bad ask-examples cache for %s", target, exc_info=True)

    if semantic_layer is not None and has_anthropic_api_key():
        examples = _semantic_examples(target, semantic_layer)
        if examples:
            try:
                cache.write_text(
                    json.dumps({"fingerprint": fp, "examples": examples}),
                    encoding="utf-8",
                )
            except Exception:
                logger.debug("Failed to write ask-examples cache", exc_info=True)
            return {"examples": examples, "source": "semantic"}

    return {"examples": _introspection_examples(table_names), "source": "introspection"}
