"""Score RDST's index plan and actions for an app-sim database against the expected set.

`score(engine, plan, actions)` takes the /index-plan body and the open action rows (each with
`type` and `payload`) and returns one line per expectation: (plant, passed, detail).
"""

from __future__ import annotations

from typing import Any

from tests.app_sim.expected import MUST_NOT, for_engine


def _adds(plan: dict[str, Any]) -> list[tuple[str, list[str]]]:
    return [(add["table"], [column.lower() for column in add["columns"]]) for add in plan["add"]]


def _matches_add(plan: dict[str, Any], table: str, options: list[list[str]]) -> str | None:
    for found_table, columns in _adds(plan):
        if found_table == table and any(columns[:len(option)] == option for option in options):
            return f"{found_table}({', '.join(columns)})"
    return None


def _settings(actions: list[dict[str, Any]]) -> set[str]:
    return {str((row["payload"].get("setting") or {}).get("name") or "").lower()
            for row in actions if row["type"] == "change_setting"}


def score(engine: str, plan: dict[str, Any], actions: list[dict[str, Any]]) -> list[tuple[str, bool, str]]:
    lines = []
    dropped = {drop["index_name"]: drop for drop in plan["drop"]}
    for record in for_engine(engine):
        plant, kind = record["plant"], record["type"]
        if kind == "add_index":
            options = [record["columns"], *record.get("acceptable_columns", [])]
            found = _matches_add(plan, record["table"], options)
            lines.append((plant, found is not None, found or "not in the plan"))
        elif kind == "drop_index":
            drop = dropped.get(record["index"])
            lines.append((plant, drop is not None and drop["reason"] in (record["reason"], "stale"),
                          f"{record['index']}: {drop['reason'] if drop else 'not dropped'}"))
        elif kind == "skip_index":
            found = _matches_add(plan, record["table"], [record["columns"]])
            lines.append((plant, found is None, found or "not added"))
        elif kind == "keep_index":
            lines.append((plant, record["index"] not in dropped,
                          "kept" if record["index"] not in dropped else "dropped"))
        elif kind == "change_setting":
            names = {record["name"], *record.get("related", []), *record.get("acceptable_also", [])}
            hit = names & _settings(actions)
            lines.append((plant, bool(hit), ", ".join(sorted(hit)) or "no setting action"))
        elif kind == "rewrite_query":
            rewrites = [row for row in actions if row["type"] == "rewrite_query"]
            expression = _matches_add(plan, record["table"], [["lower(email)"]])
            lines.append((plant, bool(rewrites) or expression is not None,
                          "rewrite" if rewrites else expression or "neither a rewrite nor an index"))
    for record in MUST_NOT:
        if record["type"] == "drop_index":
            bad = record["index"] in dropped
        else:
            bad = any(table == record["table"] and columns == record["columns"]
                      for table, columns in _adds(plan))
        lines.append((f"not {record.get('plant') or record['index']}", not bad,
                      "avoided" if not bad else "recommended anyway"))
    return lines
