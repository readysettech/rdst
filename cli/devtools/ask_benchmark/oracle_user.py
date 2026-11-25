"""Simulated user who knows what a BIRD question means and answers Ask's clarifications.

The simulator sees the question, Ask's clarification question and options, and the
case's BIRD evidence and gold SQL. It picks the option that matches the intended
meaning, or answers in one sentence of business meaning from the evidence. It never
sees results, so it cannot hand them over. Answers are cached by question and
normalized clarification text, so every model gets the same answer to the same question.

The hint-only user (``--oracle-user-knowledge hint-only``) sees the evidence but not the
gold SQL, answers only the point asked, and says it does not know when the evidence does
not decide it; a user who knows the gold SQL can restate the whole query in answers.

``report`` compares an oracle run with the same model's no-hint and hint runs:

    python -m devtools.ask_benchmark.oracle_user report --oracle DIR --no-hints DIR \
        --hints DIR [--one-question DIR]
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from .models import BenchmarkCase

ORACLE_USER_POLICY = "oracle-user-v3"
ORACLE_USER_DEFAULT_MODEL = "claude-haiku-5-5"
ORACLE_MAX_ROUNDS = 2
ANY_OPTION_ANSWER = "Any of these is fine."
UNANSWERABLE_ANSWER = (
    "Sorry, this question is out of scope, so I can not answer your question."
)
ORACLE_HINT_USER_POLICY = "oracle-user-hint-v1"
DONT_KNOW_ANSWER = "I don't know."

ORACLE_SYSTEM_PROMPT = """\
You are the person who asked a question about your company's database. You know \
exactly what you meant: your notes below say it as a hint and as the correct SQL. \
A data assistant asks you a clarifying question, usually with numbered options. Answer \
it as that person would.

Rules:
- If one option states exactly what you meant, answer with its number. Exactly means the \
same comparison (including whether the boundary itself counts), the same value and unit, \
and the same code or category as your hint and correct SQL.
- Never pick an option whose threshold, boundary, unit or category differs from your \
hint or correct SQL, even when it is close ("65 and older" is not "older than 65"). \
Instead write one short sentence that states the exact rule in words: the comparison, \
whether the boundary counts, the value, the unit and the exact code or category (for \
example "strictly older than 65" or "a triglyceride level of 200 or more").
- If no option matches, or there are no options, write one short sentence saying what \
you meant, in business terms, using only what your hint says or what the correct SQL \
implies.
- If the question is about something your intent does not decide, pick the option the \
correct SQL agrees with; if every option agrees with it, set "option" to null and \
"answer" to "Any of these is fine."
- Never write SQL. Never name a table, column or other database identifier, even when the \
assistant's question names one; say what it means instead ("the patient's age", "the \
admission status"). Stating a stored value or code is fine ("a label of '-' means not \
carcinogenic"). Never give a result or a number the question did not ask about. Answer \
only what was asked.

Reply with JSON only: \
{"option": <option number or null>, "answer": "<sentence or empty>"}"""

HINT_ONLY_SYSTEM_PROMPT = """\
You are the person who asked a question about your company's database. What you meant \
beyond the question's own words is in your note below. A data assistant asks you a \
clarifying question, usually with numbered options. Answer it as that person would.

Rules:
- Answer only the point the assistant asked about. Do not add filters, output columns, \
ordering, counting rules or anything else it did not ask about, even when your note \
covers them.
- If one option states exactly what your note means for that point, answer with its \
number. Exactly means the same comparison (including whether the boundary itself \
counts), the same value and unit, and the same code or category as your note.
- Never pick an option whose threshold, boundary, unit or category differs from your \
note, even when it is close ("65 and older" is not "older than 65"). Instead write one \
short sentence that states the exact rule in words (for example "strictly older than \
65" or "a triglyceride level of 200 or more").
- If no option matches, or there are no options, and your note decides the point, write \
one short sentence saying what you meant, in business terms, using only your note.
- If your note and the question's own words do not decide the point, set "option" to \
null and "answer" to "I don't know."
- Never write SQL. Never name a table, column or other database identifier, even when the \
assistant's question names one; say what it means instead ("the patient's age", "the \
admission status"). Stating a stored value or code from your note is fine ("a label of \
'-' means not carcinogenic"). Never give a result or a number the question did not ask \
about.

Reply with JSON only: \
{"option": <option number or null>, "answer": "<sentence or empty>"}"""

# Identifiers in code form: snake_case, digits, or camelCase as written ("PresStat").
_CODE_SHAPED = re.compile(r"_|\d|[a-z][A-Z]")
_SCHEMA_NOUNS = r"(?:columns?|tables?|fields?|attributes?|keys?)"
_IDENTIFIER_RETRY = (
    "\n\nYour previous answer named database identifiers ({names}). Give the same answer "
    "in business terms, without naming any table or column."
)
# A product surface without free text on option questions: the user must pick one.
_OPTIONS_ONLY_RETRY = (
    "\n\nThe screen offers only these options and no way to type an answer. Reply with "
    "the number of the option closest to what you meant."
)
# A question whose options the user may pick several of, as the product shows it.
_SEVERAL_NOTE = (
    " (You may pick several options: set \"option\" to a list of their numbers.)"
)
_SQL_LEAK = re.compile(r"\bselect\b[\s\S]*\bfrom\b|\bwhere\b[\s\S]*=|`", re.IGNORECASE)
_MAX_FREE_TEXT_CHARS = 300


@dataclass(frozen=True)
class OracleAnswer:
    """One simulated answer; ``answer`` is None when the question is left unanswered."""

    question_id: str
    question: str
    options: tuple[str, ...]
    answer: str | None
    choice: int | None
    reason: str
    cached: bool
    raw_reply: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question": self.question,
            "options": list(self.options),
            "answer": self.answer,
            "choice": self.choice,
            "reason": self.reason,
            "cached": self.cached,
            "raw_reply": self.raw_reply,
        }


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def cache_key(
    case_id: int,
    question: str,
    options: Iterable[str],
    model: str,
    policy: str = ORACLE_USER_POLICY,
) -> str:
    payload = [policy, model, case_id, _normalize(question)]
    payload.append([_normalize(option) for option in options])
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def build_user_message(case: BenchmarkCase, question: str, options: list[str]) -> str:
    numbered = "\n".join(f"{i}. {option}" for i, option in enumerate(options, 1))
    return (
        f"My question: {case.question}\n\n"
        f"My notes (hint): {case.evidence or '(none)'}\n"
        f"My notes (correct SQL): {case.gold_sql}\n\n"
        f"The assistant asks: {question}\n"
        f"Options:\n{numbered or '(no options; answer in words)'}"
    )


def build_hint_only_message(
    case: BenchmarkCase, question: str, options: list[str]
) -> str:
    numbered = "\n".join(f"{i}. {option}" for i, option in enumerate(options, 1))
    return (
        f"My question: {case.question}\n\n"
        f"My note: {case.evidence or '(none)'}\n\n"
        f"The assistant asks: {question}\n"
        f"Options:\n{numbered or '(no options; answer in words)'}"
    )


# What the simulated user knows: policy, system prompt and message builder.
ORACLE_USER_KNOWLEDGE: dict[
    str, tuple[str, str, Callable[[BenchmarkCase, str, list[str]], str]]
] = {
    "hint-and-sql": (ORACLE_USER_POLICY, ORACLE_SYSTEM_PROMPT, build_user_message),
    "hint-only": (
        ORACLE_HINT_USER_POLICY,
        HINT_ONLY_SYSTEM_PROMPT,
        build_hint_only_message,
    ),
}


_SQLGLOT_DIALECTS = {"postgresql": "postgres"}


def schema_names(sql: str, dialect: str | None = None) -> frozenset[str]:
    """Table and column names in ``sql``; empty when it does not parse."""
    try:
        tree = sqlglot.parse_one(sql, read=_SQLGLOT_DIALECTS.get(dialect, dialect))
    except sqlglot.errors.SqlglotError:
        return frozenset()
    nodes = [*tree.find_all(exp.Column), *tree.find_all(exp.Table)]
    return frozenset(node.name for node in nodes if node.name)


def _stem(word: str) -> str:
    word = word.casefold()
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def named_identifiers(answer: str, names: Iterable[str], user_words: str) -> list[str]:
    """Names from ``names`` that ``answer`` uses as database identifiers rather than words:
    written in code form, referred to as a column or table, or a lowercase name the user's
    own wording (``user_words``) never uses. Acronyms in capitals read as business terms."""
    words = {_stem(word) for word in re.findall(r"[A-Za-z]+", user_words)}
    found = []
    for name in sorted(names, key=str.casefold):
        token = rf"(?<![\w-]){re.escape(name)}(?![\w-])"
        for match in re.finditer(token, answer, re.IGNORECASE):
            written = match.group(0)
            before, after = answer[: match.start()], answer[match.end() :]
            as_schema = (
                (before.endswith("(") and after.startswith(")"))
                or re.match(rf"\s+{_SCHEMA_NOUNS}\b", after, re.IGNORECASE)
                or re.search(
                    rf"\b{_SCHEMA_NOUNS}\s+(?:named\s+|called\s+)?$",
                    before,
                    re.IGNORECASE,
                )
            )
            if written.isupper() and not _CODE_SHAPED.search(written):
                continue
            plain = written.islower() or written.istitle()
            if (
                _CODE_SHAPED.search(written)
                or as_schema
                or (plain and _stem(written) not in words)
            ):
                found.append(name)
                break
    return found


def parse_reply(
    raw: str,
    options: list[str],
    max_chars: int = _MAX_FREE_TEXT_CHARS,
    leak: re.Pattern[str] = _SQL_LEAK,
) -> tuple[str | None, int | None, str]:
    """(answer for Ask, chosen option number, reason) from the simulator's reply."""
    match = re.search(r"\{[\s\S]*\}", raw)
    try:
        data = json.loads(match.group(0)) if match else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None, None, "unparseable_reply"
    choice = data.get("option")
    if (
        isinstance(choice, list)
        and choice
        and all(
            isinstance(item, int) and not isinstance(item, bool) and 1 <= item <= len(options)
            for item in choice
        )
    ):
        # A question that allows several options takes them all.
        picked = list(dict.fromkeys(choice))
        return "; ".join(options[item - 1] for item in picked), picked[0], "options"
    if (
        isinstance(choice, int)
        and not isinstance(choice, bool)
        and 1 <= choice <= len(options)
    ):
        return options[choice - 1], choice, "option"
    text = data.get("answer")
    if not isinstance(text, str) or not text.strip():
        return None, None, "empty_reply"
    text = " ".join(text.split())
    if leak.search(text):
        return None, None, "sql_leak_blocked"
    if len(text) > max_chars:
        return None, None, "too_long_blocked"
    if text == ANY_OPTION_ANSWER:
        return text, None, "any_option"
    if text == UNANSWERABLE_ANSWER:
        return text, None, "unanswerable"
    if text == DONT_KNOW_ANSWER:
        # Left unanswered, so Ask proceeds as if the question was skipped.
        return None, None, "dont_know"
    return text, None, "free_text"


class OracleUserSimulator:
    """Answers clarification questions for one benchmark run; thread-safe."""

    def __init__(
        self,
        complete: Callable[[str, str], str],
        *,
        model: str = ORACLE_USER_DEFAULT_MODEL,
        cache_path: Path | None = None,
        max_answers: int | None = None,
        policy: str = ORACLE_USER_POLICY,
        system_prompt: str = ORACLE_SYSTEM_PROMPT,
        build_message: Callable[
            [BenchmarkCase, str, list[str]], str
        ] = build_user_message,
        max_free_text_chars: int = _MAX_FREE_TEXT_CHARS,
        leak_pattern: re.Pattern[str] = _SQL_LEAK,
        user_words: Callable[[BenchmarkCase], str] | None = None,
        options_only: bool = False,
    ):
        """``policy``, ``system_prompt`` and ``build_message`` define what the simulated
        user knows; a benchmark with its own clarification labels passes all three.
        ``user_words`` is the user's own wording for a case (default: question and evidence);
        free-text answers that name gold-SQL identifiers outside it are asked once to
        rephrase, then blocked. ``options_only`` models a surface where an option question
        takes only one of its options: a reply that picks none is asked once for the
        closest option, then gets the first, which that surface preselects."""
        if max_answers is not None and max_answers < 1:
            raise ValueError("max_answers must be positive")
        self._complete = complete
        self.model = model
        self.policy = policy
        self.system_prompt = system_prompt
        self.build_message = build_message
        self.max_free_text_chars = max_free_text_chars
        self.leak_pattern = leak_pattern
        self.user_words = user_words or (
            lambda case: f"{case.question} {case.evidence or ''}"
        )
        self.cache_path = cache_path
        self.max_answers = max_answers
        self.options_only = options_only
        self._lock = threading.Lock()
        self._cache: dict[str, str] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        """Read answers other runs sharing the cache file wrote; the first answer to a key
        wins, and a line cut short by a process that died while writing is skipped."""
        if self.cache_path is None or not self.cache_path.exists():
            return
        for line in self.cache_path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
                self._cache.setdefault(entry["key"], entry["raw_reply"])
            except (ValueError, KeyError, TypeError):
                continue

    def describe(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "model": self.model,
            "max_answers": self.max_answers,
            "max_rounds": ORACLE_MAX_ROUNDS,
            "max_free_text_chars": self.max_free_text_chars,
            "leak_pattern": self.leak_pattern.pattern,
            "identifier_guard": "gold-sql-names-v1",
            "options_only": self.options_only,
            "system_prompt_sha256": hashlib.sha256(
                self.system_prompt.encode()
            ).hexdigest(),
        }

    def answer(
        self, case: BenchmarkCase, questions: Iterable[Any]
    ) -> tuple[dict[str, str], list[OracleAnswer]]:
        """Answers keyed by clarification question ID, plus a record of every exchange.

        Questions past ``max_answers`` and blocked replies are left out, which Ask
        treats as skipped."""
        answers: dict[str, str] = {}
        records = []
        for index, question in enumerate(questions):
            # Suggested answers to an open question are picked like options, but the user
            # can always type instead.
            choice_only = bool(question.options)
            options = list(question.options or getattr(question, "examples", None) or [])
            if self.max_answers is not None and index >= self.max_answers:
                records.append(
                    OracleAnswer(
                        question.id,
                        question.question,
                        tuple(options),
                        None,
                        None,
                        "over_question_budget",
                        False,
                        "",
                    )
                )
                continue
            # The user sees each option with its schema hint, as the product shows it.
            hints = list(getattr(question, "hints", None) or [])
            shown = [
                f"{option} ({hint})" if hint else option
                for option, hint in zip(options, hints + [""] * len(options))
            ]
            asked = question.question + (
                _SEVERAL_NOTE if getattr(question, "multiple", False) else ""
            )
            raw, cached = self._reply(case, asked, shown)
            answer, choice, reason = parse_reply(
                raw, options, self.max_free_text_chars, self.leak_pattern
            )
            named = self._named_identifiers(case, answer, choice)
            if named:
                retry = _IDENTIFIER_RETRY.format(names=", ".join(named))
                raw, cached = self._reply(case, asked, shown, retry)
                answer, choice, reason = parse_reply(
                    raw, options, self.max_free_text_chars, self.leak_pattern
                )
                if self._named_identifiers(case, answer, choice):
                    answer, choice, reason = None, None, "identifier_blocked"
                elif reason == "free_text":
                    reason = "rephrased"
            if self.options_only and choice_only and choice is None:
                if reason in ("free_text", "rephrased"):
                    raw, cached = self._reply(case, asked, shown, _OPTIONS_ONLY_RETRY)
                    _, choice, _ = parse_reply(
                        raw, options, self.max_free_text_chars, self.leak_pattern
                    )
                reason = "forced_choice" if choice else "forced_default"
                choice = choice or 1
                answer = options[choice - 1]
            if answer is not None:
                answers[question.id] = answer
            records.append(
                OracleAnswer(
                    question.id,
                    question.question,
                    tuple(options),
                    answer,
                    choice,
                    reason,
                    cached,
                    raw,
                )
            )
        return answers, records

    def _named_identifiers(
        self, case: BenchmarkCase, answer: str | None, choice: int | None
    ) -> list[str]:
        if answer is None or choice is not None:
            return []
        names = schema_names(case.gold_sql, case.dialect)
        return named_identifiers(answer, names, self.user_words(case))

    def _reply(
        self, case: BenchmarkCase, question: str, options: list[str], retry: str = ""
    ) -> tuple[str, bool]:
        policy = f"{self.policy}:{retry}" if retry else self.policy
        key = cache_key(case.question_id, question, options, self.model, policy)
        with self._lock:
            if key not in self._cache:
                self._load_cache()
            if key in self._cache:
                return self._cache[key], True
        raw = self._complete(
            self.system_prompt, self.build_message(case, question, options) + retry
        )
        with self._lock:
            if self.cache_path is None:
                if key in self._cache:
                    return self._cache[key], True
                self._cache[key] = raw
                return raw, False
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                {
                    "key": key,
                    "question_id": case.question_id,
                    "question": question,
                    "raw_reply": raw,
                }
            )
            with self.cache_path.open("a+b") as handle:
                # Under the file lock, an answer another run wrote meanwhile wins, so every
                # model sees the same one.
                fcntl.flock(handle, fcntl.LOCK_EX)
                self._load_cache()
                if key in self._cache:
                    return self._cache[key], True
                self._cache[key] = raw
                # A line a dead process left unterminated must not swallow this one.
                torn = False
                if handle.seek(0, os.SEEK_END):
                    handle.seek(-1, os.SEEK_END)
                    torn = handle.read(1) != b"\n"
                handle.write((("\n" if torn else "") + line + "\n").encode("utf-8"))
        return raw, False


class OracleUserUnavailable(RuntimeError):
    """The simulated user's model could not be reached; the attempt goes unscored."""


def gateway_completer(
    model: str = ORACLE_USER_DEFAULT_MODEL,
    max_tokens: int = 2000,
) -> Callable[[str, str], str]:
    """Completion at temperature 0 through the Claude gateway (ANTHROPIC_BASE_URL and
    ANTHROPIC_AUTH_TOKEN). ``max_tokens`` covers any thinking the model does first."""
    import anthropic

    for variable in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
        if not os.getenv(variable):
            raise ValueError(f"The oracle user needs {variable}")
    client = anthropic.Anthropic(
        api_key=None,
        auth_token=os.environ["ANTHROPIC_AUTH_TOKEN"],
        base_url=os.environ["ANTHROPIC_BASE_URL"],
        max_retries=4,
        timeout=120,
    )

    def complete(system: str, user: str) -> str:
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                # The SDK's message methods take no sampling parameters; the API still
                # honors them.
                extra_body={"temperature": 0},
            )
        except anthropic.APIError as exc:
            raise OracleUserUnavailable(f"Oracle user call failed: {exc}") from exc
        return "".join(
            block.text
            for block in response.content
            if getattr(block, "type", "") == "text"
        )

    return complete


def _attempts(run_dir: Path) -> dict[int, dict[str, Any]]:
    """The scored attempt of each question; a run holding several models or repetitions of
    one question is refused, since the report compares one model's single answers."""
    attempts: dict[int, dict[str, Any]] = {}
    for path in sorted(run_dir.rglob("attempts.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                attempt = json.loads(line)
                # Cases with unstable gold stay out, as they do on the leaderboard.
                if not attempt.get("scored") or not (attempt.get("diagnostics") or {}).get(
                    "headline_eligible", True
                ):
                    continue
                question_id = int(attempt["question_id"])
                if question_id in attempts:
                    raise ValueError(
                        f"{run_dir} holds more than one scored attempt of question "
                        f"{question_id}; report one model and one repetition per run"
                    )
                attempts[question_id] = attempt
    return attempts


def _asked(attempt: dict[str, Any]) -> int:
    clarification = (attempt.get("diagnostics") or {}).get("clarification") or {}
    rounds = clarification.get("oracle_rounds") or []
    return sum(len(r.get("exchanges", [])) for r in rounds)


def oracle_report(
    oracle: Path, no_hints: Path, hints: Path, one_question: Path | None = None
) -> dict[str, Any]:
    runs = {
        "oracle": _attempts(oracle),
        "no_hints": _attempts(no_hints),
        "hints": _attempts(hints),
    }
    if one_question is not None:
        runs["one_question"] = _attempts(one_question)
    common = set.intersection(*(set(r) for r in runs.values()))
    if not common:
        raise ValueError("The runs share no scored questions")
    models = {run[q].get("model_name") for run in runs.values() for q in common}
    if len(models) > 1:
        raise ValueError(f"The runs are of different models: {sorted(map(str, models))}")

    def correct(name: str, qid: int) -> bool:
        return bool(runs[name][qid]["execution_correct"])

    def accuracy(name: str) -> float:
        return sum(correct(name, q) for q in common) / len(common)

    asked = {q for q in common if _asked(runs["oracle"][q])}
    need = {q for q in common if not correct("no_hints", q) and correct("hints", q)}
    wrong_without = {q for q in common if not correct("no_hints", q)}
    acc = {name: accuracy(name) for name in runs}
    gap = acc["hints"] - acc["no_hints"]
    reasons = Counter(
        e.get("reason")
        for q in common
        for r in (
            (runs["oracle"][q].get("diagnostics") or {}).get("clarification") or {}
        ).get("oracle_rounds", [])
        for e in r.get("exchanges", [])
    )
    blocked = ("sql_leak_blocked", "too_long_blocked", "identifier_blocked")
    # Oracle answers count only where the no-hint or hint run is also right, so answers
    # that carry more than the hint add nothing.
    acc["oracle_hint_capped"] = sum(
        correct("oracle", q) and (correct("no_hints", q) or correct("hints", q))
        for q in common
    ) / len(common)
    return {
        "questions": len(common),
        "accuracy": acc,
        "gap_recovered": (acc["oracle"] - acc["no_hints"]) / gap if gap else None,
        "asked_tasks": len(asked),
        "ask_rate": len(asked) / len(common),
        "questions_asked_per_task": sum(_asked(runs["oracle"][q]) for q in common)
        / len(common),
        "ask_recall": len(asked & need) / len(need) if need else None,
        # Asks on questions the no-hint run gets wrong, and on those the hints also fix.
        "ask_precision": len(asked & wrong_without) / len(asked) if asked else None,
        "ask_precision_need": len(asked & need) / len(asked) if asked else None,
        "needs_clarification": len(need),
        "fixed_by_asking": len(
            {q for q in asked if correct("oracle", q)} & wrong_without
        ),
        "broken_by_asking": len(
            {q for q in asked if not correct("oracle", q)} - wrong_without
        ),
        "blocked_answers": sum(reasons[reason] for reason in blocked),
        "identifier_blocked": reasons["identifier_blocked"],
        "identifier_rephrased": reasons["rephrased"],
        "dont_know_answers": reasons["dont_know"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m devtools.ask_benchmark.oracle_user"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser(
        "report", help="Score an oracle run against no-hint and hint runs"
    )
    report.add_argument("--oracle", type=Path, required=True)
    report.add_argument("--no-hints", type=Path, required=True)
    report.add_argument("--hints", type=Path, required=True)
    report.add_argument("--one-question", type=Path)
    args = parser.parse_args(argv)
    print(
        json.dumps(
            oracle_report(args.oracle, args.no_hints, args.hints, args.one_question),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
