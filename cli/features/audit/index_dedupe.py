"""Filter index recommendations against indexes that already exist.

Used by both the capture-service analysis path (so saved audits + emailed
reports are deduped) and the verbose terminal renderer (so the CLI output
matches). Trusts the `schema_context` string that was sent to the LLM —
that's the ground truth for "what indexes does the database already have".
"""

from __future__ import annotations

from typing import Any


def filter_existing_indexes(
    index_recs: list[dict[str, Any]],
    schema_context: str,
) -> list[dict[str, Any]]:
    """Drop recommendations for column sets that are already indexed.

    Parsing rules expect the schema_context format produced by
    `audit/prompts.py`:

        table_name:
          Indexes: idx_name(col1, col2), other_idx(col3)

    A composite index covers its prefixes — `(a, b, c)` covers `(a)` and
    `(a, b)`. We also treat reversed column order as equivalent for the
    purposes of "is this index already there" — a btree on `(a, b)` and
    `(b, a)` are functionally distinct, but recommending the inverse of an
    existing index is almost always noise from the LLM, not a real win.
    """
    if not schema_context or not index_recs:
        return index_recs

    existing_indexes: dict[str, set[tuple[str, ...]]] = {}
    current_table: str | None = None
    for line in schema_context.split("\n"):
        stripped = line.strip()
        if (stripped.endswith(":")
                and not stripped.startswith("Columns")
                and not stripped.startswith("Indexes")
                and not stripped.startswith("Foreign")
                and not stripped.startswith("NOTE")
                and not stripped.startswith("==")):
            current_table = stripped[:-1].strip().lower()
        elif stripped.startswith("Indexes:") and current_table:
            idx_text = stripped[len("Indexes:"):].strip()
            for part in idx_text.split("),"):
                part = part.strip()
                paren_start = part.find("(")
                if paren_start == -1:
                    continue
                cols_str = part[paren_start + 1:].rstrip(")")
                cols = tuple(c.strip().lower() for c in cols_str.split(",") if c.strip())
                if cols:
                    existing_indexes.setdefault(current_table, set()).add(cols)
                    for prefix_len in range(1, len(cols)):
                        existing_indexes[current_table].add(cols[:prefix_len])

    if not existing_indexes:
        return index_recs

    filtered: list[dict[str, Any]] = []
    for rec in index_recs:
        table = (rec.get("table") or "").lower()
        rec_cols = tuple(c.strip().lower() for c in (rec.get("columns") or []))
        if not rec_cols:
            filtered.append(rec)
            continue
        table_indexes = existing_indexes.get(table, set())
        if rec_cols in table_indexes or tuple(reversed(rec_cols)) in table_indexes:
            continue
        filtered.append(rec)
    return filtered


def normalize_index_rec_sql(
    index_recs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Guarantee each recommendation carries a runnable CREATE INDEX statement.

    The two analysis prompts disagree on the field name (`sql` vs
    `create_index_sql`), and the LLM sometimes returns neither. Reports need
    the actual DDL, so canonicalize: prefer whichever field the model set,
    else synthesize from table + columns. Both keys are set to the same value
    so any renderer finds it.
    """
    for rec in index_recs:
        ddl = (rec.get("create_index_sql") or rec.get("sql") or "").strip()
        if not ddl:
            table = (rec.get("table") or "").strip()
            cols = [str(c).strip() for c in (rec.get("columns") or []) if str(c).strip()]
            expr = (rec.get("expression") or "").strip()
            target = ", ".join(cols) if cols else expr
            if table and target:
                idx_name = "idx_" + "_".join(
                    [table] + (cols if cols else [expr])
                ).lower().replace(" ", "_").replace(".", "_").replace("(", "").replace(")", "")
                idx_name = idx_name[:63]
                ddl = f"CREATE INDEX {idx_name} ON {table} ({target});"
        if ddl:
            rec["create_index_sql"] = ddl
            rec["sql"] = ddl
    return index_recs


__all__ = ["filter_existing_indexes", "normalize_index_rec_sql"]
