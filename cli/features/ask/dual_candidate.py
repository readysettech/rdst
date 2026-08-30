"""Bounded alternate SQL generation with deterministic source-faithfulness selection."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from time import perf_counter
from typing import Any

import sqlglot
from sqlglot import exp

from features.ask.prompts.ask_prompts import (
    format_matched_database_values_block,
    format_provided_context_block,
    format_query_grounding_block,
)
from features.ask.sql_validation import validate_sql_for_ask

DUAL_CANDIDATE_SELECTOR_VERSION = "source-faithfulness-v6-canonical-literals"
SELECTIVE_ALTERNATE_GATE_VERSION = "selective-alternate-specific-risk-v4"
LITERAL_GROUNDING_VERSION = "canonical-equivalent-literals-v2"
SELECTIVE_WEAK_PREDICATE_MAX_PENALTY = 0.5
SELECTIVE_WEAK_COMPLEX_AGGREGATE_MIN_COUNT = 2
SELECTIVE_COLOCATED_MIN_PENALTY = 0.0
ALTERNATE_GENERATION_MAX_TOKENS = 4000
ALTERNATE_GENERATION_PURPOSE = "sql_generation_alternate"
ALTERNATE_SYSTEM_PROMPT = (
    "You are an expert text-to-SQL system. Return exactly one read-only SQL "
    "query and no explanation, markdown, or commentary. Use the requested "
    "database dialect and only identifiers present in the schema."
)

_SQL_FENCE = re.compile(
    r"```(?:sql|mysql|postgresql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL
)
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*|\d+(?:\.\d+)?")
_DATE_TOKEN = re.compile(r"(?<!\d)(\d{4})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})(?!\d)")
_YEAR_SPAN_TOKEN = re.compile(r"(?<!\d)(\d{4})\s*[-/]\s*(\d{4}|\d{2})(?!\s*[-/]\s*\d)")
_PERCENT_TOKEN = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*%")
_POSITIVE_PERCENT_ADJUSTMENT = re.compile(
    r"\b(?:higher|increase(?:d)?|more|above)\b", re.IGNORECASE
)
_NEGATIVE_PERCENT_ADJUSTMENT = re.compile(
    r"\b(?:lower|decrease(?:d)?|less|below)\b", re.IGNORECASE
)
_RANKING_WORDS = {
    "top",
    "highest",
    "lowest",
    "most",
    "least",
    "fastest",
    "slowest",
    "heaviest",
    "lightest",
    "latest",
    "earliest",
    "first",
    "last",
    "maximum",
    "minimum",
}


@dataclass(frozen=True)
class CandidateAssessment:
    penalty: float
    unsupported_literals: tuple[str, ...]
    weakly_grounded_predicates: tuple[str, ...]
    unrequested_limit: bool
    set_operations: int
    join_count: int
    table_count: int
    aggregate_count: int
    evidence_projection_matches: tuple[str, ...]
    evidence_table_matches: tuple[str, ...]


def _words(value: str) -> set[str]:
    return {token.casefold() for token in _WORD.findall(value)}


def _identifier_parts(value: str) -> set[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return _words(spaced.replace("_", " ")) | {value.casefold()}


def _preferred_identifiers(evidence: str) -> set[str]:
    result = {
        token.casefold()
        for token in _WORD.findall(evidence)
        if "_" in token or re.search(r"[a-z][A-Z]", token)
    }
    result.update(
        token.casefold()
        for token in re.findall(r"`([^`]+)`", evidence)
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_ ]*", token)
    )
    for match in re.finditer(
        r"\brefers?\s+to\s+(?:the\s+)?([A-Za-z][A-Za-z0-9_]*)",
        evidence,
        re.I,
    ):
        result.add(match.group(1).casefold())
    return result


def _identifier_in_text(identifier: str, text: str) -> bool:
    return bool(
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(identifier)}(?![A-Za-z0-9_])",
            text,
            re.I,
        )
    )


def _date_tokens(value: str) -> set[tuple[int, int, int]]:
    return {
        (int(year), int(month), int(day))
        for year, month, day in _DATE_TOKEN.findall(value)
    }


def _year_span_tokens(value: str) -> set[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    for raw_start, raw_end in _YEAR_SPAN_TOKEN.findall(value):
        start = int(raw_start)
        end = int(raw_end)
        if len(raw_end) == 2:
            end = start - (start % 100) + end
            if end < start:
                end += 100
        result.add((start, end))
    return result


def _percent_adjustment_is_grounded(raw: str, context: str) -> bool:
    try:
        factor = Decimal(raw)
    except InvalidOperation:
        return False
    for match in _PERCENT_TOKEN.finditer(context):
        percentage = Decimal(match.group(1)) / Decimal(100)
        directions: list[tuple[int, str]] = []
        for kind, pattern in (
            ("positive", _POSITIVE_PERCENT_ADJUSTMENT),
            ("negative", _NEGATIVE_PERCENT_ADJUSTMENT),
        ):
            for direction in pattern.finditer(context):
                if direction.end() <= match.start():
                    distance = match.start() - direction.end()
                elif match.end() <= direction.start():
                    distance = direction.start() - match.end()
                else:
                    distance = 0
                if distance <= 40:
                    directions.append((distance, kind))
        if not directions:
            continue
        nearest_distance = min(distance for distance, _ in directions)
        nearest_kinds = {
            kind for distance, kind in directions if distance == nearest_distance
        }
        if nearest_kinds == {"positive"} and factor == Decimal(1) + percentage:
            return True
        if nearest_kinds == {"negative"} and factor == Decimal(1) - percentage:
            return True
    return False


def _literal_is_grounded(value: object, context: str) -> bool:
    raw = str(value).strip().strip("'").strip('"').casefold()
    if not raw:
        return True
    normalized_context = " ".join(context.casefold().split())
    if raw in normalized_context:
        return True
    compact = re.sub(r"[^a-z0-9]", "", raw)
    compact_context = re.sub(r"[^a-z0-9]", "", normalized_context)
    if compact and compact in compact_context:
        return True
    raw_dates = _date_tokens(raw)
    if raw_dates and raw_dates.intersection(_date_tokens(normalized_context)):
        return True
    raw_year_spans = _year_span_tokens(raw)
    if raw_year_spans and raw_year_spans.intersection(
        _year_span_tokens(normalized_context)
    ):
        return True
    return _percent_adjustment_is_grounded(raw, normalized_context)


def _ranking_requested(question: str) -> bool:
    if _words(question) & _RANKING_WORDS:
        return True
    return bool(
        re.search(
            r"\bat least\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
            question,
            re.I,
        )
    )


def _projected_columns(tree: exp.Expression) -> tuple[str, ...]:
    select = tree.find(exp.Select)
    if select is None:
        return ()
    result: list[str] = []
    for expression in select.expressions:
        if isinstance(expression, exp.Column):
            result.append(expression.name)
        elif isinstance(expression.this, exp.Column):
            result.append(expression.this.name)
        elif expression.alias:
            result.append(expression.alias)
        else:
            result.append(expression.key)
    return tuple(result)


def _filter_predicates(tree: exp.Expression):
    kinds = (
        exp.EQ,
        exp.NEQ,
        exp.GT,
        exp.GTE,
        exp.LT,
        exp.LTE,
        exp.Like,
        exp.ILike,
        exp.Between,
        exp.In,
    )
    for node in tree.walk():
        if not isinstance(node, kinds):
            continue
        parent = node.parent
        nested = False
        in_filter = False
        while parent is not None:
            if isinstance(parent, kinds):
                nested = True
                break
            if isinstance(parent, (exp.Where, exp.Having)):
                in_filter = True
            parent = parent.parent
        if nested or not in_filter:
            continue
        yield (
            tuple(column.name for column in node.find_all(exp.Column)),
            tuple(literal.this for literal in node.find_all(exp.Literal)),
        )


def assess_candidate(
    question: str,
    provided_context: str,
    sql: str,
    dialect: str,
) -> CandidateAssessment:
    read_dialect = "postgres" if dialect in {"postgres", "postgresql"} else "mysql"
    tree = sqlglot.parse_one(sql, read=read_dialect)
    context = f"{question}\n{provided_context}"
    context_words = _words(context)
    preferred = _preferred_identifiers(provided_context)
    unsupported_literals: list[str] = []
    weak_columns: list[str] = []
    for columns, literals in _filter_predicates(tree):
        unsupported_literals.extend(
            str(literal)
            for literal in literals
            if not _literal_is_grounded(literal, context)
        )
        for column in columns:
            parts = _identifier_parts(column)
            informative = {part for part in parts if len(part) >= 3}
            if not _identifier_in_text(column, context) and (
                "_" in column
                or re.search(r"[a-z][A-Z]", column)
                or (informative and not informative.intersection(context_words))
            ):
                weak_columns.append(column)

    projections = _projected_columns(tree)
    projection_matches = tuple(
        column for column in projections if column.casefold() in preferred
    )
    tables = tuple(table.name for table in tree.find_all(exp.Table))
    dotted_table_hints = {
        match.group(1).casefold().rstrip("s")
        for match in re.finditer(
            r"\b([A-Za-z][A-Za-z0-9_]*)\.[A-Za-z][A-Za-z0-9_]*",
            provided_context,
        )
    }
    table_matches = tuple(
        table for table in tables if table.casefold().rstrip("s") in dotted_table_hints
    )
    unrequested_limit = tree.find(exp.Limit) is not None and not _ranking_requested(
        question
    )
    set_operations = sum(
        isinstance(node, (exp.Union, exp.Intersect, exp.Except)) for node in tree.walk()
    )
    joins = sum(isinstance(node, exp.Join) for node in tree.walk())
    table_count = len(tables)
    aggregate_count = sum(isinstance(node, exp.AggFunc) for node in tree.walk())
    penalty = (
        4.0 * len(unsupported_literals)
        + 1.5 * len(weak_columns)
        + (4.0 if unrequested_limit else 0.0)
        + 2.5 * set_operations
        + 0.20 * joins
        + 0.05 * max(0, table_count - 1)
        - 1.25 * len(projection_matches)
        - 0.75 * len(table_matches)
    )
    return CandidateAssessment(
        penalty=round(penalty, 6),
        unsupported_literals=tuple(unsupported_literals),
        weakly_grounded_predicates=tuple(weak_columns),
        unrequested_limit=unrequested_limit,
        set_operations=set_operations,
        join_count=joins,
        table_count=table_count,
        aggregate_count=aggregate_count,
        evidence_projection_matches=projection_matches,
        evidence_table_matches=table_matches,
    )


def _schema_table_columns(schema_info: Any) -> dict[str, set[str]]:
    tables = getattr(schema_info, "tables", None)
    if not isinstance(tables, dict) or not tables:
        raise ValueError("Schema information is unavailable")

    result: dict[str, set[str]] = {}
    for table_key, table in tables.items():
        table_name = str(getattr(table, "name", None) or table_key).casefold()
        columns = getattr(table, "columns", None)
        if not isinstance(columns, dict):
            raise ValueError(f"Schema columns are unavailable for {table_name}")
        result[table_name] = {
            str(getattr(column, "name", None) or column_key).casefold()
            for column_key, column in columns.items()
        }
    return result


def _columns_in_clause(clause: exp.Expression | None) -> set[str]:
    if clause is None:
        return set()
    return {column.name.casefold() for column in clause.find_all(exp.Column)}


def _co_located_join_opportunity(
    schema_info: Any,
    sql: str,
    dialect: str,
) -> bool:
    """Detect a direct join whose answer columns all live on one source table."""
    read_dialect = "postgres" if dialect in {"postgres", "postgresql"} else "mysql"
    tree = sqlglot.parse_one(sql, read=read_dialect)
    selects = list(tree.find_all(exp.Select))
    if len(selects) != 1 or tree.find(exp.Subquery) is not None:
        return False

    select = selects[0]
    joins = list(select.args.get("joins") or ())
    if not joins or any(not isinstance(join.this, exp.Table) for join in joins):
        return False

    referenced_tables = [table.name.casefold() for table in tree.find_all(exp.Table)]
    if len(referenced_tables) < 2:
        return False

    schema_columns = _schema_table_columns(schema_info)
    referenced_schema_columns: list[set[str]] = []
    for table_name in referenced_tables:
        if table_name not in schema_columns:
            raise ValueError(f"Referenced table {table_name} is absent from schema")
        referenced_schema_columns.append(schema_columns[table_name])

    projected_columns: set[str] = set()
    for projection in select.expressions:
        projected_columns.update(_columns_in_clause(projection))
    if not projected_columns:
        return False

    answer_columns = set(projected_columns)
    for clause_name in ("where", "having", "group", "order"):
        answer_columns.update(_columns_in_clause(select.args.get(clause_name)))
    for join in joins:
        on_clause = join.args.get("on")
        if on_clause is None:
            continue
        for predicate in on_clause.walk():
            if isinstance(predicate, exp.Predicate) and predicate.find(exp.Literal):
                answer_columns.update(_columns_in_clause(predicate))

    matching_tables = sum(
        answer_columns.issubset(columns) for columns in referenced_schema_columns
    )
    return matching_tables == 1


def alternate_generation_reasons(
    *,
    question: str,
    provided_context: str,
    primary_sql: str,
    dialect: str,
    schema_info: Any,
) -> tuple[str, ...]:
    """Return deterministic reasons to spend the one alternate-generation call.

    Assessment uncertainty deliberately fails open: preserving the alternate is
    safer than silently removing a candidate on a parser or schema edge case.
    """
    try:
        primary = assess_candidate(question, provided_context, primary_sql, dialect)
    except Exception:
        return ("assessment-error",)

    reasons: list[str] = []
    if primary.unsupported_literals:
        reasons.append("unsupported-literals")
    if primary.weakly_grounded_predicates and (
        primary.penalty <= SELECTIVE_WEAK_PREDICATE_MAX_PENALTY
        or primary.aggregate_count >= SELECTIVE_WEAK_COMPLEX_AGGREGATE_MIN_COUNT
    ):
        reasons.append("weakly-grounded-predicates")
    if primary.unrequested_limit:
        reasons.append("unrequested-limit")
    try:
        _schema_table_columns(schema_info)
        if (
            primary.penalty >= SELECTIVE_COLOCATED_MIN_PENALTY
            and _co_located_join_opportunity(schema_info, primary_sql, dialect)
        ):
            reasons.append("co-located-join-opportunity")
    except Exception:
        reasons.append("schema-assessment-error")
    return tuple(reasons)


def select_candidate(
    *,
    question: str,
    provided_context: str,
    primary_sql: str,
    alternate_sql: str,
    dialect: str,
) -> tuple[str, dict[str, Any]]:
    primary = assess_candidate(question, provided_context, primary_sql, dialect)
    alternate = assess_candidate(question, provided_context, alternate_sql, dialect)
    aggregation_shape_matches = bool(primary.aggregate_count) == bool(
        alternate.aggregate_count
    )
    selected = (
        "primary"
        if not aggregation_shape_matches or primary.penalty <= alternate.penalty
        else "alternate"
    )
    return selected, {
        "version": DUAL_CANDIDATE_SELECTOR_VERSION,
        "status": "selected",
        "selected": selected,
        "aggregation_shape_matches": aggregation_shape_matches,
        "primary": asdict(primary),
        "alternate": asdict(alternate),
        "primary_sql_sha256": hashlib.sha256(primary_sql.encode()).hexdigest(),
        "alternate_sql_sha256": hashlib.sha256(alternate_sql.encode()).hexdigest(),
    }


def build_alternate_prompt(ctx: Any) -> tuple[str, str]:
    question = ctx.refined_question or ctx.question
    if ctx.conversation_context:
        question = f"{ctx.conversation_context}\nCurrent question: {question}"
    prompt = (
        f"Dialect: {ctx.db_type}\n\n"
        f"Schema:\n{ctx.schema_formatted}"
        f"{format_query_grounding_block(getattr(ctx, 'query_grounding_block', ''))}\n\n"
        f"Question:\n{question}"
        f"{format_provided_context_block(ctx.provided_context)}"
        f"{format_matched_database_values_block(ctx.matched_database_values)}"
    )
    return ALTERNATE_SYSTEM_PROMPT, prompt


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
        raise ValueError("Alternate response does not begin with SELECT or WITH")
    return text


def generate_and_select(ctx: Any, llm_manager: Any) -> Any:
    """Selectively generate one alternate and select without database feedback."""
    primary_sql = ctx.sql or ""
    diagnostics: dict[str, Any] = {
        "version": DUAL_CANDIDATE_SELECTOR_VERSION,
        "status": "primary_kept",
        "selected": "primary",
        "execution_feedback": False,
    }
    question = ctx.refined_question or ctx.question
    reasons = alternate_generation_reasons(
        question=question,
        provided_context=ctx.provided_context,
        primary_sql=primary_sql,
        dialect=ctx.db_type,
        schema_info=getattr(ctx, "schema_info", None),
    )
    gate_diagnostics: dict[str, Any] = {
        "alternate_generation_gate_version": SELECTIVE_ALTERNATE_GATE_VERSION,
        "alternate_generation_decision": "generate" if reasons else "skip",
        "alternate_generation_reasons": reasons,
        "alternate_generation_skipped": not reasons,
    }
    diagnostics.update(gate_diagnostics)
    try:
        diagnostics["primary"] = asdict(
            assess_candidate(question, ctx.provided_context, primary_sql, ctx.db_type)
        )
    except Exception as exc:
        diagnostics["primary_assessment_error_kind"] = type(exc).__name__

    if not reasons:
        diagnostics["status"] = "primary_sufficient"
        diagnostics["primary_sql_sha256"] = hashlib.sha256(
            primary_sql.encode()
        ).hexdigest()
        ctx.dual_candidate_selection = diagnostics
        return ctx

    try:
        system, prompt = build_alternate_prompt(ctx)
        started = perf_counter()
        response = llm_manager.query(
            system_message=system,
            user_query=prompt,
            max_tokens=ALTERNATE_GENERATION_MAX_TOKENS,
            temperature=0.0,
            purpose=ALTERNATE_GENERATION_PURPOSE,
        )
        latency_ms = (perf_counter() - started) * 1000
        usage = response.get("usage") or {}
        ctx.add_llm_call(
            prompt=prompt,
            response=str(response.get("text") or ""),
            tokens=int(usage.get("total_tokens") or 0),
            latency_ms=latency_ms,
            model=str(response.get("model") or "unknown"),
            phase="alternate_generate",
        )
        alternate_sql = extract_sql(str(response.get("text") or ""))
        validation = validate_sql_for_ask(
            alternate_sql,
            enforce_result_limit=False,
        )
        if not validation.get("is_valid"):
            diagnostics["status"] = "alternate_invalid"
            diagnostics["alternate_issues"] = list(validation.get("issues") or ())
            ctx.dual_candidate_selection = diagnostics
            return ctx
        alternate_sql = validation.get("validated_sql") or alternate_sql
        selected, diagnostics = select_candidate(
            question=question,
            provided_context=ctx.provided_context,
            primary_sql=primary_sql,
            alternate_sql=alternate_sql,
            dialect=ctx.db_type,
        )
        diagnostics["execution_feedback"] = False
        diagnostics.update(gate_diagnostics)
        if selected == "alternate":
            ctx.generated_sql = alternate_sql
            ctx.sql = alternate_sql
            ctx.sql_explanation = (
                "Selected the candidate with stronger source grounding."
            )
        ctx.dual_candidate_selection = diagnostics
        return ctx
    except Exception as exc:
        diagnostics["status"] = "alternate_error"
        diagnostics["error_kind"] = type(exc).__name__
        diagnostics["error"] = str(exc)
        ctx.dual_candidate_selection = diagnostics
        return ctx


__all__ = [
    "ALTERNATE_GENERATION_MAX_TOKENS",
    "ALTERNATE_GENERATION_PURPOSE",
    "DUAL_CANDIDATE_SELECTOR_VERSION",
    "LITERAL_GROUNDING_VERSION",
    "SELECTIVE_ALTERNATE_GATE_VERSION",
    "SELECTIVE_WEAK_PREDICATE_MAX_PENALTY",
    "SELECTIVE_WEAK_COMPLEX_AGGREGATE_MIN_COUNT",
    "SELECTIVE_COLOCATED_MIN_PENALTY",
    "alternate_generation_reasons",
    "assess_candidate",
    "generate_and_select",
    "select_candidate",
]
