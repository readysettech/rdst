"""The explanation at the top of each Actions section, written from what RDST measured.

A story has a headline, a short lead (what to do and why, in a sentence or two), the
technical detail behind it, and the facts that detail rests on.
"""

from __future__ import annotations

from typing import Any

from features.monitor.presentation import format_size

# A table taking this many writes a second makes every extra index on it expensive.
WRITE_HEAVY = 20.0
# Below this, a table's writes aren't worth naming.
NOTABLE_WRITES = 5.0


def _pct(share: float | None) -> str:
    value = float(share or 0) * 100
    return f"{value:.0f}%" if value >= 10 else f"{value:.1f}%"


def _rate(per_s: float) -> str:
    return f"{per_s:,.0f}" if per_s >= 10 else f"{per_s:.1f}"


def _span(seconds: float) -> str:
    if seconds >= 2 * 86400:
        return f"{int(seconds // 86400)} days"
    if seconds >= 2 * 3600:
        return f"{int(seconds // 3600)} hours"
    return f"{max(1, int(seconds // 60))} minutes"


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _on(tables: list[str]) -> str:
    return f" on {tables[0]}" if len(tables) == 1 else ""


def index_story(plan: dict[str, Any]) -> dict[str, Any]:
    adds, drops = plan.get("add") or [], plan.get("drop") or []
    load = plan.get("load") or {}
    busiest = [row for row in load.get("busiest_writes") or []
               if row["writes_per_s"] >= NOTABLE_WRITES]
    left_out = len(plan.get("later") or []) + len(plan.get("skipped") or [])
    observed = float(plan.get("observed_s") or 0)

    helped: dict[str, dict[str, Any]] = {}
    for add in adds:
        for query in add.get("helps") or []:
            helped.setdefault(query["key"], query)
    top = sorted(helped.values(), key=lambda query: -float(query.get("share") or 0))
    helped_share = sum(float(query.get("share") or 0) for query in top)
    add_tables = sorted({add["table"] for add in adds})
    drop_tables = sorted({drop["table"] for drop in drops})
    slowed: dict[str, dict[str, Any]] = {}
    for drop in drops:
        for writer in drop.get("slows") or []:
            slowed.setdefault(writer["key"], writer)
    writers = sorted(slowed.values(), key=lambda writer: -float(writer.get("share") or 0))
    slowed_share = sum(float(writer.get("share") or 0) for writer in writers)

    lead = []
    if adds:
        lead.append(f"Add {_plural(len(adds), 'index', 'indexes')}{_on(add_tables)} to speed up "
                    f"{_plural(len(top), 'query', 'queries')} that "
                    f"{'takes' if len(top) == 1 else 'take'} {_pct(helped_share)} of database "
                    "time.")
    if drops and writers:
        lead.append(f"Drop {_plural(len(drops), 'unused index', 'unused indexes')}"
                    f"{_on(drop_tables)} to speed up "
                    f"{_plural(len(writers), 'write', 'writes')} that "
                    f"{'takes' if len(writers) == 1 else 'take'} {_pct(slowed_share)} of "
                    "database time.")
    elif drops:
        writes = max(float(drop.get("table_writes_per_s") or 0) for drop in drops)
        lead.append(f"Drop {_plural(len(drops), 'unused index', 'unused indexes')}"
                    f"{_on(drop_tables)}. {'It slows' if len(drops) == 1 else 'They slow'} "
                    "down every write"
                    + (f", and {drop_tables[0]} takes {_rate(writes)} writes a second."
                       if len(drop_tables) == 1 and writes >= NOTABLE_WRITES else "."))
    if not adds and not drops:
        lead.append("Your indexes match how your queries use them. None need adding or "
                    "dropping.")

    details = []
    if load.get("queries"):
        basis = (f"Over the last {_span(observed)}, RDST watched "
                 f"{_plural(load['queries'], 'busy query', 'busy queries')}, "
                 f"{_pct(load.get('queries_share'))} of database time. Reads took "
                 f"{_pct(load.get('read_share'))} of the time and writes "
                 f"{_pct(load.get('write_share'))}.")
        if busiest:
            basis += (f" Most writes hit {busiest[0]['table']}, at "
                      f"{_rate(busiest[0]['writes_per_s'])} a second.")
        details.append(basis)
    if adds:
        names = [query.get("name") or "a query" for query in top[:3]]
        if len(top) > 3:
            names.append(_plural(len(top) - 3, "more query", "more queries"))
        one = len(names) == 1
        details.append(f"{_join(names)} {'filters or sorts' if one else 'filter or sort'} on "
                       f"columns no index covers. The new "
                       f"{'index covers' if len(adds) == 1 else 'indexes cover'} "
                       f"{'it' if one else 'them'}.")
    if drops:
        age = min(float(drop.get("evidence_age_s") or 0) for drop in drops)
        size = sum(int(drop.get("size_bytes") or 0) for drop in drops)
        them = "it" if len(drops) == 1 else "them"
        these = "this index" if len(drops) == 1 else "these indexes"
        text = ""
        if writers:
            names = [writer.get("name") or "a write" for writer in writers[:3]]
            text = (f"Every run of {_join(names)} also updates {these}. After you drop {them}, "
                    "RDST measures those writes before and after. ")
        text += (f"No query read {these} in the last {_span(age)}. Dropping {them} frees "
                 f"{format_size(size)}.")
        if any(drop.get("confidence") == "low" for drop in drops):
            text += (" That's a short window, so check for monthly jobs or replicas that read "
                     f"{them} first.")
        details.append(text)
    heavy = busiest[0] if busiest and busiest[0]["writes_per_s"] >= WRITE_HEAVY else None
    if heavy and left_out:
        details.append(
            f"The {heavy['table']} table takes {_rate(heavy['writes_per_s'])} writes a second, "
            "so RDST "
            f"left out {_plural(left_out, 'smaller win', 'smaller wins')}. "
            f"{'It' if left_out == 1 else 'They'} would cost every write more than "
            f"{'it saves' if left_out == 1 else 'they save'}.")
    elif left_out:
        details.append(f"RDST left out {_plural(left_out, 'smaller win', 'smaller wins')} that "
                       "would speed up little and still cost every write.")

    if adds and drops:
        headline = f"Add {_plural(len(adds), 'index', 'indexes')}, drop {len(drops)}"
    elif adds:
        headline = f"Add {_plural(len(adds), 'index', 'indexes')}"
    elif drops:
        headline = f"Drop {_plural(len(drops), 'index', 'indexes')}"
    else:
        headline = "Your indexes fit your queries"
    facts = []
    if load.get("queries"):
        facts += [{"label": "Reads", "value": f"{_pct(load.get('read_share'))} of time"},
                  {"label": "Writes", "value": f"{_pct(load.get('write_share'))} of time"}]
        if busiest:
            facts.append({"label": "Most writes",
                          "value": f"{busiest[0]['table']}, {_rate(busiest[0]['writes_per_s'])}/s"})
        facts.append({"label": "Indexes today", "value": str(load.get("indexes", 0))})
    return {"headline": headline, "lead": lead, "details": details, "facts": facts}


def _sentence(text: str) -> str:
    return f"{text[:1].upper()}{text[1:]}."


def _titles(cards: list[dict[str, Any]]) -> str:
    return _join([card["title"][:1].lower() + card["title"][1:] for card in cards])


def section_story(category: str, cards: list[dict[str, Any]]) -> dict[str, Any]:
    """The story for settings, maintenance, rewrites or caching, from its open changes."""
    count = len(cards)
    if category == "settings":
        return {"headline": f"Change {_plural(count, 'server setting', 'server settings')}",
                "lead": [_sentence(_titles(cards))],
                "details": ["RDST compared each setting with what it measured on this database. "
                            "Settings that only differ from a rule of thumb stay in the daily "
                            "report."],
                "facts": []}
    if category == "maintenance":
        return {"headline": f"Run {_plural(count, 'maintenance task', 'maintenance tasks')}",
                "lead": [_sentence(_titles(cards))],
                "details": ["Vacuum and fresh statistics keep the planner's row estimates "
                            "accurate. Each one is safe to run while the app is up."],
                "facts": []}
    if category == "rewrites":
        names = _join([card["title"].removeprefix("Rewrite ") for card in cards])
        return {"headline": f"Rewrite {_plural(count, 'query', 'queries')}",
                "lead": [f"Rewrite {names} so {'it does' if count == 1 else 'they do'} less "
                         "work."],
                "details": ["RDST rewrote each query and tested the rewrite against your data. "
                            "A rewrite changes your app's code."],
                "facts": []}
    return {"headline": f"Cache {_plural(count, 'query', 'queries')}",
            "lead": [_sentence(_titles(cards))],
            "details": ["These read the same results over and over."], "facts": []}
