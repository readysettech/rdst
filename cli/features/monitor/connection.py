"""Whether RDST can reach each monitored database, and the gap in readings an outage leaves.

A database counts as unreachable after LOST_AFTER_FAILURES failed checks in a row, or once
the first failure is LOST_AFTER_SECONDS old, so a single blip never alerts. Checks come from
the collector (every minute for a monitored target) and from the monitor loop's own index
scan. The Act now item closes on the first successful check, saying when monitoring resumed
and which span has no readings.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable

from shared import product_telemetry
from shared.monitor.monitor_store import MonitorStore

logger = logging.getLogger(__name__)

LOST_AFTER_FAILURES = 2
LOST_AFTER_SECONDS = 120
ITEM = ("monitoring_issue", "lost_connection")
TITLE = "Can't reach the database"


def clock_time(ts: int, *, meridiem: bool = True) -> str:
    """'9:55 AM' in local time, without platform-specific strftime flags."""
    moment = datetime.fromtimestamp(ts)
    text = f"{moment.hour % 12 or 12}:{moment.minute:02d}"
    return f"{text} {'AM' if moment.hour < 12 else 'PM'}" if meridiem else text


def span_text(start: int, end: int) -> str:
    """'9:55 to 11:15 AM', '11:50 PM to 12:20 AM', or with weekdays past a day."""
    first, last = datetime.fromtimestamp(start), datetime.fromtimestamp(end)
    if end - start >= 86400 or first.date() != last.date() and end - start >= 12 * 3600:
        return (f"{first.strftime('%a')} {clock_time(start)} to "
                f"{last.strftime('%a')} {clock_time(end)}")
    same_half = first.date() == last.date() and (first.hour < 12) == (last.hour < 12)
    return f"{clock_time(start, meridiem=not same_half)} to {clock_time(end)}"


# Common driver errors, in words a user can act on, each with what to check. The raw error
# stays on the item.
_REASONS = (
    (("password authentication failed", "access denied for user", "authentication failed"),
     "The database rejected the password.",
     "Update the password for this database in Settings."),
    (("timeout expired", "timed out", "connection timed out"), "The connection timed out.",
     "Check that the database is running and that this computer can reach its host and port."),
    (("could not translate host name", "name or service not known", "nodename nor servname",
      "getaddrinfo failed"), "The host name didn't resolve.",
     "Check the host name for this database in Settings."),
    (("connection refused",), "The server refused the connection.",
     "Check that the database is running and listening on that port."),
    (("no pg_hba.conf entry", "not allowed to connect"),
     "The server doesn't allow connections from this machine.",
     "Allow this computer in the server's access rules, then check again."),
    (("ssl", "certificate"), "The TLS connection failed.",
     "Check the TLS settings for this database in Settings."),
    (("does not exist",), "The database or user doesn't exist.",
     "Check the database and user names in Settings."),
    (("too many connections", "remaining connection slots"), "Every connection slot is in use.",
     "Close idle connections or raise the server's connection limit."),
)
UNKNOWN_FIX = ("Check that the database is running, that this computer can reach it, and that "
               "its connection details in Settings are right.")


def connection_problem(error: str | None) -> dict[str, str]:
    """Why RDST can't connect, and what to check, in words a user can act on."""
    lowered = (error or "").lower()
    if (error or "").startswith("SSH "):
        return {"reason": error[:300], "fix": "Check the SSH tunnel settings for this database."}
    for needles, reason, fix in _REASONS:
        if any(needle in lowered for needle in needles):
            return {"reason": reason, "fix": fix}
    return {"reason": "RDST couldn't connect.", "fix": UNKNOWN_FIX}


def reason_text(error: str | None) -> str | None:
    if not error:
        return None
    if error.startswith("SSH "):
        return error[:300]
    lowered = error.lower()
    for needles, text, _ in _REASONS:
        if any(needle in lowered for needle in needles):
            return text
    return None


def lost_message(target: str, since: int) -> str:
    return (f"RDST can't reach {target} since {clock_time(since)}. "
            "Monitoring is paused until it's back.")


def resumed_message(gap_from: int, resumed_at: int) -> str:
    if clock_time(gap_from) == clock_time(resumed_at):
        return f"Monitoring resumed at {clock_time(resumed_at)} after a brief interruption."
    return (f"Monitoring resumed at {clock_time(resumed_at)}. "
            f"No readings from {span_text(gap_from, resumed_at)}.")


def _password_missing(target: str) -> bool:
    from shared.config.targets import TargetsConfig

    try:
        config = TargetsConfig()
        config.load()
        env = (config.get(target) or {}).get("password_env")
    except Exception:
        return False
    return bool(env and not os.environ.get(env))


class ConnectionWatch:
    """Debounced reachability per target. Thread-safe: checks arrive from worker threads."""

    def __init__(self, store: MonitorStore, *, clock: Callable[[], float] = time.time,
                 on_recovered: Callable[[str], None] | None = None) -> None:
        self.store = store
        self.clock = clock
        self.on_recovered = on_recovered
        self._lock = threading.RLock()
        # target -> (failures in a row, first failure ts, latest error)
        self._failing: dict[str, tuple[int, int, str | None]] = {}
        self._last_check: dict[str, tuple[int, bool]] = {}

    def collection(self, target: str, ok: bool, ts: int | None = None,
                   error: str | None = None) -> None:
        """A scheduled collection's outcome. Only monitored targets alert, and every
        failure is kept as a failed collect pulse so the chart can tell an outage from
        RDST not running."""
        try:
            if not self.store.levers(target)["enabled"]:
                with self._lock:
                    self._failing.pop(target, None)
                return
            now = int(self.clock() if ts is None else ts)
            if not ok:
                error = self._reason(target, error)
                self.store.record_pulse(target, now, "collect", False,
                                        error or "Couldn't reach the database")
            self.observe(target, ok, now, error)
        except Exception:
            logger.warning("Connection check for %s not recorded", target, exc_info=True)

    def observe(self, target: str, ok: bool, ts: int | None = None,
                error: str | None = None) -> None:
        now = int(self.clock() if ts is None else ts)
        with self._lock:
            latest, recovered = self._last_check.get(target, (0, False))
            if now < latest or now == latest and recovered and not ok:
                return
            # A missing password has its own Inbox item; it isn't also a lost connection.
            if not ok and _password_missing(target):
                self._failing.pop(target, None)
                return
            self._last_check[target] = (now, ok)
            if ok:
                self._failing.pop(target, None)
                self._recovered(target, now)
                return
            error = self._reason(target, error)
            failures, since, first_error = self._failing.get(target, (0, now, error))
            first_error = error or first_error
            failures += 1
            self._failing[target] = (failures, since, first_error)
            if failures >= LOST_AFTER_FAILURES or now - since >= LOST_AFTER_SECONDS:
                self._file(target, now, since, first_error)

    def tick(self) -> None:
        """File an ongoing outage at its deadline, even while a retry is still pending."""
        now = int(self.clock())
        with self._lock:
            for target, (_, since, error) in list(self._failing.items()):
                if not self.store.levers(target)["enabled"]:
                    self._failing.pop(target, None)
                elif now - since >= LOST_AFTER_SECONDS:
                    self._file(target, now, since, error)

    @staticmethod
    def _reason(target: str, error: str | None) -> str | None:
        from shared.ssh_tunnel import get_tunnel_manager

        return get_tunnel_manager().failure_reason(target) or error

    def _open_item(self, target: str) -> dict[str, Any] | None:
        rows = self.store.read("SELECT * FROM inbox_item WHERE target=? AND type=? AND subject=?",
                               (target, *ITEM))
        if rows and rows[0]["state"] not in ("resolved", "dismissed"):
            return rows[0]
        return None

    def _last_reading(self, target: str, before: int) -> int | None:
        rows = self.store.read("SELECT MAX(ts) ts FROM monitor_pulse WHERE target=? "
                               "AND kind='collect' AND ok=1 AND ts<=?", (target, before))
        if rows and rows[0]["ts"] is not None:
            return int(rows[0]["ts"])
        rows = self.store.read("SELECT MAX(bucket_start) ts FROM db_series WHERE target=? "
                               "AND res=60 AND bucket_start<=?", (target, before))
        return int(rows[0]["ts"]) if rows and rows[0]["ts"] is not None else None

    def _file(self, target: str, now: int, since: int, error: str | None) -> None:
        with self._lock:
            failing = self._failing.get(target)
            if failing is None or failing[1] != since:
                return
            self._file_current(target, now, since, error)

    def _file_current(self, target: str, now: int, since: int, error: str | None) -> None:
        open_item = self._open_item(target)
        if open_item is not None:
            # Keep the outage's own start across updates and restarts; an update never pushes.
            body = json.loads(open_item["body_json"])
            if body.get("failed_since"):
                if error and body.get("error") != error[:300]:
                    body["error"] = error[:300]
                    message = lost_message(target, int(body.get('gap_from') or body['failed_since']))
                    reason = reason_text(error)
                    body["message"] = f"{message} {reason}" if reason else message
                    self.store.update_item_body(open_item["id"], body)
                return
        last = self._last_reading(target, since)
        # The gap starts at the last reading before the first failure (a minute's slack).
        gap_from = last if last is not None and since - last <= 15 * 60 else since
        body = {"target": target, "failed_since": since, "last_reading_at": last,
                "gap_from": gap_from, "message": lost_message(target, gap_from)}
        if error:
            body["error"] = error[:300]
            if reason := reason_text(error):
                body["message"] += f" {reason}"
        self.store.upsert_item(target, *ITEM, 1.0, TITLE, body, now, delivery="notify")
        if open_item is None:
            product_telemetry.connection_lost(target)

    def _recovered(self, target: str, now: int) -> None:
        item = self._open_item(target)
        if item is None:
            return
        body = json.loads(item["body_json"])
        gap_from = int(body.get("gap_from") or body.get("failed_since") or item["created_at"])
        # A measured recovery: the Inbox lists it green in Open for a day.
        body.update({"resumed_at": now, "gap_to": now, "gap_from": gap_from,
                     "message": resumed_message(gap_from, now),
                     "resolution": {"before": {}, "after": {"resumed_at": now}}})
        self.store.update_item_body(item["id"], body)
        self.store.action_item(item["id"], "resolve", now, resolved_by="code")
        self.store.notify_recovery(item["id"], now)
        product_telemetry.connection_restored(target, now - gap_from)
        if self.on_recovered is not None:
            try:
                self.on_recovered(target)
            except Exception:
                logger.debug("Recovery hook failed for %s", target, exc_info=True)
