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
3. Shape hints for LIMIT / OFFSET.

Every suggestion carries a provenance string so the user can see where a
value came from before running anything with it.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp, parse_one

from shared.db_connection import (
    create_mysql_connection_from_params,
    postgres_connection_kwargs,
    resolve_connection_params,
)

logger = logging.getLogger(__name__)

MAX_SUGGESTIONS = 3
SAMPLE_TIMEOUT_MS = 2000

_PG_PLACEHOLDER_RE = re.compile(r"\$(\d+)")
_NAMED_PLACEHOLDER_RE = re.compile(r"(?<![:\w]):([A-Za-z_]\w*)")
_STRING_OR_QUESTION_RE = re.compile(r"'[^']*'|\"[^\"]*\"|\?")
_LITERAL_RE = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|-?\b\d+(?:\.\d+)?\b")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

_JSON_EXTRACT_NODES = tuple(
    getattr(exp, name)
    for name in ("JSONExtract", "JSONExtractScalar", "JSONBExtract", "JSONBExtractScalar")
    if hasattr(exp, name)
)

SOURCE_MYSQL_DIGEST_SAMPLE = "performance_schema digest sample"
SOURCE_PG_STAT_ACTIVITY = "pg_stat_activity (running now)"


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
    try:
        conn = _connect(engine, target, target_config)
    except Exception as exc:
        logger.debug("Parameter suggestions: connection failed: %s", exc)

    if conn is not None:
        try:
            sample = _fetch_sample(conn, engine, sql, query_hash)
            if sample:
                aligned = align_sample_values(sample["sql"], placeholders)
                sample["aligned"] = aligned is not None
                if aligned:
                    for key, value in aligned.items():
                        suggestions[key].append({"value": value, "provenance": sample["source"]})
                result["sample"] = sample
            for placeholder in placeholders:
                binding = bindings.get(placeholder["key"])
                if not binding or not binding.get("column"):
                    continue
                for value in _sample_column_values(conn, engine, binding):
                    if not any(s["value"] == value["value"] for s in suggestions[placeholder["key"]]):
                        suggestions[placeholder["key"]].append(value)
        except Exception as exc:
            logger.debug("Parameter suggestions: sampling failed: %s", exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    for placeholder in placeholders:
        binding = bindings.get(placeholder["key"]) or {}
        kind = binding.get("kind")
        if kind in ("limit", "offset") and not suggestions[placeholder["key"]]:
            suggestions[placeholder["key"]].append(
                {"value": "10" if kind == "limit" else "0", "provenance": f"Query shape ({kind.upper()})"}
            )
        result["placeholders"].append(
            {
                "placeholder": placeholder["placeholder"],
                "index": placeholder["index"],
                "column": binding.get("column"),
                "suggestions": suggestions[placeholder["key"]][:MAX_SUGGESTIONS],
            }
        )
    return result


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


def align_sample_values(sample_sql: str, placeholders: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    """Map placeholders to the literals of a captured instance, by position.

    Digesters replace every literal in order of appearance, so the Nth literal
    of the instance is the Nth placeholder of the template. If the counts
    disagree (IN lists collapse, for example) no mapping is returned.
    """
    if _PG_PLACEHOLDER_RE.search(sample_sql):
        return None
    literals = [m.group(0) for m in _LITERAL_RE.finditer(sample_sql)]
    if len(literals) != len(placeholders):
        return None
    values = {}
    for placeholder, literal in zip(placeholders, literals):
        values[placeholder["key"]] = _unquote(literal)
    return values


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


def _fetch_sample(conn, engine: str, sql: str, query_hash: Optional[str]) -> Optional[Dict[str, Any]]:
    cur = conn.cursor()
    try:
        if engine == "mysql":
            if query_hash:
                cur.execute(
                    "SELECT QUERY_SAMPLE_TEXT, QUERY_SAMPLE_SEEN FROM "
                    "performance_schema.events_statements_summary_by_digest WHERE DIGEST = %s",
                    (query_hash,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return {"sql": row[0], "source": SOURCE_MYSQL_DIGEST_SAMPLE, "seen_at": str(row[1])}
            cur.execute(
                "SELECT QUERY_SAMPLE_TEXT, QUERY_SAMPLE_SEEN FROM "
                "performance_schema.events_statements_summary_by_digest "
                "WHERE DIGEST_TEXT = %s AND QUERY_SAMPLE_TEXT IS NOT NULL LIMIT 1",
                (" ".join(sql.split()),),
            )
            row = cur.fetchone()
            if row and row[0]:
                return {"sql": row[0], "source": SOURCE_MYSQL_DIGEST_SAMPLE, "seen_at": str(row[1])}
            return None

        if not query_hash or not query_hash.lstrip("-").isdigit():
            return None
        cur.execute("SELECT current_setting('server_version_num')::int")
        if (cur.fetchone() or [0])[0] < 140000:
            return None
        cur.execute(
            "SELECT query, query_start FROM pg_stat_activity "
            "WHERE query_id IS NOT NULL AND abs(query_id)::text = %s "
            "AND pid <> pg_backend_pid() AND query NOT LIKE '%%$1%%' "
            "ORDER BY query_start DESC LIMIT 1",
            (query_hash.lstrip("-"),),
        )
        row = cur.fetchone()
        if row and row[0]:
            return {"sql": row[0], "source": SOURCE_PG_STAT_ACTIVITY, "seen_at": str(row[1])}
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
        if engine == "postgresql":
            cur.execute(
                "SELECT (most_common_vals::text::text[])[1:%s] FROM pg_stats "
                "WHERE tablename = %s AND attname = %s "
                + ("AND schemaname = %s" if schema else "AND schemaname = ANY(current_schemas(false))"),
                (MAX_SUGGESTIONS, table, column) + ((schema,) if schema else ()),
            )
            row = cur.fetchone()
            if row and row[0]:
                return [{"value": str(v), "provenance": f"Common value in {label} (pg_stats)"} for v in row[0]]
            qualified = ".".join(f'"{p}"' for p in (schema, table) if p)
            cur.execute(f"SET LOCAL statement_timeout = {SAMPLE_TIMEOUT_MS}")
            cur.execute(
                f'SELECT DISTINCT "{column}" FROM {qualified} WHERE "{column}" IS NOT NULL LIMIT {MAX_SUGGESTIONS}'
            )
        else:
            qualified = ".".join(f"`{p}`" for p in (schema, table) if p)
            cur.execute(
                f"SELECT /*+ MAX_EXECUTION_TIME({SAMPLE_TIMEOUT_MS}) */ DISTINCT `{column}` "
                f"FROM {qualified} WHERE `{column}` IS NOT NULL LIMIT {MAX_SUGGESTIONS}"
            )
        rows = cur.fetchall() or []
        return [{"value": _to_text(r[0]), "provenance": f"Sampled from {label}"} for r in rows]
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
    resolved = resolve_connection_params(target=target, target_config=target_config)
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
