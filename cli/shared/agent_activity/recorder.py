"""Record what an agent action does while it does it.

An action (a deep analysis, an Ask, a classification, an attention check, a daily
snapshot) opens `action(...)`, which sets a context variable for its duration. The AI
clients and the database connection helpers check that variable: every AI call and
every statement run on the user's database inside the action is added to it, and the
action is written to the audit store as one entry when it ends.

Statement text is stored with its literals replaced by `?`, and statement parameters are
never stored, so sampled values and row data stay out of the log.
"""

from __future__ import annotations

import logging
import math
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

JEV_MODEL = "jev-1.13.0"
# Keyservice charges Jev for input tokens only, at this price per million.
JEV_INPUT_MICROUSD_PER_MILLION = 42_000
MAX_STATEMENTS = 200
MAX_SQL_CHARS = 20_000

# What each kind of AI call sends, by operation (the cataloged purpose).
_ASK_KINDS = ["your question", "schema", "column values matching the question"]
_ANALYZE_KINDS = ["SQL text with sampled parameter values", "query plan", "schema"]
DATA_KINDS: dict[str, list[str]] = {
    "monitor_analyze": _ANALYZE_KINDS,
    "analyze_query": _ANALYZE_KINDS,
    "analyze_index": _ANALYZE_KINDS,
    "analyze_rewrite": _ANALYZE_KINDS,
    "analyze_cache": _ANALYZE_KINDS,
    "monitor_report": ["SQL text", "query statistics", "schema", "health checks"],
    "monitor_attention": ["SQL text", "query statistics"],
    "query_registry_analysis": ["SQL text", "schema"],
    "sql_generation": _ASK_KINDS,
    "sql_generation_alternate": _ASK_KINDS,
    "sql_recovery": _ASK_KINDS,
    "sql_refinement": _ASK_KINDS,
    "sql_validation_repair": _ASK_KINDS,
    "clarification": ["your question", "schema"],
    "correction_intent_routing": ["your question"],
    "example_questions": ["schema"],
}

_CURRENT: ContextVar["Activity | None"] = ContextVar("rdst_agent_activity", default=None)


def ai_data_kinds(operation: str | None) -> list[str]:
    return list(DATA_KINDS.get(operation or "", ["prompt text"]))


def jev_cost_usd(tokens_in: int | None) -> float | None:
    if tokens_in is None:
        return None
    return max(1, math.ceil(int(tokens_in) * JEV_INPUT_MICROUSD_PER_MILLION / 1_000_000)) / 1e6


def judge_summary(outcome: str, items: int, dry_run: bool) -> tuple[str, str]:
    """The entry outcome and one-line summary of one attention check."""
    if outcome == "filed":
        return "done", (f"{items} {'item' if items == 1 else 'items'} filed" if items
                        else "Nothing new to file")
    return {
        "dry_run": ("done", "Dry run, nothing filed"),
        "empty": ("skipped", "Nothing to check"),
        "daily_cap": ("skipped", "Daily limit reached"),
        "backoff": ("skipped", "Waiting to retry after an error"),
        "local_only": ("skipped", "Local monitoring only, AI checks are off"),
        "allowance": ("skipped", "AI trial limit reached, retrying later"),
        "error": ("failed", "The AI check didn't complete"),
        "filing_error": ("failed", "Filing the result failed"),
    }.get(outcome, ("done", outcome.replace("_", " ").capitalize()))


_MARKER = re.compile(r"^\s*/\*\s*rdst[:/][^*]*\*/\s*", re.IGNORECASE)
_LITERAL = re.compile(r"'(?:''|[^'])*'|(?<![\w$.])-?\d+(?:\.\d+)?(?![\w$])")
_PG_TIMEOUT = re.compile(
    r"^\s*SET\s+(?:LOCAL\s+|SESSION\s+)?statement_timeout\s*(?:=|TO)\s*"
    r"(%s|'?(\d+)\s*(ms|s)?'?)", re.IGNORECASE)
_MYSQL_TIMEOUT = re.compile(
    r"^\s*SET\s+(?:SESSION\s+)?max_execution_time\s*=\s*(%s|(\d+))", re.IGNORECASE)
_HINT_TIMEOUT = re.compile(r"MAX_EXECUTION_TIME\s*\(\s*(\d+)\s*\)", re.IGNORECASE)
_OPTIONS_TIMEOUT = re.compile(r"statement_timeout\s*=\s*(\d+)")
_EXPLAIN_ANALYZE = re.compile(r"^EXPLAIN\s*(?:\(\s*ANALYZE(?:\s+TRUE)?\s*[,)]|ANALYZE\b)",
                              re.IGNORECASE)
_SESSION = re.compile(
    r"^(?:SET|SHOW|RESET|BEGIN|COMMIT|ROLLBACK|START\s+TRANSACTION)\b|"
    r"^SELECT\s+(?:current_setting|pg_backend_pid|version|connection_id|1\b|database\(\))",
    re.IGNORECASE)
# Reads that fetch real values for a query's placeholders: a live session's statement
# text, a table's common values, or a few distinct values from the column.
_SAMPLE = re.compile(
    r"^SELECT\s+query\b.*\bpg_stat_activity\b|events_statements_history|QUERY_SAMPLE_TEXT|"
    r"most_common_vals|^SELECT\s+(?:/\*\+[^*]*\*/\s*)?DISTINCT\b", re.IGNORECASE | re.DOTALL)
_CATALOG = re.compile(
    r"information_schema|pg_catalog|\bpg_(?:class|index|indexes|namespace|attribute|"
    r"stat\w*|settings|extension|available_extensions|constraint|tables|database|"
    r"total_relation_size|relation_size|size_pretty)\b|performance_schema|\bsys\.",
    re.IGNORECASE)


def statement_kind(text: str) -> str:
    body = text.lstrip()
    if body[:7].upper() == "EXPLAIN":
        return "explain_analyze" if _EXPLAIN_ANALYZE.match(body) else "explain"
    if "hypopg" in body.lower():
        return "hypothetical_index"
    if _SESSION.match(body):
        return "session"
    if _SAMPLE.search(body):
        return "sample"
    if _CATALOG.search(body):
        return "catalog"
    return "read"


def placeholder_text(sql: str) -> str:
    """Statement text for the log: marker comment dropped, literals replaced by `?`."""
    text = _MARKER.sub("", sql or "")
    text = " ".join(_LITERAL.sub("?", text).split())
    return text[:MAX_SQL_CHARS]


def _timeout_ms(match: re.Match | None, params: Any) -> int | None:
    if match is None:
        return None
    if match.group(1) == "%s":
        value = params[0] if isinstance(params, (list, tuple)) and params else None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    try:
        number = int(match.group(2))
    except (TypeError, ValueError, IndexError):
        return None
    unit = match.group(3) if match.re is _PG_TIMEOUT else "ms"
    return number * 1000 if (unit or "").lower() == "s" else number


class Activity:
    """One agent action in progress: what it ran and which AI calls it made."""

    def __init__(self, action: str, target: str | None, *, subject: str = "",
                 run_ref: str | None = None, read_kind: str = "read",
                 details: dict[str, Any] | None = None, default_outcome: str = "done",
                 clock: Callable[[], float] = time.time) -> None:
        self.action = action
        self.target = target or ""
        self.subject = subject
        self.run_ref = run_ref
        self.read_kind = read_kind
        self.details: dict[str, Any] = dict(details or {})
        self.clock = clock
        self.started = int(clock())
        self.outcome: str | None = None
        self.default_outcome = default_outcome
        self.summary = ""
        self.decision: str | None = None
        self.items_filed = 0
        # Written even when it ran nothing and called no AI (an attention check that
        # found nothing new is still a check).
        self.keep = False
        self._statements: dict[tuple[str, str], dict[str, Any]] = {}
        self._dropped_statements = 0
        self._ai_calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def describe(self, **fields: Any) -> None:
        for name in ("subject", "summary", "decision", "outcome", "items_filed", "keep",
                     "target"):
            if name in fields and fields[name] is not None:
                setattr(self, name, fields.pop(name))
        self.details.update({key: value for key, value in fields.items() if value is not None})

    def statement(self, sql: str, *, kind: str | None = None, duration_ms: float | None = None,
                  error: str | None = None, timeout_ms: int | None = None,
                  executions: int = 1) -> None:
        raw = _MARKER.sub("", sql or "")
        kind = kind or statement_kind(raw)
        if kind == "read":
            kind = self.read_kind
        # A session timeout is configuration, not data: its value stays readable.
        if kind == "session" and "timeout" in raw.lower():
            text = " ".join(raw.split())[:MAX_SQL_CHARS]
            if timeout_ms is not None:
                text = text.replace("%s", str(timeout_ms))
        else:
            text = placeholder_text(raw)
        with self._lock:
            key = (text, kind)
            row = self._statements.get(key)
            if row is None:
                if len(self._statements) >= MAX_STATEMENTS:
                    self._dropped_statements += executions
                    return
                row = self._statements[key] = {
                    "sql": text, "kind": kind, "executions": 0, "duration_ms": None,
                    "max_ms": None, "timeout_ms": None, "error": None}
            row["executions"] += executions
            if duration_ms is not None:
                row["duration_ms"] = (row["duration_ms"] or 0) + duration_ms
                row["max_ms"] = max(row["max_ms"] or 0, duration_ms / max(1, executions))
            if timeout_ms is not None:
                row["timeout_ms"] = timeout_ms
            if error:
                row["error"] = error[:500]

    def ai_call(self, call: dict[str, Any]) -> None:
        with self._lock:
            self._ai_calls.append({"ts": int(self.clock()), **call})

    @property
    def statements(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._statements.values()]

    @property
    def ai_calls(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(call) for call in self._ai_calls]

    def entry(self) -> dict[str, Any]:
        details = dict(self.details)
        if self._dropped_statements:
            details["statements_not_listed"] = self._dropped_statements
        return {"ts": self.started, "finished_at": int(self.clock()), "target": self.target,
                "action": self.action, "subject": self.subject,
                "outcome": self.outcome or self._implied_outcome(), "summary": self.summary,
                "decision": self.decision, "details": details, "run_ref": self.run_ref,
                "items_filed": self.items_filed}

    def _implied_outcome(self) -> str:
        calls = self._ai_calls
        if calls and all(call.get("error") for call in calls):
            return "failed"
        return self.default_outcome

    def empty(self) -> bool:
        return not self.keep and not self._statements and not self._ai_calls

    def finish(self, store: Any = None) -> int | None:
        if self.empty():
            return None
        try:
            from shared.agent_activity.store import AgentActivityStore

            return (store or AgentActivityStore()).append(
                self.entry(), self.statements, self.ai_calls)
        except Exception:
            logger.warning("Agent activity entry not recorded", exc_info=True)
            return None


def current() -> Activity | None:
    return _CURRENT.get()


def describe(**fields: Any) -> None:
    """Add subject, summary, decision, outcome or details to the running action, if any."""
    activity = _CURRENT.get()
    if activity is not None:
        activity.describe(**fields)


@contextmanager
def action(name: str, target: str | None, *, subject: str = "", run_ref: str | None = None,
           read_kind: str = "read", keep: bool = False, store: Any = None,
           default_outcome: str = "done", **details: Any) -> Iterator[Activity]:
    """Open one agent action; everything recorded inside it becomes one audit entry."""
    activity = Activity(name, target, subject=subject, run_ref=run_ref, read_kind=read_kind,
                        details=details, default_outcome=default_outcome)
    activity.keep = keep
    token = _CURRENT.set(activity)
    try:
        yield activity
    except BaseException as exc:
        if activity.outcome is None:
            import asyncio

            activity.outcome = ("cancelled" if isinstance(exc, asyncio.CancelledError)
                                else "failed")
            if not activity.summary and not isinstance(exc, asyncio.CancelledError):
                activity.summary = str(exc).splitlines()[0][:300] if str(exc) else \
                    type(exc).__name__
        raise
    finally:
        try:
            _CURRENT.reset(token)
        except ValueError:
            # An async generator can finish in another context; the variable dies with it.
            pass
        activity.finish(store)


def record_entry(entry: dict[str, Any], statements: list[dict[str, Any]] | None = None,
                 ai_calls: list[dict[str, Any]] | None = None) -> int | None:
    """Write an action that was summarized after it ran (a benchmark)."""
    try:
        from shared.agent_activity.store import AgentActivityStore

        return AgentActivityStore().append({"ts": int(time.time()), **entry},
                                           statements or [], ai_calls or [])
    except Exception:
        logger.warning("Agent activity entry not recorded", exc_info=True)
        return None


# AI calls

def record_ai_call(**call: Any) -> None:
    activity = _CURRENT.get()
    if activity is not None:
        activity.ai_call(call)


def record_llm_call(provider: str, model: str, purpose: str | None, operation: str | None,
                    started: float, *, usage: dict[str, Any] | None = None,
                    raw: dict[str, Any] | None = None, error: str | None = None) -> None:
    """One LLMManager call. Hosted calls carry their charged cost; Claude calls are
    priced from the published per-token rates."""
    if _CURRENT.get() is None:
        return
    usage = usage or {}
    tokens_in = usage.get("prompt_tokens")
    tokens_out = usage.get("completion_tokens")
    cost, estimated = None, False
    raw_usage = (raw or {}).get("usage") if isinstance((raw or {}).get("usage"), dict) else {}
    if isinstance(raw_usage.get("cost_microusd"), (int, float)):
        cost = float(raw_usage["cost_microusd"]) / 1e6
    elif provider == "claude" and tokens_in is not None:
        try:
            from features.analyze.functions.llm_analysis import estimate_cost

            cost, estimated = estimate_cost(int(tokens_in), int(tokens_out or 0), model), True
        except Exception:
            cost = None
    op = operation or purpose or "other"
    record_ai_call(provider=provider, model=model, operation=op, purpose=purpose or "other",
                   data_kinds=ai_data_kinds(op if op in DATA_KINDS else purpose),
                   tokens_in=tokens_in, tokens_out=tokens_out, cost_usd=cost,
                   cost_estimated=estimated,
                   duration_ms=round((time.monotonic() - started) * 1000, 1), error=error)


def record_jev_call(path: str, payload: dict[str, Any], body: dict[str, Any] | None,
                    started: float, *, error: str | None = None) -> None:
    if _CURRENT.get() is None:
        return
    usage = (body or {}).get("usage") if isinstance((body or {}).get("usage"), dict) else {}
    tokens_in = usage.get("input_tokens")
    operation = str((payload.get("attribution") or {}).get("operation") or path.rsplit("/", 1)[-1])
    record_ai_call(provider="readyset-jev", model=str((body or {}).get("model") or JEV_MODEL),
                   operation=operation,
                   purpose="attention_check" if operation == "monitor_attention"
                   else "classification",
                   data_kinds=ai_data_kinds(operation), tokens_in=tokens_in,
                   tokens_out=usage.get("output_tokens"), cost_usd=jev_cost_usd(tokens_in),
                   cost_estimated=True, request_id=payload.get("request_id"),
                   duration_ms=round((time.monotonic() - started) * 1000, 1), error=error)


# Statements on the user's database

def _record(conn: Any, query: Any, params: Any, run: Callable[[], Any], cursor: Any) -> Any:
    activity = getattr(conn, "_rdst_activity", None)
    if activity is None:
        return run()
    try:
        if hasattr(query, "as_string"):
            text = query.as_string(cursor)
        elif isinstance(query, (bytes, bytearray)):
            text = bytes(query).decode("utf-8", "replace")
        else:
            text = str(query)
    except Exception:
        text = "(statement text unavailable)"
    started = time.perf_counter()
    error = None
    try:
        return run()
    except Exception as exc:
        error = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
        raise
    finally:
        try:
            body = _MARKER.sub("", text)
            timeout = _timeout_ms(_PG_TIMEOUT.match(body) or _MYSQL_TIMEOUT.match(body), params)
            if timeout is not None:
                conn._rdst_timeout_ms = timeout
            hint = _HINT_TIMEOUT.search(body)
            activity.statement(
                text, duration_ms=round((time.perf_counter() - started) * 1000, 2), error=error,
                timeout_ms=int(hint.group(1)) if hint else getattr(conn, "_rdst_timeout_ms", None))
        except Exception:
            logger.debug("Statement not recorded", exc_info=True)


_PG_CURSORS: dict[type, type] = {}
_PG_CONNECTION: list[type] = []


def _pg_cursor_class(base: type) -> type:
    cls = _PG_CURSORS.get(base)
    if cls is None:
        class RecordingCursor(base):  # type: ignore[misc, valid-type]
            def execute(self, query, vars=None):
                return _record(self.connection, query, vars,
                               lambda: base.execute(self, query, vars), self)

            def executemany(self, query, vars_list):
                return _record(self.connection, query, None,
                               lambda: base.executemany(self, query, vars_list), self)

        cls = _PG_CURSORS[base] = RecordingCursor
    return cls


def _pg_connection_class() -> type | None:
    if _PG_CONNECTION:
        return _PG_CONNECTION[0]
    try:
        import psycopg2.extensions as extensions
    except ImportError:
        return None
    base = getattr(extensions, "connection", None)
    if not isinstance(base, type):
        return None

    class RecordingConnection(base):  # type: ignore[misc, valid-type]
        def cursor(self, *args, **kwargs):
            factory = kwargs.pop("cursor_factory", None)
            if len(args) > 1:
                factory = args[1]
                args = args[:1] + args[2:]
            chosen = factory or self.cursor_factory or extensions.cursor
            return super().cursor(*args, cursor_factory=_pg_cursor_class(chosen), **kwargs)

    _PG_CONNECTION.append(RecordingConnection)
    return RecordingConnection


def pg_connection_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """psycopg2.connect kwargs that record the connection's statements into the running
    action. Unchanged when no action is running."""
    activity = _CURRENT.get()
    if activity is None or "connection_factory" in kwargs:
        return kwargs
    cls = _pg_connection_class()
    if cls is None:
        return kwargs
    options = _OPTIONS_TIMEOUT.search(str(kwargs.get("options") or ""))
    timeout = int(options.group(1)) if options else None

    def factory(*args: Any, **factory_kwargs: Any) -> Any:
        conn = cls(*args, **factory_kwargs)
        conn._rdst_activity = activity
        conn._rdst_timeout_ms = timeout
        return conn

    return {**kwargs, "connection_factory": factory}


def instrument_mysql(conn: Any) -> Any:
    """Record a PyMySQL connection's statements into the running action, if any."""
    activity = _CURRENT.get()
    if activity is None or conn is None:
        return conn
    try:
        original = conn.cursor
        conn._rdst_activity = activity
        conn._rdst_timeout_ms = None

        def cursor(*args: Any, **kwargs: Any) -> Any:
            cur = original(*args, **kwargs)
            execute = cur.execute
            cur.execute = lambda query, args=None: _record(
                conn, query, args, lambda: execute(query, args), cur)
            return cur

        conn.cursor = cursor
    except Exception:
        logger.debug("MySQL connection not instrumented", exc_info=True)
    return conn


def recorded_snapshot(run_report: Callable[..., Any]) -> Callable[..., Any]:
    """Record each daily snapshot run (`run_report(target, trigger, ...)`) as one entry."""
    import functools

    @functools.wraps(run_report)
    async def wrapper(target: str, trigger: str, *args: Any, **kwargs: Any) -> Any:
        with action("daily_snapshot", target,
                    subject="Nightly" if trigger == "nightly" else "On request",
                    trigger=trigger) as activity:
            result = await run_report(target, trigger, *args, **kwargs)
            if isinstance(result, dict):
                report = result.get("report_json") if isinstance(
                    result.get("report_json"), dict) else {}
                if result.get("id") is not None:
                    activity.run_ref = f"report_run:{result['id']}"
                activity.describe(
                    outcome={"done": "done", "failed": "failed",
                             "cancelled": "cancelled"}.get(str(result.get("state"))),
                    summary="The written summary didn't finish; it retries shortly"
                    if report.get("narrative_error") else None,
                    report_id=result.get("id"), period_start=result.get("period_start"),
                    period_end=result.get("period_end"))
            return result

    return wrapper


def record_benchmark(target: str | None, event: Any, *, mode: str, lanes: list[str] | None,
                     duration_seconds: int, concurrency: int, interval_ms: int,
                     statement_timeout_ms: int | None) -> int | None:
    """One finished benchmark: each query's SQL with its execution count and timing."""
    statements = []
    names = []
    measured = []
    kind = getattr(event, "type", "")
    elapsed = float(getattr(event, "elapsed_seconds", 0) or 0)
    registry = None
    try:
        from shared.query_registry import QueryRegistry

        registry = QueryRegistry()
    except Exception:
        registry = None
    for stats in getattr(event, "queries", None) or []:
        names.append(stats.query_name)
        entry = None
        if registry is not None:
            try:
                entry = registry.get_query(stats.query_hash)
            except Exception:
                entry = None
        runs = int(stats.executions or 0)
        if not runs:
            continue
        measured.append(_benchmark_numbers(
            entry.hash if entry is not None else stats.query_hash, stats, elapsed))
        statements.append({
            "sql": placeholder_text(entry.sql) if entry is not None and entry.sql
            else f"(query {stats.query_name})",
            "kind": "benchmark", "executions": runs,
            "duration_ms": round(float(stats.avg_ms or 0) * runs, 2),
            "max_ms": stats.max_ms, "timeout_ms": statement_timeout_ms,
            "error": (f"{stats.timeouts} timed out" if stats.timeouts else None)
            or (stats.last_error[:300] if stats.failures and stats.last_error else None)})
    subject = ", ".join(names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
    shape = (f"{concurrency} at a time" if mode == "concurrency"
             else f"every {interval_ms} ms")
    return record_entry({
        "target": target or "", "action": "benchmark", "subject": subject,
        "outcome": "done" if kind == "complete" else "failed",
        "summary": getattr(event, "message", "") if kind == "error" else
        f"{getattr(event, 'total_executions', 0)} executions in "
        f"{round(getattr(event, 'elapsed_seconds', 0) or 0)} s",
        "decision": f"Ran read-only SELECTs {shape} for up to {duration_seconds} s on "
        + " and ".join(lanes or ["origin"]),
        "details": {"mode": mode, "lanes": lanes or ["origin"],
                    "statement_timeout_ms": statement_timeout_ms,
                    "duration_seconds": duration_seconds, "concurrency": concurrency,
                    "interval_ms": interval_ms, "elapsed_seconds": round(elapsed, 2),
                    "readyset_setup": getattr(event, "readyset_setup", None),
                    "queries": measured}}, statements, [])


def _lane_numbers(stats: Any, elapsed: float) -> dict[str, Any]:
    def value(name: str) -> Any:
        return stats.get(name) if isinstance(stats, dict) else getattr(stats, name, None)

    successes = int(value("successes") or 0)
    return {"executions": int(value("executions") or 0), "successes": successes,
            "failures": int(value("failures") or 0),
            "p50_ms": value("p50_ms"), "p95_ms": value("p95_ms"), "avg_ms": value("avg_ms"),
            "qps": round(successes / elapsed, 3) if elapsed > 0 else None}


def _benchmark_numbers(query_hash: str, stats: Any, elapsed: float) -> dict[str, Any]:
    """One query's measurement, kept so its detail can list past benchmarks."""
    numbers = {"hash": query_hash, "name": stats.query_name, **_lane_numbers(stats, elapsed)}
    readyset = (getattr(stats, "lanes", None) or {}).get("readyset")
    if isinstance(readyset, dict) and readyset.get("successes"):
        numbers["readyset"] = _lane_numbers(readyset, elapsed)
    return numbers
