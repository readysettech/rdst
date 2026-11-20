"""Inbox pushes to a Slack channel through the user's own incoming webhook.

The monitor decides what pushes: it records a `notified` inbox event for exactly the
items the desktop app shows as system notifications (per-event switches, quiet hours and
the daily cap already applied). This module forwards those same events to Slack, worded
the way the desktop app words them. The webhook URL is a secret: it comes from
RDST_SLACK_WEBHOOK_URL or the secret store, and never reaches logs or config.toml.
"""

import asyncio
import json
import logging
import os
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

ENV_NAME = "RDST_SLACK_WEBHOOK_URL"
SECRET_NAME = "RDST_SLACK_WEBHOOK_URL"
PUBLIC_URL_ENV = "RDST_PUBLIC_URL"
ALLOWED_HOSTS = ("hooks.slack.com", "hooks.slack-gov.com")
ALLOWED_PATH_PREFIXES = ("/services/", "/triggers/", "/workflows/")
TIMEOUT_SECONDS = 5
POLL_SECONDS = 2.0
# Act now: these types, or a severity of 0.85 and up (as the Inbox and desktop app band it).
ACT_NOW_TYPES = {"stuck_query", "monitoring_issue"}
ACT_NOW_SEVERITY = 0.85

_status_lock = threading.Lock()
_status: dict[str, Any] = {"last_sent_at": None, "last_error": None, "last_error_at": None}


class InvalidWebhookUrl(ValueError):
    pass


def validate_webhook_url(url: str) -> str:
    """The URL, trimmed, if it's a Slack incoming webhook; raises InvalidWebhookUrl."""
    url = (url or "").strip()
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.port
            or parts.username or parts.password
            or not parts.path.startswith(ALLOWED_PATH_PREFIXES)):
        raise InvalidWebhookUrl(
            "Use a Slack incoming webhook URL, like https://hooks.slack.com/services/...")
    return url


def _store():
    from shared.secret_store_service import SecretStoreService

    return SecretStoreService()


def configured() -> tuple[str | None, str | None]:
    """(url, source) where source is "env" or "settings"; (None, None) when unset."""
    env = os.environ.get(ENV_NAME, "").strip()
    if env:
        return env, "env"
    try:
        saved = _store().get_secret(SECRET_NAME)
    except Exception:
        logger.debug("Slack webhook lookup failed", exc_info=True)
        saved = None
    return (saved, "settings") if saved else (None, None)


def save_webhook_url(url: str) -> bool:
    url = validate_webhook_url(url)
    result = _store().set_secret(SECRET_NAME, url, apply_to_environment=False)
    return bool(result.get("persisted"))


def clear_webhook_url() -> None:
    _store().clear_required([SECRET_NAME], clear_environment="none")


def status() -> dict[str, Any]:
    with _status_lock:
        return dict(_status)


def _record(error: str | None) -> None:
    with _status_lock:
        if error is None:
            _status["last_sent_at"] = int(time.time())
            _status["last_error"] = None
            _status["last_error_at"] = None
        else:
            _status["last_error"] = error
            _status["last_error_at"] = int(time.time())


def _public_url() -> str | None:
    base = os.environ.get(PUBLIC_URL_ENV, "").strip().rstrip("/")
    return base if base.startswith(("https://", "http://")) else None


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _body(event: dict[str, Any]) -> dict[str, Any]:
    try:
        body = json.loads(event.get("body_json") or "{}")
    except (TypeError, ValueError):
        return {}
    if isinstance(body, str):
        return {"message": body}
    return body if isinstance(body, dict) else {}


def _detail(event: dict[str, Any], body: dict[str, Any]) -> str:
    target = event.get("target") or ""
    if event.get("summary"):
        return f"{target}: {event['summary']}"
    for key in ("message", "summary", "description", "detail", "error"):
        if isinstance(body.get(key), str) and body[key]:
            return body[key]
    numbers = body.get("numbers")
    if isinstance(numbers, dict) and isinstance(numbers.get("mean_ms"), (int, float)):
        return f"{target}: {round(numbers['mean_ms'])} ms mean latency."
    return f"Database: {target}"


def _act_now(event: dict[str, Any]) -> bool:
    if event.get("state") in ("resolved", "dismissed"):
        return False
    severity = event.get("severity")
    return event.get("type") in ACT_NOW_TYPES or (
        isinstance(severity, (int, float)) and severity >= ACT_NOW_SEVERITY)


def message_for_event(event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """(dedupe key, Slack payload) for one `notified` inbox event, or None to skip it."""
    if event.get("kind") != "notified" or not isinstance(event.get("item_id"), int):
        return None
    target = str(event.get("target") or "")
    body = _body(event)
    item_id = event["item_id"]
    if event.get("type") == "monitoring_issue" and event.get("subject") == "lost_connection":
        failed_since = body.get("failed_since")
        if event.get("state") == "dismissed" or (
                isinstance(failed_since, (int, float)) and (event.get("ts") or 0) < failed_since):
            return None
        resumed = event.get("state") == "resolved" or isinstance(body.get("resumed_at"), (int, float))
        episode = failed_since if isinstance(failed_since, (int, float)) else event.get("id")
        key = f"{item_id}:{'resumed' if resumed else 'lost'}:{episode}"
        title = f"Monitoring resumed on {target}" if resumed else f"Can't reach {target}"
        message = body.get("message") if isinstance(body.get("message"), str) else None
        detail = message or (f"RDST can reach {target} again." if resumed else
                             f"RDST can't reach {target}. Monitoring is paused until it's back.")
        urgent = not resumed
    else:
        key = f"{item_id}:{event.get('id')}"
        title = str(event.get("title") or "New inbox alert")
        detail = _detail(event, body)
        urgent = _act_now(event)
    text = f"{':rotating_light: ' if urgent else ''}*{_escape(title)}*\n{_escape(detail)}"
    base = _public_url()
    if base:
        text += f"\n<{base}/inbox?item={item_id}|Open in RDST>"
    return key, {"text": text, "unfurl_links": False, "unfurl_media": False}


def post(url: str, payload: dict[str, Any]) -> str | None:
    """Send one message. Returns None on success, else an error safe to show and log."""
    try:
        import requests

        response = requests.post(validate_webhook_url(url), json=payload,
                                 timeout=TIMEOUT_SECONDS, allow_redirects=False)
    except InvalidWebhookUrl as exc:
        error = str(exc)
    except Exception as exc:
        # The exception text can carry the URL; only its class is kept.
        error = f"Slack couldn't be reached ({type(exc).__name__})."
    else:
        if 200 <= response.status_code < 300:
            _record(None)
            return None
        reason = (response.text or "").strip()[:80]
        error = f"Slack answered {response.status_code}" + (f": {reason}" if reason else ".")
    _record(error)
    logger.warning("Slack notification not delivered: %s", error)
    return error


def send_test(url: str | None = None) -> str | None:
    url = url or configured()[0]
    if not url:
        return "Add a Slack webhook URL first."
    base = _public_url()
    text = ("*RDST test notification*\nAlerts from RDST's monitoring show up here."
            + (f"\n<{base}/inbox|Open the Inbox>" if base else ""))
    return post(url, {"text": text, "unfurl_links": False, "unfurl_media": False})


class SlackForwarder:
    """Forwards new `notified` inbox events to the configured webhook, in the background."""

    def __init__(self, store: Any = None, *, poll_seconds: float = POLL_SECONDS) -> None:
        self.store = store
        self.poll_seconds = poll_seconds
        self.cursor: int | None = None
        self._sent: OrderedDict[str, None] = OrderedDict()
        self._task: asyncio.Task | None = None

    def start(self) -> "SlackForwarder":
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="slack-notifications")
        return self

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _monitor_store(self):
        if self.store is None:
            from shared.monitor.monitor_store import MonitorStore

            store = MonitorStore()
            if not store.path.exists():
                return None
            self.store = store
        return self.store

    def _first(self, key: str) -> bool:
        if key in self._sent:
            return False
        self._sent[key] = None
        while len(self._sent) > 500:
            self._sent.popitem(last=False)
        return True

    def tick(self) -> int:
        """Forward what arrived since the last tick. Returns how many messages were sent."""
        store = self._monitor_store()
        if store is None:
            return 0
        if self.cursor is None:
            # Start at the newest event: history from before startup isn't replayed.
            self.cursor = store.read("SELECT COALESCE(MAX(id),0) AS id FROM inbox_event")[0]["id"]
            return 0
        rows = store.inbox_events(self.cursor)
        if not rows:
            return 0
        self.cursor = rows[-1]["id"]
        url, _ = configured()
        if not url:
            return 0
        sent = 0
        for row in rows:
            if row.get("kind") != "notified":
                continue
            row = dict(row)
            row["summary"] = _item_summary(store, row["item_id"])
            message = message_for_event(row)
            if message is None or not self._first(message[0]):
                continue
            if post(url, message[1]) is None:
                sent += 1
        return sent

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                logger.warning("Slack notification check failed", exc_info=True)
            await asyncio.sleep(self.poll_seconds)


def _item_summary(store: Any, item_id: int) -> str | None:
    try:
        from features.monitor.api import views

        rows = store.read("SELECT * FROM inbox_item WHERE id=?", (item_id,))
        return views.inbox_items(store, rows)[0].get("summary") if rows else None
    except Exception:
        logger.debug("No summary for inbox item %s", item_id, exc_info=True)
        return None
