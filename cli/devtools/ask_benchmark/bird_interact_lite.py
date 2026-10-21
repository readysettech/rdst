"""BIRD-Interact-Lite for rdst ask: SELECT tasks, a labeled user simulator and official scoring.

Each task is an ambiguous question over a PostgreSQL database. Following the official c-Interact
setup, the assistant sees the database's knowledge base minus the entries the task masks; the
simulated user knows the labeled ambiguity points, the masked entries, the clear question and
the correct SQL, and answers only what is asked. After a solved first question the user asks a
follow-up, which rdst gets with the earlier exchange as provided context.

The task file is the public release merged with the emailed ground truth; it stays outside the
repository and is passed in by path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from .models import BenchmarkCase
from .oracle_user import UNANSWERABLE_ANSWER

SIMULATOR_POLICY = "bird-interact-lite-user-v4"
KNOWLEDGE_FIELDS = ("id", "knowledge", "description", "definition")
# Knowledge definitions are formulas ("SNQI = SnrRatio - ..."), so only query text is a leak.
SIMULATOR_LEAK = re.compile(r"\bselect\b[\s\S]*\bfrom\b|`", re.IGNORECASE)

SIMULATOR_SYSTEM_PROMPT = f"""\
You are the person at a company who gave a data task to an AI data assistant. The assistant \
is not sure what you meant and asks a clarifying question, usually with numbered options. Your \
notes below hold the ambiguity points labeled for your task, the knowledge entries the \
assistant cannot see, the clear version of your task and the correct SQL.

Rules:
- Answer only if the question is about one of the labeled ambiguity points, or about a detail \
the correct SQL must settle that is still a real ambiguity. Otherwise answer exactly: \
"{UNANSWERABLE_ANSWER}"
- If the answer is a hidden knowledge entry (a formula, threshold or category definition), \
state that definition in one or two sentences in business terms, even when an option is close: \
the assistant cannot compute it from an option's wording.
- Otherwise, if one option states exactly what you meant, answer with its number; if none \
does, or there are no options, write one short sentence in business terms.
- Never pick an option whose threshold, boundary, unit or category differs from your notes, \
even when it is close; instead state the exact rule: the comparison, whether the boundary \
counts, the value, the unit and the exact code or category.
- Never name a table, column or other database identifier, even when the question names one; \
say what it means instead ("the scan's zone", "the preservation status"). Stating a stored \
value or code is fine ("a status of 'Poor' or 'Critical'").
- Never write SQL or SQL fragments, never quote the correct SQL, never restate the clear \
version of the task, and never give away other clarifications. Answer only what was asked.

Reply with JSON only: \
{{"option": <option number or null>, "answer": "<sentence or empty>"}}"""


def load_tasks(path: Path, *, exclude: Iterable[str] = ()) -> list[dict[str, Any]]:
    """The SELECT ("Query") tasks of a merged task file, minus ``exclude``."""
    skipped = set(exclude)
    tasks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        task = json.loads(line)
        if task["category"] == "Query" and task["instance_id"] not in skipped:
            tasks.append(task)
    return tasks


def solution_sql(task: dict[str, Any], phase: str = "initial") -> str:
    """The single reference query of the first question or the follow-up."""
    sql = task["sol_sql"] if phase == "initial" else task["follow_up"]["sol_sql"]
    statements = [sql] if isinstance(sql, str) else list(sql)
    if len(statements) != 1:
        raise ValueError(f"{task['instance_id']}: expected one {phase} query")
    return statements[0]


def masked_ids(task: dict[str, Any]) -> set[int]:
    """Knowledge entries the official setup removes from the assistant's view."""
    return {
        entry["deleted_knowledge"]
        for entry in task["knowledge_ambiguity"]
        if entry.get("deleted_knowledge") is not None
    }


def knowledge_text(
    entries: Sequence[dict[str, Any]], hidden: set[int] = frozenset()
) -> str:
    visible = [
        {field: entry[field] for field in KNOWLEDGE_FIELDS if field in entry}
        for entry in entries
        if entry.get("id") not in hidden
    ]
    return "Domain knowledge for this database:\n" + json.dumps(visible, indent=2)


def ambiguity_points(task: dict[str, Any]) -> str:
    return (
        "user_query_ambiguity:\n"
        + json.dumps(task["user_query_ambiguity"], indent=2)
        + "\n\nknowledge_ambiguity:\n"
        + json.dumps(task["knowledge_ambiguity"], indent=2)
    )


def simulator_message(
    task: dict[str, Any],
    knowledge: Sequence[dict[str, Any]],
    question: str,
    options: list[str],
) -> str:
    hidden = masked_ids(task)
    hidden_entries = [
        {field: entry[field] for field in KNOWLEDGE_FIELDS if field in entry}
        for entry in knowledge
        if entry.get("id") in hidden
    ]
    numbered = "\n".join(f"{i}. {option}" for i, option in enumerate(options, 1))
    return (
        f"My task: {task['amb_user_query']}\n\n"
        f"Labeled ambiguity points:\n{ambiguity_points(task)}\n\n"
        f"Knowledge the assistant cannot see:\n{json.dumps(hidden_entries, indent=2)}\n\n"
        f"Clear version of my task: {task['query']}\n\n"
        f"Correct SQL:\n{solution_sql(task)}\n\n"
        f"The assistant asks: {question}\n"
        f"Options:\n{numbered or '(no options; answer in words)'}"
    )


def user_words(task: dict[str, Any]) -> str:
    """The user's own wording for a task: both versions of the question and the labeled
    terms. Identifiers outside it read as schema names in a simulated answer."""
    labels = [
        entry["term"]
        for group in task["user_query_ambiguity"].values()
        for entry in group
    ]
    labels += [entry["term"] for entry in task["knowledge_ambiguity"]]
    return " ".join([task["amb_user_query"], task["query"], *labels])


def follow_up_context(
    knowledge: str,
    question: str,
    exchanges: Sequence[tuple[str, str]],
    sql: str,
) -> str:
    """Provided context for the follow-up: the knowledge the assistant saw, then the earlier
    question, the clarifications it got and the SQL that answered it."""
    lines = [knowledge, "", "Earlier in this conversation the user asked:", question]
    if exchanges:
        lines.append("The assistant asked for clarification:")
        lines += [f"- Q: {asked} A: {answer}" for asked, answer in exchanges]
    lines += [
        "The SQL that answered it:",
        sql,
        "The user's next question follows up on that answer.",
    ]
    return "\n".join(lines)


def case_for(
    task: dict[str, Any],
    question_id: int,
    *,
    question: str,
    context: str,
    phase: str = "initial",
) -> BenchmarkCase:
    return BenchmarkCase(
        question_id=question_id,
        db_id=task["selected_database"],
        question=question,
        evidence=context,
        gold_sql=solution_sql(task, phase),
        difficulty=str(task.get("difficulty_tier") or ""),
        dialect="postgresql",
    )


# Official SELECT comparison (test_case_default and ex_base in the BIRD-Interact repository):
# comments, DISTINCT and ROUND are removed from both queries, numbers round to 2 places and
# dates to YYYY-MM-DD, rows compare as sets unless order matters, and an empty result fails.


def _remove_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    sql = re.sub(r"--.*?(\r\n|\r|\n|$)", r"\1", sql)
    return re.sub(r"\n\s*\n+", "\n", sql).strip()


def _remove_distinct(sql: str) -> str:
    sql = re.sub(r"\bDISTINCT\b(?![^()]*\bON\b)", "", sql, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", sql).strip()


def _remove_round(sql: str) -> str:
    pattern = re.compile(r"ROUND\s*\(", re.IGNORECASE)
    while match := pattern.search(sql):
        start, open_paren = match.start(), match.end() - 1
        depth, first_end = 0, len(sql)
        for i in range(open_paren + 1, len(sql)):
            if sql[i] == "(":
                depth += 1
            elif sql[i] == ")":
                if depth == 0:
                    first_end = i
                    break
                depth -= 1
            elif sql[i] == "," and depth == 0:
                first_end = i
                break
        depth, close = 0, -1
        for i in range(open_paren, len(sql)):
            if sql[i] == "(":
                depth += 1
            elif sql[i] == ")":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close == -1:
            break
        sql = sql[:start] + sql[open_paren + 1 : first_end].strip() + sql[close + 1 :]
    return sql


def official_sql(sql: str) -> str:
    return _remove_round(_remove_distinct(_remove_comments(sql)))


def _round_value(item: Any, places: int = 2) -> Any:
    quantizer = Decimal(1).scaleb(-places)
    if isinstance(item, Decimal):
        return float(item.quantize(quantizer, rounding=ROUND_HALF_UP))
    if isinstance(item, float):
        return float(Decimal(str(item)).quantize(quantizer, rounding=ROUND_HALF_UP))
    if isinstance(item, (list, tuple)):
        return type(item)(_round_value(x, places) for x in item)
    if isinstance(item, dict):
        return {key: _round_value(value, places) for key, value in item.items()}
    return item


def normalize_rows(rows: Iterable[Sequence[Any]]) -> list[tuple[Any, ...]]:
    out = []
    for row in rows:
        values: list[Any] = []
        for item in row:
            if isinstance(item, (date, datetime)):
                values.append(item.strftime("%Y-%m-%d"))
                continue
            item = _round_value(item)
            values.append(
                json.dumps(item, sort_keys=True)
                if isinstance(item, (dict, list))
                else item
            )
        out.append(tuple(values))
    return out


def rows_match(
    predicted: Iterable[Sequence[Any]],
    gold: Iterable[Sequence[Any]],
    conditions: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    pred_rows, gold_rows = normalize_rows(predicted), normalize_rows(gold)
    if not pred_rows or not gold_rows:
        return False, "empty result"
    if (conditions or {}).get("order"):
        return pred_rows == gold_rows, None
    return set(pred_rows) == set(gold_rows), None


def reward(initial_correct: bool, follow_up_correct: bool) -> float:
    """Official c-Interact reward without the debugging turn: 0.7 and 0.3 on the first try."""
    return (0.7 if initial_correct else 0.0) + (
        0.3 if initial_correct and follow_up_correct else 0.0
    )
