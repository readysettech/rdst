from __future__ import annotations

import json
import re

from features.ask.prompts.ask_prompts import format_provided_context_block

from .models import BenchmarkCase, ContextMode

_SQL_FENCE = re.compile(
    r"```(?:sql|mysql|postgresql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL
)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


def build_model_only_prompt(
    case: BenchmarkCase, schema: str, context_mode: ContextMode
) -> tuple[str, str]:
    system = (
        "You are an expert text-to-SQL system. Return exactly one read-only SQL "
        "query and no explanation, markdown, or commentary. Use the requested "
        "database dialect and only identifiers present in the schema."
    )
    provided_context = provided_context_for_case(case, context_mode)
    prompt = (
        f"Dialect: {case.dialect}\n\n"
        f"Schema:\n{schema}\n\n"
        f"Question:\n{case.question}"
        f"{format_provided_context_block(provided_context)}"
    )
    return system, prompt


def question_for_rdst(case: BenchmarkCase, context_mode: ContextMode) -> str:
    return case.question


def provided_context_for_case(case: BenchmarkCase, context_mode: ContextMode) -> str:
    if context_mode == ContextMode.EVIDENCE:
        return case.evidence.strip()
    return ""


def extract_sql(response: str) -> str:
    text = _THINK_BLOCK.sub("", response).strip()
    fence = _SQL_FENCE.search(text)
    if fence:
        text = fence.group(1).strip()
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            for key in ("sql", "query"):
                value = payload.get(key)
                if isinstance(value, str):
                    text = value.strip()
                    break
    text = re.sub(r"^SQL\s*:\s*", "", text, flags=re.IGNORECASE).strip()
    if not re.match(r"^\(*\s*(SELECT|WITH)\b", text, re.IGNORECASE):
        raise ValueError("Model response does not begin with SELECT or WITH")
    return text
