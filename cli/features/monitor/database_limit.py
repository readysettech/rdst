"""The account-wide limit on AI-monitored databases (CONTRACT.md, "Monitored-database limit").

Each monitored database is identified by a fingerprint: a SHA-256 over its engine, host,
port and database name, salted with the signed-in account's id, so the Key Service can
count distinct databases without learning which ones they are. Loopback hosts are one
host, so a database reached as localhost and as 127.0.0.1 counts once.

Turning monitoring on claims a slot on the Key Service; turning it off releases it. The
Key Service owns the limits (3 databases, or 10 when the user's own Anthropic key pays for
analysis and reports). A database that doesn't get a slot is still monitored locally:
charts, lost-connection alerts and code-owned items keep running, and its AI checks stay
off until a slot frees up. Each target's AI state is one of:

- ``on``: the Key Service holds a slot for it.
- ``local``: the account was full when it was turned on; local monitoring only.
- ``pending``: the Key Service couldn't be asked (offline or signed out); signed-out AI
  work waits for sign-in and the claim is retried.

Alongside the state, each target keeps why AI isn't on (``ai_reason``) and since when
(``ai_since``), so the app can say why at once instead of showing "starting" forever.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

LIMIT_CODE = "MONITOR_DATABASE_LIMIT"
LIMIT_MESSAGE = ("This account already uses AI monitoring on as many databases as it can. "
                 "Turn off monitoring on another database to free a slot.")
SUBJECT = "database_limit"
TITLE = "Monitoring limit reached"
# How long a refused target waits before its next AI check tries again.
RETRY_S = 3600
# How often enabled databases re-claim their slots, which keeps them from aging out.
REFRESH_S = 6 * 3600
# How often a pending claim (offline or signed out at the time) is retried.
PENDING_RETRY_S = 60
# How often a local-only database asks again, so a freed slot or a raised limit resumes AI.
LOCAL_RETRY_S = 900
AI_STATE_KEY = "ai_monitoring"
AI_STATES = ("on", "local", "pending")
AI_REASON_KEY = "ai_reason"
AI_SINCE_KEY = "ai_since"
# Why AI monitoring isn't on: signed_out (no Readyset session), unreachable (the Key Service
# didn't answer), service_error (it answered with an error), limit (every slot is in use),
# quota (the account's AI trial allowance is used up).
AI_REASONS = ("signed_out", "unreachable", "service_error", "limit", "quota")
# The account's monthly AI allowance: refusal codes and the kind of AI work they hold.
ALLOWANCE_CODES = {"JEV_BUDGET_EXHAUSTED": "jev", "HOSTED_CAP_REACHED": "hosted",
                   "HOSTED_GLOBAL_CAP_REACHED": "hosted"}
ALLOWANCE_TITLE = "AI limit reached"
# How often one call checks whether a used-up allowance was raised.
ALLOWANCE_RETRY_S = 900
SUPPORT_EMAIL = "hello@readyset.io"
# monitor_setting rows that belong to the account rather than one database.
ACCOUNT = "_account"
_DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306}
_LOOPBACK = {"localhost", "localhost.localdomain", "::1", "0:0:0:0:0:0:0:1", "0.0.0.0", "::"}
_TIMEOUT_S = 5

_summary_lock = threading.Lock()
_last_summary: dict[str, Any] | None = None
_last_refresh: dict[str, float] = {}


def _account_salt() -> str:
    from shared import account_session

    account = account_session.account_metadata()
    return str(account.get("user_id") or account.get("email") or "").strip().lower()


def normalize_host(host: Any) -> str:
    """One name for the machine itself, whichever way the target spells it."""
    value = str(host or "").strip().lower().strip("[]")
    if not value or value in _LOOPBACK or value.startswith("127."):
        return "localhost"
    return value


def fingerprint(target_config: dict[str, Any] | None, salt: str | None = None) -> str | None:
    """A stable, non-reversible id for one database, salted per account."""
    if not target_config:
        return None
    engine = str(target_config.get("engine") or "postgresql").strip().lower()
    host = normalize_host(target_config.get("host"))
    database = str(target_config.get("database") or "").strip()
    try:
        port = int(target_config.get("port") or _DEFAULT_PORTS.get(engine, 0))
    except (TypeError, ValueError):
        port = 0
    salt = _account_salt() if salt is None else salt
    material = "\0".join(("rdst-monitor-database-v1", salt, engine, host, str(port), database))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _target_config(target: str) -> dict[str, Any] | None:
    from shared.config.targets import TargetsConfig

    config = TargetsConfig()
    config.load()
    return config.get(target)


def target_fingerprint(target: str) -> str | None:
    try:
        return fingerprint(_target_config(target))
    except Exception:
        logger.debug("no monitoring fingerprint for %s", target, exc_info=True)
        return None


def own_key() -> bool:
    """Whether the user's own Anthropic key pays for deep analysis and reports."""
    try:
        from shared.llm_manager.key_resolution import resolve_api_key

        return resolve_api_key().provider == "claude"
    except Exception:
        return False


def is_limit_error(error: Any) -> bool:
    return LIMIT_CODE in str(getattr(error, "code", "") or "") or LIMIT_CODE in str(error or "")


def limit_reached(target: str, *, where: str, kind: str = "databases",
                  active: int | None = None, limit: int | None = None) -> None:
    """The one place a monitoring limit refusal is reported.

    `kind` is ``databases`` (the AI-monitored database limit) or ``jev`` / ``hosted`` (the
    account's monthly AI allowance for Jev or for hosted analysis and reports). `where` is
    ``enable`` (the account was full when monitoring was turned on),
    ``retry`` (the user asked again from a local-only database), ``refresh`` (a re-claim
    was refused) or ``keyservice`` (a monitoring AI call was refused). Telemetry hooks in
    here.
    """
    logger.info("Monitoring limit reached for %s (%s, %s): %s of %s", target, kind, where,
                active, limit)
    from shared import product_telemetry

    product_telemetry.monitoring_limit_reached(
        "database_limit" if kind == "databases" else f"{kind}_allowance", feature=where,
        target=target, monitored_count=active, limit=limit)


def is_allowance_error(error: Any) -> str | None:
    """``jev`` or ``hosted`` when the account's monthly AI allowance refused the call."""
    text = f"{getattr(error, 'code', '') or ''} {error or ''}"
    for code, kind in ALLOWANCE_CODES.items():
        if code in text:
            return kind
    return None


def _allowance_key(kind: str) -> str:
    return f"{kind}_allowance_at"


def allowance_paused(store: Any, kind: str, now: int) -> bool:
    """Whether AI work of this kind is held after the allowance refused it.

    One call tries again every ALLOWANCE_RETRY_S, so a raised limit is picked up without a
    restart and a used-up allowance never sees a retry storm.
    """
    rows = store.read("SELECT value FROM monitor_setting WHERE target=? AND key=?",
                      (ACCOUNT, _allowance_key(kind)))
    held = int(rows[0]["value"]) if rows and str(rows[0]["value"]).isdigit() else 0
    return bool(held) and now < held + ALLOWANCE_RETRY_S


def allowance_reached(store: Any) -> list[str]:
    """The allowance kinds currently used up, oldest first."""
    rows = store.read("SELECT key FROM monitor_setting WHERE target=? AND key IN (?,?) "
                      "AND value!='0' ORDER BY value", (ACCOUNT, _allowance_key("jev"),
                                                       _allowance_key("hosted")))
    return [row["key"].split("_", 1)[0] for row in rows]


def file_allowance(store: Any, target: str, kind: str, now: int) -> None:
    """The allowance refused a monitoring call: hold this kind of AI work and say so."""
    first = kind not in allowance_reached(store)
    store.write_setting(ACCOUNT, _allowance_key(kind), str(now))
    store.upsert_item(target, "monitoring_issue", f"{kind}_allowance", 1.0, ALLOWANCE_TITLE,
                      {"target": target, "kind": kind, "contact": SUPPORT_EMAIL}, now)
    if first:
        limit_reached(target, where="keyservice", kind=kind)


def clear_allowance(store: Any, kind: str, now: int) -> None:
    """A call of this kind went through: the allowance was raised or renewed."""
    if kind == "hosted" and own_key():
        return
    rows = store.read("SELECT value FROM monitor_setting WHERE target=? AND key=?",
                      (ACCOUNT, _allowance_key(kind)))
    if not rows or rows[0]["value"] == "0":
        return
    store.write_setting(ACCOUNT, _allowance_key(kind), "0")
    for row in store.read("SELECT target FROM inbox_item WHERE type='monitoring_issue' "
                          "AND subject=? AND state!='resolved'", (f"{kind}_allowance",)):
        store.action_item_for_subject(row["target"], "monitoring_issue", f"{kind}_allowance", now)


def ai_state(store: Any, target: str) -> str:
    """This target's AI monitoring state; ``off`` when monitoring is off."""
    rows = store.read("SELECT key,value FROM monitor_setting WHERE target=? "
                      "AND key IN ('enabled', ?)", (target, AI_STATE_KEY))
    values = {row["key"]: row["value"] for row in rows}
    if values.get("enabled") != "1":
        return "off"
    state = values.get(AI_STATE_KEY)
    return state if state in AI_STATES else "pending"


def display_state(store: Any, target: str) -> str:
    """The AI state the app shows: ``paused`` while the account's AI allowance is used up, and
    ``pending`` for a slot held while signed out (AI can't run until the user signs in)."""
    state = ai_state(store, target)
    if state in ("on", "local", "pending") and not _signed_in():
        return "pending"
    if state in ("on", "pending") and any(
            kind != "hosted" or not own_key() for kind in allowance_reached(store)):
        return "paused"
    return state


def set_ai_state(store: Any, target: str, state: str, reason: str | None = None,
                 now: int | None = None) -> None:
    """Record the target's AI state and why AI isn't on; `ai_since` moves only on a change."""
    rows = store.read("SELECT key,value FROM monitor_setting WHERE target=? AND key IN (?,?,?)",
                      (target, AI_STATE_KEY, AI_REASON_KEY, AI_SINCE_KEY))
    values = {row["key"]: row["value"] for row in rows}
    reason = reason if state != "on" and reason in AI_REASONS else ""
    changed = values.get(AI_STATE_KEY) != state or (values.get(AI_REASON_KEY) or "") != reason
    store.write_setting(target, AI_STATE_KEY, state)
    if changed or not values.get(AI_SINCE_KEY):
        store.write_setting(target, AI_REASON_KEY, reason)
        store.write_setting(target, AI_SINCE_KEY, str(int(time.time() if now is None else now)))


def ai_reason(store: Any, target: str) -> dict[str, Any]:
    """Why this target's AI monitoring isn't on, and since when: ``{"reason", "since"}``.

    ``reason`` is one of AI_REASONS or None (AI is on, or it's starting with no known cause
    yet); ``since`` is when the current state began, in epoch seconds, or None.
    """
    state = display_state(store, target)
    if state == "off":
        return {"reason": None, "since": None}
    rows = store.read("SELECT key,value FROM monitor_setting WHERE target=? AND key IN (?,?)",
                      (target, AI_REASON_KEY, AI_SINCE_KEY))
    values = {row["key"]: row["value"] for row in rows}
    since = int(values[AI_SINCE_KEY]) if str(values.get(AI_SINCE_KEY) or "").isdigit() else None
    if state == "paused":
        return {"reason": "quota", "since": since}
    if state == "on":
        return {"reason": None, "since": since}
    if state == "local":
        return {"reason": "limit", "since": since}
    reason = values.get(AI_REASON_KEY) or None
    if not _signed_in():
        reason = "signed_out"
    elif reason == "signed_out":
        # Signed in since the last claim: ask now rather than at the next retry.
        claim_target(store, target, where="refresh")
        return ai_reason(store, target) if ai_state(store, target) != "pending" or (
            _stored_reason(store, target) != "signed_out") else {"reason": None, "since": since}
    return {"reason": reason if reason in AI_REASONS else None, "since": since}


def _stored_reason(store: Any, target: str) -> str | None:
    rows = store.read("SELECT value FROM monitor_setting WHERE target=? AND key=?",
                      (target, AI_REASON_KEY))
    return rows[0]["value"] if rows else None


def hold_signed_out(store: Any, target: str, now: int) -> None:
    """Record the sign-in hold while preserving a refused database's slot policy."""
    if ai_state(store, target) != "local":
        set_ai_state(store, target, "pending", "signed_out", now)


def paused(store: Any, target: str, now: int) -> bool:
    """Whether automatic AI work (analyses, report narratives) for this target is held.

    Held when the database is local-only, the hosted allowance is used up, or the Key
    Service recently refused it a slot, or the user is signed out.
    """
    if not _signed_in():
        hold_signed_out(store, target, now)
        return True
    if ai_state(store, target) == "local" or (
            allowance_paused(store, "hosted", now) and not own_key()):
        return True
    rows = store.read("SELECT updated_at FROM inbox_item WHERE target=? AND type='monitoring_issue' "
                      "AND subject=? AND state!='resolved'", (target, SUBJECT))
    return bool(rows) and now < int(rows[0]["updated_at"]) + RETRY_S


def file_refusal(store: Any, target: str, now: int, limit: int | None = None) -> None:
    store.upsert_item(target, "monitoring_issue", SUBJECT, 1.0, TITLE,
                      {"target": target, "limit": limit, "message": LIMIT_MESSAGE}, now)
    limit_reached(target, where="keyservice", limit=limit)


def clear(store: Any, target: str, now: int) -> None:
    store.action_item_for_subject(target, "monitoring_issue", SUBJECT, now)


class _NoAnswer:
    """Why a Key Service request got no usable answer: ``signed_out``, ``unreachable`` or
    ``service_error``. Falsy, so callers can test it like None."""

    def __init__(self, reason: str):
        self.reason = reason

    def __bool__(self) -> bool:
        return False


def _request(method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]] | _NoAnswer:
    """(status, body) from the Key Service, or a falsy _NoAnswer saying why there's none."""
    from shared import account_session
    from shared.keyservice import keyservice_url

    try:
        token = account_session.access_token()
    except Exception:
        return _NoAnswer("signed_out")
    if not token:
        return _NoAnswer("signed_out")
    try:
        response = requests.request(method, keyservice_url(path), timeout=_TIMEOUT_S,
                                    headers={"Authorization": f"Bearer {token}",
                                             "Accept": "application/json"}, **kwargs)
    except requests.RequestException:
        return _NoAnswer("unreachable")
    try:
        body = response.json()
    except ValueError:
        return _NoAnswer("service_error")
    return (response.status_code, body) if isinstance(body, dict) else _NoAnswer("service_error")


def _signed_in() -> bool:
    """Read current authorization at admission, including sign-out during a run."""
    try:
        from shared import account_session

        return bool(account_session.access_token())
    except Exception:
        return False


def _remember(body: dict[str, Any]) -> None:
    global _last_summary
    if "limit" in body and "active" in body:
        with _summary_lock:
            _last_summary = {key: body.get(key) for key in
                             ("limit", "own_key_limit", "active", "hosted_active", "databases")}


def account_slots() -> dict[str, Any] | None:
    """The Key Service's view of this account's monitored databases, when reachable."""
    result = _request("GET", "/v1/monitor/databases")
    if not result or result[0] != 200:
        return None
    _remember(result[1])
    return result[1]


def claim(target_config: dict[str, Any] | None, *, with_own_key: bool | None = None,
          label: str | None = None) -> dict[str, Any]:
    """Ask the Key Service for this database's slot.

    Returns ``{"state": "on" | "local" | "pending", ...}`` plus the account summary when
    the Key Service answered.
    """
    fp = fingerprint(target_config)
    if fp is None:
        return {"state": "pending", "reason": "signed_out" if not _signed_in() else
                "service_error"}
    mine = own_key() if with_own_key is None else with_own_key
    result = _request("POST", "/v1/monitor/databases/claim",
                      json={"fingerprint": fp, "own_key": mine,
                            **({"label": label} if label else {})})
    if not result:
        return {"state": "pending", "reason": getattr(result, "reason", "unreachable")}
    status, body = result
    if status == 200:
        _remember(body)
        return {**body, "state": "on"}
    if status == 403 and body.get("code") == LIMIT_CODE:
        _remember(body)
        return {**body, "state": "local", "reason": "limit"}
    if status == 401:
        return {"state": "pending", "reason": "signed_out"}
    return {"state": "pending", "reason": "service_error"}


def claim_target(store: Any, target: str, *, where: str = "enable") -> str:
    """Claim the target's slot and record its AI state. Returns the state."""
    try:
        config = _target_config(target)
    except Exception:
        config = None
    result = claim(config, label=target)
    state = result["state"]
    previous = ai_state(store, target)
    set_ai_state(store, target, state, result.get("reason"))
    _last_refresh[target] = time.monotonic()
    if state == "on":
        clear(store, target, int(time.time()))
    elif state == "local" and previous != "local":
        summary = status_from(result)
        limit_reached(target, where=where, active=summary["in_use"], limit=summary["limit"])
    return state


def release(target_config: dict[str, Any] | None) -> bool:
    """Free the database's slot after monitoring is turned off. Best effort."""
    return release_fingerprint(fingerprint(target_config))


def release_fingerprint(fp: str | None) -> bool:
    """Free one slot by its fingerprint, including one another computer holds."""
    if fp is None:
        return False
    result = _request("POST", "/v1/monitor/databases/release", json={"fingerprint": fp})
    if not result or result[0] != 200:
        return False
    _remember(result[1])
    return bool(result[1].get("released"))


def refresh_claims(store: Any, targets: set[str], *, force: bool = False) -> None:
    """Keep enabled databases' slots fresh, and retry claims that couldn't reach the service.

    Slots are re-claimed every REFRESH_S so they never age out while the app runs; a
    pending claim is retried every PENDING_RETRY_S, and a local-only database asks again
    every LOCAL_RETRY_S, so a freed slot or a limit an admin raised turns AI back on.
    """
    now = time.monotonic()
    for target in sorted(targets):
        try:
            state = ai_state(store, target)
            due = {"pending": PENDING_RETRY_S, "local": LOCAL_RETRY_S}.get(state, REFRESH_S)
            if state == "off":
                continue
            if not force and now - _last_refresh.get(target, float("-inf")) < due:
                continue
            claim_target(store, target, where="refresh")
        except Exception:
            logger.debug("Monitoring slot refresh failed for %s", target, exc_info=True)


def status_from(summary: dict[str, Any] | None, *, with_own_key: bool | None = None,
                local_in_use: int = 0) -> dict[str, Any]:
    """The limit the app shows: AI monitoring on `in_use` of `limit` databases.

    With the user's own key the limit is the Key Service's own-key limit and every
    monitored database counts; otherwise it's the hosted limit and hosted slots count.
    Without an answer from the Key Service, the limit is unknown and this machine's
    AI-monitored databases are counted.
    """
    mine = own_key() if with_own_key is None else with_own_key
    if not summary or summary.get("limit") is None:
        return {"in_use": local_in_use, "limit": None, "own_key": mine, "known": False}
    if mine:
        limit = int(summary.get("own_key_limit") or summary["limit"])
        in_use = int(summary.get("active") or 0)
    else:
        limit = int(summary["limit"])
        in_use = int(summary.get("hosted_active", summary.get("active")) or 0)
    return {"in_use": in_use, "limit": limit, "own_key": mine, "known": True}


def status(store: Any, *, target: str | None = None, refresh: bool = True) -> dict[str, Any]:
    """The account's monitoring limit and this machine's targets by AI state.

    With `target`, `target_fits` says whether turning it on would get AI monitoring: a
    slot is free, or the account already holds one for that database.
    """
    targets = sorted(store.monitored_targets())
    # Reasons first: reading one can claim a slot (signed in since the last try).
    reasons = {target: ai_reason(store, target) for target in targets}
    states = {target: display_state(store, target) for target in targets}
    summary = account_slots() if refresh else None
    if summary is None:
        with _summary_lock:
            summary = dict(_last_summary) if _last_summary else None
    signed_in = _signed_in()
    if not signed_in:
        summary = None
    local_in_use = sum(1 for state in states.values() if state == "on")
    result = status_from(summary, local_in_use=local_in_use)
    result["full"] = bool(result["known"] and result["in_use"] >= result["limit"])
    result["signed_in"] = signed_in
    result["allowance"] = [kind for kind in allowance_reached(store)
                           if kind != "hosted" or not result["own_key"]]
    result["contact"] = SUPPORT_EMAIL
    result["targets"] = [{"target": name, "ai": states[name], **reasons[name]}
                         for name in targets]
    result["elsewhere"] = _elsewhere(summary)
    if target is not None:
        result["target_fits"] = None if not result["known"] else (
            not result["full"] or _holds_slot(summary, target_fingerprint(target), result["own_key"]))
    return result


def _elsewhere(summary: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Slots the account holds for databases this computer doesn't have: monitored on
    another computer, or removed here without being turned off first."""
    rows = (summary or {}).get("databases") or []
    if not rows:
        return []
    from shared.config.targets import TargetsConfig

    try:
        cfg = TargetsConfig()
        cfg.load()
        local = {target_fingerprint(name) for name in cfg.list_targets()}
    except Exception:
        logger.debug("couldn't read targets for the slot list", exc_info=True)
        return []
    return sorted(({"fingerprint": row["fingerprint"], "tier": row.get("tier") or "hosted",
                    "last_seen_at": row.get("last_seen_at"), "label": row.get("label")}
                   for row in rows if row.get("fingerprint") not in local),
                  key=lambda row: -(row["last_seen_at"] or 0))


def _holds_slot(summary: dict[str, Any] | None, fp: str | None, mine: bool) -> bool:
    rows = (summary or {}).get("databases") or []
    return fp is not None and any(row.get("fingerprint") == fp and (
        mine or row.get("tier", "hosted") == "hosted") for row in rows)
