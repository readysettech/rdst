"""Privacy-safe product events: continuous analysis, the desktop app and app errors.

Every event goes through `shared.telemetry.telemetry.track`, so it keeps the same gating as
the rest of RDST's telemetry (RDST_TELEMETRY, the config opt-out, RDST_TESTING, the ingest
key, and the `internal_user` tag the PostHog destinations filter on).

Properties never carry SQL, hostnames, database or target names, file paths or emails. A
database is named by `database_id`: a prefix of its salted monitoring fingerprint. Free text
(error messages) passes through `scrub` first. TELEMETRY.md in
docs/plans/continuous_analysis lists every event.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import traceback
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Errors: at most this many app_error events a minute, and one per distinct error an hour.
APP_ERROR_PER_MINUTE = 5
APP_ERROR_DEDUPE_S = 3600
# monitoring_limit_reached: once a day per reason and database.
LIMIT_DEDUPE_S = 86400
MESSAGE_LIMIT = 200

_BANNED_KEYS = frozenset({
    "sql", "query", "query_text", "text", "normalized_sql", "host", "hostname", "server",
    "database", "database_name", "dbname", "db", "target", "target_name", "name", "email",
    "user", "username", "password", "path", "file", "url", "title", "body", "detail",
})

_LIMIT_REASONS = {
    "MONITOR_DATABASE_LIMIT": "database_limit",
    "HOSTED_CAP_REACHED": "monthly_quota",
    "JEV_BUDGET_EXHAUSTED": "monthly_quota",
    "TRIAL_EXHAUSTED": "monthly_quota",
    "BUDGET_EXHAUSTED": "monthly_quota",
    "HOSTED_GLOBAL_CAP_REACHED": "global_cap",
}

_SQL_START = re.compile(
    r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\S+\s+SET|DELETE\s+FROM|WITH\s+\S+\s+AS|"
    r"CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE)\b[\s\S]*")
_SQL_LOWER = re.compile(r"\b(select\s[\s\S]*?\sfrom\s|insert\s+into\s|delete\s+from\s|"
                        r"update\s+\S+\s+set\s)[\s\S]*", re.IGNORECASE)
_VERSION = re.compile(r"[A-Za-z0-9_.+-]{1,60}")
_ROUTE = re.compile(r"/[A-Za-z0-9_{}/.-]{1,120}")
_SCRUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email]"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://\S+", re.IGNORECASE), "[url]"),
    (re.compile(r"\b(host|hostname|hostaddr|server|port|dbname|database|db|user|username|"
                r"password|passwd|sslrootcert|target|schema|table|relation)\s*[=:]\s*\S+",
                re.IGNORECASE), r"\1=[redacted]"),
    (re.compile(r"'[^']*'|\"[^\"]*\"|`[^`]*`|“[^”]*”"), "[value]"),
    (re.compile(r"\[?\b[0-9a-f]{0,4}(:[0-9a-f]{0,4}){2,7}\b\]?", re.IGNORECASE), "[ip]"),
    (re.compile(r"\b\d{1,3}(\.\d{1,3}){3}(:\d+)?\b"), "[ip]"),
    (re.compile(r"\b[A-Za-z]:\\[^\s]*"), "[path]"),
    (re.compile(r"(~|\.{1,2})?(/[^\s/:,;()]+){2,}/?"), "[path]"),
    (re.compile(r"\b[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*"
                r"\.(com|net|org|io|dev|cloud|internal|local|localdomain|lan|aws|azure|"
                r"amazonaws|rds|gcp|app|co|us|eu|uk|de|in|ai|db|svc|cluster)\b(:\d+)?",
                re.IGNORECASE), "[host]"),
    (re.compile(r"\blocalhost(:\d+)?\b", re.IGNORECASE), "[host]"),
)


def scrub(text: Any, limit: int = MESSAGE_LIMIT) -> str:
    """A short message with SQL, hostnames, addresses, paths, emails and quoted values removed."""
    value = str(text or "")
    value = _SQL_START.sub("[sql]", value)
    value = _SQL_LOWER.sub("[sql]", value)
    for pattern, replacement in _SCRUBS:
        value = pattern.sub(replacement, value)
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def safe_properties(properties: dict[str, Any] | None) -> dict[str, Any]:
    """Event properties with identifying keys dropped and free text scrubbed."""
    safe: dict[str, Any] = {}
    for key, value in (properties or {}).items():
        if not isinstance(key, str) or key.lower() in _BANNED_KEYS:
            continue
        if value is None or isinstance(value, (bool, int, float)):
            safe[key] = value
        elif key == "route" and isinstance(value, str) and _ROUTE.fullmatch(value):
            safe[key] = value
        elif isinstance(value, str) and key.endswith("version") and _VERSION.fullmatch(value):
            safe[key] = value
        elif isinstance(value, str):
            safe[key] = scrub(value)
        elif isinstance(value, (list, tuple)):
            safe[key] = [item if isinstance(item, (bool, int, float)) else scrub(item, 60)
                         for item in list(value)[:20]
                         if item is None or isinstance(item, (bool, int, float, str))]
    return safe


_version: str | None = None


def app_version() -> str:
    """The real RDST version (the telemetry manager's own `rdst_version` is a fixed string)."""
    global _version
    if _version is None:
        try:
            from importlib.metadata import version

            _version = version("rdst")
        except Exception:
            try:
                from _version import __version__

                _version = str(__version__)
            except Exception:
                _version = "unknown"
    return _version


def surface() -> str:
    if os.environ.get("RDST_DESKTOP") == "1":
        return "desktop"
    if "RDST_WEB_SERVE_STATIC" in os.environ:
        return "web"
    return "cli"


def surface_properties() -> dict[str, Any]:
    """Properties that let a funnel tell desktop, browser and CLI users apart."""
    props: dict[str, Any] = {"surface": surface(), "app_version": app_version(),
                             "is_desktop": surface() == "desktop"}
    desktop_version = os.environ.get("RDST_DESKTOP_VERSION")
    if desktop_version:
        props["desktop_version"] = scrub(desktop_version, 40)
    return props


def _telemetry() -> Any:
    from shared.telemetry import telemetry

    return telemetry


def identity(device_id: str) -> dict[str, Any]:
    """The local person identity. Email is used only to set the PostHog profile."""
    from shared import account_session

    metadata = account_session.account_metadata() if account_session.is_signed_in_locally() else {}
    user_id = str(metadata.get("user_id") or "")
    return {
        "distinct_id": user_id or device_id,
        "device_id": device_id,
        "account_kind": "signed_in" if user_id else "device_only",
        "email": str(metadata.get("email") or "") if user_id else "",
    }


def enabled() -> bool:
    try:
        telemetry = _telemetry()
        return bool(telemetry.POSTHOG_API_KEY) and telemetry.is_enabled()
    except Exception:
        return False


def capture(event: str, properties: dict[str, Any] | None = None, *,
            trusted: dict[str, Any] | None = None) -> None:
    """Send one event with safe properties. Never raises.

    `trusted` values are built by this module from known-safe parts (an admin link) and
    skip the scrubbing that would mangle them.
    """
    try:
        if not enabled():
            return
        _telemetry().track(event, {**safe_properties(properties), **(trusted or {}),
                                   **surface_properties()})
    except Exception:
        logger.debug("telemetry event %s not sent", event, exc_info=True)


# Small persistent state: first-time flags and dedupe times, kept apart from stats.json.

_state_lock = threading.RLock()


def _state_path():
    from shared.constants import rdst_data_dir

    return rdst_data_dir() / "telemetry_state.json"


def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(state: dict[str, Any]) -> None:
    try:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        logger.debug("telemetry state not saved", exc_info=True)


def claim_once(flag: str) -> bool:
    """True the first time `flag` is claimed on this install."""
    with _state_lock:
        state = _read_state()
        flags = state.setdefault("once", {})
        if flags.get(flag):
            return False
        flags[flag] = int(time.time())
        _write_state(state)
        return True


def claim_every(key: str, seconds: int, now: float | None = None) -> bool:
    """True when `key` was last claimed at least `seconds` ago (or never)."""
    now = time.time() if now is None else now
    with _state_lock:
        state = _read_state()
        times = state.setdefault("last", {})
        if now - float(times.get(key) or 0) < seconds:
            return False
        times[key] = int(now)
        _write_state(state)
        return True


def state_value(key: str) -> Any:
    with _state_lock:
        return _read_state().get("values", {}).get(key)


def set_state_value(key: str, value: Any) -> None:
    with _state_lock:
        state = _read_state()
        state.setdefault("values", {})[key] = value
        _write_state(state)


# Databases

_ids: dict[str, tuple[float, str | None]] = {}
_ID_TTL_S = 600


def _salt() -> str:
    try:
        from shared import account_session

        account = account_session.account_metadata()
        salt = str(account.get("user_id") or "").strip().lower()
        if salt:
            return salt
    except Exception:
        pass
    return _telemetry().device_id


def database_id(target: str | None = None, config: dict[str, Any] | None = None) -> str | None:
    """A short, salted, non-reversible id for one database (never its name or host).

    Signed in, it's a prefix of the fingerprint the Key Service counts, so the two can be
    joined; signed out, it's salted with this install's random device id.
    """
    if target and config is None:
        cached = _ids.get(target)
        if cached and time.time() - cached[0] < _ID_TTL_S:
            return cached[1]
    try:
        from features.monitor.database_limit import fingerprint

        if config is None:
            from shared.config.targets import TargetsConfig

            targets = TargetsConfig()
            targets.load()
            config = targets.get(target) or {}
        fp = fingerprint(config, salt=_salt())
        value = fp[:16] if fp else None
    except Exception:
        value = None
    if value is None and target:
        value = hashlib.sha256(f"rdst-telemetry-target\0{_salt()}\0{target}".encode()).hexdigest()[:16]
    if target:
        _ids[target] = (time.time(), value)
    return value


def engine_of(target: str) -> str:
    try:
        from shared.config.targets import TargetsConfig

        targets = TargetsConfig()
        targets.load()
        return str((targets.get(target) or {}).get("engine") or "unknown").lower()
    except Exception:
        return "unknown"


def own_key() -> bool:
    """Whether the user's own Anthropic key pays for deep analysis and reports."""
    try:
        from features.monitor.database_limit import own_key as resolved

        return bool(resolved())
    except Exception:
        pass
    try:
        return _telemetry()._get_auth_type() == "own_key"
    except Exception:
        return False


def database_properties(target: str) -> dict[str, Any]:
    return {"database_id": database_id(target), "engine": engine_of(target)}


# Monitoring events

def monitoring_enabled(target: str, *, monitored_count: int, ai_monitoring: str | None = None,
                       signed_in: bool | None = None) -> None:
    if not enabled():
        return
    key = own_key()
    if ai_monitoring is None:
        if signed_in is None:
            try:
                from features.monitor.loop import signed_in as check

                signed_in = check()
            except Exception:
                signed_in = False
        ai_monitoring = "on" if (signed_in or key) else "local_only"
    capture("monitoring_enabled", {
        **database_properties(target),
        "display_name": "RDST Monitoring Enabled",
        "first_for_account": claim_once(f"monitoring_enabled:{_account_scope()}"),
        "ai_monitoring": ai_monitoring,
        "own_key": key,
        "monitored_count": monitored_count,
        "account_kind": account_kind(),
    })


def monitoring_disabled(target: str, *, monitored_count: int, reason: str = "user") -> None:
    if not enabled():
        return
    capture("monitoring_disabled", {
        **database_properties(target),
        "display_name": "RDST Monitoring Disabled",
        "monitored_count": monitored_count,
        "reason": reason,
        "own_key": own_key(),
        "account_kind": account_kind(),
    })


def _account_scope() -> str:
    try:
        from shared import account_session

        account_id = account_session.account_metadata().get("analytics_account_id")
        if account_id:
            return str(account_id)
    except Exception:
        pass
    return "device"


def desktop_installed(properties: dict[str, Any]) -> None:
    """The desktop app's first run on a new install.

    Also sends the existing `installation` event (install_method=desktop), which the Slack
    destination announces, unless this install already sent it for the desktop app: the
    backend sends it itself when the desktop's first run is also RDST's first on the machine.
    """
    if not enabled():
        return
    capture("desktop_installed", properties)
    telemetry = _telemetry()
    if not telemetry._get_stats().get("desktop_installations"):
        telemetry.track_installation("desktop")


# The Key Service admin dashboard's drill-down for one user. Slack builds its link from
# `admin_base_url` and `account_user_id`; `admin_user_url` is the same link, ready made.
ADMIN_USER_PATH = "/admin#/users/{user_id}"


def account_kind() -> str:
    """``signed_in`` with a Readyset account, else ``device_only``."""
    try:
        from shared import account_session

        return "signed_in" if account_session.is_signed_in_locally() else "device_only"
    except Exception:
        return "device_only"


def _account_admin() -> dict[str, Any]:
    """The account's Key Service user id and its admin drill-down link, when signed in."""
    try:
        from shared import account_session
        from shared.keyservice import keyservice_url

        user_id = str(account_session.account_metadata().get("user_id") or "").strip()
        if not user_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", user_id):
            return {}
        return {"account_user_id": user_id, "admin_base_url": keyservice_url("/admin"),
                "admin_user_url": keyservice_url(ADMIN_USER_PATH.format(user_id=user_id))}
    except Exception:
        return {}


def limit_reason(code: Any) -> str | None:
    text = str(code or "")
    for marker, reason in _LIMIT_REASONS.items():
        if marker in text:
            return reason
    return None


def monitoring_limit_reached(reason: str, *, feature: str, target: str | None = None,
                             fingerprint: str | None = None, code: str | None = None,
                             monitored_count: int | None = None,
                             limit: int | None = None) -> None:
    """One event for every monitoring, AI quota or global cap refusal, deduped per day."""
    if not enabled():
        return
    db = database_id(target) if target else (fingerprint[:16] if fingerprint else None)
    if not claim_every(f"limit:{reason}:{db or '-'}", LIMIT_DEDUPE_S):
        return
    props: dict[str, Any] = {
        "display_name": "RDST Monitoring Limit Reached",
        "reason": reason, "feature": feature, "database_id": db, "own_key": own_key(),
        "account_kind": account_kind(),
    }
    if target:
        props["engine"] = engine_of(target)
    if code:
        props["code"] = scrub(code, 60)
    if monitored_count is not None:
        props["monitored_count"] = monitored_count
    if limit is not None:
        props["limit"] = limit
    capture("monitoring_limit_reached", props, trusted=_account_admin())


def refusal(code: Any, *, feature: str) -> None:
    """A Key Service refusal code seen by an AI client; files a limit event when it is one."""
    try:
        reason = limit_reason(code)
        # Database limit refusals are reported by features.monitor.database_limit.
        if reason is None or reason == "database_limit":
            return
        from shared.llm_manager.inference_attribution import monitor_database_headers

        fingerprint = next(iter(monitor_database_headers().values()), None)
        # Monitoring work carries its database's fingerprint; a user's own run doesn't.
        monitoring_limit_reached(reason, feature=feature if fingerprint else f"{feature}_user",
                                 fingerprint=fingerprint, code=str(code))
    except Exception:
        logger.debug("limit refusal not reported", exc_info=True)


def connection_lost(target: str) -> None:
    if not enabled():
        return
    capture("connection_lost", database_properties(target))


def connection_restored(target: str, downtime_s: int) -> None:
    if not enabled():
        return
    capture("connection_restored", {**database_properties(target), "downtime_s": max(0, downtime_s)})


def inbox_item_filed(item: dict[str, Any], kind: str, store: Any = None) -> None:
    """A new, reopened or worse inbox item; the first one per database also sends first_inbox_item."""
    if not enabled():
        return
    try:
        from features.monitor.presentation import severity_band

        band = severity_band(str(item.get("type")), float(item.get("severity") or 0))
    except Exception:
        band = "unknown"
    target = str(item.get("target") or "")
    item_type = str(item.get("type") or "unknown")
    props = {**database_properties(target), "kind": item_type, "band": band,
             "change": kind, "delivery": item.get("delivery"),
             "subject_kind": _subject_kind(item_type, str(item.get("subject") or ""))}
    capture("inbox_item_filed", props)
    if kind == "created" and item_type != "monitoring_issue" and props["database_id"] and \
            claim_once(f"first_inbox_item:{props['database_id']}"):
        enabled_at = None
        try:
            if store is None:
                from shared.monitor.monitor_store import MonitorStore

                store = MonitorStore()
            enabled_at = store.enabled_at(target)
        except Exception:
            pass
        now = int(item.get("created_at") or time.time())
        capture("first_inbox_item", {
            **{k: props[k] for k in ("database_id", "engine", "kind", "band")},
            "display_name": "RDST First Inbox Item",
            "minutes_since_enable": round((now - enabled_at) / 60) if enabled_at else None,
        })


def _subject_kind(item_type: str, subject: str) -> str:
    """The issue name for monitoring issues (a fixed vocabulary); query subjects are hashes."""
    if item_type == "monitoring_issue" and re.fullmatch(r"[a-z_]{3,40}", subject):
        return subject
    return "query" if re.fullmatch(r"[0-9a-f]{8,}", subject) else "other"


def deep_analysis_completed(target: str, *, ok: bool, duration_s: float, reason: str,
                            mode: str | None, param_source: str | None, user_requested: bool,
                            score: Any = None, error: str | None = None) -> None:
    if not enabled():
        return
    props: dict[str, Any] = {
        **database_properties(target), "ok": ok, "duration_s": round(max(0.0, duration_s), 1),
        "trigger": reason, "mode": mode, "values_source": param_source,
        "user_requested": user_requested, "own_key": own_key(),
        "score_bucket": _score_bucket(score),
    }
    if not ok:
        props["error_class"] = _error_class(error)
    capture("deep_analysis_completed", props)


def _score_bucket(score: Any) -> str | None:
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if value < 40:
        return "0-39"
    if value < 70:
        return "40-69"
    if value < 90:
        return "70-89"
    return "90-100"


def _error_class(error: str | None) -> str:
    """A stable class for a failure message: its leading code, or a short generic label."""
    text = str(error or "")
    match = re.match(r"\s*([A-Z][A-Z0-9]*_[A-Z0-9_]{1,60})\b", text)
    if match:
        return match.group(1)
    match = re.match(r"\s*([A-Z][A-Za-z0-9]+(Error|Exception|Timeout))\b", text)
    if match:
        return match.group(1)
    lowered = text.lower()
    for needle, label in (("timed out", "timeout"), ("timeout", "timeout"),
                          ("safety", "unsafe_query"), ("no longer available", "query_gone"),
                          ("connection", "connection"), ("permission", "permission")):
        if needle in lowered:
            return label
    return "other"


def report_generated(target: str, *, trigger: str, ok: bool, duration_s: float,
                     narrative_ok: bool | None = None, error_class: str | None = None,
                     cancelled: bool = False) -> None:
    """Called from the event loop, so the config read happens on a worker thread."""
    if not enabled():
        return
    threading.Thread(target=_report_generated, daemon=True, name="report-telemetry",
                     args=(target, trigger, ok, duration_s, narrative_ok, error_class,
                           cancelled)).start()


def _report_generated(target: str, trigger: str, ok: bool, duration_s: float,
                      narrative_ok: bool | None, error_class: str | None,
                      cancelled: bool) -> None:
    props: dict[str, Any] = {**database_properties(target), "trigger": trigger, "ok": ok,
                             "duration_s": round(max(0.0, duration_s), 1)}
    if narrative_ok is not None:
        props["narrative_ok"] = narrative_ok
    if cancelled:
        props["cancelled"] = True
    if error_class:
        props["error_class"] = error_class
    capture("report_generated", props)


# Errors

class _ErrorGate:
    """A few app_error events a minute, each distinct error at most once an hour."""

    def __init__(self, per_minute: int = APP_ERROR_PER_MINUTE,
                 dedupe_s: int = APP_ERROR_DEDUPE_S) -> None:
        self.per_minute = per_minute
        self.dedupe_s = dedupe_s
        self._lock = threading.Lock()
        self._sent: deque[float] = deque()
        self._last: dict[tuple, float] = {}
        self._suppressed: dict[tuple, int] = {}

    def admit(self, key: tuple, now: float | None = None) -> int | None:
        """The number of suppressed repeats to report with this one, or None to drop it."""
        now = time.monotonic() if now is None else now
        with self._lock:
            while self._sent and now - self._sent[0] >= 60:
                self._sent.popleft()
            last = self._last.get(key)
            if (last is not None and now - last < self.dedupe_s) or \
                    len(self._sent) >= self.per_minute:
                self._suppressed[key] = self._suppressed.get(key, 0) + 1
                return None
            if len(self._last) > 500:
                self._last.clear()
                self._suppressed.clear()
            self._last[key] = now
            self._sent.append(now)
            return self._suppressed.pop(key, 0)


_gate = _ErrorGate()
_reporting = threading.local()


def _location(error: BaseException | None) -> str | None:
    if error is None or error.__traceback__ is None:
        return None
    frames = traceback.extract_tb(error.__traceback__)
    if not frames:
        return None
    frame = frames[-1]
    return f"{os.path.basename(frame.filename)}:{frame.lineno} {frame.name}"


def report_error(component: str, error: BaseException | None = None, *,
                 error_class: str | None = None, message: str | None = None,
                 source: str = "backend", handled: bool = True,
                 extra: dict[str, Any] | None = None) -> None:
    """Send `app_error`: error class, component, a scrubbed short message, never raising."""
    if getattr(_reporting, "active", False):
        return
    _reporting.active = True
    try:
        if not enabled():
            return
        cls = scrub(error_class or (type(error).__name__ if error is not None else "Error"), 80)
        text = scrub(message if message is not None else (str(error) if error else ""))
        component = scrub(component, 60) or "unknown"
        repeats = _gate.admit((source, component, cls, text[:80]))
        if repeats is None:
            return
        props: dict[str, Any] = {"source": source, "component": component, "error_class": cls,
                                 "message": text, "handled": handled, "suppressed_repeats": repeats}
        location = _location(error)
        if location:
            props["location"] = scrub(location, 80)
        if extra:
            props.update(safe_properties(extra))
            route = extra.get("route")
            if isinstance(route, str) and _ROUTE.fullmatch(route):
                props["route"] = route
        capture("app_error", props)
    except Exception:
        pass
    finally:
        _reporting.active = False


# Logger name prefix -> component. Most specific first.
LOG_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("features.monitor.analyze_queue", "analyze_queue"),
    ("features.monitor.report", "report"),
    ("features.monitor.judge", "monitor_ai"),
    ("features.monitor.param_sampler", "collector"),
    ("features.monitor.api", "monitor_api"),
    ("features.monitor", "monitor_loop"),
    ("shared.monitor", "monitor_store"),
    ("features.query_registry", "collector"),
    ("shared.query_registry", "collector"),
    ("shared.agent_activity", "agent_activity"),
    ("features.agent_activity", "agent_activity"),
    ("shared.account_session", "account"),
    ("shared.llm_manager", "llm"),
    ("uvicorn.error", "server"),
)


def component_for(logger_name: str) -> str:
    for prefix, component in LOG_COMPONENTS:
        if logger_name == prefix or logger_name.startswith(prefix + "."):
            return component
    return "backend"


class AppErrorLogHandler(logging.Handler):
    """Reports logged errors (ERROR, or WARNING with a traceback) as app_error.

    The message is the log call's format string, not the formatted text, so target names,
    hosts and SQL passed as arguments never reach it.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.ERROR and not record.exc_info:
            return
        try:
            error = record.exc_info[1] if record.exc_info else None
            template = record.msg if isinstance(record.msg, str) else type(record.msg).__name__
            detail = scrub(template, 120)
            if error is not None and str(error):
                detail = f"{detail}: {scrub(str(error), 120)}"
            report_error(component_for(record.name), error,
                         error_class=type(error).__name__ if error is not None else "LoggedError",
                         message=detail, extra={"level": record.levelname.lower()})
        except Exception:
            pass


_installed = False
_install_lock = threading.Lock()
_handler: AppErrorLogHandler | None = None


def install_error_capture() -> None:
    """Report logged errors and uncaught exceptions from this backend process. Idempotent."""
    global _installed, _handler
    with _install_lock:
        if _installed:
            return
        _installed = True
        _handler = AppErrorLogHandler()
        # Only the outermost loggers: a record reaches every ancestor's handlers, so one
        # handler on both a logger and its parent would see each record twice.
        prefixes = [prefix for prefix, _component in LOG_COMPONENTS]
        for prefix in prefixes:
            if not any(prefix.startswith(other + ".") for other in prefixes):
                logging.getLogger(prefix).addHandler(_handler)

        previous_thread_hook = threading.excepthook

        def thread_hook(args: Any) -> None:
            if args.exc_type is not SystemExit:
                report_error("thread", args.exc_value, handled=False)
            previous_thread_hook(args)

        threading.excepthook = thread_hook
        previous_hook = sys.excepthook

        def process_hook(exc_type: Any, exc: Any, tb: Any) -> None:
            if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
                report_error("process", exc, handled=False)
            previous_hook(exc_type, exc, tb)

        sys.excepthook = process_hook


def install_asyncio_capture(loop: Any) -> None:
    """Report exceptions nobody awaited (crashed background tasks) on `loop`."""
    previous: Callable[..., Any] | None = loop.get_exception_handler()

    def handler(event_loop: Any, context: dict[str, Any]) -> None:
        error = context.get("exception")
        if error is not None and not isinstance(error, (KeyboardInterrupt, SystemExit)):
            report_error("async_task", error, handled=False)
        if previous is not None:
            previous(event_loop, context)
        else:
            event_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)
