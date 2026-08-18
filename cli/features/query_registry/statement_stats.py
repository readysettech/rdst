"""Two-phase statement-statistics sources for the discovery collector.

Engine-specific readers of cumulative statement counters, designed from
docs/architecture/rdst-query-observation-deep-research.md (Q1/Q2/Q3, Q9
rules 5-6). Phase A reads counters only (no query text); Phase B fetches
text solely for engine keys the caller has never resolved.

The engines are asymmetric and the code keeps that visible (Q2):
- PostgreSQL has no server-side change cursor, so every collection is a
  full sweep of pg_stat_statements(false); it is incremental in output
  only (CollectResult.incremental is False).
- MySQL's LAST_SEEN column supports genuine incremental fetches, and
  DIGEST_TEXT lives in the same table, so MySQL has no Phase B.

Every database interaction goes through an injected executor callable
`(sql, params) -> rows`, so the module is fully unit-testable and a later
wiring wave can adapt it onto the existing DataManager. Counter rows are
emitted as shared.query_registry.delta_processor.CounterRow, ready for
process_snapshot().
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional, Sequence

from shared.query_registry.delta_processor import (
    CounterRow,
    compute_mysql_epoch_id,
    compute_pg_epoch_id,
)

# Tunable constants, each with its rationale (research section 4: keep them in
# one module until Phase 0 measurements confirm or replace the defaults).

# Collector-session work_mem: keeps the pg_stat_statements tuplestore from spilling to disk while
# it holds the shared LWLock (the Q1 outage class).
PG_COLLECTOR_WORK_MEM = "64MB"
# Two statement_timeout budgets for two query classes (pganalyze precedent): 30 s for the Phase A
# counter sweep, 120 s for Phase B text retrieval.
PG_STATS_TIMEOUT_MS = 30_000
PG_TEXT_TIMEOUT_MS = 120_000
# Phase B batch size: bounds one ANY(...) parameter array so a large unknown-key backlog (e.g.
# first run against a full view) fetches text in several bounded queries.
PG_TEXT_FETCH_BATCH = 500
# The Uptime status variable has 1 s granularity, so now-minus-uptime jitters by about a second
# between collections; reuse the previous start estimate when the new one lands inside this band.
MYSQL_SERVER_START_TOLERANCE_S = 5.0
# One bounded activity snapshot per collection cycle recovers production parameter values
# (research Q7): row-capped, on its own short statement budget, and excluding RDST's own
# sessions. It is a point-in-time read, never a sampling loop, and never a QPS source.
ACTIVITY_SAMPLE_LIMIT = 200
ACTIVITY_SAMPLE_TIMEOUT_MS = 5_000
# performance_schema TIMER_WAIT columns are picoseconds; CounterRow times are milliseconds.
MYSQL_PICOSECONDS_PER_MS = 1e9

Executor = Callable[[str, Optional[Sequence[object]]], Sequence[Sequence[object]]]


@dataclass(frozen=True)
class CollectResult:
    """Outcome of one Phase A collection against one target.

    rows carry engine counters keyed by the serialized engine_key; texts maps
    engine_key to statement text when the engine returns it in the same sweep
    (MySQL DIGEST_TEXT); needs_text lists keys the caller must resolve through
    Phase B (PostgreSQL only). capabilities is the source_capabilities payload
    for collector_state (capacity and completeness signals, Q1). incremental
    tells the API/UI whether rows are changes-since-cursor or a full sweep.

    sample_texts maps engine_key to one real execution's literal-bearing text
    (MySQL QUERY_SAMPLE_TEXT) when the server provides it. Identity resolution
    must keep using texts: digest placeholders ('?') and sample literals
    (':pN') normalize to different hashes, so the sample only supplies
    observed parameter values, never identity.
    """

    rows: list[CounterRow]
    epoch_id: str
    capabilities: dict[str, object]
    next_cursor_state: Optional[dict[str, object]]
    needs_text: frozenset[str]
    texts: dict[str, str]
    incremental: bool
    sample_texts: dict[str, str] = field(default_factory=dict)


def pg_engine_key(userid: object, dbid: object, queryid: object, toplevel: str) -> str:
    """Serialize the full pg_stat_statements hash key (Q3): the view is keyed by the 4-tuple
    (userid, dbid, queryid, toplevel). queryid stays raw and signed; abs() would collide +n/-n.
    """
    return f"{userid}:{dbid}:{queryid}:{toplevel}"


def parse_pg_engine_key(key: str) -> tuple[int, int, int, str]:
    userid, dbid, queryid, toplevel = key.split(":")
    return int(userid), int(dbid), int(queryid), toplevel


def mysql_engine_key(schema_name: Optional[str], digest: str) -> str:
    """MySQL digest rows are keyed by (SCHEMA_NAME, DIGEST); a NULL schema serializes as ''."""
    return f"{schema_name or ''}:{digest}"


def _toplevel_flag(value: object) -> str:
    if isinstance(value, str):
        return "t" if value.lower() in ("t", "true") else "f"
    return "t" if value else "f"


def _to_epoch_seconds(value: object) -> float:
    """Normalize server-supplied timestamps (datetime, numeric, or ISO string) to unix seconds.
    Naive datetimes are treated as UTC: both collector sessions run in UTC.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, str):
        return _to_epoch_seconds(datetime.fromisoformat(value))
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise TypeError(f"cannot interpret timestamp value {value!r}") from exc


class StatementStatsSource(abc.ABC):
    """One engine's statement-statistics reader.

    session_setup_statements must run through the same executor (same
    transaction/session) before collect()'s queries; collect() passes them
    through the executor itself, and exposes them so a wiring layer that
    manages transactions explicitly can do the same.
    """

    session_setup_statements: tuple[str, ...] = ()

    @abc.abstractmethod
    def collect(
        self,
        executor: Executor,
        cursor_state: Optional[dict[str, object]] = None,
        known_text_keys: Iterable[str] = frozenset(),
    ) -> CollectResult:
        """Run Phase A: session setup, epoch/capability reads, and the counter sweep."""

    def activity_sample(self, executor: Executor) -> list[str]:
        """One bounded snapshot of the literal-bearing texts running right now.

        The rows recover observed parameter values for identities the counter
        lane already resolved; activity sampling misses fast statements and
        over-represents slow ones, so the snapshot is never admission
        evidence (research Q7). Engines without an activity view sample
        nothing.
        """
        return []


# Every statement this module issues carries the self-traffic marker: the
# engine's queryid jumble ignores comments while the stored first-seen text
# keeps them, so RDST's own observation queries are recognizable in
# pg_stat_statements text and never enter the Query Library.
RDST_OBSERVE_MARKER = "/*rdst:observe*/ "

PG_VERSION_SQL = RDST_OBSERVE_MARKER + "SELECT current_setting('server_version_num')::int"

# Epoch material (Q9 rule 5) and captured_at in one round trip. dealloc rides along as a capacity
# signal but is never part of the epoch identity (Q9 rule 6). The info view exists on PG 14+.
PG_EPOCH_SQL_14 = (
    RDST_OBSERVE_MARKER + "SELECT extract(epoch FROM now())::float8,"
    " extract(epoch FROM pg_postmaster_start_time())::float8,"
    " extract(epoch FROM i.stats_reset)::float8, i.dealloc"
    " FROM pg_stat_statements_info AS i"
)
PG_EPOCH_SQL_13 = (
    RDST_OBSERVE_MARKER + "SELECT extract(epoch FROM now())::float8,"
    " extract(epoch FROM pg_postmaster_start_time())::float8"
)

PG_SETTINGS_SQL = (
    RDST_OBSERVE_MARKER + "SELECT name, setting FROM pg_settings"
    " WHERE name IN ('pg_stat_statements.max', 'pg_stat_statements.track')"
)

# The activity snapshot reads only 'active' sessions: an idle session's query
# column shows its last completed statement, which can be arbitrarily stale
# evidence for "values running in production". RDST's own sessions are
# excluded by pid and by the connection lane's application_name prefix.
PG_ACTIVITY_SAMPLE_SQL = (
    RDST_OBSERVE_MARKER + "SELECT query FROM pg_stat_activity"
    " WHERE state = 'active'"
    " AND pid != pg_backend_pid()"
    " AND COALESCE(application_name, '') NOT LIKE 'rdst/%'"
    " AND datname = current_database()"
    f" LIMIT {ACTIVITY_SAMPLE_LIMIT}"
)


def pg_phase_a_sql(server_version_num: int) -> str:
    """Version-conditional Phase A SQL (Q1: three variants selected on server_version_num, never
    by probing columns). Counters only via pg_stat_statements(false), scoped to the current
    database's dbid; ranking and filtering happen in the store, so no SUM/ORDER BY/LIMIT/LIKE.
    stddev is selected so the SQL shape is final; the wiring wave surfaces it once the store
    carries its column.
    """
    if server_version_num < 130000:
        raise ValueError(
            f"pg_stat_statements source requires PostgreSQL 13+ "
            f"(server_version_num={server_version_num})"
        )
    columns = ["userid", "dbid", "queryid"]
    if server_version_num >= 140000:
        columns.append("toplevel")
    columns += [
        "calls",
        "rows",
        "total_exec_time",
        "mean_exec_time",
        "min_exec_time",
        "max_exec_time",
        "stddev_exec_time",
    ]
    if server_version_num >= 170000:
        columns += [
            "extract(epoch FROM stats_since)::float8 AS stats_since",
            "extract(epoch FROM minmax_stats_since)::float8 AS minmax_stats_since",
        ]
    return (
        RDST_OBSERVE_MARKER + "SELECT " + ", ".join(columns)
        + " FROM pg_stat_statements(false)"
        " WHERE dbid = (SELECT oid FROM pg_database WHERE datname = current_database())"
        " AND queryid IS NOT NULL"
    )


class PgStatStatementsSource(StatementStatsSource):
    """Full-sweep pg_stat_statements reader: full in input, incremental in output (Q2).

    pg_stat_statements stores normalized text only, so the counter sweep
    itself never carries parameter values; observed values come from this
    source's per-cycle pg_stat_activity snapshot (activity_sample), and the
    adapter telemetry lane is the eventual exact source.
    """

    session_setup_statements = (
        f"SET LOCAL work_mem = '{PG_COLLECTOR_WORK_MEM}'",
        f"SET LOCAL statement_timeout = {PG_STATS_TIMEOUT_MS}",
    )
    # Phase B runs under its own, longer budget in its own transaction.
    text_fetch_setup_statements = (f"SET LOCAL statement_timeout = {PG_TEXT_TIMEOUT_MS}",)
    # The activity snapshot gets its own short budget: it is optional value
    # recovery and must never eat into the cycle's counter work.
    activity_setup_statements = (
        f"SET LOCAL statement_timeout = {ACTIVITY_SAMPLE_TIMEOUT_MS}",
    )

    def activity_sample(self, executor: Executor) -> list[str]:
        for statement in self.activity_setup_statements:
            executor(statement, None)
        rows = executor(PG_ACTIVITY_SAMPLE_SQL, None)
        return [str(row[0]) for row in rows if row and row[0]]

    def collect(
        self,
        executor: Executor,
        cursor_state: Optional[dict[str, object]] = None,
        known_text_keys: Iterable[str] = frozenset(),
    ) -> CollectResult:
        for statement in self.session_setup_statements:
            executor(statement, None)

        version = int(executor(PG_VERSION_SQL, None)[0][0])  # type: ignore[arg-type]
        phase_a = pg_phase_a_sql(version)  # validates the version floor

        if version >= 140000:
            now_epoch, start_epoch, stats_reset, dealloc = executor(PG_EPOCH_SQL_14, None)[0]
        else:
            now_epoch, start_epoch = executor(PG_EPOCH_SQL_13, None)[0]
            stats_reset, dealloc = None, None
        captured_at = _to_epoch_seconds(now_epoch)
        epoch_id = compute_pg_epoch_id(start_epoch, stats_reset)

        has_toplevel = version >= 140000
        has_stats_since = version >= 170000
        counters_start = 4 if has_toplevel else 3
        rows: list[CounterRow] = []
        for raw in executor(phase_a, None):
            toplevel = _toplevel_flag(raw[3]) if has_toplevel else "t"
            calls, row_count, total, mean, minimum, maximum = raw[
                counters_start : counters_start + 6
            ]
            stats_since_raw = raw[counters_start + 7] if has_stats_since else None
            minmax_since_raw = raw[counters_start + 8] if has_stats_since else None
            rows.append(
                CounterRow(
                    engine_key=pg_engine_key(raw[0], raw[1], raw[2], toplevel),
                    calls=int(calls),  # type: ignore[arg-type]
                    rows=int(row_count),  # type: ignore[arg-type]
                    total_exec_time=float(total),  # type: ignore[arg-type]
                    mean_exec_time=float(mean),  # type: ignore[arg-type]
                    min_exec_time=float(minimum),  # type: ignore[arg-type]
                    max_exec_time=float(maximum),  # type: ignore[arg-type]
                    captured_at=captured_at,
                    epoch_id=epoch_id,
                    stats_since=(
                        _to_epoch_seconds(stats_since_raw)
                        if stats_since_raw is not None
                        else None
                    ),
                    minmax_stats_since=(
                        _to_epoch_seconds(minmax_since_raw)
                        if minmax_since_raw is not None
                        else None
                    ),
                )
            )

        capabilities: dict[str, object] = {
            "statement_count": len(rows),
            "dealloc": int(dealloc) if dealloc is not None else None,  # type: ignore[arg-type]
            # Below PG 14 the toplevel column does not exist: with track = all, nested and
            # top-level executions of the same statement are indistinguishable here.
            "toplevel_ambiguous": not has_toplevel,
            "pgss_max": None,
            "track": None,
        }
        try:
            settings = {name: setting for name, setting in executor(PG_SETTINGS_SQL, None)}
            raw_max = settings.get("pg_stat_statements.max")
            capabilities["pgss_max"] = int(raw_max) if raw_max is not None else None
            capabilities["track"] = settings.get("pg_stat_statements.track")
        except Exception:
            pass  # capacity settings are advisory; the sweep result stands without them

        needs_text = frozenset(row.engine_key for row in rows) - frozenset(known_text_keys)
        return CollectResult(
            rows=rows,
            epoch_id=epoch_id,
            capabilities=capabilities,
            next_cursor_state=None,
            needs_text=needs_text,
            texts={},
            incremental=False,
        )

    def text_fetch_sql(
        self, keys: Iterable[str], server_version_num: int
    ) -> list[tuple[str, tuple[list[int], int]]]:
        """Phase B (Q3d): batched, parameterized text fetch for unknown engine keys only. Run
        each batch under text_fetch_setup_statements. The WHERE is applied after the SRF
        materializes, so it saves text I/O and transfer, not the scan; in steady state the
        needs_text set is empty and this never runs.
        """
        columns = "userid, dbid, queryid, query"
        if server_version_num >= 140000:
            columns = "userid, dbid, queryid, toplevel, query"
        sql = (
            f"{RDST_OBSERVE_MARKER}SELECT {columns} FROM pg_stat_statements"
            " WHERE queryid = ANY(%s) AND dbid = %s"
        )
        by_dbid: dict[int, set[int]] = {}
        for key in keys:
            _, dbid, queryid, _ = parse_pg_engine_key(key)
            by_dbid.setdefault(dbid, set()).add(queryid)
        batches: list[tuple[str, tuple[list[int], int]]] = []
        for dbid in sorted(by_dbid):
            queryids = sorted(by_dbid[dbid])
            for start in range(0, len(queryids), PG_TEXT_FETCH_BATCH):
                batches.append((sql, (queryids[start : start + PG_TEXT_FETCH_BATCH], dbid)))
        return batches


# LAST_SEEN is a TIMESTAMP interpreted in the session time zone (Q2 caveat 4); pin the session
# to UTC so the cursor comparison is stable regardless of server default.
MYSQL_SESSION_SETUP = ("SET time_zone = '+00:00'",)

# The incremental cursor comes from the server clock, never RDST's (Q2 caveat 4).
MYSQL_CURSOR_SQL = RDST_OBSERVE_MARKER + "SELECT NOW(6)"

# Server start derived in a single statement so both readings share one instant; a restart clears
# performance_schema, making the start moment the stats epoch (compute_mysql_epoch_id).
MYSQL_SERVER_START_SQL = (
    RDST_OBSERVE_MARKER + "SELECT UNIX_TIMESTAMP(NOW(6)) - CAST(variable_value AS DECIMAL(20,6))"
    " FROM performance_schema.global_status WHERE variable_name = 'Uptime'"
)

_MYSQL_DIGEST_COLUMNS = (
    "SCHEMA_NAME, DIGEST, DIGEST_TEXT, COUNT_STAR,"
    " SUM_TIMER_WAIT, MIN_TIMER_WAIT, AVG_TIMER_WAIT, MAX_TIMER_WAIT,"
    " SUM_ROWS_SENT, SUM_ROWS_EXAMINED, SUM_NO_INDEX_USED, FIRST_SEEN, LAST_SEEN"
)
_MYSQL_DIGEST_TABLE = "performance_schema.events_statements_summary_by_digest"

# The catch-all NULL-digest row matches every incremental window once present (its LAST_SEEN
# updates constantly), so it is excluded here and quantified separately (Q2 caveat 5).
MYSQL_SWEEP_SQL = (
    f"{RDST_OBSERVE_MARKER}SELECT {_MYSQL_DIGEST_COLUMNS} FROM {_MYSQL_DIGEST_TABLE} WHERE DIGEST IS NOT NULL"
)
# '>=' (never '>'): sub-second LAST_SEEN resolution is not guaranteed (MariaDB is 1 s), so '>'
# silently drops rows updated later within the boundary second; overlap and dedupe instead.
MYSQL_INCREMENTAL_SQL = MYSQL_SWEEP_SQL + " AND LAST_SEEN >= %s"

# MySQL 8.0.1+ keeps one real execution's literal-bearing text per digest row; MariaDB and older
# MySQL lack the column, so the sweep only selects it after a one-time capability probe.
MYSQL_SAMPLE_PROBE_SQL = (
    RDST_OBSERVE_MARKER + "SELECT 1 FROM information_schema.columns"
    " WHERE table_schema = 'performance_schema'"
    " AND table_name = 'events_statements_summary_by_digest'"
    " AND column_name = 'QUERY_SAMPLE_TEXT'"
)
MYSQL_SWEEP_SAMPLE_SQL = (
    f"{RDST_OBSERVE_MARKER}SELECT {_MYSQL_DIGEST_COLUMNS}, QUERY_SAMPLE_TEXT"
    f" FROM {_MYSQL_DIGEST_TABLE} WHERE DIGEST IS NOT NULL"
)
MYSQL_INCREMENTAL_SAMPLE_SQL = MYSQL_SWEEP_SAMPLE_SQL + " AND LAST_SEEN >= %s"

# PROCESSLIST equivalent of the PostgreSQL activity snapshot. The
# connect-attrs subquery mirrors the live top lane's RDST self-session
# exclusion; the optimizer hint is this SELECT's own short budget.
MYSQL_ACTIVITY_SAMPLE_SQL = (
    RDST_OBSERVE_MARKER
    + f"SELECT /*+ MAX_EXECUTION_TIME({ACTIVITY_SAMPLE_TIMEOUT_MS}) */ INFO"
    " FROM information_schema.PROCESSLIST"
    " WHERE INFO IS NOT NULL"
    " AND COMMAND != 'Sleep'"
    " AND ID != CONNECTION_ID()"
    " AND ID NOT IN ("
    "SELECT PROCESSLIST_ID FROM performance_schema.session_account_connect_attrs"
    " WHERE ATTR_NAME = 'program_name' AND ATTR_VALUE LIKE 'rdst/%')"
    f" LIMIT {ACTIVITY_SAMPLE_LIMIT}"
)

MYSQL_NULL_DIGEST_SQL = f"{RDST_OBSERVE_MARKER}SELECT COUNT_STAR FROM {_MYSQL_DIGEST_TABLE} WHERE DIGEST IS NULL"
MYSQL_ROW_COUNT_SQL = f"{RDST_OBSERVE_MARKER}SELECT COUNT(*) FROM {_MYSQL_DIGEST_TABLE} WHERE DIGEST IS NOT NULL"
MYSQL_DIGEST_LOST_SQL = "SHOW GLOBAL STATUS LIKE 'Performance_schema_digest_lost'"
MYSQL_DIGESTS_SIZE_SQL = "SHOW VARIABLES LIKE 'performance_schema_digests_size'"


class MysqlDigestSource(StatementStatsSource):
    """Genuinely incremental events_statements_summary_by_digest reader (Q2). DIGEST_TEXT is in
    the same table, so text arrives with the counters and there is no Phase B. Where the server
    has QUERY_SAMPLE_TEXT, the sweep also carries it as sample_texts for observed parameter
    values; without the column the source degrades to DIGEST_TEXT alone.
    """

    session_setup_statements = MYSQL_SESSION_SETUP

    def __init__(self) -> None:
        # One capability probe per source lifetime; None until a probe succeeds.
        self._sample_text_capable: Optional[bool] = None

    def activity_sample(self, executor: Executor) -> list[str]:
        rows = executor(MYSQL_ACTIVITY_SAMPLE_SQL, None)
        return [str(row[0]) for row in rows if row and row[0]]

    def _sample_capable(self, executor: Executor) -> bool:
        if self._sample_text_capable is None:
            try:
                rows = executor(MYSQL_SAMPLE_PROBE_SQL, None)
            except Exception:
                # A failed probe degrades this sweep to DIGEST_TEXT and is
                # retried next cycle; only a successful probe is cached.
                return False
            self._sample_text_capable = bool(rows)
        return self._sample_text_capable

    def collect(
        self,
        executor: Executor,
        cursor_state: Optional[dict[str, object]] = None,
        known_text_keys: Iterable[str] = frozenset(),
    ) -> CollectResult:
        for statement in self.session_setup_statements:
            executor(statement, None)

        # Capture the next cursor before the sweep: rows updated mid-sweep fall into the next
        # window's '>=' overlap instead of being skipped.
        server_now = executor(MYSQL_CURSOR_SQL, None)[0][0]
        next_cursor = self._cursor_string(server_now)
        captured_at = _to_epoch_seconds(server_now)

        raw_start = float(executor(MYSQL_SERVER_START_SQL, None)[0][0])  # type: ignore[arg-type]
        previous = dict(cursor_state or {})
        previous_start = previous.get("server_start")
        restarted = False
        if previous_start is not None and (
            abs(raw_start - float(previous_start)) <= MYSQL_SERVER_START_TOLERANCE_S  # type: ignore[arg-type]
        ):
            server_start: float = float(previous_start)  # type: ignore[arg-type]
        else:
            server_start = raw_start
            restarted = previous_start is not None
        epoch_id = compute_mysql_epoch_id(server_start)

        sample_capable = self._sample_capable(executor)

        # A restart empties the table and starts a new epoch; the old cursor belongs to the old
        # epoch, so re-baseline with a full sweep.
        last_seen = previous.get("last_seen") if not restarted else None
        if last_seen is None:
            sweep_sql = MYSQL_SWEEP_SAMPLE_SQL if sample_capable else MYSQL_SWEEP_SQL
            raw_rows = executor(sweep_sql, None)
        else:
            incremental_sql = (
                MYSQL_INCREMENTAL_SAMPLE_SQL if sample_capable else MYSQL_INCREMENTAL_SQL
            )
            raw_rows = executor(incremental_sql, (last_seen,))

        rows: list[CounterRow] = []
        texts: dict[str, str] = {}
        sample_texts: dict[str, str] = {}
        for raw in raw_rows:
            schema_name, digest, digest_text = raw[0], raw[1], raw[2]
            key = mysql_engine_key(schema_name, digest)  # type: ignore[arg-type]
            rows.append(
                CounterRow(
                    engine_key=key,
                    calls=int(raw[3]),  # type: ignore[arg-type]
                    rows=int(raw[8]),  # type: ignore[arg-type]
                    total_exec_time=float(raw[4]) / MYSQL_PICOSECONDS_PER_MS,  # type: ignore[arg-type]
                    mean_exec_time=float(raw[6]) / MYSQL_PICOSECONDS_PER_MS,  # type: ignore[arg-type]
                    min_exec_time=float(raw[5]) / MYSQL_PICOSECONDS_PER_MS,  # type: ignore[arg-type]
                    max_exec_time=float(raw[7]) / MYSQL_PICOSECONDS_PER_MS,  # type: ignore[arg-type]
                    captured_at=captured_at,
                    epoch_id=epoch_id,
                    first_seen=_to_epoch_seconds(raw[11]),
                )
            )
            if digest_text is not None:
                texts[key] = str(digest_text)
            if sample_capable:
                sample = raw[13]
                if sample:
                    sample_texts[key] = str(sample)

        null_bucket = executor(MYSQL_NULL_DIGEST_SQL, None)
        capabilities: dict[str, object] = {
            "unattributed_executions": int(null_bucket[0][0]) if null_bucket else 0,  # type: ignore[arg-type]
            "digest_row_count": int(executor(MYSQL_ROW_COUNT_SQL, None)[0][0]),  # type: ignore[arg-type]
            "digest_lost": self._show_value(executor, MYSQL_DIGEST_LOST_SQL),
            "digests_size": self._show_value(executor, MYSQL_DIGESTS_SIZE_SQL),
            "query_sample_text": sample_capable,
        }

        return CollectResult(
            rows=rows,
            epoch_id=epoch_id,
            capabilities=capabilities,
            next_cursor_state={"last_seen": next_cursor, "server_start": server_start},
            needs_text=frozenset(),
            texts=texts,
            incremental=True,
            sample_texts=sample_texts,
        )

    @staticmethod
    def _cursor_string(server_now: object) -> str:
        if isinstance(server_now, datetime):
            return server_now.strftime("%Y-%m-%d %H:%M:%S.%f")
        return str(server_now)

    @staticmethod
    def _show_value(executor: Executor, sql: str) -> Optional[int]:
        try:
            rows = executor(sql, None)
            return int(rows[0][1]) if rows else None  # type: ignore[arg-type]
        except Exception:
            return None  # completeness signals are advisory, never fatal to the sweep
