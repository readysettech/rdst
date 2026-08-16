"""Self-contained HTML dashboard for benchmark comparison summaries.

Layout: headline KPI tiles, an accuracy-vs-cost hero chart, a ranked
leaderboard, then per-topic tables (latency, billing, difficulty and
database breakdowns, failures, tokens and stage costs, reasoning) and a
methodology section. Plotly is inlined so the page works offline; every
dynamic string is HTML-escaped before it reaches markup or figure JSON.
"""

from __future__ import annotations

import html
from decimal import Decimal
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from plotly.offline import get_plotlyjs

from shared.persistence import write_text


class DashboardError(ValueError):
    pass


_PLOT_CONFIG = {"responsive": True, "displaylogo": False}
_FONT_STACK = 'system-ui, -apple-system, "Segoe UI", sans-serif'
_SURFACE = "#fcfcfb"
_INK = "#0b0b0b"
_INK_2 = "#52514e"
_MUTED = "#898781"
_GRID = "#e1e0d9"
_ACCENT = "#2a78d6"
_SEQ_RAMP = (
    "#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7",
    "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281",
    "#0d366b",
)  # fmt: skip
_TOKEN_SERIES = (
    ("Uncached input", "#2a78d6"),
    ("Cached input", "#eb6834"),
    ("Visible output", "#1baf7a"),
    ("Reasoning", "#eda100"),
)
_DIFFICULTY_ORDER = ("simple", "moderate", "challenging")

_CSS = """
:root{--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;
--muted:#898781;--grid:#e1e0d9;--line:rgba(11,11,11,.1);--accent:#2a78d6;
--good:#006300;--warn:#8a5b00}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 20px 64px}
.eyebrow{font-size:12px;font-weight:600;letter-spacing:.08em;
text-transform:uppercase;color:var(--muted);margin:0}
h1{margin:2px 0 4px;font-size:26px;line-height:1.25}
.page-meta{margin:0;color:var(--ink-2);font-size:13.5px}
.card{background:var(--surface);border:1px solid var(--line);
border-radius:10px;padding:20px 22px;margin-top:20px}
.card h2{margin:0 0 2px;font-size:18px}
.lead{margin:0 0 14px;color:var(--ink-2);font-size:13.5px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));
gap:12px;margin-top:20px}
.tile{background:var(--surface);border:1px solid var(--line);
border-radius:10px;padding:14px 16px}
.tile .label{font-size:11.5px;color:var(--muted);letter-spacing:.05em;
text-transform:uppercase}
.tile .value{font-size:25px;font-weight:600;margin-top:2px;
overflow-wrap:anywhere;line-height:1.2}
.tile .sub{font-size:12.5px;color:var(--ink-2);margin-top:3px;
overflow-wrap:anywhere}
.note-warn{margin:0 0 14px;padding:8px 12px;font-size:13px;color:var(--warn);
background:rgba(250,178,25,.10);border-left:3px solid #fab219;
border-radius:0 6px 6px 0}
.table-wrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px}
caption{text-align:left;font-size:12.5px;color:var(--muted);
padding:0 0 8px}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--grid);
white-space:nowrap}
thead th{font-size:11px;color:var(--muted);text-transform:uppercase;
letter-spacing:.05em}
th.num,td.num{text-align:right;font-variant-numeric:tabular-nums}
tbody tr:hover{background:rgba(42,120,214,.05)}
tr.winner{background:rgba(12,163,12,.07)}
td.zero{color:var(--muted)}
.badge{display:inline-block;font-size:10.5px;font-weight:600;
border-radius:999px;padding:1px 8px;margin-left:6px;vertical-align:1px}
.badge.winner{background:rgba(12,163,12,.12);color:var(--good)}
.badge.pareto{background:rgba(42,120,214,.12);color:#1c5cab}
.badge.hist{background:rgba(137,135,129,.16);color:var(--ink-2)}
.ex-cell{min-width:170px}
.ex-val{font-weight:600}
.ex-ci{color:var(--muted);font-size:12px;margin-left:6px}
.ci-track{position:relative;display:block;height:6px;margin-top:5px;
background:var(--grid);border-radius:3px;min-width:130px}
.ci-span{position:absolute;top:0;height:100%;border-radius:3px;
background:rgba(42,120,214,.35)}
.ci-dot{position:absolute;top:-1px;width:8px;height:8px;margin-left:-4px;
border-radius:50%;background:var(--accent);border:2px solid var(--surface)}
.bar-track{display:block;height:6px;min-width:110px;background:var(--grid);
border-radius:3px}
.bar-fill{display:block;height:100%;background:var(--accent);
border-radius:3px}
td.heat{text-align:right;font-variant-numeric:tabular-nums}
.swatches{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;
color:var(--ink-2);margin:0 0 10px}
.swatch{display:inline-block;width:10px;height:10px;border-radius:2px;
margin-right:5px;vertical-align:-1px}
.comp-bar{display:flex;gap:2px;height:10px;min-width:160px}
.comp-bar span{height:100%;border-radius:2px}
h3{margin:18px 0 8px;font-size:14.5px}
figure{margin:0}
figcaption{color:var(--muted);font-size:12.5px;margin-top:8px}
dl.meta{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px;
margin:0;font-size:13.5px}
dl.meta dt{font-weight:600}
dl.meta dd{margin:0;color:var(--ink-2);overflow-wrap:anywhere}
ul.plain{margin:4px 0 0;padding-left:18px}
"""


def write_dashboard(summary: dict[str, Any], path: Path) -> Path:
    boards = summary.get("leaderboards", [])
    if not boards:
        raise DashboardError("Comparison summary has no leaderboards")
    if len(boards) != 1:
        raise DashboardError(
            "Dashboard requires one compatible track/context/transport leaderboard"
        )
    board = boards[0]
    models = board.get("models", [])
    if not models:
        raise DashboardError("Comparison leaderboard has no models")
    for model in models:
        _decimal_float(model.get("cost_per_question_usd"))
    ranked = sorted(
        models,
        key=lambda model: (
            -float(model["execution_accuracy"]),
            _decimal_float(model.get("cost_per_question_usd")),
            model["model_name"],
        ),
    )

    title = (
        f"RDST Text-to-SQL: {board['track']} / {board['context_mode']} / "
        f"{board['transport']}"
    )
    sections = "\n".join(
        (
            _header_section(summary, board, title),
            _kpi_section(board, ranked),
            _hero_section(board, ranked),
            _leaderboard_section(board, ranked),
            _latency_section(ranked),
            _billing_section(ranked),
            _breakdown_section(ranked),
            _failure_section(ranked),
            _token_section(ranked),
            _reasoning_section(ranked),
            _methodology_section(summary, board),
        )
    )
    document = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="light">\n'
        f"<title>{_esc(title)}</title>\n"
        f"<style>{_CSS}</style>\n"
        f"<script>{get_plotlyjs()}</script>\n"
        f"</head>\n<body>\n<main>\n{sections}\n</main>\n</body>\n</html>\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, document)
    return path


def _header_section(summary: dict[str, Any], board: dict[str, Any], title: str) -> str:
    meta = [
        f"{summary.get('attempt_count', 0)} attempts",
        f"{board.get('expected_attempts_per_model', 0)} tasks per model",
    ]
    if summary.get("benchmark_elapsed_ms") is not None:
        minutes = float(summary["benchmark_elapsed_ms"]) / 60_000
        meta.append(f"{minutes:.1f} min wall time")
    source_runs = _source_runs(summary)
    if source_runs:
        meta.append(f"{len(source_runs)} source runs")
    return (
        '<header class="page">\n'
        '<p class="eyebrow">RDST Text-to-SQL Benchmark</p>\n'
        f"<h1>{_esc(title)}</h1>\n"
        f'<p class="page-meta">{" &middot; ".join(_esc(item) for item in meta)}</p>\n'
        "</header>"
    )


def _kpi_section(board: dict[str, Any], ranked: list[dict[str, Any]]) -> str:
    top = ranked[0]
    tiles = [
        _tile(
            "Top execution accuracy",
            _pct(top["execution_accuracy"]),
            f"{_esc(top['model_name'])} &middot; "
            f"{_usd(top.get('cost_per_question_usd'))}/task",
        )
    ]
    winner = next((m for m in ranked if m.get("cost_efficiency_winner")), None)
    if winner:
        sub = (
            f"{_pct(winner['execution_accuracy'])} EX &middot; "
            f"{_usd(winner.get('cost_per_correct_usd'))}/correct"
        )
        if board.get("winner_statistically_unstable"):
            sub += " &middot; ordering uncertain"
        tiles.append(_tile("Cost-efficient winner", _esc(winner["model_name"]), sub))
    cheapest = min(ranked, key=lambda m: _decimal_float(m.get("cost_per_question_usd")))
    tiles.append(
        _tile(
            "Cheapest cold cost",
            f"{_usd(cheapest.get('cost_per_question_usd'))}/task",
            f"{_esc(cheapest['model_name'])} &middot; "
            f"{_pct(cheapest['execution_accuracy'])} EX",
        )
    )
    fastest = min(ranked, key=lambda m: float(m.get("mean_latency_ms", 0.0)))
    tiles.append(
        _tile(
            "Fastest mean task",
            _seconds(fastest.get("mean_latency_ms", 0.0)),
            f"{_esc(fastest['model_name'])} &middot; "
            f"{_pct(fastest['execution_accuracy'])} EX",
        )
    )
    pareto_count = sum(1 for m in ranked if m.get("pareto_optimal"))
    tiles.append(
        _tile(
            "Models compared",
            str(len(ranked)),
            f"{pareto_count} on the Pareto frontier",
        )
    )
    return f'<section class="kpis" aria-label="Headline results">{"".join(tiles)}</section>'


def _tile(label: str, value: str, sub: str) -> str:
    return (
        f'<div class="tile"><div class="label">{label}</div>'
        f'<div class="value">{value}</div><div class="sub">{sub}</div></div>'
    )


def _hero_section(board: dict[str, Any], ranked: list[dict[str, Any]]) -> str:
    complete = [m for m in ranked if m.get("coverage") == 1.0]
    figure = go.Figure()
    if complete:
        best = max(float(m["execution_accuracy"]) for m in complete)
        figure.add_hrect(
            y0=max(best - 0.02, 0.0),
            y1=1.0,
            fillcolor="rgba(42,120,214,0.06)",
            line_width=0,
        )
    frontier = sorted(
        (
            (
                _decimal_float(m.get("cost_per_question_usd")),
                float(m["execution_accuracy"]),
            )
            for m in complete
            if m.get("pareto_optimal")
        ),
    )
    if frontier:
        figure.add_trace(
            go.Scatter(
                x=[point[0] for point in frontier],
                y=[point[1] for point in frontier],
                mode="lines",
                line={"dash": "dot", "width": 1.5, "color": _MUTED},
                hoverinfo="skip",
                showlegend=False,
            )
        )
    groups = (
        ("Pareto-optimal", _ACCENT, [m for m in ranked if m.get("pareto_optimal")]),
        (
            "Other models",
            _MUTED,
            [m for m in ranked if not m.get("pareto_optimal")],
        ),
    )
    position_cycle = ("top center", "bottom center")
    label_index = 0
    for name, color, members in groups:
        if not members:
            continue
        labels, positions = [], []
        for model in members:
            if _hero_labeled(model):
                text = _esc(model["model_name"])
                if model.get("cost_efficiency_winner"):
                    text += " (winner)"
                labels.append(text)
                positions.append(position_cycle[label_index % len(position_cycle)])
                label_index += 1
            else:
                labels.append("")
                positions.append("top center")
        figure.add_trace(
            go.Scatter(
                x=[_decimal_float(m.get("cost_per_question_usd")) for m in members],
                y=[float(m["execution_accuracy"]) for m in members],
                mode="markers+text",
                name=name,
                text=labels,
                textposition=positions,
                textfont={"size": 11.5, "color": _INK_2},
                cliponaxis=False,
                marker={
                    "size": [
                        14 if m.get("cost_efficiency_winner") else 10 for m in members
                    ],
                    "color": color,
                    "symbol": [
                        "diamond" if _is_historical(m) else "circle" for m in members
                    ],
                    "line": {"width": 1.5, "color": _SURFACE},
                },
                error_y={
                    "type": "data",
                    "symmetric": False,
                    "array": [
                        max(
                            float(m["accuracy_ci_95"][1])
                            - float(m["execution_accuracy"]),
                            0.0,
                        )
                        for m in members
                    ],
                    "arrayminus": [
                        max(
                            float(m["execution_accuracy"])
                            - float(m["accuracy_ci_95"][0]),
                            0.0,
                        )
                        for m in members
                    ],
                    "color": "rgba(11,11,11,0.15)",
                    "thickness": 1,
                    "width": 0,
                },
                hovertemplate=[_hover_text(m) for m in members],
            )
        )
    figure.update_xaxes(
        title_text="Standardized cold USD per task (log scale)",
        type="log",
        dtick="D2",
        tickprefix="$",
    )
    figure.update_yaxes(
        title_text="Execution accuracy", tickformat=".0%", range=[0, 1.04]
    )
    _style_figure(figure, height=520)
    caption = (
        "Error bars are 95% confidence intervals on execution accuracy. The shaded "
        "band marks winner eligibility (within two points of the best complete "
        "model); the dotted line is the accuracy-cost Pareto frontier. Diamonds are "
        "historical reference entries. Hover any point for full metrics."
    )
    return _section(
        "hero",
        "Accuracy vs standardized cold cost",
        _figure_html(figure, "hero-chart"),
        caption=caption,
    )


def _hero_labeled(model: dict[str, Any]) -> bool:
    return bool(
        model.get("pareto_optimal")
        or model.get("cost_efficiency_eligible")
        or model.get("cost_efficiency_winner")
    )


def _leaderboard_section(board: dict[str, Any], ranked: list[dict[str, Any]]) -> str:
    show_coverage = any(m.get("coverage") != 1.0 for m in ranked)
    head = [
        "<th>#</th><th>Model</th><th>Reasoning</th>",
        '<th class="ex-cell">EX (95% CI)</th>',
        '<th class="num">Cold $/task</th><th class="num">Cold $/correct</th>',
        '<th class="num">Billed $/task</th>',
        '<th class="num">Mean</th><th class="num">P95</th>',
    ]
    if show_coverage:
        head.append('<th class="num">Coverage</th>')
    rows = []
    for rank, model in enumerate(ranked, start=1):
        badges = ""
        if model.get("cost_efficiency_winner"):
            badges += '<span class="badge winner">winner</span>'
        if model.get("pareto_optimal"):
            badges += '<span class="badge pareto">pareto</span>'
        if _is_historical(model):
            badges += '<span class="badge hist">historical</span>'
        row_class = ' class="winner"' if model.get("cost_efficiency_winner") else ""
        cells = [
            f'<td class="num">{rank}</td>',
            f'<th scope="row">{_esc(model["model_name"])}{badges}</th>',
            f"<td>{_esc(model.get('reasoning_label', ''))}</td>",
            _ex_cell(model),
            f'<td class="num">{_usd(model.get("cost_per_question_usd"))}</td>',
            f'<td class="num">{_usd(model.get("cost_per_correct_usd"))}</td>',
            f'<td class="num">{_usd(model.get("billed_cost_per_question_usd"))}</td>',
            f'<td class="num">{_seconds(model.get("mean_latency_ms", 0.0))}</td>',
            f'<td class="num">{_seconds(model.get("p95_latency_ms", 0.0))}</td>',
        ]
        if show_coverage:
            cells.append(f'<td class="num">{_pct(model.get("coverage", 0.0))}</td>')
        rows.append(f"<tr{row_class}>{''.join(cells)}</tr>")
    note = ""
    if board.get("winner_statistically_unstable"):
        note = (
            '<p class="note-warn">Paired uncertainty does not establish the '
            "winner's accuracy ordering; treat the winner call as provisional.</p>"
        )
    table = _table(
        "".join(head),
        rows,
        caption="Ranked by execution accuracy, then standardized cold cost per task.",
    )
    return _section("leaderboard", "Leaderboard", note + table)


def _ex_cell(model: dict[str, Any]) -> str:
    accuracy = float(model["execution_accuracy"])
    low, high = (float(value) for value in model["accuracy_ci_95"])
    return (
        '<td class="ex-cell"><span class="ex-val">'
        f"{_pct(accuracy)}</span>"
        f'<span class="ex-ci">{low:.0%}&ndash;{high:.0%}</span>'
        '<span class="ci-track">'
        f'<span class="ci-span" style="left:{low:.1%};width:{max(high - low, 0.0):.1%}"></span>'
        f'<span class="ci-dot" style="left:{accuracy:.1%}"></span>'
        "</span></td>"
    )


def _latency_section(ranked: list[dict[str, Any]]) -> str:
    ordered = sorted(ranked, key=lambda m: float(m.get("mean_latency_ms", 0.0)))
    max_mean = max((float(m.get("mean_latency_ms", 0.0)) for m in ordered), default=0.0)
    rows = []
    for model in ordered:
        mean_ms = float(model.get("mean_latency_ms", 0.0))
        share = mean_ms / max_mean if max_mean else 0.0
        rows.append(
            "<tr>"
            f'<th scope="row">{_esc(model["model_name"])}</th>'
            f'<td class="num">{_seconds(mean_ms)}</td>'
            f'<td><span class="bar-track"><span class="bar-fill" '
            f'style="width:{share:.1%}"></span></span></td>'
            f'<td class="num">{_seconds(model.get("median_latency_ms", 0.0))}</td>'
            f'<td class="num">{_seconds(model.get("p95_latency_ms", 0.0))}</td>'
            f'<td class="num">{_seconds(model.get("mean_model_call_latency_ms", 0.0))}</td>'
            f'<td class="num">{float(model.get("tasks_per_minute", 0.0)):.1f}</td>'
            "</tr>"
        )
    head = (
        '<th>Model</th><th class="num">Mean</th><th></th>'
        '<th class="num">Median</th><th class="num">P95</th>'
        '<th class="num">Model call mean</th><th class="num">Tasks/min</th>'
    )
    return _section(
        "latency",
        "Latency",
        _table(head, rows, caption="Per-task latency over scored attempts."),
    )


def _billing_section(ranked: list[dict[str, Any]]) -> str:
    rows = []
    for model in ranked:
        repair_rate = float(model.get("repair_rate", 0.0))
        rows.append(
            "<tr>"
            f'<th scope="row">{_esc(model["model_name"])}</th>'
            f'<td class="num">{_usd(model.get("billed_cost_per_question_usd"))}</td>'
            f'<td class="num">{_usd(model.get("billed_cost_per_correct_usd"))}</td>'
            f'<td class="num">{_usd(model.get("actual_cost_usd"))}</td>'
            f'<td class="num">{_usd(model.get("expected_billed_cost_usd"))}</td>'
            f'<td class="num">{_usd(model.get("billing_variance_usd"))}</td>'
            f'<td class="num">{_usd(model.get("repair_actual_cost_usd"))}</td>'
            f'<td class="num">{_pct(repair_rate)}</td>'
            "</tr>"
        )
    head = (
        '<th>Model</th><th class="num">Billed $/task</th>'
        '<th class="num">Billed $/correct</th><th class="num">Actual total</th>'
        '<th class="num">Expected billed</th><th class="num">Variance</th>'
        '<th class="num">Repair spend</th><th class="num">Repair rate</th>'
    )
    caption = (
        "Observed provider billing, including repair calls. Operational only: the "
        "leaderboard and hero chart rank exclusively on standardized cold cost."
    )
    return _section("billing", "Observed billing", _table(head, rows, caption=caption))


def _breakdown_section(ranked: list[dict[str, Any]]) -> str:
    difficulties = {key for m in ranked for key in m.get("accuracy_by_difficulty", {})}
    difficulty_order = [d for d in _DIFFICULTY_ORDER if d in difficulties] + sorted(
        difficulties - set(_DIFFICULTY_ORDER)
    )
    databases = sorted(
        {key for m in ranked for key in m.get("accuracy_by_database", {})}
    )
    body = (
        "<h3>By difficulty</h3>"
        + _heat_table(ranked, "accuracy_by_difficulty", difficulty_order)
        + "<h3>By database</h3>"
        + _heat_table(ranked, "accuracy_by_database", databases)
    )
    caption = (
        "Execution accuracy over scored attempts; darker is higher. Hover a cell "
        "for the attempt count and confidence interval."
    )
    return _section("breakdown", "Accuracy breakdown", body, caption=caption)


def _heat_table(ranked: list[dict[str, Any]], field: str, columns: list[str]) -> str:
    head = "<th>Model</th>" + "".join(
        f'<th class="num">{_esc(column)}</th>' for column in columns
    )
    rows = []
    for model in ranked:
        cells = [f'<th scope="row">{_esc(model["model_name"])}</th>']
        breakdown = model.get(field, {})
        for column in columns:
            entry = breakdown.get(column)
            if entry is None:
                cells.append('<td class="num zero">n/a</td>')
                continue
            accuracy = float(entry["execution_accuracy"])
            low, high = (float(v) for v in entry.get("accuracy_ci_95", (0.0, 0.0)))
            step = min(round(accuracy * (len(_SEQ_RAMP) - 1)), len(_SEQ_RAMP) - 1)
            ink = "#ffffff" if step >= 7 else _INK
            title = f"n={entry.get('attempt_count', 0)}, CI {low:.0%}-{high:.0%}"
            cells.append(
                f'<td class="heat" style="background:{_SEQ_RAMP[step]};color:{ink}" '
                f'title="{_esc(title)}">{_pct(accuracy)}</td>'
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return _table(head, rows)


def _failure_section(ranked: list[dict[str, Any]]) -> str:
    outcomes = sorted(
        {
            outcome
            for model in ranked
            for outcome in model.get("outcome_counts", {})
            if outcome != "correct"
        }
    )
    head = ['<th>Model</th><th class="num">Correct</th>']
    head.extend(
        f'<th class="num">{_esc(outcome.replace("_", " "))}</th>'
        for outcome in outcomes
    )
    head.append('<th class="num">Silent wrong</th><th class="num">Unscored</th>')
    rows = []
    for model in ranked:
        counts = model.get("outcome_counts", {})
        cells = [
            f'<th scope="row">{_esc(model["model_name"])}</th>',
            _count_cell(counts.get("correct", 0)),
        ]
        cells.extend(_count_cell(counts.get(outcome, 0)) for outcome in outcomes)
        cells.append(
            f'<td class="num">{_pct(model.get("silent_wrong_rate", 0.0))}</td>'
        )
        cells.append(_count_cell(model.get("unscored_attempt_count", 0)))
        rows.append(f"<tr>{''.join(cells)}</tr>")
    caption = (
        "Attempt outcomes per model. Silent wrong is the share of scored attempts "
        "that executed but returned an incorrect result."
    )
    return _section(
        "failures", "Failures", _table("".join(head), rows, caption=caption)
    )


def _count_cell(count: int) -> str:
    zero = " zero" if not count else ""
    return f'<td class="num{zero}">{int(count):,}</td>'


def _token_section(ranked: list[dict[str, Any]]) -> str:
    swatches = "".join(
        f'<span><span class="swatch" style="background:{color}"></span>{label}</span>'
        for label, color in _TOKEN_SERIES
    )
    rows = []
    for model in ranked:
        parts = _token_parts(model)
        total = sum(parts)
        segments = "".join(
            f'<span style="width:{value / total:.1%};background:{color}"></span>'
            for value, (_, color) in zip(parts, _TOKEN_SERIES)
            if total and value
        )
        rows.append(
            "<tr>"
            f'<th scope="row">{_esc(model["model_name"])}</th>'
            f'<td><span class="comp-bar">{segments}</span></td>'
            + "".join(f'<td class="num">{value:,}</td>' for value in parts)
            + f'<td class="num">{total:,}</td></tr>'
        )
    head = (
        "<th>Model</th><th>Composition</th>"
        + "".join(f'<th class="num">{label}</th>' for label, _ in _TOKEN_SERIES)
        + '<th class="num">Total</th>'
    )
    body = (
        f'<div class="swatches">{swatches}</div>'
        + _table(head, rows, caption="Token totals across scored model calls.")
        + "<h3>Standardized cold cost by stage</h3>"
        + _stage_table(ranked)
    )
    return _section("tokens", "Tokens and stage cost", body)


def _token_parts(model: dict[str, Any]) -> tuple[int, int, int, int]:
    cached = int(model.get("cached_input_tokens", 0))
    reasoning = int(model.get("reasoning_tokens", 0))
    return (
        max(int(model.get("input_tokens", 0)) - cached, 0),
        cached,
        max(int(model.get("output_tokens", 0)) - reasoning, 0),
        reasoning,
    )


def _stage_table(ranked: list[dict[str, Any]]) -> str:
    stages = sorted({stage for m in ranked for stage in m.get("cost_by_stage", {})})
    head = "<th>Model</th>" + "".join(
        f'<th class="num">{_esc(stage.replace("_", " "))}</th>' for stage in stages
    )
    rows = []
    for model in ranked:
        cells = [f'<th scope="row">{_esc(model["model_name"])}</th>']
        for stage in stages:
            entry = model.get("cost_by_stage", {}).get(stage)
            if entry is None:
                cells.append('<td class="num zero">n/a</td>')
            else:
                cells.append(
                    f'<td class="num">{_usd(entry.get("normalized_cold_cost_usd"))} '
                    f"({int(entry.get('call_count', 0))} calls)</td>"
                )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return _table(head, rows)


def _reasoning_section(ranked: list[dict[str, Any]]) -> str:
    figure = go.Figure(
        go.Scatter(
            x=[int(m.get("reasoning_tokens", 0)) for m in ranked],
            y=[float(m["execution_accuracy"]) for m in ranked],
            mode="markers",
            marker={
                "size": 10,
                "color": _ACCENT,
                "symbol": [
                    "diamond" if _is_historical(m) else "circle" for m in ranked
                ],
                "line": {"width": 1.5, "color": _SURFACE},
            },
            hovertemplate=[_hover_text(m) for m in ranked],
            showlegend=False,
        )
    )
    figure.update_xaxes(title_text="Total reasoning tokens (scored calls)")
    figure.update_yaxes(
        title_text="Execution accuracy", tickformat=".0%", range=[0, 1.04]
    )
    _style_figure(figure, height=420)
    caption = (
        "Reasoning spend against accuracy; the reasoning effort each entry ran "
        "with is listed in the leaderboard and in each point's hover detail."
    )
    return _section(
        "reasoning",
        "Reasoning",
        _figure_html(figure, "reasoning-chart"),
        caption=caption,
    )


def _methodology_section(summary: dict[str, Any], board: dict[str, Any]) -> str:
    entries = [
        (
            "Cost basis",
            (
                "Standardized cold cost recomputes each call from observed tokens "
                "at pinned cold route prices, so caching policy differences never "
                "affect the ranking. Observed billing is reported separately."
            ),
        ),
        (
            "Winner rule",
            (
                "Cheapest model by standardized cold cost per correct answer among "
                "eligible models: complete coverage and execution accuracy within "
                "two percentage points of the best complete model."
            ),
        ),
        (
            "Pareto frontier",
            (
                "A model is Pareto-optimal when no other complete model has both "
                "higher accuracy and lower standardized cold cost per task."
            ),
        ),
        (
            "Uncertainty",
            (
                "95% intervals use Wilson bounds for single-repetition runs and a "
                "question-level bootstrap otherwise. Paired bootstrap deltas flag "
                "when the winner's ordering is statistically unstable."
            ),
        ),
    ]
    excluded = summary.get("headline_excluded_case_ids", [])
    if excluded:
        entries.append(
            (
                "Accuracy basis",
                (
                    "Internal headline EX excludes optimizer-sensitive gold cases "
                    + ", ".join(str(case_id) for case_id in excluded)
                    + ". Official full-set EX remains available in the machine-readable report."
                ),
            )
        )
    if any(_is_historical(m) for m in board.get("models", [])):
        entries.append(
            (
                "Historical entries",
                (
                    "Models labeled historical are prior-run reference points and "
                    "are drawn as diamonds in the charts."
                ),
            )
        )
    items = "".join(f"<dt>{name}</dt><dd>{text}</dd>" for name, text in entries)
    provenance = []
    source_runs = _source_runs(summary)
    if source_runs:
        runs = "".join(f"<li>{_esc(run)}</li>" for run in source_runs)
        provenance.append(f'<h3>Source runs</h3><ul class="plain">{runs}</ul>')
    warnings = summary.get("comparison", {}).get("compatibility_warnings", [])
    if warnings:
        notes = "".join(f"<li>{_esc(warning)}</li>" for warning in warnings)
        provenance.append(
            f'<h3>Compatibility warnings</h3><ul class="plain">{notes}</ul>'
        )
    return _section(
        "methodology",
        "Methodology and provenance",
        f'<dl class="meta">{items}</dl>{"".join(provenance)}',
    )


def _source_runs(summary: dict[str, Any]) -> list[str]:
    runs = summary.get("source_runs") or summary.get("comparison", {}).get("run_ids")
    return [str(run) for run in runs] if runs else []


def _section(
    section_id: str, heading: str, body: str, caption: str | None = None
) -> str:
    caption_html = f'<p class="lead">{caption}</p>' if caption else ""
    return (
        f'<section id="{section_id}" class="card">'
        f"<h2>{heading}</h2>{caption_html}{body}</section>"
    )


def _table(head: str, rows: list[str], caption: str | None = None) -> str:
    caption_html = f"<caption>{caption}</caption>" if caption else ""
    return (
        '<div class="table-wrap"><table>'
        f"{caption_html}<thead><tr>{head}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _style_figure(figure: go.Figure, *, height: int) -> None:
    figure.update_layout(
        template="plotly_white",
        autosize=True,
        height=height,
        margin={"t": 24, "r": 16, "b": 56, "l": 64},
        paper_bgcolor=_SURFACE,
        plot_bgcolor=_SURFACE,
        font={"family": _FONT_STACK, "size": 13, "color": _INK},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.0, "x": 0.0},
        hoverlabel={"font": {"family": _FONT_STACK, "size": 12.5}},
    )
    figure.update_xaxes(gridcolor=_GRID, zeroline=False, linecolor=_GRID)
    figure.update_yaxes(gridcolor=_GRID, zeroline=False, linecolor=_GRID)


def _figure_html(figure: go.Figure, div_id: str) -> str:
    fragment = figure.to_html(
        full_html=False,
        include_plotlyjs=False,
        div_id=div_id,
        config=_PLOT_CONFIG,
        default_width="100%",
    )
    return f"<figure>{fragment}</figure>"


def _is_historical(model: dict[str, Any]) -> bool:
    return "historical" in str(model.get("model_name", ""))


def _decimal_float(value: Any) -> float:
    if value is None:
        raise DashboardError("Dashboard requires complete standardized task costs")
    number = float(Decimal(str(value)))
    if number <= 0:
        raise DashboardError("Dashboard requires positive standardized task costs")
    return number


def _esc(value: Any, *, quote: bool = True) -> str:
    return html.escape(str(value), quote=quote)


def _usd(value: Any) -> str:
    if value is None:
        return "n/a"
    number = float(Decimal(str(value)))
    if number == 0:
        return "$0"
    if abs(number) >= 1000:
        return f"${number:,.0f}"
    if abs(number) >= 1:
        return f"${number:,.2f}"
    return f"${number:.3g}"


def _pct(value: Any) -> str:
    return f"{float(value):.1%}"


def _seconds(milliseconds: Any) -> str:
    return f"{float(milliseconds) / 1000:.2f}s"


def _hover_text(model: dict[str, Any]) -> str:
    billed = model.get("billed_cost_per_question_usd")
    cold_correct = model.get("cost_per_correct_usd")
    low, high = (float(value) for value in model["accuracy_ci_95"])
    return (
        f"<b>{_esc(model['model_name'])}</b><br>"
        f"Reasoning: {_esc(model.get('reasoning_label', 'n/a'))}<br>"
        f"EX: {_pct(model['execution_accuracy'])} "
        f"(CI {low:.0%}-{high:.0%})<br>"
        f"Cold/task: {_usd(model.get('cost_per_question_usd'))}<br>"
        f"Cold/correct: {_usd(cold_correct)}<br>"
        f"Billed/task: {_usd(billed)}<br>"
        f"Mean latency: {_seconds(model.get('mean_latency_ms', 0.0))}<br>"
        f"P95 latency: {_seconds(model.get('p95_latency_ms', 0.0))}<br>"
        f"Reasoning tokens: {int(model.get('reasoning_tokens', 0)):,}"
        "<extra></extra>"
    )
