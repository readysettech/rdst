"""Signs that a query ran but its result does not answer the question.

Three signs send the SQL back to the model for one repair: no rows, an output column that is
NULL in every row, and rows that repeat exactly. Each is often right (a filter nothing
matches, a value nobody recorded, a list of names), so the repair prompt says to return the
same SQL when the result is what the question asks for, and Ask keeps a repair only when it
runs, returns the same columns, keeps at least as many distinct rows as the original had
(removing repeats is allowed, dropping answers is not), and removes a sign without adding one.
"""

from __future__ import annotations

from typing import Any

VERSION = "result-check-repair-v1"
FEEDBACK_PREFIX = "The query ran, but its result looks wrong: "


def issues(columns: list[str], rows: list[Any]) -> list[str]:
    """The signs in a result, as sentences for the repair prompt."""
    return list(_signs(columns, rows).values())


def signs(columns: list[str], rows: list[Any]) -> set[str]:
    """The signs in a result by kind (and column), for comparing two results."""
    return set(_signs(columns, rows))


def _signs(columns: list[str], rows: list[Any]) -> dict[str, str]:
    if not rows:
        return {"no-rows": "it returned no rows."}
    found = {}
    values = [_values(row) for row in rows]
    for i, name in enumerate(columns):
        if all(v[i] is None for v in values):
            found[f"null:{name.casefold()}"] = f"column {name} is NULL in every returned row."
    repeats = len(values) - len(set(map(repr, values)))
    if repeats:
        found["repeats"] = f"{repeats} of {len(values)} returned rows repeat another row exactly."
    return found


def distinct_rows(rows: list[Any]) -> int:
    """How many different rows a result holds."""
    return len({repr(_values(row)) for row in rows})


def _values(row: Any) -> tuple:
    return tuple(row.values()) if isinstance(row, dict) else tuple(row)


def repair_feedback(found: list[str]) -> str:
    return (
        FEEDBACK_PREFIX
        + " ".join(found)
        + " Check that each join keeps one row per entity the question asks about, that"
        " JSON key paths follow the stored nesting (a dotted name such as a.b is usually a"
        " nested path), and that filter literals match stored values. If the result is what"
        " the question asks for, return the same SQL unchanged."
    )
