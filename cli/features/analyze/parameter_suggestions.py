"""Suggest real values for the placeholders of a templated query.

Queries that arrive from pg_stat_statements or performance_schema carry `$1`
or `?` instead of values, and EXPLAIN needs values. Rather than inventing
them, this module looks for values the database already knows about:

1. A captured statement instance. MySQL 8 keeps `QUERY_SAMPLE_TEXT` for every
   digest; PostgreSQL 14+ exposes `query_id` in pg_stat_activity, so a query
   that is running right now can be matched to its pg_stat_statements row.
   When the instance's literals line up with the placeholders, each
   placeholder gets its real value.
2. Values sampled from the compared column: PostgreSQL's most common values
   from pg_stats, otherwise a bounded DISTINCT read of the column.
3. Type-safe shape hints for positions where NULL is not valid SQL.

Every suggestion carries a provenance string so the user can see where a
value came from before running anything with it.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp, parse_one

from shared.db_connection import (
    create_mysql_connection_from_params,
    postgres_connection_kwargs,
    rdst_self_marker,
    resolve_connection_params,
)
from shared import analysis_lane

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3
SAMPLE_TIMEOUT_MS = 2000

# Column-value sampling reads user tables; the lane tags the connection and
# the marker keeps the DISTINCT reads out of registry admission.
ANALYZE_LANE = "rdst/analyze"
_SELF_MARKER = rdst_self_marker(ANALYZE_LANE)

_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")
_NAMED_PLACEHOLDER_RE = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")
_STRING_OR_QUESTION_RE = re.compile(r"'[^']*'|\"[^\"]*\"|\?")
_LITERAL_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|-?\b\d+(?:\.\d+)?\b")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_REGISTRY_HASH_RE = re.compile(r"^[0-9a-f]{12}$")

_JSON_EXTRACT_NODES = tuple(
    getattr(exp, name)
    for name in ("JSONExtract", "JSONExtractScalar", "JSONBExtract", "JSONBExtractScalar")
    if hasattr(exp, name)
)

SOURCE_MYSQL_DIGEST_SAMPLE = "performance_schema digest sample"
SOURCE_PG_STAT_ACTIVITY = "pg_stat_activity (running now)"
SOURCE_STORED_SAMPLE = "monitor activity sample"
SOURCE_MYSQL_HISTORY = "performance_schema statement history"
BURST_SECONDS = 3.0
BURST_INTERVAL_SECONDS = 0.05
BURST_SQL_BUDGET_SECONDS = 0.15
_SQL_TOKEN_RE = re.compile(
    r"\s+|--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|"
    r"\$\d+|(?<!:):[A-Za-z_]\w*|\?|(?<![\w$])-?\d+(?:\.\d+)?(?![\w$])|"
    r"[A-Za-z_][\w$]*|.", re.DOTALL)


@analysis_lane.connection_budget()
def suggest_parameter_values(
    sql: str,
    target: str,
    target_config: Optional[Dict[str, Any]] = None,
    query_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """Return per-placeholder value suggestions and, when found, a captured instance."""
    placeholders = enumerate_placeholders(sql)
    result: Dict[str, Any] = {"placeholders": [], "sample": None}
    if not placeholders:
        return result

    target_config = target_config or {}
    engine = (target_config.get("engine") or "postgresql").lower()
    engine = "mysql" if engine in ("mysql", "mariadb") else "postgresql"

    bindings = bind_placeholders_to_columns(sql, engine, placeholders)
    suggestions: Dict[str, List[Dict[str, str]]] = {p["key"]: [] for p in placeholders}

    conn = None
    raw_conn = None
    monitor_run = None
    try:
        raw_conn = _connect(engine, target, target_config)
        monitor_run = analysis_lane.register(target, raw_conn, engine, target_config)
        conn = analysis_lane.StatementConnection(raw_conn, monitor_run)
    except Exception as exc:
        logger.debug("Parameter suggestions: connection failed: %s", exc)

    if conn is not None:
        try:
            candidates = _stored_samples(target, query_hash)
            live = _fetch_sample(conn, engine, sql, query_hash, target)
            if live:
                candidates.insert(0, live)
            sample = _use_aligned_sample(candidates, sql, placeholders, suggestions)
            if sample is None and engine == "postgresql" and query_hash:
                burst = _burst_pg_sample(conn, query_hash, target)
                if burst:
                    sample = _use_aligned_sample([burst], sql, placeholders, suggestions)
            result["sample"] = sample
            if sample and sample["source"] != SOURCE_STORED_SAMPLE:
                _remember_sample(target, query_hash, sample["sql"])
            for placeholder in placeholders:
                if suggestions[placeholder["key"]]:
                    continue
                binding = bindings.get(placeholder["key"])
                if not binding or not binding.get("column"):
                    continue
                for value in _sample_column_values(conn, engine, binding):
                    if not any(s["value"] == value["value"] for s in suggestions[placeholder["key"]]):
                        suggestions[placeholder["key"]].append(value)
        except Exception as exc:
            logger.debug("Parameter suggestions: sampling failed: %s", exc)
        finally:
            analysis_lane.unregister(monitor_run, raw_conn)
            try:
                conn.close()
            except Exception:
                pass

    for placeholder in placeholders:
        binding = bindings.get(placeholder["key"]) or {}
        kind = binding.get("kind")
        if not suggestions[placeholder["key"]]:
            hint = _shape_hint(sql, placeholder, kind)
            if hint is not None:
                suggestions[placeholder["key"]].append(hint)
        result["placeholders"].append(
            {
                "placeholder": placeholder["placeholder"],
                "index": placeholder["index"],
                "column": binding.get("column"),
                "suggestions": suggestions[placeholder["key"]][:MAX_SUGGESTIONS],
            }
        )
    return result


def _shape_hint(sql: str, placeholder: Dict[str, Any], kind: str | None) -> Dict[str, str] | None:
    """Use a harmless literal where SQL's grammar requires one.

    This is a planning hint, not a sampled value. Callers must keep its
    param_source as ``none`` and use plain EXPLAIN.
    """
    token = placeholder["placeholder"]
    match = re.search(r"(?<![\w$])" + re.escape(token) + r"(?!\w)", sql)
    if match is None:
        return None
    before = sql[:match.start()].rstrip().lower()
    after = sql[match.end():].lstrip().lower()
    if re.search(r"\binterval\s*$", before):
        # MySQL names the unit after the number (INTERVAL ? DAY); PostgreSQL's literal has it.
        unit = re.match(r"(?:sql_tsi_)?(?:microsecond|second|minute|hour|day|week|month|"
                        r"quarter|year)\b", after)
        value, label = ("7" if unit else "7 days"), "INTERVAL"
    elif re.search(r"\bdate\s*$", before):
        value, label = "2000-01-01", "DATE"
    elif re.search(r"\b(?:timestamp|timestamptz|time)\s*$", before):
        value, label = "2000-01-01 00:00:00", "TIMESTAMP"
    elif kind == "limit" or re.search(r"\blimit\s*$", before):
        value, label = "10", "LIMIT"
    elif kind == "offset" or re.search(r"\boffset\s*$", before):
        value, label = "0", "OFFSET"
    elif re.search(r"\b(?:i?like)\s*$", before):
        value, label = "%", "LIKE"
    elif re.search(r"\bin\s*\(\s*$", before) or (before.endswith(",") and re.search(r"\bin\s*\([^)]*$", before)):
        value, label = None, "IN"
    elif after.startswith("::") or re.search(r"\bcast\s*\(\s*$", before):
        value, label = None, "CAST"
    else:
        return None
    return {"value": value, "provenance": f"Query shape ({label})"}


def enumerate_placeholders(sql: str) -> List[Dict[str, Any]]:
    """List placeholders in textual order using the desktop's keying convention.

    `$N` keys are the token itself, `?` keys are `?<ordinal>` (1-based),
    `:name` keys are the token itself.
    """
    found: List[Tuple[int, str, str, int]] = []
    seen = set()
    for match in _PG_PLACEHOLDER_RE.finditer(sql):
        token = match.group(0)
        if token not in seen:
            seen.add(token)
            found.append((match.start(), token, token, int(match.group(1))))
    ordinal = 0
    for match in _STRING_OR_QUESTION_RE.finditer(sql):
        if match.group(0) == "?":
            ordinal += 1
            found.append((match.start(), "?", f"?{ordinal}", ordinal))
    for match in _NAMED_PLACEHOLDER_RE.finditer(sql):
        token = match.group(0)
        if token not in seen:
            seen.add(token)
            found.append((match.start(), token, token, len(found) + 1))
    found.sort(key=lambda item: item[0])
    return [{"placeholder": token, "key": key, "index": index} for _, token, key, index in found]


def _tokens(sql: str) -> List[str]:
    return [token for match in _SQL_TOKEN_RE.finditer(sql)
            if (token := match.group(0)) and not token.isspace()
            and not token.startswith(("--", "/*"))]


def _same_fixed_token(expected: str, actual: str) -> bool:
    if expected.upper() == actual.upper():
        return True
    def identifier(token: str) -> str:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', '`'}:
            return token[1:-1].replace(token[0] * 2, token[0]).lower()
        return token.lower()
    return identifier(expected) == identifier(actual) and bool(re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_$]*", identifier(expected)))


def _align_sample_literals(sample_sql: str, template_sql: str,
                           placeholders: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    template, sample = _tokens(template_sql), _tokens(sample_sql)
    if len(template) != len(sample):
        return None
    keys = {p["placeholder"]: p["key"] for p in placeholders if p["placeholder"] != "?"}
    question_keys = iter(p["key"] for p in placeholders if p["placeholder"] == "?")
    values: Dict[str, str] = {}
    for expected, actual in zip(template, sample):
        if expected == "?" or expected in keys:
            key = next(question_keys, None) if expected == "?" else keys[expected]
            if key is None or not (actual.startswith("'") or
                                   re.fullmatch(r"-?\d+(?:\.\d+)?", actual) or
                                   actual.upper() in {"NULL", "TRUE", "FALSE"}):
                return None
            if key in values and values[key] != actual:
                return None
            values[key] = actual
        elif not _same_fixed_token(expected, actual):
            return None
    return values if len(values) == len(placeholders) else None


def align_sample_values(sample_sql: str, placeholders: List[Dict[str, Any]],
                        template_sql: str | None = None) -> Optional[Dict[str, str]]:
    """Align a captured execution with the normalized text when available."""
    if template_sql is not None:
        aligned = _align_sample_literals(sample_sql, template_sql, placeholders)
        return {key: _unquote(value) for key, value in aligned.items()} if aligned else None
    if _PG_PLACEHOLDER_RE.search(sample_sql):
        return None
    literals = [m.group(0) for m in _LITERAL_RE.finditer(sample_sql)]
    if len(literals) != len(placeholders):
        return None
    return {placeholder["key"]: _unquote(literal)
            for placeholder, literal in zip(placeholders, literals)}


def _use_aligned_sample(candidates: List[Dict[str, Any]], sql: str,
                        placeholders: List[Dict[str, Any]],
                        suggestions: Dict[str, List[Dict[str, str]]]) -> Optional[Dict[str, Any]]:
    for sample in candidates:
        aligned = _align_sample_literals(sample["sql"], sql, placeholders)
        if aligned:
            for key, literal in aligned.items():
                suggestions[key].append({"value": _unquote(literal), "literal_sql": literal,
                                         "provenance": sample["source"]})
            return {**sample, "aligned": True}
    return None


def _stored_samples(target: str, query_hash: Optional[str]) -> List[Dict[str, Any]]:
    if not query_hash:
        return []
    try:
        from shared.monitor.monitor_store import MonitorStore, default_monitor_db_path
        if not default_monitor_db_path().exists():
            return []
        rows = MonitorStore().read("SELECT text,observed_at FROM query_sample WHERE target=? "
                                   "AND query_key=? ORDER BY id DESC LIMIT 5", (target, query_hash))
        return [{"sql": row["text"], "source": SOURCE_STORED_SAMPLE,
                 "seen_at": str(row["observed_at"])} for row in rows]
    except Exception as exc:
        logger.debug("Parameter suggestions: stored sample lookup failed: %s", exc)
        return []


def _remember_sample(target: str, query_hash: Optional[str], sql: str) -> None:
    """Retain a verified live instance for analyses after the query stops running."""
    if not query_hash or not _REGISTRY_HASH_RE.fullmatch(query_hash):
        return
    try:
        from shared.monitor.monitor_store import MonitorStore, default_monitor_db_path
        if default_monitor_db_path().exists():
            MonitorStore().record_sample(target, query_hash, sql, int(time.time()))
    except Exception as exc:
        logger.debug("Parameter suggestions: live sample retention failed: %s", exc)


def _burst_pg_sample(conn, query_hash: str, target: str, *,
                     clock=time.monotonic, sleep=time.sleep) -> Optional[Dict[str, Any]]:
    query_ids = _pg_query_ids(query_hash, target)
    if not query_ids:
        return None
    cur = conn.cursor()
    try:
        cur.execute("SELECT current_setting('server_version_num')::int")
        if (cur.fetchone() or [0])[0] < 140000:
            return None
        cur.execute("SET LOCAL statement_timeout = 200")
        deadline = clock() + BURST_SECONDS
        sql_time = 0.0
        while clock() < deadline and sql_time < BURST_SQL_BUDGET_SECONDS:
            query_started = clock()
            cur.execute(
                "SELECT query,query_start FROM pg_stat_activity WHERE query_id = ANY(%s) "
                "AND state='active' AND datname=current_database() AND pid<>pg_backend_pid() "
                "AND COALESCE(application_name,'') NOT LIKE 'rdst/%%' "
                "ORDER BY query_start DESC LIMIT 1", (query_ids,))
            row = cur.fetchone()
            sql_time += clock() - query_started
            if row and row[0]:
                return {"sql": _to_text(row[0]), "source": SOURCE_PG_STAT_ACTIVITY,
                        "seen_at": str(row[1])}
            sleep(min(BURST_INTERVAL_SECONDS, max(0, deadline - clock())))
        return None
    except Exception as exc:
        logger.debug("Parameter suggestions: burst lookup failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        cur.close()


def bind_placeholders_to_columns(
    sql: str, engine: str, placeholders: List[Dict[str, Any]]
) -> Dict[str, Dict[str, Any]]:
    """Work out which column (or LIMIT/OFFSET) each placeholder is compared against."""
    dialect = "mysql" if engine == "mysql" else "postgres"
    # Anonymous `?` placeholders carry no identity through the parser, so give
    # each one a name that encodes its textual ordinal before parsing.
    ordinal = 0

    def _name_question_marks(match: re.Match) -> str:
        nonlocal ordinal
        if match.group(0) != "?":
            return match.group(0)
        ordinal += 1
        return f":rdstq{ordinal}"

    prepared = _STRING_OR_QUESTION_RE.sub(_name_question_marks, sql)
    try:
        tree = parse_one(prepared, dialect=dialect)
    except Exception:
        return {}

    aliases: Dict[str, str] = {}
    tables: List[str] = []
    for table in tree.find_all(exp.Table):
        name = table.name
        if not name:
            continue
        tables.append(name)
        if table.alias:
            aliases[table.alias] = name

    wanted = {p["key"] for p in placeholders}
    bindings: Dict[str, Dict[str, Any]] = {}
    for node in tree.walk():
        if isinstance(node, exp.Parameter):
            key = f"${node.name}" if node.name else None
        elif isinstance(node, exp.Placeholder) and node.this:
            name = str(node.this)
            key = f"?{name[len('rdstq'):]}" if name.startswith("rdstq") else f":{name}"
        else:
            continue
        if key in wanted and key not in bindings:
            bindings[key] = _describe_binding(node, aliases, tables)
    return bindings


def _describe_binding(node: exp.Expression, aliases: Dict[str, str], tables: List[str]) -> Dict[str, Any]:
    parent = node.parent
    while isinstance(parent, (exp.Paren, exp.Neg, exp.Cast)):
        parent = parent.parent
    if isinstance(parent, exp.Limit):
        return {"kind": "limit"}
    if isinstance(parent, exp.Offset):
        return {"kind": "offset"}
    if isinstance(parent, exp.Tuple) and isinstance(parent.parent, exp.In):
        parent = parent.parent
    if isinstance(parent, _JSON_EXTRACT_NODES):
        # The placeholder stands for a JSON path (`col->>?`); the column's rows
        # are whole documents, not candidate values.
        return {"kind": "json_path"}
    column = None
    if isinstance(parent, (exp.Binary, exp.In, exp.Between, exp.Like, exp.ILike)):
        for side in (parent.this, parent.args.get("expression"), parent.args.get("low"), parent.args.get("high")):
            while isinstance(side, (exp.Cast, exp.Paren)):
                side = side.this
            if isinstance(side, exp.Column):
                column = side
                break
    if column is None:
        return {"kind": "unknown"}
    table = column.table
    table = aliases.get(table, table) if table else (tables[0] if len(tables) == 1 else "")
    return {
        "kind": "column",
        "table": table,
        "column_name": column.name,
        "column": f"{table}.{column.name}" if table else column.name,
    }


def _registry_hash_query_ids(target: str, query_hash: str) -> List[int]:
    """Return the pg_stat_statements queryids a registry hash identifies.

    The discovery collector already resolves each engine key to the
    normalized hash the Query Library stores, and keeps those aliases per
    epoch in the observation store; reading them back is the only mapping
    between the two identities. A missing store, epoch, or alias yields
    nothing, and the caller skips the activity lane.
    """
    if not target:
        return []
    try:
        from features.query_registry.discovery import query_discovery
        from features.query_registry.statement_stats import parse_pg_engine_key

        store = query_discovery.existing_store()
        if store is None:
            return []
        epoch_id = (store.get_collector_state(target) or {}).get("epoch_id")
        if not epoch_id:
            return []
        aliases = store.get_identity_aliases(target, epoch_id)
    except Exception as exc:
        logger.debug("Parameter suggestions: identity alias lookup failed: %s", exc)
        return []

    query_ids = []
    for engine_key, normalized_hash in aliases.items():
        if normalized_hash != query_hash:
            continue
        try:
            query_ids.append(parse_pg_engine_key(engine_key)[2])
        except ValueError:
            continue
    return query_ids


def _pg_query_ids(query_hash: Optional[str], target: str) -> List[int]:
    """Resolve the caller's query identity to pg_stat_activity query_id values.

    A numeric hash is a pg query_id already; both signs are matched because
    the engine reports the jumble signed. A 12-hex hash is a registry
    identity and routes through the collector's alias mapping.
    """
    if not query_hash:
        return []
    if query_hash.lstrip("-").isdigit():
        value = abs(int(query_hash))
        return [value, -value]
    if _REGISTRY_HASH_RE.match(query_hash):
        return _registry_hash_query_ids(target, query_hash)
    return []


def _mysql_digests(query_hash: Optional[str], target: str) -> List[str]:
    if not query_hash:
        return []
    try:
        from features.query_registry.discovery import query_discovery
        store = query_discovery.existing_store()
        epoch_id = (store.get_collector_state(target) or {}).get("epoch_id") if store else None
        aliases = store.get_identity_aliases(target, epoch_id) if epoch_id else {}
        return list(dict.fromkeys(key.rsplit(":", 1)[-1] for key, value in aliases.items()
                                  if value == query_hash and ":" in key))
    except Exception:
        return []


def _fetch_sample(
    conn, engine: str, sql: str, query_hash: Optional[str], target: str = ""
) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    try:
        if engine == "mysql":
            digests = _mysql_digests(query_hash, target)
            if not digests:
                # A direct collector or a fresh scheduler may not have written
                # its identity aliases yet. DIGEST_TEXT is the stored query text.
                try:
                    cur.execute(
                        "SELECT /*+ MAX_EXECUTION_TIME(150) */ DIGEST FROM "
                        "performance_schema.events_statements_summary_by_digest "
                        "WHERE SCHEMA_NAME=DATABASE() AND DIGEST_TEXT=%s LIMIT 1",
                        (" ".join(sql.split()),),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        digests = [str(row[0])]
                except Exception:
                    conn.rollback()
            for digest in digests:
                for table in ("events_statements_history_long", "events_statements_history"):
                    try:
                        cur.execute(
                            "SELECT /*+ MAX_EXECUTION_TIME(150) */ SQL_TEXT,TIMER_START "
                            "FROM performance_schema." + table +
                            " WHERE DIGEST=%s AND SQL_TEXT IS NOT NULL "
                            "ORDER BY TIMER_START DESC LIMIT 1", (digest,))
                        row = cur.fetchone()
                        if row and row[0]:
                            return {"sql": _to_text(row[0]), "source": SOURCE_MYSQL_HISTORY,
                                    "seen_at": str(row[1])}
                    except Exception:
                        conn.rollback()
            if query_hash:
                cur.execute(
                    "SELECT QUERY_SAMPLE_TEXT, QUERY_SAMPLE_SEEN FROM "
                    "performance_schema.events_statements_summary_by_digest WHERE DIGEST = %s",
                    (query_hash,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return {"sql": _to_text(row[0]), "source": SOURCE_MYSQL_DIGEST_SAMPLE, "seen_at": str(row[1])}
            cur.execute(
                "SELECT QUERY_SAMPLE_TEXT, QUERY_SAMPLE_SEEN FROM "
                "performance_schema.events_statements_summary_by_digest "
                "WHERE DIGEST_TEXT = %s AND QUERY_SAMPLE_TEXT IS NOT NULL LIMIT 1",
                (" ".join(sql.split()),),
            )
            row = cur.fetchone()
            if row and row[0]:
                return {"sql": _to_text(row[0]), "source": SOURCE_MYSQL_DIGEST_SAMPLE, "seen_at": str(row[1])}
            return None

        query_ids = _pg_query_ids(query_hash, target)
        if not query_ids:
            return None
        cur.execute("SELECT current_setting('server_version_num')::int")
        if (cur.fetchone() or [0])[0] < 140000:
            return None
        cur.execute(
            "SELECT query, query_start FROM pg_stat_activity "
            "WHERE query_id = ANY(%s) AND state='active' "
            "AND datname=current_database() AND pid <> pg_backend_pid() "
            "AND COALESCE(application_name,'') NOT LIKE 'rdst/%%' "
            "ORDER BY query_start DESC LIMIT 1",
            (query_ids,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return {"sql": _to_text(row[0]), "source": SOURCE_PG_STAT_ACTIVITY, "seen_at": str(row[1])}
        return None
    except Exception as exc:
        logger.debug("Parameter suggestions: sample lookup failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        cur.close()


def _sample_column_values(conn, engine: str, binding: Dict[str, Any]) -> List[Dict[str, str]]:
    table, column = binding.get("table") or "", binding.get("column_name") or ""
    schema = ""
    if "." in table:
        schema, table = table.split(".", 1)
    for part in (schema, table, column):
        if part and not _IDENTIFIER_RE.match(part):
            return []
    if not table or not column:
        return []
    label = f"{table}.{column}"
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT data_type FROM information_schema.columns WHERE table_name = %s "
            "AND column_name = %s AND table_schema = "
            + ("%s" if schema else ("ANY(current_schemas(false))" if engine == "postgresql"
                                      else "DATABASE()")),
            (table, column) + ((schema,) if schema else ()),
        )
        type_row = cur.fetchone()
        column_type = type_row[0] if type_row and isinstance(type_row[0], str) else None

        def suggestion(value: str, provenance: str) -> Dict[str, str]:
            result = {"value": value, "provenance": provenance}
            if column_type:
                result["column_type"] = column_type
            return result

        if engine == "postgresql":
            cur.execute(
                "SELECT (most_common_vals::text::text[])[1:%s] FROM pg_stats "
                "WHERE tablename = %s AND attname = %s "
                + ("AND schemaname = %s" if schema else "AND schemaname = ANY(current_schemas(false))"),
                (MAX_SUGGESTIONS, table, column) + ((schema,) if schema else ()),
            )
            row = cur.fetchone()
            if row and row[0]:
                return [suggestion(str(v), f"Common value in {label} (pg_stats)") for v in row[0]]
            qualified = ".".join(f'"{p}"' for p in (schema, table) if p)
            cur.execute(f"SET LOCAL statement_timeout = {SAMPLE_TIMEOUT_MS}")
            cur.execute(
                f'{_SELF_MARKER}SELECT DISTINCT "{column}" FROM {qualified} WHERE "{column}" IS NOT NULL LIMIT {MAX_SUGGESTIONS}'
            )
        else:
            qualified = ".".join(f"`{p}`" for p in (schema, table) if p)
            cur.execute(
                f"{_SELF_MARKER}SELECT /*+ MAX_EXECUTION_TIME({SAMPLE_TIMEOUT_MS}) */ DISTINCT `{column}` "
                f"FROM {qualified} WHERE `{column}` IS NOT NULL LIMIT {MAX_SUGGESTIONS}"
            )
        rows = cur.fetchall() or []
        return [suggestion(_to_text(r[0]), f"Sampled from {label}") for r in rows]
    except Exception as exc:
        logger.debug("Parameter suggestions: column sample failed for %s: %s", label, exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    finally:
        cur.close()


def _connect(engine: str, target: str, target_config: Dict[str, Any]):
    resolved = resolve_connection_params(
        target=target, target_config=target_config, lane=ANALYZE_LANE
    )
    if engine == "mysql":
        return create_mysql_connection_from_params(resolved)
    import psycopg2

    conn = psycopg2.connect(**postgres_connection_kwargs(resolved))
    conn.autocommit = False
    return conn


def _unquote(literal: str) -> str:
    if len(literal) >= 2 and literal[0] == literal[-1] and literal[0] in "'\"":
        return literal[1:-1].replace(literal[0] * 2, literal[0])
    return literal


def _to_text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)
