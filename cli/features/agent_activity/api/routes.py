"""The agent activity log: everything the RDST agent did, newest first."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from features.agent_activity.api import models
from features.agent_activity.api.models import AgentAction, AgentGroup, AgentOutcome
from features.monitor.api.models import documented
from shared.agent_activity.store import RETENTION_DAYS, AgentActivityStore

router = APIRouter()

CSV_COLUMNS = ("time_utc", "database", "action", "subject", "outcome", "summary", "decision",
               "finding", "question", "query_key", "statements", "ai_calls", "tokens_in",
               "tokens_out", "cost_usd", "items_filed", "source")


def _store() -> AgentActivityStore:
    return AgentActivityStore()


@router.get("/agent-activity", **documented(models.AgentActivityPage))
def agent_activity(
    target: Optional[str] = None,
    action: Optional[AgentAction] = None,
    outcome: Optional[AgentOutcome] = None,
    since: Optional[int] = Query(None, description="Epoch seconds, inclusive"),
    until: Optional[int] = Query(None, description="Epoch seconds, exclusive"),
    cursor: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    group: AgentGroup = Query("day", description="Fold attention checks, classifications "
                                                 "and skipped deep analyses per database "
                                                 "and day or hour; 'none' lists every entry"),
    utc_offset: int = Query(0, ge=-900, le=900,
                            description="Minutes east of UTC that set local days and hours"),
) -> dict:
    store = _store()
    try:
        page = store.page(target=target, action=action, outcome=outcome, since=since,
                          until=until, cursor=cursor, limit=limit, group=group,
                          utc_offset=utc_offset)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**page, "targets": store.targets(), "retention_days": RETENTION_DAYS}


def _day(ts: int, utc_offset: int) -> str:
    return datetime.fromtimestamp(ts + utc_offset * 60, timezone.utc).strftime("%Y-%m-%d")


@router.get("/agent-activity/export.csv", response_class=Response,
            responses={200: {"content": {"text/csv": {}}}})
def agent_activity_export(
    target: Optional[str] = None,
    action: Optional[AgentAction] = None,
    since: Optional[int] = Query(None, description="From, epoch seconds, inclusive"),
    until: Optional[int] = Query(None, description="To, epoch seconds, exclusive"),
    utc_offset: int = Query(0, ge=-900, le=900,
                            description="Minutes east of UTC that name the file's days"),
) -> Response:
    """Every entry in the range, one row each, attention checks included."""
    if since is not None and until is not None and since >= until:
        raise HTTPException(422, "The range ends before it starts")
    rows = _store().export_rows(target=target, action=action, since=since, until=until)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([
            datetime.fromtimestamp(row["ts"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            row["target"], row["action"], row["subject"], row["outcome"], row["summary"],
            row["decision"] or "", row["finding"] or "", row["question"] or "",
            row["query_key"] or "", row["statement_count"], row["ai_call_count"],
            row["tokens_in"], row["tokens_out"], f"{row['cost_usd']:.6f}", row["items_filed"],
            row["source"]])
    name = "rdst-agent-activity"
    if since is not None:
        name += f"-from-{_day(since, utc_offset)}"
    if until is not None:
        name += f"-to-{_day(until - 1, utc_offset)}"
    return Response(buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@router.get("/agent-activity/{entry_id}", **documented(models.AgentActivityDetail))
def agent_activity_entry(entry_id: int) -> dict:
    entry = _store().entry(entry_id)
    if entry is None:
        raise HTTPException(404, "Activity entry not found")
    return entry
