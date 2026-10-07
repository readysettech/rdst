"""The index plan: which indexes to keep, add and drop, given the observed reads and writes.

Pure code over facts gathered elsewhere (index_plan_inputs), so the same inputs always give
the same plan. Read benefit comes from deep analyses of the heaviest queries; write cost from
the tables' insert, update and delete rates; unused indexes from the engine's own counters.

Inputs:
  engine: "postgresql" | "mysql"
  now, observed_s (how long RDST has read statistics), counter_age_s (how long the engine's
  counters have run)
  existing: [{schema, table, index_name, columns, method, unique, primary, scans,
              window_scans, size_bytes, last_scan_at}]
  tables: {table: {writes_per_s, peak_writes_per_s, upd_per_s, hot_ratio, live_rows,
                   size_bytes, updated_columns}}
  queries: [{key, name, share, calls_per_s, kind, suggestions: [{table, columns, method,
             sql, gain_pct, verified, impact}]}]
  replicas: whether the primary streams to replicas RDST can't see
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

# A suggestion's expected share of its query's time saved, when the planner didn't verify it.
IMPACT_GAIN = {"high": 0.6, "medium": 0.3, "low": 0.1}
UNKNOWN_GAIN = 0.2
# Fractions of all database time.
MIN_BENEFIT = 0.002
SMALL_BENEFIT = 0.02
# Rows written per second to a table.
WRITE_HEAVY = 20.0
VERY_WRITE_HEAVY = 200.0
MANY_INDEXES = 6
# HOT updates keep the table's indexes untouched; indexing an updated column ends that.
HOT_SHARE = 0.5
# The plan adds at most this many indexes, each worth at least MIN_ADD_BENEFIT; the rest wait.
MAX_ADDS = 5
MIN_ADD_BENEFIT = 0.01
# Beyond a few key columns an index mostly covers the select list, which every write pays for.
MAX_KEY_COLUMNS = 3
CONFIDENT_AGE_S = 7 * 86400
STALE_SCAN_S = 30 * 86400


def _norm(name: Any) -> str:
    return str(name or "").split(".")[-1].strip().strip('"`').lower()


def _ident(index: dict[str, Any]) -> tuple[str, str, str]:
    """An index's identity: MySQL names indexes per table, so a name alone repeats."""
    return (str(index.get("schema") or "").lower(), _norm(index["table"]), index["index_name"])


def _expression(text: str) -> str:
    """lower((email)::text) and lower(email) compare equal: casts and spare parentheses go."""
    text = re.sub(r"::[a-z_ ]+(\[\])?", "", text.lower()).replace(" ", "")
    while True:
        # Parentheses that don't belong to a function call.
        simpler = re.sub(r"(?<!\w)\((\w+)\)", r"\1", text)
        simpler = re.sub(r"(?<!\w)\((\w+\(\w*\))\)", r"\1", simpler)
        if simpler == text:
            return text
        text = simpler


def _columns(columns: Any) -> tuple[str, ...]:
    """Lowercased column names; an expression stays whole, minus any ASC/DESC."""
    parts = []
    for column in columns or []:
        text = _norm(column) if "(" not in str(column) else _expression(str(column).strip())
        if text:
            parts.append(text if "(" in text else text.split()[0])
    return tuple(parts)


# Index types PostgreSQL takes after USING; anything else an analysis writes ("btree
# (expression index)", "composite") is a plain B-tree.
METHODS = ("btree", "hash", "gin", "gist", "brin", "spgist")


def _method(method: Any) -> str:
    words = str(method or "").strip().lower().split()
    return words[0] if words and words[0] in METHODS else "btree"


def _id(kind: str, table: str, parts: tuple[str, ...]) -> str:
    return f"{kind}:" + hashlib.sha1(f"{table}:{','.join(parts)}".encode()).hexdigest()[:12]


def _prefix(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    return 0 < len(short) <= len(long) and long[:len(short)] == short


def _gain(suggestion: dict[str, Any]) -> float:
    if suggestion.get("gain_pct") is not None:
        return max(0.0, min(1.0, float(suggestion["gain_pct"]) / 100))
    return IMPACT_GAIN.get(str(suggestion.get("impact") or "").lower(), UNKNOWN_GAIN)


_PLAIN_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


def _quote(engine: str, name: str) -> str:
    """An identifier as SQL: backticks on MySQL, double quotes on PostgreSQL unless plain."""
    if engine == "mysql":
        return "`" + name.replace("`", "``") + "`"
    return name if _PLAIN_NAME.match(name) else '"' + name.replace('"', '""') + '"'


def _part(engine: str, column: str) -> str:
    # Both engines take an expression in an index only inside its own parentheses.
    if "(" in column:
        return f"({column})"
    return _quote(engine, column)


def _index_name(table: str, columns: tuple[str, ...]) -> str:
    return re.sub(r"\W+", "_", f"idx_{table}_{'_'.join(columns)}").strip("_")[:63]


def _undo_sql(engine: str, table: str, columns: tuple[str, ...]) -> str:
    return _drop_sql(engine, {"table": table, "index_name": _index_name(table, columns)})


def _restore_sql(engine: str, index: dict[str, Any]) -> str | None:
    """The dropped index rebuilt from its own definition, online where the engine can."""
    definition = str(index.get("definition") or "").strip().rstrip(";")
    if not definition.upper().startswith(("CREATE INDEX", "CREATE UNIQUE INDEX")):
        return None
    if engine != "mysql":
        definition = re.sub(r"^CREATE (UNIQUE )?INDEX ", r"CREATE \1INDEX CONCURRENTLY ",
                            definition, flags=re.IGNORECASE)
    return definition + ";"


def _create_sql(engine: str, table: str, columns: tuple[str, ...], method: str) -> str:
    name = _index_name(table, columns)
    parts = ", ".join(_part(engine, column) for column in columns)
    if engine == "mysql":
        return f"CREATE INDEX {_quote(engine, name)} ON {_quote(engine, table)} ({parts});"
    using = "" if method == "btree" else f" USING {method}"
    return (f"CREATE INDEX CONCURRENTLY {_quote(engine, name)} ON {_quote(engine, table)}"
            f"{using} ({parts});")


def _drop_sql(engine: str, index: dict[str, Any]) -> str:
    if engine == "mysql":
        return (f"ALTER TABLE {_quote(engine, index['table'])} "
                f"DROP INDEX {_quote(engine, index['index_name'])};")
    return (f"DROP INDEX CONCURRENTLY {_quote(engine, index.get('schema') or 'public')}."
            f"{_quote(engine, index['index_name'])};")


def _candidates(queries: list[dict[str, Any]]) -> dict[tuple[str, tuple[str, ...], str], dict]:
    """Every suggested index, one entry per (table, columns, method), with the queries it helps."""
    found: dict[tuple[str, tuple[str, ...], str], dict] = {}
    for query in queries:
        for suggestion in query.get("suggestions") or []:
            table, columns = _norm(suggestion.get("table")), _columns(suggestion.get("columns"))
            if not table or not columns:
                continue
            trimmed = len(columns) > MAX_KEY_COLUMNS
            columns = columns[:MAX_KEY_COLUMNS]
            if trimmed:
                # A shorter index isn't the one the planner checked.
                suggestion = {**suggestion, "verified": False, "gain_pct": None}
            key = (table, columns, _method(suggestion.get("method")))
            entry = found.setdefault(key, {"table": table, "columns": columns, "method": key[2],
                                           "helps": {}, "roles": {}})
            _add_roles(entry["roles"], suggestion.get("roles") or {})
            gain = _gain(suggestion)
            helped = entry["helps"].get(query["key"])
            if helped is None or gain > helped["gain"]:
                entry["helps"][query["key"]] = {
                    "key": query["key"], "name": query.get("name"), "share": float(query.get("share") or 0),
                    "gain": gain, "verified": bool(suggestion.get("verified")),
                    "gain_pct": suggestion.get("gain_pct"), "impact": suggestion.get("impact")}
    return found


def _covering(existing: list[dict[str, Any]], table: str, columns: tuple[str, ...],
              method: str) -> dict[str, Any] | None:
    for index in existing:
        if _norm(index["table"]) == table and _method(index.get("method")) == method \
                and _plain(index) and _prefix(columns, _columns(index.get("columns"))):
            return index
    return None


ROLE_RANK = {"equality": 0, "sort": 1, "range": 2}


def _add_roles(into: dict[str, str], roles: dict[str, str]) -> None:
    """How queries use each column; equality wins when queries differ."""
    for column, role in roles.items():
        name = _norm(column)
        if role in ROLE_RANK and (name not in into or ROLE_RANK[role] < ROLE_RANK[into[name]]):
            into[name] = role


def _merge(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold each candidate into a longer one on the same table that starts with its columns.

    Columns arrive ordered by how each query uses them, so the same columns in another order
    serve other queries and stay a separate index.
    """
    kept: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda entry: (-len(entry["columns"]), entry["columns"])):
        host = next((other for other in kept if other["table"] == candidate["table"]
                     and other["method"] == candidate["method"]
                     and _prefix(candidate["columns"], other["columns"])), None)
        if host is None:
            kept.append({**candidate, "helps": dict(candidate["helps"]), "merged": [],
                         "roles": dict(candidate["roles"])})
            continue
        host["merged"].append(candidate["columns"])
        _add_roles(host["roles"], candidate["roles"])
        for key, helped in candidate["helps"].items():
            if key not in host["helps"] or helped["gain"] > host["helps"][key]["gain"]:
                host["helps"][key] = helped
    return _alternatives(kept)


def _strength(entry: dict[str, Any]) -> tuple[float, int]:
    return (sum(helped["share"] * helped["gain"] for helped in entry["helps"].values()),
            len(entry["columns"]))


def _alternatives(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one index per set of queries on a table: a candidate that only helps queries a
    stronger one on the same table already helps is another way to speed up the same queries,
    whatever kind of index it is."""
    kept: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda entry: (_strength(entry), entry["columns"]),
                        reverse=True):
        if not any(other["table"] == entry["table"]
                   and set(entry["helps"]) <= set(other["helps"]) for other in kept):
            kept.append(entry)
    return kept


def _write_cost(table: dict[str, Any] | None, columns: tuple[str, ...], existing_count: int,
                engine: str) -> dict[str, Any]:
    table = table or {}
    updated = {_norm(column) for column in table.get("updated_columns") or []}
    hot_ratio = table.get("hot_ratio")
    hot_risk = (engine == "postgresql" and bool(updated & set(columns))
                and hot_ratio is not None and hot_ratio >= HOT_SHARE)
    return {"writes_per_s": float(table.get("writes_per_s") or 0),
            "peak_writes_per_s": float(table.get("peak_writes_per_s") or 0),
            "existing_indexes": existing_count, "updated_columns": sorted(updated & set(columns)),
            "hot_risk": hot_risk}


def _label(benefit: float, cost: dict[str, Any]) -> tuple[str, str]:
    writes = max(cost["writes_per_s"], cost["peak_writes_per_s"] / 2)
    if benefit < SMALL_BENEFIT and (writes >= VERY_WRITE_HEAVY or cost["hot_risk"]):
        return "skip", "writes_outweigh"
    if cost["hot_risk"]:
        return "worth_it", "hot_updates"
    if writes >= WRITE_HEAVY or cost["existing_indexes"] >= MANY_INDEXES:
        return "worth_it", "write_heavy" if writes >= WRITE_HEAVY else "many_indexes"
    return "safe", "read_heavy"


def _plain(index: dict[str, Any]) -> bool:
    """A full, valid index: one a planner can use in place of another on the same columns."""
    return not index.get("partial") and index.get("valid", True)


def _carries(cover: dict[str, Any], index: dict[str, Any]) -> bool:
    """The covering index holds every INCLUDE column the dropped one serves reads from."""
    held = set(_columns(cover.get("columns"))) | set(_columns(cover.get("include")))
    return set(_columns(index.get("include"))) <= held


def _evidence_age(index: dict[str, Any], now: int, observed_s: int, counter_age_s: int) -> int:
    # An index RDST watched appear has no reads to show from before it existed.
    first_seen = index.get("first_seen")
    if first_seen and int(first_seen) > now - observed_s + 60:
        return max(0, now - int(first_seen))
    return max(observed_s, counter_age_s)


def _defeated(index: dict[str, Any], filtered: dict[str, list[str]]) -> bool:
    """Queries filter on the index's leading column, so its idleness says the queries can't
    use it (a function on the column, a cast, an OR), not that nothing needs it."""
    columns = _columns(index.get("columns"))
    return bool(columns) and columns[0] in set(filtered.get(_norm(index["table"])) or ())


# The writes a drop names: the busiest few that maintain the index.
MAX_SLOWED = 3


def _slows(index: dict[str, Any], writers: dict[str, list[dict[str, Any]]]) -> list[dict]:
    """The write statements that keep this index up to date: every insert and delete on its
    table, and the updates that change one of its columns."""
    columns = {_expression(column) for column in _columns(index.get("columns"))}
    found = [{key: writer[key] for key in ("key", "name", "verb", "share", "calls_per_s")}
             for writer in writers.get(_norm(index["table"])) or []
             if writer["verb"] != "update" or columns & set(writer.get("set_columns") or ())]
    found.sort(key=lambda writer: (-writer["share"], writer["key"]))
    return found[:MAX_SLOWED]


def _drops(existing: list[dict[str, Any]], engine: str, now: int, observed_s: int,
           counter_age_s: int, replicas: bool, tables: dict[str, dict[str, Any]],
           filtered: dict[str, list[str]] | None = None,
           writers: dict[str, list[dict[str, Any]]] | None = None) -> list[dict]:
    """Unused indexes first, then duplicates of an index the plan keeps.

    A duplicate drops only when the index covering it is full, valid and stays; of two
    identical indexes the one with more reads stays. Constraint and invalid indexes never drop.
    """
    # An index with no statistics yet has unknown use, which never reads as unused.
    removable = [index for index in existing if not index.get("unique") and not index.get("primary")
                 and index.get("scans") is not None and index.get("valid", True)]
    chosen: dict[tuple[str, str, str], tuple[str, dict[str, Any] | None]] = {}
    for index in removable:
        last = index.get("last_scan_at")
        if _defeated(index, filtered or {}):
            continue
        if not index.get("scans") and not index.get("window_scans"):
            chosen[_ident(index)] = ("unused", None)
        elif last and now - int(last) >= STALE_SCAN_S and not index.get("window_scans"):
            chosen[_ident(index)] = ("stale", None)
    for index in sorted(removable, key=lambda item: (int(item.get("scans") or 0), item["index_name"])):
        if _ident(index) in chosen or not _plain(index):
            continue
        table, columns = _norm(index["table"]), _columns(index.get("columns"))
        cover = next((other for other in existing if other is not index
                      and _ident(other) not in chosen and _plain(other)
                      and _norm(other["table"]) == table
                      and _method(other.get("method")) == _method(index.get("method"))
                      and _prefix(columns, _columns(other.get("columns")))
                      and _carries(other, index)
                      and (len(_columns(other.get("columns"))) > len(columns)
                           or int(other.get("scans") or 0) > int(index.get("scans") or 0)
                           or (int(other.get("scans") or 0) == int(index.get("scans") or 0)
                               and other["index_name"] < index["index_name"]))), None)
        if cover is not None:
            chosen[_ident(index)] = ("duplicate", cover)
    drops: list[dict[str, Any]] = []
    for index in removable:
        if _ident(index) not in chosen:
            continue
        reason, cover = chosen[_ident(index)]
        table = _norm(index["table"])
        writes = float((tables.get(table) or {}).get("writes_per_s") or 0)
        last = index.get("last_scan_at")
        age = _evidence_age(index, now, observed_s, counter_age_s)
        confident = reason == "duplicate" or age >= CONFIDENT_AGE_S or \
            (reason == "stale" and bool(last))
        drops.append({
            "id": _id("drop", table, (index["index_name"],)), "table": table,
            "schema": index.get("schema"), "index_name": index["index_name"],
            "columns": list(_columns(index.get("columns"))), "reason": reason,
            "covered_by": cover["index_name"] if cover is not None else None,
            "size_bytes": int(index.get("size_bytes") or 0), "scans": int(index.get("scans") or 0),
            "last_scan_at": last, "evidence_age_s": age,
            "confidence": "high" if confident else "low", "table_writes_per_s": writes,
            "replica_caveat": replicas and reason != "duplicate",
            "slows": _slows(index, writers or {}),
            "sql": _drop_sql(engine, index), "restore_sql": _restore_sql(engine, index)})
    drops.sort(key=lambda drop: (drop["confidence"] != "high", drop["reason"] != "duplicate",
                                 -drop["table_writes_per_s"], -drop["size_bytes"], drop["index_name"]))
    return drops


def build_plan(inputs: dict[str, Any]) -> dict[str, Any]:
    engine = inputs.get("engine") or "postgresql"
    now = int(inputs.get("now") or 0)
    existing = list(inputs.get("existing") or [])
    tables = {_norm(name): facts for name, facts in (inputs.get("tables") or {}).items()}
    queries = list(inputs.get("queries") or [])
    observed_s = int(inputs.get("observed_s") or 0)
    counter_age_s = int(inputs.get("counter_age_s") or 0)

    covered = []
    open_candidates = []
    for (table, columns, method), entry in sorted(_candidates(queries).items()):
        index = _covering(existing, table, columns, method)
        if index is not None:
            covered.append({"table": table, "columns": list(columns),
                            "covered_by": index["index_name"], "queries": sorted(entry["helps"])})
        else:
            open_candidates.append(entry)

    adds, skipped = [], []
    for entry in _merge(open_candidates):
        helps = sorted(entry["helps"].values(),
                       key=lambda helped: (-helped["share"] * helped["gain"], helped["key"]))
        benefit = sum(helped["share"] * helped["gain"] for helped in helps)
        if benefit < MIN_BENEFIT:
            continue
        existing_count = sum(1 for index in existing if _norm(index["table"]) == entry["table"]
                             and not index.get("primary"))
        cost = _write_cost(tables.get(entry["table"]), entry["columns"], existing_count, engine)
        label, reason = _label(benefit, cost)
        # A wider index that starts with an existing one makes that one redundant once built.
        replaces = sorted(index["index_name"] for index in existing
                          if _norm(index["table"]) == entry["table"] and not index.get("unique")
                          and not index.get("primary")
                          and _method(index.get("method")) == entry["method"]
                          and _prefix(_columns(index.get("columns")), entry["columns"]))
        add = {"id": _id("add", entry["table"], entry["columns"]), "table": entry["table"],
               "replaces": replaces,
               "columns": list(entry["columns"]), "method": entry["method"],
               "roles": [entry["roles"].get(column) for column in entry["columns"]],
               "merged": [list(columns) for columns in entry["merged"]],
               "helps": helps, "benefit": round(benefit, 4),
               "verified": any(helped["verified"] for helped in helps),
               "write_cost": cost, "label": label, "label_reason": reason,
               "sql": _create_sql(engine, entry["table"], entry["columns"], entry["method"]),
               "undo_sql": _undo_sql(engine, entry["table"], entry["columns"])}
        (skipped if label == "skip" else adds).append(add)
    adds.sort(key=lambda add: (-add["benefit"], add["id"]))
    skipped.sort(key=lambda add: (-add["benefit"], add["id"]))
    later = [add for index, add in enumerate(adds)
             if index >= MAX_ADDS or add["benefit"] < MIN_ADD_BENEFIT]
    adds = [add for add in adds if add not in later]

    filtered = inputs.get("filtered") or {}
    drops = _drops(existing, engine, now, observed_s, counter_age_s,
                   bool(inputs.get("replicas")), tables, filtered,
                   {_norm(table): rows for table, rows in (inputs.get("writers") or {}).items()})
    defeated = [{"index_name": index["index_name"], "table": _norm(index["table"]),
                 "columns": list(_columns(index.get("columns")))}
                for index in existing
                if index.get("scans") is not None and not index.get("scans")
                and not index.get("window_scans") and not index.get("primary")
                and _defeated(index, filtered)]
    dropped = {(drop["table"], drop["index_name"]) for drop in drops}
    keep = [{"index_name": index["index_name"], "table": _norm(index["table"]),
             "columns": list(_columns(index.get("columns"))), "unique": bool(index.get("unique")),
             "primary": bool(index.get("primary")), "scans": int(index.get("scans") or 0),
             "window_scans": int(index.get("window_scans") or 0),
             "size_bytes": int(index.get("size_bytes") or 0)}
            for index in existing if (_norm(index["table"]), index["index_name"]) not in dropped]
    keep.sort(key=lambda index: (index["table"], index["index_name"]))
    busiest = sorted(((name, float(facts.get("writes_per_s") or 0)) for name, facts in tables.items()),
                     key=lambda item: -item[1])
    load = {**(inputs.get("load") or {}), "indexes": len(existing),
            "busiest_writes": [{"table": name, "writes_per_s": round(rate, 1)}
                               for name, rate in busiest[:3] if rate >= 1]}
    return {"engine": engine, "computed_at": now, "observed_s": observed_s,
            "counter_age_s": counter_age_s, "load": load,
            "keep": keep, "add": adds, "drop": drops,
            "skipped": skipped, "later": later, "covered": covered, "defeated": defeated,
            "sql": "\n".join([add["sql"] for add in adds] + [drop["sql"] for drop in drops]),
            # Undo restores what the plan drops before removing what it adds.
            "rollback_sql": "\n".join([drop["restore_sql"] for drop in drops if drop["restore_sql"]]
                                      + [add["undo_sql"] for add in adds]) or None}
