"""RDST audit/fleet HTML report renderer.

One unified renderer with three sections: Overview, Detailed Analysis, Next Steps.
Handles single-target (N=1) and fleet (N>=2) by parameter, not by separate entry points.

Follows Tanmay's design system from rdst_fleet_combined.html: Inter body, Plus
Jakarta Sans headings, JetBrains Mono for code, slate-900 brand color, four
severity variants (ok/warn/crit/info) with soft backgrounds and colored borders.

Public API:
- render_report_html(results, fleet_insights, title=None) -> str
    Accepts a list of audit_result dicts. If len(results) == 1 it renders the
    single-target variant; otherwise the fleet variant. fleet_insights is the
    parsed fleet LLM response dict (health_score, top_findings, fleet_findings,
    next_steps); may be None for single-target.

Thin back-compat wrappers render_v3_single_html / render_v3_fleet_html still
exist for existing CLI call sites until they're updated.
"""

from __future__ import annotations

import html as _h
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ============================================================================
# Design system — Tanmay's tokens from rdst_fleet_combined.html
# ============================================================================

STYLE_BLOCK = """
<style>
:root {
  --bg:#F8F9FB; --surface:#FFFFFF; --surface-alt:#F1F4F8;
  --border:#E2E7EE; --border-light:#EEF1F6;
  --text-1:#111827; --text-2:#4B5563; --text-3:#9CA3AF;
  --brand:#0F172A; --brand-light:#1E293B;
  --accent:#0EA5E9; --accent-soft:#E0F2FE;
  --ok:#10B981; --ok-soft:#ECFDF5; --ok-border:#A7F3D0; --ok-dark:#065F46;
  --warn:#F59E0B; --warn-soft:#FFFBEB; --warn-border:#FDE68A; --warn-dark:#92400E;
  --crit:#EF4444; --crit-soft:#FEF2F2; --crit-border:#FECACA; --crit-dark:#991B1B;
  --info:#3B82F6; --info-soft:#EFF6FF; --info-border:#BFDBFE; --info-dark:#1E40AF;
  --chart-1:#0EA5E9; --chart-2:#8B5CF6; --chart-gray:#CBD5E1;
}
* { margin:0; padding:0; box-sizing:border-box; }
body {
  font-family:'Inter',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:14px; line-height:1.6; color:var(--text-2);
  background:var(--bg); -webkit-font-smoothing:antialiased;
}
.container { max-width:980px; margin:0 auto; padding:28px 24px; }
h2,h3,h4 { font-family:'Plus Jakarta Sans','Inter',sans-serif; color:var(--text-1); }

.sec { margin-bottom:28px; }
.sec-head {
  display:flex; align-items:center; gap:10px;
  padding-bottom:10px; margin-bottom:16px;
  border-bottom:2px solid var(--border-light);
}
.sec-num {
  width:26px; height:26px; border-radius:8px;
  background:var(--brand); color:#fff;
  font-size:12px; font-weight:700;
  display:flex; align-items:center; justify-content:center; flex-shrink:0;
}
.sec-title { font-size:17px; font-weight:700; letter-spacing:-0.2px; }
.sec-badge {
  font-size:10px; font-weight:600; color:var(--text-3);
  background:var(--surface-alt); padding:2px 8px; border-radius:10px; margin-left:auto;
}

.card { background:var(--surface); border:1px solid var(--border); border-radius:12px; overflow:hidden; }
.card-pad { padding:18px 20px; }

.dot { width:8px; height:8px; border-radius:50%; display:inline-block; flex-shrink:0; }
.dot-ok { background:var(--ok); }
.dot-warn { background:var(--warn); }
.dot-crit { background:var(--crit); }
.dot-info { background:var(--info); }

.stag {
  display:inline-flex; align-items:center; gap:5px;
  padding:3px 10px; border-radius:6px;
  font-size:11px; font-weight:600;
}
.stag-ok { background:var(--ok-soft); color:var(--ok-dark); border:1px solid var(--ok-border); }
.stag-warn { background:var(--warn-soft); color:var(--warn-dark); border:1px solid var(--warn-border); }
.stag-crit { background:var(--crit-soft); color:var(--crit-dark); border:1px solid var(--crit-border); }
.stag-info { background:var(--info-soft); color:var(--info-dark); border:1px solid var(--info-border); }

.gauge { display:flex; align-items:center; gap:10px; margin:6px 0; }
.gauge-lbl { font-size:12px; color:var(--text-2); width:130px; flex-shrink:0; font-weight:500; }
.gauge-track { flex:1; height:7px; background:var(--surface-alt); border-radius:4px; overflow:hidden; }
.gauge-fill { height:100%; border-radius:4px; }
.gauge-val { font-size:12px; font-weight:600; color:var(--text-1); width:80px; text-align:right; flex-shrink:0; }
.fill-ok { background:var(--ok); }
.fill-warn { background:var(--warn); }
.fill-crit { background:var(--crit); }
.fill-accent { background:var(--accent); }
.fill-gray { background:var(--chart-gray); }

.dtable { width:100%; border-collapse:collapse; font-size:13px; }
.dtable thead tr { background:var(--brand); }
.dtable th {
  padding:9px 14px; font-family:'Plus Jakarta Sans',sans-serif;
  font-size:10px; font-weight:600; color:rgba(255,255,255,.7);
  letter-spacing:.7px; text-transform:uppercase; text-align:center;
}
.dtable th:first-child { text-align:left; }
.dtable td {
  padding:9px 14px; border-bottom:1px solid var(--border-light);
  text-align:center; vertical-align:middle;
}
.dtable td:first-child { text-align:left; }
.dtable tbody tr:last-child td { border-bottom:none; }

.ibox { border-radius:10px; padding:13px 18px; margin:14px 0; font-size:13px; line-height:1.6; color:var(--text-2); }
.ibox b { color:var(--text-1); }
.ibox-ok { background:var(--ok-soft); border:1px solid var(--ok-border); border-left:3px solid var(--ok); }
.ibox-warn { background:var(--warn-soft); border:1px solid var(--warn-border); border-left:3px solid var(--warn); }
.ibox-crit { background:var(--crit-soft); border:1px solid var(--crit-border); border-left:3px solid var(--crit); }
.ibox-info { background:var(--info-soft); border:1px solid var(--info-border); border-left:3px solid var(--info); }

.finding { display:flex; gap:10px; padding:10px 14px; background:var(--surface); border:1px solid var(--border); border-radius:8px; font-size:13px; line-height:1.55; margin-bottom:6px; }
.finding-dot { margin-top:5px; }
.finding b { color:var(--text-1); }

.actions { list-style:none; background:var(--surface); border:1px solid var(--border); border-radius:12px; overflow:hidden; padding:0; }
.actions li { padding:13px 18px; border-bottom:1px solid var(--border-light); font-size:13px; line-height:1.6; display:flex; gap:10px; }
.actions li:last-child { border-bottom:none; }
.act-n {
  flex-shrink:0; width:22px; height:22px; border-radius:50%;
  background:var(--brand); color:#fff; font-size:11px; font-weight:700;
  display:flex; align-items:center; justify-content:center; margin-top:2px;
}
.actions .cmd {
  display:block; margin-top:8px; padding:6px 10px;
  background:var(--surface-alt); border:1px solid var(--border);
  border-radius:6px; color:var(--text-1);
  font-family:'JetBrains Mono',monospace; font-size:11.5px; line-height:1.6;
}
.actions .savings { display:inline-block; margin-top:6px; font-size:12px; font-weight:600; color:var(--ok-dark); }

.mono { font-family:'JetBrains Mono',monospace; }

.hero { background:var(--brand); border-radius:16px; overflow:hidden; margin-bottom:24px; position:relative; }
.hero-bar { height:3px; background:linear-gradient(90deg,#0EA5E9,#8B5CF6,#0EA5E9); }
.hero-inner { padding:28px 32px 24px; }
.hero-top { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:16px; }
.hero-badge {
  font-family:'Plus Jakarta Sans',sans-serif;
  font-size:10px; font-weight:700; letter-spacing:1.5px; text-transform:uppercase;
  padding:4px 10px; border-radius:4px;
  background:rgba(14,165,233,.12); border:1px solid rgba(14,165,233,.25); color:#7DD3FC;
}
.hero-date { font-size:11px; color:rgba(255,255,255,.3); }
.hero-title { font-family:'Plus Jakarta Sans',sans-serif; font-size:26px; font-weight:800; color:#fff; letter-spacing:-.5px; margin-bottom:4px; }
.hero-sub { font-size:13px; color:rgba(255,255,255,.45); margin-bottom:20px; }

.bluf { display:flex; gap:24px; align-items:stretch; padding-top:20px; border-top:1px solid rgba(255,255,255,.07); }
.bluf-score { flex-shrink:0; display:flex; flex-direction:column; align-items:center; justify-content:center; position:relative; width:140px; }
.bluf-score svg { display:block; }
.score-bg { fill:none; stroke:rgba(255,255,255,.07); stroke-width:8; }
.score-fill { fill:none; stroke-width:8; stroke-linecap:round; }
.score-lbl { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; }
.score-num { font-family:'Plus Jakarta Sans',sans-serif; font-size:34px; font-weight:800; color:#fff; line-height:1; }
.score-of { font-size:11px; color:rgba(255,255,255,.35); margin-top:3px; }
.score-word { font-family:'Plus Jakarta Sans',sans-serif; font-size:11px; font-weight:700; color:#7DD3FC; letter-spacing:.5px; margin-top:5px; }
.bluf-stats { flex:1; display:grid; grid-template-columns:repeat(3,1fr); gap:10px; }
.bluf-card { background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.06); border-radius:10px; padding:14px 12px; text-align:center; }
.bluf-val { font-family:'Plus Jakarta Sans',sans-serif; font-size:22px; font-weight:800; color:#fff; line-height:1.1; }
.bluf-val.green { color:#34D399; }
.bluf-val.cyan { color:#67E8F9; }
.bluf-lbl { font-size:10px; color:rgba(255,255,255,.45); text-transform:uppercase; letter-spacing:.4px; margin-top:3px; font-weight:500; }
.bluf-det { font-size:10px; color:rgba(255,255,255,.3); margin-top:2px; }

.top-findings { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:24px; }
.tf-card { border-radius:10px; padding:14px 16px; display:flex; gap:10px; align-items:flex-start; }
.tf-card.crit { background:var(--crit-soft); border:1px solid var(--crit-border); }
.tf-card.warn { background:var(--warn-soft); border:1px solid var(--warn-border); }
.tf-card.ok { background:var(--ok-soft); border:1px solid var(--ok-border); }
.tf-card.info { background:var(--info-soft); border:1px solid var(--info-border); }
.tf-dot { width:10px; height:10px; border-radius:50%; flex-shrink:0; margin-top:4px; }
.tf-text { font-size:12px; line-height:1.5; }
.tf-card.crit .tf-text { color:var(--crit-dark); }
.tf-card.warn .tf-text { color:var(--warn-dark); }
.tf-card.ok .tf-text { color:var(--ok-dark); }
.tf-card.info .tf-text { color:var(--info-dark); }
.tf-text strong { display:block; font-size:13px; margin-bottom:2px; }

.topo { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:16px; }
.topo-card { background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:14px 16px; position:relative; overflow:hidden; }
.topo-card::before { content:''; position:absolute; top:0; left:0; right:0; height:3px; }
.topo-card.primary::before { background:var(--chart-1); }
.topo-card.replica::before { background:var(--chart-2); }
.topo-role { font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:1px; margin-bottom:3px; }
.topo-card.primary .topo-role { color:var(--chart-1); }
.topo-card.replica .topo-role { color:var(--chart-2); }
.topo-name { font-family:'Plus Jakarta Sans',sans-serif; font-size:14px; font-weight:700; color:var(--text-1); margin-bottom:8px; }
.topo-grid { display:grid; grid-template-columns:1fr 1fr; gap:4px; font-size:11px; }
.topo-grid span > span.ml { font-size:9px; text-transform:uppercase; letter-spacing:.4px; color:var(--text-3); font-weight:600; display:block; }
.topo-grid span > span.mv { font-weight:600; color:var(--text-1); font-size:12px; }

.target-head { margin:24px 0 14px; padding-bottom:12px; border-bottom:2px solid var(--border-light); display:flex; align-items:center; gap:10px; }
.target-head:first-child { margin-top:0; }
.target-role-pill { font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:1px; padding:3px 10px; border-radius:100px; }
.target-role-pill.primary { background:rgba(14,165,233,.10); border:1px solid rgba(14,165,233,.3); color:var(--chart-1); }
.target-role-pill.replica { background:rgba(139,92,246,.10); border:1px solid rgba(139,92,246,.3); color:var(--chart-2); }
.target-name { font-family:'Plus Jakarta Sans',sans-serif; font-size:16px; font-weight:700; color:var(--text-1); }

.subhead { font-family:'Plus Jakarta Sans',sans-serif; font-size:14px; font-weight:700; color:var(--text-1); margin:16px 0 8px; }

.kv-table { width:100%; border-collapse:collapse; font-size:13px; }
.kv-table td { padding:10px 14px; border-bottom:1px solid var(--border-light); }
.kv-table td:first-child { font-weight:600; color:var(--text-1); width:200px; }
.kv-table tbody tr:last-child td { border-bottom:none; }

.findings-list { list-style:none; padding:0; margin:12px 0; background:#F8FAFC; border:1px solid var(--border-light); border-radius:12px; overflow:hidden; }
.findings-list li { padding:12px 18px; border-bottom:1px solid var(--border-light); font-size:13px; color:#334155; line-height:1.6; display:flex; gap:10px; align-items:flex-start; }
.findings-list li:last-child { border-bottom:none; }
.findings-list .f-mark { flex-shrink:0; margin-top:0; }

.query-table td code { font-family:'JetBrains Mono',monospace; font-size:11px; color:var(--text-2); line-height:1.5; }
.query-table .qh { font-family:'JetBrains Mono',monospace; font-size:11px; color:#00838F; font-weight:700; }
.query-table .qshow { margin-top:4px; font-family:'JetBrains Mono',monospace; font-size:10px; color:var(--text-3); }

.idx-card { border:1px solid var(--border-light); border-radius:8px; padding:14px 16px; margin:10px 0; background:var(--surface); }
.idx-card .idx-title { font-family:'Plus Jakarta Sans',sans-serif; font-weight:700; color:var(--text-1); margin-bottom:4px; }
.idx-card .idx-reason { font-size:13px; color:var(--text-2); margin-bottom:8px; }
.idx-card pre { background:var(--brand); color:#CBD5E1; padding:10px 14px; border-radius:6px; font-family:'JetBrains Mono',monospace; font-size:11.5px; line-height:1.75; margin:0; white-space:pre-wrap; }
.idx-card pre .kw { color:#A78BFA; font-weight:600; }
.idx-card pre .id { color:#7DD3FC; }
.idx-card pre .on { color:#FCD34D; }

.closing-verdict { margin-top:20px; padding:14px 18px; border-radius:10px; font-size:13px; line-height:1.6; border:1px solid var(--border); border-left:3px solid var(--text-3); color:var(--text-2); background:var(--surface-alt); }
.closing-verdict.ok { background:var(--ok-soft); border-color:var(--ok-border); border-left-color:var(--ok); color:var(--ok-dark); }
.closing-verdict.warn { background:var(--warn-soft); border-color:var(--warn-border); border-left-color:var(--warn); color:var(--warn-dark); }
.closing-verdict b { color:var(--text-1); }

.footer { text-align:center; padding:20px 0 12px; border-top:1px solid var(--border); margin-top:32px; font-size:11px; color:var(--text-3); }
.footer b { color:var(--text-2); }

/* Native collapsible — per-target sections + SQL expansion */
details.target-section { margin:24px 0; }
details.target-section > summary { list-style:none; cursor:pointer; padding:12px 14px; background:var(--surface); border:1px solid var(--border); border-radius:10px; display:flex; align-items:center; gap:10px; }
details.target-section > summary::-webkit-details-marker { display:none; }
details.target-section > summary::before { content:'▶'; color:var(--text-3); font-size:14px; transition:transform .2s; }
details.target-section[open] > summary::before { transform:rotate(90deg); }
details.target-section > summary .summary-meta { margin-left:auto; font-size:11px; color:var(--text-3); font-weight:500; }
details.target-section > .target-body { padding:14px 4px 0 4px; }
details.sql-expand { cursor:default; }
details.sql-expand > summary { list-style:none; cursor:pointer; display:inline-block; font-size:10px; color:var(--text-3); font-family:'JetBrains Mono',monospace; padding:2px 0; }
details.sql-expand > summary::-webkit-details-marker { display:none; }
details.sql-expand > summary::before { content:'▸ show full SQL'; }
details.sql-expand[open] > summary::before { content:'▾ hide SQL'; }
details.sql-expand > pre { margin-top:6px; padding:10px 12px; background:var(--brand); color:#CBD5E1; border-radius:6px; font-family:'JetBrains Mono',monospace; font-size:11px; line-height:1.5; white-space:pre-wrap; word-break:break-word; }

@media(max-width:720px){
  .topo, .top-findings, .bluf-stats { grid-template-columns:1fr; }
  .bluf { flex-direction:column; }
}
</style>
"""


# ============================================================================
# Helpers
# ============================================================================


def esc(s: Any) -> str:
    if s is None:
        return ""
    return _h.escape(str(s))


def fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "—"


def fmt_ms(n: Any) -> str:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "—"
    if v >= 1000:
        return f"{v/1000:.2f}s"
    if v >= 10:
        return f"{v:.0f} ms"
    return f"{v:.2f} ms"


def fmt_pct(n: Any) -> str:
    try:
        return f"{float(n):.1f}%"
    except (TypeError, ValueError):
        return "—"


def fmt_mb(mb: Any) -> str:
    try:
        v = float(mb)
    except (TypeError, ValueError):
        return "—"
    if v >= 1024:
        return f"{v/1024:.1f} GB"
    return f"{v:.0f} MB"


def _label_from_score(s: Optional[int]) -> str:
    if s is None:
        return ""
    if s >= 90:
        return "EXCELLENT"
    if s >= 75:
        return "GOOD"
    if s >= 60:
        return "FAIR"
    if s >= 40:
        return "POOR"
    return "CRITICAL"


def _score_color(s: Optional[int]) -> str:
    if s is None:
        return "#67E8F9"
    if s >= 75:
        return "#34D399"
    if s >= 60:
        return "#67E8F9"
    if s >= 40:
        return "#F59E0B"
    return "#EF4444"


def _role_of(result: Dict[str, Any]) -> str:
    metrics = result.get("metrics") or {}
    if metrics.get("is_replica"):
        return "replica"
    return "primary"


def _role_label(role: str) -> str:
    return "Read Replica" if role == "replica" else "Primary"


def _highlight_index_sql(sql: str) -> str:
    """Lightly syntax-color a CREATE INDEX statement. Escapes first so the
    wrapping <span> tags don't get escaped.
    """
    import re
    escaped = esc(sql)
    # Order matters — keywords first, then identifiers.
    escaped = re.sub(
        r"\b(CREATE INDEX CONCURRENTLY|CREATE INDEX|CREATE UNIQUE INDEX|DROP INDEX CONCURRENTLY|DROP INDEX|USING)\b",
        r'<span class="kw">\1</span>',
        escaped,
    )
    escaped = re.sub(r"\b(ON)\b", r'<span class="on">\1</span>', escaped)
    return escaped


def _engine_version(result: Dict[str, Any]) -> str:
    m = result.get("metrics") or {}
    raw = (m.get("server_version") or "").strip()
    if not raw:
        return (result.get("engine") or "unknown").title()
    if "PostgreSQL" in raw:
        parts = raw.split()
        if len(parts) >= 2:
            return f"PostgreSQL {parts[1]}"
    if "MySQL" in raw:
        parts = raw.split()
        return parts[0] if parts else raw
    return raw.split(" ")[0] if " " in raw else raw


# ============================================================================
# Fleet savings calculation
# ============================================================================


def compute_fleet_savings(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Derive fleet-level savings from replica elimination + rightsizing.

    Model: when a caching layer absorbs repetitive reads, replicas become
    redundant. Estimated savings =
        sum(replica_monthly_cost) +
        sum(rightsize_savings_on_remaining_targets)
      - cache_infra_cost  (50% of the largest single-target cost)

    Returns {} when pricing is unavailable for any target.
    """
    total_current = 0.0
    replica_eliminate = 0.0
    non_replica_rightsize = 0.0
    max_cost = 0.0
    for r in results:
        sizing = r.get("sizing") or {}
        cost = sizing.get("current_monthly_cost_usd") or 0.0
        if not cost:
            continue
        total_current += cost
        max_cost = max(max_cost, cost)
        metrics = r.get("metrics") or {}
        if metrics.get("is_replica"):
            replica_eliminate += cost
        else:
            saved = sizing.get("potential_savings_usd") or 0.0
            non_replica_rightsize += saved

    if total_current == 0:
        return {}

    cache_infra = round(max_cost * 0.5, 2)
    gross = replica_eliminate + non_replica_rightsize
    net = max(0.0, gross - cache_infra)
    pct = round((net / total_current) * 100, 1) if total_current else 0
    return {
        "total_savings_usd": round(net, 2),
        "total_current_usd": round(total_current, 2),
        "cache_infra_usd": cache_infra,
        "replica_eliminate_usd": round(replica_eliminate, 2),
        "rightsize_usd": round(non_replica_rightsize, 2),
        "pct_reduction": pct,
    }


# ============================================================================
# Section builders
# ============================================================================


def _render_hero(
    title: str,
    subtitle: str,
    date_label: str,
    score: Optional[int],
    score_label: Optional[str],
    stats: List[Dict[str, str]],
    badge_label: str = "RDST Audit",
) -> str:
    if score is not None:
        try:
            s = max(0, min(100, int(score)))
        except (TypeError, ValueError):
            s = 0
        col = _score_color(s)
        r = 56
        circ = 2 * 3.14159 * r
        offset = circ - (circ * s / 100)
        cx, cy = 70, 70
        lbl = esc((score_label or _label_from_score(s)))
        # All text rendered as SVG <text> elements inside the ring so they
        # never overflow the circle — coordinate system guarantees alignment.
        ring_html = (
            f'<div class="bluf-score">'
            f'<svg width="140" height="140" viewBox="0 0 140 140" style="display:block;">'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="8"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{col}" stroke-width="8" '
            f'stroke-linecap="round" stroke-dasharray="{circ:.1f}" stroke-dashoffset="{offset:.1f}" '
            f'transform="rotate(-90 {cx} {cy})"/>'
            f'<text x="{cx}" y="{cy - 12}" text-anchor="middle" '
            f'style="font-family:Plus Jakarta Sans,Inter,sans-serif;font-size:32px;font-weight:800;fill:#fff;">{s}</text>'
            f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" '
            f'style="font-size:11px;fill:rgba(255,255,255,.35);">/ 100</text>'
            f'<text x="{cx}" y="{cy + 20}" text-anchor="middle" '
            f'style="font-family:Plus Jakarta Sans,Inter,sans-serif;font-size:11px;font-weight:700;fill:{col};letter-spacing:.5px;">{lbl}</text>'
            f'</svg>'
            f'<div style="text-align:center;margin-top:4px;font-size:9px;color:rgba(255,255,255,.4);'
            f'letter-spacing:.6px;text-transform:uppercase;font-weight:600;">Health Score</div>'
            f'</div>'
        )
    else:
        ring_html = ""

    stat_cards = []
    for st in stats:
        val_cls = ""
        v = st.get("value", "")
        lbl = (st.get("label") or "").lower()
        if "$" in v:
            val_cls = " green"
        elif "hit" in lbl:
            val_cls = " cyan"
        stat_cards.append(
            f'<div class="bluf-card">'
            f'<div class="bluf-val{val_cls}">{esc(v)}</div>'
            f'<div class="bluf-lbl">{esc(st.get("label",""))}</div>'
            f'<div class="bluf-det">{esc(st.get("detail",""))}</div>'
            f'</div>'
        )

    return (
        '<div class="hero">'
        '<div class="hero-bar"></div>'
        '<div class="hero-inner">'
        '<div class="hero-top">'
        f'<span class="hero-badge">{esc(badge_label)}</span>'
        '<span style="display:flex;align-items:center;gap:10px;">'
        f'<span class="hero-date">{esc(date_label)}</span>'
        '<button onclick="(function(){var a=document.createElement(\'a\');'
        'a.href=\'data:text/html;charset=utf-8,\'+encodeURIComponent(document.documentElement.outerHTML);'
        'a.download=document.title.replace(/[^a-zA-Z0-9_-]/g,\'_\')+\'.html\';'
        'a.click();})()" style="padding:5px 12px;background:rgba(255,255,255,.12);'
        'border:1px solid rgba(255,255,255,.2);border-radius:6px;color:#fff;'
        'font-size:11px;font-weight:600;cursor:pointer;letter-spacing:.3px;">Download</button>'
        '</span>'
        '</div>'
        f'<h1 class="hero-title">{esc(title)}</h1>'
        f'<p class="hero-sub">{esc(subtitle)}</p>'
        '<div class="bluf">'
        f'{ring_html}'
        f'<div class="bluf-stats">{"".join(stat_cards)}</div>'
        '</div></div></div>'
    )


def _render_top_findings(findings: List[Dict[str, Any]]) -> str:
    if not findings:
        return ""
    cards = []
    for f in findings[:3]:
        sev = (f.get("severity") or "info").lower()
        if sev not in ("ok", "warn", "crit", "info"):
            sev = "info"
        cards.append(
            f'<div class="tf-card {sev}">'
            f'<span class="tf-dot dot-{sev}"></span>'
            f'<div class="tf-text">'
            f'<strong>{esc(f.get("title",""))}</strong>'
            f'{esc(f.get("body",""))}'
            f'</div></div>'
        )
    return f'<div class="top-findings">{"".join(cards)}</div>'


def _sec_head(num: int, title: str, badge: Optional[str] = None) -> str:
    b = f'<span class="sec-badge">{esc(badge)}</span>' if badge else ""
    return (
        f'<div class="sec-head">'
        f'<span class="sec-num">{num}</span>'
        f'<h2 class="sec-title">{esc(title)}</h2>'
        f'{b}</div>'
    )


def _render_topology_cards(results: List[Dict[str, Any]]) -> str:
    cards = []
    for r in results:
        role = _role_of(r)
        m = r.get("metrics") or {}
        wl = r.get("workload") or {}
        qps = None
        if wl.get("duration_seconds"):
            qps = (wl.get("total_queries") or 0) / wl["duration_seconds"]
        size_str = fmt_mb(m.get("database_size_mb") or 0)
        conn_str = (
            f'{m.get("active_connections", 0)} / {fmt_int(m.get("max_connections", 0))}'
        )
        rw_str = f"{int(m.get('read_pct') or 0)} % / {int(m.get('write_pct') or 0)} %"

        rows = [
            ("Engine", _engine_version(r)),
            ("Size", size_str),
            ("QPS", f"{qps:.0f}" if qps else "—"),
            ("Conns", conn_str),
            ("R / W", rw_str),
        ]
        if role == "primary":
            storage = (m.get("storage_allocated_gb") or 0)
            rows.append(("Storage", f"{storage:.0f} GB" if storage else "—"))
        else:
            rows.append(("Lag", "—"))

        grid = "".join(
            f'<span><span class="ml">{esc(label)}</span><span class="mv">{esc(val)}</span></span>'
            for label, val in rows
        )
        cards.append(
            f'<div class="topo-card {role}">'
            f'<div class="topo-role">{_role_label(role)}</div>'
            f'<div class="topo-name">{esc(r.get("target_name",""))}</div>'
            f'<div class="topo-grid">{grid}</div>'
            f'</div>'
        )
    return f'<div class="topo">{"".join(cards)}</div>'


def _render_resource_gauges(results: List[Dict[str, Any]]) -> str:
    cpu_capture, cpu_7d, cache, conn_use = [], [], [], []
    storage_alloc = []
    db_size = 0.0
    txid_ages = []

    for r in results:
        m = r.get("metrics") or {}
        s = r.get("sizing") or {}
        cw = r.get("cloudwatch_cpu") or {}
        if s.get("estimated_cpu_pct") is not None:
            cpu_capture.append(float(s["estimated_cpu_pct"]))
        if cw.get("avg_cpu") is not None:
            cpu_7d.append(float(cw["avg_cpu"]))
        if m.get("cache_hit_rate"):
            cache.append(float(m["cache_hit_rate"]))
        if m.get("connection_utilization_pct") is not None:
            conn_use.append(float(m["connection_utilization_pct"]))
        if m.get("storage_allocated_gb"):
            storage_alloc.append(float(m["storage_allocated_gb"]))
            db_size += float(m.get("database_size_mb") or 0) / 1024.0
        hr = (r.get("health_report") or {}).get("vacuum_bloat") or {}
        if hr.get("txid_age_pct") is not None:
            txid_ages.append(float(hr["txid_age_pct"]))

    def _avg(lst):
        return sum(lst) / len(lst) if lst else None

    def _gauge(lbl, val_str, pct, color):
        pct_clamped = max(0, min(100, pct or 0))
        return (
            f'<div class="gauge">'
            f'<span class="gauge-lbl">{esc(lbl)}</span>'
            f'<div class="gauge-track"><div class="gauge-fill {color}" style="width:{pct_clamped:.1f}%"></div></div>'
            f'<span class="gauge-val">{esc(val_str)}</span>'
            f'</div>'
        )

    rows = []
    a = _avg(cpu_capture)
    if a is not None:
        c = "fill-ok" if a < 30 else ("fill-warn" if a < 70 else "fill-crit")
        rows.append(_gauge("CPU (capture)", f"{a:.0f} %", a, c))
    a = _avg(cpu_7d)
    if a is not None:
        c = "fill-ok" if a < 30 else ("fill-warn" if a < 70 else "fill-crit")
        rows.append(_gauge("CPU (1-day avg)", f"{a:.0f} %", a, c))
    a = _avg(cache)
    if a is not None:
        rows.append(_gauge("Buffer Cache Hit", f"{a:.0f} %", a, "fill-accent"))
    a = _avg(conn_use)
    if a is not None:
        c = "fill-ok" if a < 50 else ("fill-warn" if a < 80 else "fill-crit")
        rows.append(_gauge("Connection Use", f"{a:.1f} %", a, c))
    if storage_alloc and db_size > 0:
        alloc = sum(storage_alloc)
        used_pct = (db_size / alloc * 100) if alloc else 0
        rows.append(_gauge("Storage Used", f"{db_size:.1f} / {alloc:.0f} GB", used_pct, "fill-ok"))
    a = _avg(txid_ages)
    if a is not None:
        c = "fill-ok" if a < 25 else ("fill-warn" if a < 50 else "fill-crit")
        rows.append(_gauge("TXID Age", f"{a:.0f} %", a, c))

    if not rows:
        return ""
    return f'<div class="card card-pad" style="margin-bottom:16px;">{"".join(rows)}</div>'


def _render_sizing_caching_table(results: List[Dict[str, Any]]) -> str:
    rows = []
    for r in results:
        role = _role_of(r)
        sizing = r.get("sizing") or {}
        verdict = (sizing.get("verdict") or "unknown").upper().replace("_", "-")
        if verdict in ("OVERSIZED", "UNDER-PROVISIONED"):
            sev_sizing = "warn"
        elif verdict == "RIGHT-SIZED":
            sev_sizing = "ok"
        else:
            sev_sizing = "info"

        potential = sizing.get("potential_savings_usd") or 0
        rightsize = "Yes" if potential and potential > 0 else "No"

        cache_opp = r.get("cache_opportunity") or {}
        score = int(cache_opp.get("score") or 0)
        if score >= 70:
            cache_label, cache_sev = "Strong Candidate", "ok"
        elif score >= 40:
            cache_label, cache_sev = "Good Candidate", "ok"
        else:
            cache_label, cache_sev = "Marginal", "warn"

        rows.append(
            f"<tr>"
            f'<td><b>{esc(r.get("target_name",""))}</b></td>'
            f'<td>{_role_label(role)}</td>'
            f'<td>{_engine_version(r)}</td>'
            f'<td><span class="stag stag-{sev_sizing}">{esc(verdict)}</span></td>'
            f'<td><span class="stag stag-{("ok" if rightsize == "Yes" else "warn")}">{esc(rightsize)}</span></td>'
            f'<td><span class="stag stag-{cache_sev}">{esc(cache_label)}</span></td>'
            f"</tr>"
        )
    thead = (
        '<thead><tr>'
        '<th>Target</th><th>Role</th><th>Engine</th>'
        '<th>Sizing Status</th><th>Rightsizing Candidate</th><th>Caching Candidate</th>'
        '</tr></thead>'
    )
    return f'<div class="card" style="margin:20px 0;"><table class="dtable">{thead}<tbody>{"".join(rows)}</tbody></table></div>'


def _render_findings_list(findings: List[Dict[str, Any]]) -> str:
    if not findings:
        return ""
    items = []
    for f in findings:
        sev = (f.get("severity") or "info").lower()
        items.append(
            f'<div class="finding">'
            f'<span class="dot dot-{sev} finding-dot"></span>'
            f'<div><b>{esc(f.get("title",""))}.</b> {esc(f.get("body",""))}</div>'
            f'</div>'
        )
    return f'<div style="margin-top:12px;">{"".join(items)}</div>'


def _render_section_1_overview(
    results: List[Dict[str, Any]],
    fleet_insights: Optional[Dict[str, Any]],
    is_single: bool,
) -> str:
    n = len(results)
    badge = f"{n} instance{'s' if n != 1 else ''}"
    parts = [_sec_head(1, "Overview", badge)]

    if not is_single:
        parts.append(_render_topology_cards(results))
    else:
        r = results[0]
        role = _role_of(r)
        parts.append(
            f'<div class="topo"><div class="topo-card {role}" style="grid-column:1 / -1;">'
            f'<div class="topo-role">{_role_label(role)}</div>'
            f'<div class="topo-name">{esc(r.get("target_name",""))}</div>'
            f'<div style="font-size:12px;color:var(--text-2);margin-top:4px;">'
            f'{_engine_version(r)} · {esc(r.get("instance_class") or "—")} · {esc(r.get("region") or "—")}'
            f'</div></div></div>'
        )

    gauges = _render_resource_gauges(results)
    if gauges:
        parts.append(gauges)

    parts.append(_render_sizing_caching_table(results))

    # Executive summary — use fleet LLM text if present, else synthesize.
    # Only synthesize for fleet (N>=2); single-target reports use per-target
    # health_analysis executive_summary if available, or skip the block.
    summary_text = (fleet_insights or {}).get("executive_summary") or ""
    if not summary_text:
        if is_single:
            ha = results[0].get("health_analysis") or {}
            summary_text = ha.get("executive_summary") or ""
        else:
            summary_text = _synthesize_fleet_summary(results)
    if summary_text:
        parts.append(f'<div class="ibox ibox-info">{summary_text}</div>')

    findings = (fleet_insights or {}).get("fleet_findings") or (fleet_insights or {}).get("findings") or []
    if not findings and not is_single:
        # Fallback: aggregate per-target findings so Findings section always has content in fleet reports
        findings = _aggregate_target_findings(results)
    if findings:
        parts.append('<h3 class="subhead">Findings</h3>')
        parts.append(_render_findings_list(findings))

    return f'<div class="sec">{"".join(parts)}</div>'


def _synthesize_fleet_summary(results: List[Dict[str, Any]]) -> str:
    """Generate a deterministic fleet narrative when LLM output is absent."""
    n = len(results)
    if n == 0:
        return ""
    # Instance classes summary
    classes = [r.get("instance_class") for r in results if r.get("instance_class")]
    cls_summary = f"{n} × {classes[0]}" if classes and all(c == classes[0] for c in classes) else (f"{n} instances" if not classes else f"mixed: {', '.join(sorted(set(classes)))}")
    # DB size across targets (use max, since a fleet of replicas usually shares the same DB)
    sizes_mb = [float((r.get("metrics") or {}).get("database_size_mb") or 0) for r in results]
    max_mb = max(sizes_mb) if sizes_mb else 0
    size_str = fmt_mb(max_mb)
    # R/W profile — fleet-wide read % (weighted by query count if possible)
    read_pcts = [float((r.get("metrics") or {}).get("read_pct") or 0) for r in results if (r.get("metrics") or {}).get("read_pct")]
    avg_read = sum(read_pcts) / len(read_pcts) if read_pcts else 0
    # Replica/primary breakdown
    replicas = sum(1 for r in results if (r.get("metrics") or {}).get("is_replica"))
    primaries = n - replicas
    if primaries == 1 and replicas >= 1:
        role_str = f"1 primary + {replicas} read replica{'s' if replicas > 1 else ''}"
    elif primaries == 0 and replicas:
        role_str = f"{replicas} read replica{'s' if replicas > 1 else ''} (no primary in group)"
    elif replicas == 0 and primaries:
        role_str = f"{primaries} primar{'ies' if primaries > 1 else 'y'}"
    else:
        role_str = f"{primaries} primar{'ies' if primaries > 1 else 'y'} + {replicas} replica{'s' if replicas > 1 else ''}"

    # Cache hit avg
    cache_hits = [float((r.get("metrics") or {}).get("cache_hit_rate") or 0) for r in results if (r.get("metrics") or {}).get("cache_hit_rate")]
    avg_cache = sum(cache_hits) / len(cache_hits) if cache_hits else None

    narrative = f"This fleet runs <b>{role_str}</b>"
    if classes and all(c == classes[0] for c in classes):
        narrative += f" on <b>{classes[0]}</b>"
    if max_mb:
        narrative += f" against a <b>{size_str}</b> database"
    narrative += "."
    if avg_read >= 90:
        narrative += f" Workload is <b>{int(avg_read)}% reads</b>"
    elif avg_read:
        narrative += f" Workload is <b>{int(avg_read)}% reads / {int(100-avg_read)}% writes</b>"
    if avg_cache:
        narrative += f" with a <b>{avg_cache:.0f}% buffer cache hit rate</b>."
    else:
        narrative += "."
    # Caching candidate count
    caching = sum(1 for r in results if (r.get("cache_opportunity") or {}).get("score", 0) >= 40)
    if caching and caching < n:
        narrative += f" {caching} of {n} targets show a meaningful caching opportunity."
    elif caching == n and n > 1:
        narrative += " All targets show a meaningful caching opportunity."
    return narrative


def _aggregate_target_findings(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    sev_order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
    out: List[Dict[str, Any]] = []
    for r in results:
        ha = r.get("health_analysis") or {}
        for f in (ha.get("findings") or [])[:3]:
            tname = r.get("target_name") or ""
            out.append({
                "severity": f.get("severity", "info"),
                "title": f.get("title", ""),
                "body": f"[{tname}] {f.get('body','')}" if tname else f.get("body", ""),
            })
    out.sort(key=lambda f: sev_order.get((f.get("severity") or "info").lower(), 9))
    return out[:8]


def _render_target_detailed(r: Dict[str, Any], skip_header: bool = False) -> str:
    role = _role_of(r)
    name = r.get("target_name") or "target"
    m = r.get("metrics") or {}
    workload = r.get("workload") or {}
    rs_results = r.get("readyset_results") or []
    ha = r.get("health_analysis") or {}

    if skip_header:
        header = ""
    else:
        header = (
            f'<div class="target-head">'
            f'<span class="target-role-pill {role}">{_role_label(role).upper()}</span>'
            f'<span class="target-name">{esc(name)}</span>'
            f'</div>'
        )

    read_pct = m.get("read_pct") or 0
    if read_pct >= 90:
        rw_note = " — Read-heavy — excellent caching candidate"
    elif read_pct >= 60:
        rw_note = " — Moderately read-heavy"
    else:
        rw_note = ""
    # Per-target QPS from this target's own capture (sum(calls) / duration).
    target_calls = (workload or {}).get("total_queries")
    if target_calls is None:
        target_calls = sum(int(q.get("calls") or 0) for q in ((workload or {}).get("queries") or []))
    target_dur = (workload or {}).get("duration_seconds") or 0
    target_qps_str = ""
    if target_dur and target_calls:
        qps = target_calls / target_dur
        target_qps_str = f"{qps:.1f}" if qps < 10 else f"{qps:.0f}"

    db_rows = [
        ("Server Version", _engine_version(r)),
        ("Database Size", fmt_mb(m.get("database_size_mb") or 0)),
        (
            "Connections",
            f'{m.get("active_connections", 0)} / {fmt_int(m.get("max_connections", 0))} ({fmt_pct(m.get("connection_utilization_pct"))})',
        ),
        ("Buffer Cache Hit", fmt_pct(m.get("cache_hit_rate"))),
        ("Shared Buffers", fmt_mb(m.get("shared_buffers_mb") or 0)),
        ("Read / Write", f"{int(read_pct)}% / {int(m.get('write_pct') or 0)}%{rw_note}"),
        ("Tracked Queries", str(m.get("tracked_query_count", 0))),
    ]
    if target_qps_str:
        db_rows.append(("Captured QPS", f"{target_qps_str}  ({target_calls:,} executions over {int(target_dur)}s)"))
    db_table = (
        '<h3 class="subhead">Database Overview</h3>'
        '<div class="card"><table class="kv-table"><tbody>'
        + "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in db_rows)
        + "</tbody></table></div>"
    )

    findings_src = ha.get("findings") or ha.get("top_findings") or []
    findings_block = ""
    if findings_src:
        items = []
        for f in findings_src[:6]:
            sev = (f.get("severity") or "info").lower()
            mark_color = {"ok": "#16A34A", "warn": "#F59E0B", "crit": "#EF4444", "info": "#3B82F6"}.get(sev, "#3B82F6")
            title = f.get("title") or ""
            body = f.get("body") or ""
            text = f"<b>{esc(title)}.</b> {esc(body)}" if title and body else esc(title or body)
            items.append(f'<li><span class="f-mark" style="color:{mark_color};font-weight:700;">&#10003;</span><span>{text}</span></li>')
        findings_block = '<h3 class="subhead">Key Findings</h3><ul class="findings-list">' + "".join(items) + "</ul>"

    captured = workload.get("queries") or []
    # Filter system/internal queries that shouldn't appear in reports.
    _sys_markers = ("@@", "pg_stat_", "pg_settings", "pg_catalog", "performance_schema",
                    "information_schema", "pg_replication", "mysql.", "sys.")
    _filtered = []
    for _q in captured:
        _sql = ((_q.get("normalized_query") or _q.get("query_text") or "")).lower()
        _first = _sql.lstrip().split()[0] if _sql.strip() else ""
        if _first not in ("select", "with"):
            continue
        if any(s in _sql for s in _sys_markers):
            continue
        _filtered.append(_q)
    captured = _filtered
    queries_block = ""
    if captured:
        rs_by_hash = {}
        for rr in rs_results:
            h = rr.get("query_hash") or rr.get("hash")
            if h:
                rs_by_hash[h[:12]] = rr
        has_rs = any(rs_by_hash.values())

        thead_cells = ['<th>Query</th>', '<th>DB Calls</th>', '<th>DB Avg</th>', '<th>DB Load %</th>']
        if has_rs:
            thead_cells += ['<th>Readyset Avg</th>', '<th>Speedup</th>']
        thead = f'<thead><tr>{"".join(thead_cells)}</tr></thead>'

        rows = []
        for i, q in enumerate(captured[:10], start=1):
            qhash = (q.get("query_hash") or "")[:12]
            full_sql = (q.get("normalized_query") or q.get("query_text") or "").strip()
            sql_preview = full_sql[:160] + ("…" if len(full_sql) > 160 else "")
            calls = q.get("calls") or 0
            avg = float(q.get("avg_time_ms") or 0)
            pct = float(q.get("pct_total_time") or 0)
            avg_col = "#15803D" if avg < 10 else ("#D97706" if avg < 100 else "#DC2626")
            load_col = "#334155" if pct < 10 else ("#D97706" if pct < 20 else "#DC2626")
            rs = rs_by_hash.get(qhash) or {}
            rs_avg = rs.get("readyset_ms") or rs.get("avg_rs_ms")
            speedup = rs.get("speedup") or rs.get("speedup_x") or 0

            # Always render an expandable full-SQL toggle, regardless of
            # preview length — clicking the query row reveals the full text.
            expand = (
                f'<details class="sql-expand"><summary></summary>'
                f'<pre>{esc(full_sql)}</pre></details>'
            )
            qcell = (
                f'<td style="padding:10px 12px;background:#FFFFFF;text-align:left;">'
                f'<strong class="qh">#{i} · {esc(qhash)}</strong><br>'
                f'<code>{esc(sql_preview)}</code>'
                f'{expand}'
                f'</td>'
            )
            row_cells = [
                qcell,
                f'<td style="font-weight:600">{fmt_int(calls)}</td>',
                f'<td><strong style="color:{avg_col}">{fmt_ms(avg)}</strong></td>',
                f'<td><strong style="color:{load_col}">{pct:.1f}%</strong></td>',
            ]
            if has_rs:
                if rs_avg:
                    row_cells.append(f'<td><strong style="color:#15803D">{fmt_ms(rs_avg)}</strong></td>')
                else:
                    row_cells.append('<td style="color:#9CA3AF">—</td>')
                if speedup and float(speedup) > 0:
                    sp = float(speedup)
                    sp_col = "#15803D" if sp >= 5 else ("#D97706" if sp >= 2 else "#334155")
                    row_cells.append(f'<td><strong style="color:{sp_col};font-size:14px">{sp:.1f}x</strong></td>')
                else:
                    row_cells.append('<td style="color:#9CA3AF">—</td>')
            rows.append(f"<tr>{''.join(row_cells)}</tr>")

        cacheable_count = sum(1 for rr in rs_results if rr.get("cacheable"))
        intro = (
            f"{len(captured)} queries captured"
            + (f" — <strong>{cacheable_count}</strong> cacheable by Readyset." if has_rs else ".")
        )
        queries_block = (
            '<h3 class="subhead">Captured Queries &amp; Performance</h3>'
            f'<p style="color:var(--text-2);font-size:13px;margin-bottom:10px">{intro}</p>'
            f'<div class="card query-table"><table class="dtable">{thead}<tbody>{"".join(rows)}</tbody></table></div>'
        )

    wa = workload.get("analysis") or {}
    idx_recs = wa.get("index_recommendations") or ha.get("index_suggestions") or []
    idx_block = ""
    if idx_recs:
        cards_html = []
        for rec in idx_recs[:8]:
            table = rec.get("table") or ""
            cols = rec.get("columns") or []
            cols_str = ", ".join(cols) if cols else ""
            title = f"{table} ({cols_str})" if table and cols_str else (rec.get("sql") or "")
            reason = rec.get("reason") or ""
            sql = rec.get("create_index_sql") or rec.get("sql") or ""
            cards_html.append(
                f'<div class="idx-card">'
                f'<div class="idx-title">{esc(title)}</div>'
                + (f'<div class="idx-reason">{esc(reason)}</div>' if reason else "")
                + f'<pre>{_highlight_index_sql(sql)}</pre>'
                f'</div>'
            )
        idx_block = '<h3 class="subhead">Index Recommendations</h3>' + "".join(cards_html)

    priorities = wa.get("optimization_priorities") or []
    prio_block = ""
    if priorities:
        rows_html = []
        for i, p in enumerate(priorities[:6], start=1):
            effort = (p.get("effort") or "medium").lower()
            impact = (p.get("impact") or "medium").lower()
            effort_col = {"low": "#15803D", "medium": "#D97706", "high": "#DC2626"}.get(effort, "#334155")
            impact_col = {"high": "#15803D", "medium": "#D97706", "low": "#DC2626"}.get(impact, "#334155")
            rows_html.append(
                f"<tr>"
                f'<td style="padding:8px 12px;font-weight:700;color:#00838F">{i}</td>'
                f'<td style="padding:8px 12px;text-align:left">'
                f'<strong>{esc(p.get("action",""))}</strong>'
                + (f'<br><span style="font-size:12px;color:#64748B">{esc(p.get("category",""))}</span>' if p.get("category") else "")
                + "</td>"
                f'<td style="padding:8px 12px;color:{effort_col};font-weight:600;font-size:12px;text-transform:uppercase">{esc(effort)}</td>'
                f'<td style="padding:8px 12px;color:{impact_col};font-weight:600;font-size:12px;text-transform:uppercase">{esc(impact)}</td>'
                f"</tr>"
            )
        prio_block = (
            '<h3 class="subhead">Optimization Priorities</h3>'
            '<div class="card"><table class="dtable">'
            '<thead><tr>'
            '<th style="width:30px">#</th><th>Action</th>'
            '<th>Effort</th><th>Impact</th>'
            '</tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody></table></div>'
        )

    cache_opp = r.get("cache_opportunity") or {}
    score = int(cache_opp.get("score") or 0)
    verdict_block = ""
    if score >= 70:
        verdict_block = (
            '<div class="closing-verdict ok">'
            '<b>Strong candidate for Readyset caching.</b> '
            'Read-heavy workload with highly repetitive queries makes this target an excellent fit for a caching layer.'
            '</div>'
        )
    elif score >= 40:
        verdict_block = (
            '<div class="closing-verdict warn">'
            '<b>Good candidate for Readyset caching.</b> '
            'Partial read repetition suggests caching would yield measurable benefit.'
            '</div>'
        )

    return "".join([header, db_table, findings_block, queries_block, idx_block, prio_block, verdict_block])


def _render_section_2_detailed(results: List[Dict[str, Any]], is_single: bool) -> str:
    parts = [_sec_head(2, "Detailed Analysis", f"{len(results)} target{'s' if len(results) != 1 else ''}")]
    if is_single:
        # Inline — no collapsible for single-target
        for r in results:
            parts.append(_render_target_detailed(r))
    else:
        # All collapsed by default — user expands the ones they want to drill into
        for i, r in enumerate(results):
            role = _role_of(r)
            name = r.get("target_name") or "target"
            engine_v = _engine_version(r)
            role_label = _role_label(role).upper()
            cap = len((r.get("workload") or {}).get("queries") or [])
            meta = f"{cap} queries captured" if cap else "no capture"
            open_attr = ""
            parts.append(
                f'<details class="target-section"{open_attr}>'
                f'<summary>'
                f'<span class="target-role-pill {role}">{role_label}</span>'
                f'<span class="target-name">{esc(name)}</span>'
                f'<span class="summary-meta">{esc(engine_v)} · {esc(meta)}</span>'
                f'</summary>'
                f'<div class="target-body">{_render_target_detailed(r, skip_header=True)}</div>'
                f'</details>'
            )
    return f'<div class="sec">{"".join(parts)}</div>'


def _render_section_next_steps(
    fleet_insights: Optional[Dict[str, Any]],
    section_num: int = 3,
) -> str:
    steps = (fleet_insights or {}).get("next_steps") or (fleet_insights or {}).get("recommended_actions") or []
    if not steps:
        return ""

    items = []
    for i, step in enumerate(steps[:8], start=1):
        rank = step.get("rank") or i
        title = step.get("title") or ""
        body = step.get("body") or ""
        commands = step.get("commands") or []
        savings = step.get("estimated_savings_usd")

        cmd_html = ""
        if commands:
            cmd_html = "".join(f'<code class="cmd">{esc(cmd)}</code>' for cmd in commands)

        savings_html = ""
        if savings:
            try:
                sv = float(savings)
                if sv > 0:
                    savings_html = f'<span class="savings">Estimated savings: ${sv:,.0f}/mo</span>'
            except (TypeError, ValueError):
                pass

        items.append(
            f'<li>'
            f'<span class="act-n">{rank}</span>'
            f'<div><b>{esc(title)}.</b> {esc(body)}{cmd_html}{savings_html}</div>'
            f'</li>'
        )

    return (
        f'<div class="sec">'
        + _sec_head(section_num, "Next Steps", f"{len(items)} action{'s' if len(items) != 1 else ''}")
        + f'<ol class="actions">{"".join(items)}</ol>'
        + '</div>'
    )


def _render_section_sizing(
    results: List[Dict[str, Any]],
    fleet_savings: Optional[Dict[str, Any]],
    is_single: bool,
) -> str:
    """§3 Sizing & Savings — only renders when AWS pricing data is available."""
    has_pricing = any(
        (r.get("sizing") or {}).get("current_monthly_cost_usd")
        for r in results
    )
    if not has_pricing:
        return ""

    parts = [_sec_head(3, "Sizing & Savings")]
    parts.append(
        '<div class="ibox ibox-warn">Estimates based on on-demand instance pricing and '
        'observed workload patterns. Actual savings will vary depending on reserved '
        'instance commitments, caching strategy, and deployment architecture.</div>'
    )

    # Per-target breakdown table
    thead = (
        '<thead><tr>'
        '<th>Target</th><th>Instance</th><th>Current</th>'
        '<th>Rightsize To</th><th>Rightsize Savings</th>'
        '<th>With Caching</th><th>Caching Savings</th>'
        '</tr></thead>'
    )
    rows = []
    for r in results:
        s = r.get("sizing") or {}
        cur = s.get("current_monthly_cost_usd") or 0
        if not cur:
            continue
        sugg_class = s.get("suggested_instance_class") or "—"
        sugg_cost = s.get("suggested_monthly_cost_usd")
        sugg_save = s.get("potential_savings_usd") or 0
        rs_class = s.get("readyset_projected_class") or "—"
        rs_cost = s.get("readyset_projected_cost_usd")
        rs_save = s.get("readyset_projected_savings_usd") or 0

        rows.append(
            f"<tr>"
            f'<td><b>{esc(r.get("target_name",""))}</b></td>'
            f'<td style="font-family:monospace;font-size:12px;">{esc(r.get("instance_class") or "—")}</td>'
            f'<td>${int(cur):,}/mo</td>'
            f'<td style="font-family:monospace;font-size:12px;">{esc(sugg_class)}</td>'
            f'<td style="color:var(--ok-dark);font-weight:600;">'
            + (f'-${int(sugg_save):,}/mo' if sugg_save else "—")
            + '</td>'
            f'<td style="font-family:monospace;font-size:12px;">{esc(rs_class)}</td>'
            f'<td style="color:var(--ok-dark);font-weight:600;">'
            + (f'-${int(rs_save):,}/mo' if rs_save else "—")
            + '</td>'
            f"</tr>"
        )

    if rows:
        parts.append(
            f'<div class="card" style="margin:14px 0;">'
            f'<table class="dtable">{thead}<tbody>{"".join(rows)}</tbody></table>'
            f'</div>'
        )

    # Fleet-level summary if available
    if fleet_savings and fleet_savings.get("total_savings_usd"):
        fs = fleet_savings
        parts.append(
            f'<div class="ibox ibox-ok">'
            f'<b>Fleet total:</b> ${int(fs.get("total_current_usd") or 0):,}/mo current → '
            f'estimated <b>${int(fs["total_savings_usd"]):,}/mo savings</b> '
            f'({fs.get("pct_reduction", 0):.0f}% reduction). '
            f'Includes ${int(fs.get("replica_eliminate_usd") or 0):,} from replica elimination, '
            f'${int(fs.get("rightsize_usd") or 0):,} from rightsizing, '
            f'minus ${int(fs.get("cache_infra_usd") or 0):,} estimated cache infrastructure.'
            f'</div>'
        )
    elif is_single:
        r = results[0]
        s = r.get("sizing") or {}
        rs_save = s.get("readyset_projected_savings_usd") or 0
        sugg_save = s.get("potential_savings_usd") or 0
        total_save = max(rs_save, sugg_save)
        cur = s.get("current_monthly_cost_usd") or 0
        if total_save and cur:
            pct = int(total_save / cur * 100)
            parts.append(
                f'<div class="ibox ibox-ok">'
                f'<b>Estimated savings:</b> ${int(total_save):,}/mo ({pct}% reduction) '
                f'by rightsizing'
                + (' and deploying a caching layer.' if rs_save else '.')
                + '</div>'
            )

    return f'<div class="sec">{"".join(parts)}</div>'


# ============================================================================
# Document assembly
# ============================================================================


def _fmt_date_label(created_at: Optional[str], duration: Optional[int] = None) -> str:
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            base = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            base = created_at[:16]
    else:
        base = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if duration:
        return f"{base} · capture {duration}s"
    return base


def _build_hero_stats(
    results: List[Dict[str, Any]],
    fleet_savings: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    # Per-target QPS = sum(query.calls) / capture_duration. Fleet QPS = sum of
    # per-target QPSs. No averaging, no assumptions — each target contributes
    # its own measured rate.
    total_queries = 0
    total_qps = 0.0
    cache_hits = []
    for r in results:
        wl = r.get("workload") or {}
        target_calls = wl.get("total_queries")
        if target_calls is None:
            target_calls = sum(int(x.get("calls") or 0) for x in (wl.get("queries") or []))
        target_dur = wl.get("duration_seconds") or 0
        target_qps = (target_calls / target_dur) if (target_dur and target_calls) else 0
        total_queries += target_calls
        total_qps += target_qps
        m = r.get("metrics") or {}
        if m.get("cache_hit_rate"):
            cache_hits.append(float(m["cache_hit_rate"]))

    # Always produce exactly 3 tiles. Empty values shown as "—" so the layout
    # stays consistent across scenarios (no AWS, no duration, etc).
    qps_str = ""
    if total_qps >= 1:
        qps_str = f"{total_qps:.0f} QPS combined"
    elif total_qps > 0:
        qps_str = f"{total_qps:.2f} QPS combined"
    queries_tile = {
        "value": fmt_int(total_queries) if total_queries else "—",
        "label": "Queries Captured",
        "detail": qps_str if qps_str else ("no capture window" if total_queries == 0 else ""),
    }
    cache_tile = {
        "value": f"{sum(cache_hits) / len(cache_hits):.0f}%" if cache_hits else "—",
        "label": "Cache Hit Rate",
        "detail": "Data fits in memory" if cache_hits and sum(cache_hits) / len(cache_hits) >= 99 else "",
    }

    # Savings tile — fleet first, then per-target fallback, then placeholder
    savings_tile = None
    if fleet_savings and fleet_savings.get("total_savings_usd"):
        savings_tile = {
            "value": f"${int(fleet_savings['total_savings_usd']):,}/mo",
            "label": "Savings Identified",
            "detail": f"{fleet_savings.get('pct_reduction', 0):.0f}% fleet cost reduction",
        }
    else:
        single_save = 0.0
        single_pct = 0.0
        for r in results:
            s = r.get("sizing") or {}
            save = s.get("readyset_projected_savings_usd") or s.get("potential_savings_usd") or 0
            cur = s.get("current_monthly_cost_usd") or 0
            if save:
                single_save += save
            if cur and save:
                single_pct = (save / cur * 100)
        if single_save:
            savings_tile = {
                "value": f"${int(single_save):,}/mo",
                "label": "Savings Identified",
                "detail": f"{single_pct:.0f}% reduction" if single_pct else "",
            }
    if savings_tile is None:
        # Pricing not available — fall back to an always-meaningful metric:
        # average read percentage across targets. Universally defined whether
        # we have AWS data or not; reads 90%+ is the caching-fit signal.
        reads = [
            float((r.get("metrics") or {}).get("read_pct") or 0)
            for r in results
            if (r.get("metrics") or {}).get("read_pct") is not None
        ]
        if reads:
            avg_read = sum(reads) / len(reads)
            fit_detail = (
                "caching fits well" if avg_read >= 90
                else "caching fits partially" if avg_read >= 60
                else "write-heavy workload"
            )
            savings_tile = {
                "value": f"{avg_read:.0f}%",
                "label": "Read Traffic",
                "detail": fit_detail,
            }
        else:
            savings_tile = {"value": "—", "label": "Read Traffic", "detail": ""}

    return [queries_tile, cache_tile, savings_tile]


def render_report_html(
    results: List[Dict[str, Any]],
    fleet_insights: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
) -> str:
    if not results:
        return "<!DOCTYPE html><html><body><p>No results.</p></body></html>"

    is_single = len(results) == 1
    fleet_savings = None
    if not is_single:
        fleet_savings = compute_fleet_savings(results)

    fleet_insights = fleet_insights or {}

    if is_single:
        r = results[0]
        hero_title = title or (r.get("target_name") or "target")
        engine_v = _engine_version(r)
        cls = r.get("instance_class") or "—"
        region = r.get("region") or "—"
        hero_sub = f"{engine_v} · {cls} · {region}"
    else:
        hero_title = title or "fleet"
        engines = set(_engine_version(r) for r in results)
        classes = set((r.get("instance_class") or "—") for r in results)
        hero_sub = f"{len(results)} × {' / '.join(sorted(engines))} · {' / '.join(sorted(classes))}"

    duration = None
    for r in results:
        wl = r.get("workload") or {}
        if wl.get("duration_seconds"):
            duration = wl["duration_seconds"]
            break
    date_label = _fmt_date_label((results[0] or {}).get("audited_at"), duration)

    hero_stats = _build_hero_stats(results, fleet_savings)
    score = fleet_insights.get("health_score") if fleet_insights else None
    if score is None and is_single:
        ha = results[0].get("health_analysis") or {}
        score = ha.get("health_score")
    score_label = fleet_insights.get("health_label") if fleet_insights else None
    if score_label is None and is_single:
        ha = results[0].get("health_analysis") or {}
        score_label = ha.get("health_label")
    if score_label is None:
        score_label = _label_from_score(score)

    top_findings: List[Dict[str, Any]] = []
    if fleet_insights.get("top_findings"):
        top_findings = fleet_insights["top_findings"]
    elif is_single:
        ha = results[0].get("health_analysis") or {}
        top_findings = ha.get("top_findings") or []
    else:
        # Fleet but no fleet_insights — aggregate the highest-severity per-target
        # top_findings so the hero still has callouts to anchor on.
        sev_order = {"crit": 0, "warn": 1, "info": 2, "ok": 3}
        agg: List[Dict[str, Any]] = []
        for r in results:
            ha = r.get("health_analysis") or {}
            for f in (ha.get("top_findings") or [])[:2]:
                tname = r.get("target_name") or ""
                agg.append({
                    "severity": f.get("severity", "info"),
                    "title": f.get("title", ""),
                    "body": f"{tname}: {f.get('body','')}" if tname else f.get("body", ""),
                })
        agg.sort(key=lambda f: sev_order.get((f.get("severity") or "info").lower(), 9))
        top_findings = agg[:3]

    # §3 Sizing & Savings — only when AWS pricing is available
    sizing_section = _render_section_sizing(results, fleet_savings, is_single)
    has_sizing = bool(sizing_section)

    body_parts = [
        _render_hero(hero_title, hero_sub, date_label, score, score_label, hero_stats, "RDST Audit Report"),
        _render_top_findings(top_findings),
        _render_section_1_overview(results, fleet_insights, is_single),
        _render_section_2_detailed(results, is_single),
    ]

    if has_sizing:
        body_parts.append(sizing_section)

    if not (fleet_insights.get("next_steps") or fleet_insights.get("recommended_actions")):
        synthetic = _synthesize_next_steps(results, fleet_savings)
        if synthetic:
            fleet_insights = {**fleet_insights, "next_steps": synthetic}

    # Next Steps is §4 when Sizing exists, §3 otherwise
    next_step_num = 4 if has_sizing else 3
    section_ns = _render_section_next_steps(fleet_insights, next_step_num)
    if section_ns:
        body_parts.append(section_ns)

    footer = (
        '<div class="footer">'
        f'<b>RDST</b> · Generated {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}'
        '</div>'
    )

    return (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        f'<title>{esc(hero_title)} — RDST Health Report</title>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Plus+Jakarta+Sans:wght@500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">'
        f"{STYLE_BLOCK}"
        '</head><body><div class="container">'
        + "".join(body_parts)
        + footer
        + "</div></body></html>"
    )


def _synthesize_next_steps(
    results: List[Dict[str, Any]],
    fleet_savings: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    rank = 1
    seen_actions: set = set()
    for r in results:
        wa = (r.get("workload") or {}).get("analysis") or {}
        for p in (wa.get("optimization_priorities") or [])[:3]:
            action = p.get("action") or ""
            if not action or action in seen_actions:
                continue
            seen_actions.add(action)
            body = p.get("details") or ""
            step: Dict[str, Any] = {"rank": rank, "title": action, "body": body}
            steps.append(step)
            rank += 1
            if rank > 6:
                return steps
    if fleet_savings and fleet_savings.get("total_savings_usd"):
        steps.append({
            "rank": rank,
            "title": "Deploy a caching layer on the primary",
            "body": "Captured queries show high repetition — a caching layer would cut read load on the database and reduce per-query latency.",
            "commands": [
                f'rdst cache deploy --target {results[0].get("target_name","<target>")}',
            ],
            "estimated_savings_usd": fleet_savings["total_savings_usd"],
        })
    return steps


# ============================================================================
# Back-compat shims
# ============================================================================


def render_v3_single_html(
    audit_result: Dict[str, Any],
    workload: Optional[Dict[str, Any]] = None,
    rs_results: Optional[List[Dict[str, Any]]] = None,
) -> str:
    r = dict(audit_result or {})
    if workload is not None:
        r["workload"] = workload
    if rs_results is not None:
        r["readyset_results"] = rs_results
    return render_report_html([r], fleet_insights=None, title=r.get("target_name"))


def render_v3_fleet_html(
    fleet_snapshot: Dict[str, Any],
    fleet_insights: Optional[Dict[str, Any]] = None,
) -> str:
    results = fleet_snapshot.get("results") or []
    return render_report_html(
        results,
        fleet_insights=fleet_insights,
        title=fleet_snapshot.get("name") or "fleet",
    )


__all__ = [
    "render_report_html",
    "render_v3_single_html",
    "render_v3_fleet_html",
    "compute_fleet_savings",
]
