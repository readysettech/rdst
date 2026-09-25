"""Persistent display names shared by every monitor presentation."""

from __future__ import annotations

import json
import re

from shared.monitor.monitor_store import MonitorStore
from shared.monitor.statements import distinct_names

_SETTING = "query_display_names_v2"
_LEGACY_SUFFIX = re.compile(r" \(\d+(?: columns?)?\)$")


def target_names(store: MonitorStore, target: str,
                 texts: dict[str, str] | None = None) -> dict[str, str]:
    """Assign across the target's history, keeping reservations across retention and restarts.

    First-seen ties use insertion order. The write transaction serializes allocations from
    concurrent requests and processes. Reads of already named queries don't write.
    """
    known = {row["query_key"]: row["text"] for row in store.read(
        "SELECT query_key,text FROM query_text WHERE target=? ORDER BY first_seen,rowid",
        (target,)) if row["text"]}
    for key, text in sorted((texts or {}).items()):
        if text and key not in known:
            known[key] = text
    rows = store.read("SELECT value FROM monitor_setting WHERE target=? AND key=?",
                      (target, _SETTING))
    saved = json.loads(rows[0]["value"]) if rows else {}
    if rows and known.keys() <= saved.keys() and len(set(saved.values())) == len(saved):
        return saved
    # Naming parses whole query families, so it runs before the write transaction; the
    # transaction only redoes it when another writer changed the names in between.
    basis = rows[0]["value"] if rows else None
    names = _assign(known, basis, None) if rows else None

    def allocate(conn):
        row = conn.execute("SELECT value FROM monitor_setting WHERE target=? AND key=?",
                           (target, _SETTING)).fetchone()
        current = row[0] if row else None
        if names is not None and current == basis:
            result = names
        elif row is not None:
            result = _assign(known, current, None)
        else:
            legacy = conn.execute("SELECT value FROM monitor_setting WHERE target=? AND key=?",
                                  (target, "query_display_names")).fetchone()
            result = _assign(known, None, legacy[0] if legacy else None)
        conn.execute("INSERT INTO monitor_setting VALUES (?,?,?) "
                     "ON CONFLICT(target,key) DO UPDATE SET value=excluded.value",
                     (target, _SETTING, json.dumps(result)))
        return result

    return store._write(allocate)


def _assign(known: dict[str, str], saved: str | None, legacy: str | None) -> dict[str, str]:
    """Names for every known query, extending the saved reservations."""
    if saved is not None:
        return distinct_names(known, json.loads(saved))
    old = json.loads(legacy) if legacy else {}
    existing = {key: name for key, name in old.items() if not _LEGACY_SUFFIX.search(name)}
    # Retained reservations without SQL still get an ordinal display label.
    for key, name in old.items():
        if key not in known and key not in existing:
            base = _LEGACY_SUFFIX.sub("", name)
            candidate, index = base, 2
            while candidate in existing.values():
                candidate = f"{base}, variant {index}"
                index += 1
            existing[key] = candidate
    return distinct_names(known, existing)
