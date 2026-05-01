#!/usr/bin/env python3
"""End-to-end PostHog smoke test for the `command_run` telemetry CM.

Runs each user-facing command (analyze / top historical / top realtime /
scan dry-run), then polls the PostHog query API and asserts that exactly
one event of the expected name landed for the local device — with the
fields the CM is supposed to populate (`source`, `success`, `duration_ms`,
`target_engine`).

Why this exists: unit and integration tests cover the route → CM →
finalizer plumbing in-process, but they can't see whether PostHog
*actually* receives the event. A regression where the SDK is
mis-configured, the network call silently fails, or the property names
drift from what dashboards expect would slip past every test in the
repo. This script is the only thing that exercises the real ingest
pipeline.

It's intentionally manual (under `tests/manual/`, not collected by
`pytest`) — it talks to a live PostHog project, takes ~1 minute, and
needs a real target with a working DB connection.

Usage:

    export POSTHOG_PERSONAL_KEY=phx_...        # required (read scope)
    export POSTHOG_PROJECT_ID=260434           # default: 260434
    export RDST_SMOKE_TARGET=mytarget          # configured rdst target
    # plus whatever password_env that target needs in the env

    python tests/manual/posthog_smoke.py

Requires the target to have:
  - a working DB connection (analyze / top run real queries)
  - a semantic-layer YAML at ~/.rdst/semantic-layer/<target>.yaml
    (scan refuses to run without one)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

POSTHOG_HOST = "https://us.posthog.com"
INGEST_GRACE_SECONDS = 15
POLL_TIMEOUT_SECONDS = 90
POLL_INTERVAL_SECONDS = 5


def _make_scan_corpus() -> Path:
    """Write a tiny SQLAlchemy snippet so scan has something to extract."""
    import tempfile

    corpus = Path(tempfile.mkdtemp(prefix="rdst_smoke_scan_"))
    (corpus / "queries.py").write_text(
        "from sqlalchemy import select\n"
        "def fetch(s):\n"
        "    return s.execute(select(User).limit(10)).scalars().all()\n"
    )
    return corpus


def _device_id() -> str:
    p = Path.home() / ".rdst" / "device_id"
    if not p.exists():
        sys.exit("ERROR: ~/.rdst/device_id missing — run rdst once first.")
    return p.read_text().strip()


def _list_events(
    api_key: str,
    project_id: str,
    event_name: str,
    distinct_id: str,
    after_iso: str,
) -> list[dict]:
    """List recent events via the REST `/events/` endpoint.

    Why this and not HogQL: the HogQL query API caches empty results
    aggressively (~60s) and `refresh="blocking"` makes each poll take
    20–60s — turning a 4-event smoke run into a 5-minute wait. The
    REST endpoint is uncached, accepts plain ISO-8601 with `Z`
    suffix (no project-tz ambiguity), and returns immediately.
    """
    qs = urllib.parse.urlencode(
        {
            "event": event_name,
            "distinct_id": distinct_id,
            "after": after_iso,
        }
    )
    req = urllib.request.Request(
        f"{POSTHOG_HOST}/api/projects/{project_id}/events/?{qs}",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    return payload.get("results") or []


def _run_cmd(args: list[str], timeout: int = 60) -> tuple[int, str]:
    """Run a CLI command, capture combined output, return (rc, output).

    Hard timeout so an interactive command (e.g. `rdst top` without
    --duration) can't wedge the smoke run forever.
    """
    print(f"  $ {' '.join(args)}", flush=True)
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL
        )
    except subprocess.TimeoutExpired as e:
        print(f"    TIMEOUT after {timeout}s (telemetry may still have fired)")
        return 124, (e.stdout or "") + (e.stderr or "")
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def _poll_for_event(
    api_key: str,
    project_id: str,
    device_id: str,
    event_name: str,
    since_iso: str,
) -> list[dict]:
    """Poll until the event appears or timeout. Returns list of property dicts.

    `since_iso` is in UTC ISO-8601 with a trailing `Z` so the REST
    endpoint matches our cutoff without timezone guesswork.
    """
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        events = _list_events(
            api_key, project_id, event_name, device_id, since_iso
        )
        if events:
            return [
                {
                    "source": e.get("properties", {}).get("source"),
                    "success": e.get("properties", {}).get("success"),
                    "duration_ms": e.get("properties", {}).get("duration_ms"),
                    "target_engine": e.get("properties", {}).get("target_engine"),
                    "error_type": e.get("properties", {}).get("error_type"),
                    "dry_run": e.get("properties", {}).get("dry_run"),
                }
                for e in events
            ]
        time.sleep(POLL_INTERVAL_SECONDS)
    return []


def _assert(cond: bool, msg: str, failures: list[str]) -> None:
    print(f"    {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        failures.append(msg)


def main() -> int:
    # Line-buffer stdout so progress streams when the script is piped
    # into `tee` / `tail` — otherwise everything sits in the buffer
    # until exit and the operator can't tell whether polling is alive.
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    api_key = os.environ.get("POSTHOG_PERSONAL_KEY")
    project_id = os.environ.get("POSTHOG_PROJECT_ID", "260434")
    target = os.environ.get("RDST_SMOKE_TARGET")
    if not api_key:
        sys.exit("ERROR: POSTHOG_PERSONAL_KEY is required.")
    if not target:
        sys.exit("ERROR: RDST_SMOKE_TARGET is required.")

    device_id = _device_id()
    repo_root = Path(__file__).resolve().parents[2]
    rdst_py = repo_root / "rdst.py"
    if not rdst_py.exists():
        sys.exit(f"ERROR: cannot locate rdst.py at {rdst_py}")

    # Disable any RDST_TESTING leak from a parent shell.
    env_mode = "TELEMETRY ENABLED" if not os.environ.get("RDST_TESTING") else "TESTING"
    print(f"PostHog smoke test")
    print(f"  device_id : {device_id}")
    print(f"  target    : {target}")
    print(f"  rdst path : {rdst_py}")
    print(f"  mode      : {env_mode}")
    print()

    since_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"Cutoff for fresh events: {since_iso}\n")

    base = ["uv", "run", "python", str(rdst_py)]

    # ---- run commands ------------------------------------------------------
    print("Running commands...")
    cmds = [
        (
            "analyze",
            base
            + [
                "analyze",
                "-q",
                "SELECT 1",
                "--target",
                target,
                "--fast",
                "--skip-warning",
            ],
        ),
        # `--historical` → command_run_sync("top") → emits top_run.
        # Without --historical, top defaults to interactive realtime mode
        # which would block forever in a script.
        (
            "top_historical",
            base + ["top", "--target", target, "--limit", "5", "--historical"],
        ),
        # `--duration N` makes realtime mode self-terminate without a TTY.
        # Realtime emits top_realtime_run (separate event from top_run).
        (
            "top_realtime",
            base + ["top", "--target", target, "--duration", "2"],
        ),
        # Scan takes the target name via `--schema`, not `--target`.
        # `--dry-run` skips the LLM-conversion phase. We point it at a
        # tiny synthetic corpus instead of the repo to keep extraction
        # fast (real source trees take many seconds).
        (
            "scan",
            base
            + [
                "scan",
                str(_make_scan_corpus()),
                "--schema",
                target,
                "--dry-run",
            ],
        ),
    ]
    for label, argv in cmds:
        rc, _ = _run_cmd(argv)
        # The CLI may legitimately return non-zero (e.g. if the user has
        # no LLM key); we only care that telemetry fired. Surface the rc
        # for the operator without aborting.
        if rc != 0:
            print(f"    note: {label} exited rc={rc} (ok if telemetry still fired)")

    print(f"\nWaiting {INGEST_GRACE_SECONDS}s for PostHog ingest...\n")
    time.sleep(INGEST_GRACE_SECONDS)

    # ---- assertions --------------------------------------------------------
    failures: list[str] = []
    expected = [
        ("analyze_run", {"source": "cli", "duration_ms": True}),
        ("top_run", {"source": "cli", "duration_ms": True}),
        ("top_realtime_run", {"source": "cli", "duration_ms": True}),
        ("scan_run", {"source": "cli", "dry_run": True}),
    ]
    for event_name, requirements in expected:
        print(f"Checking event: {event_name}")
        rows = _poll_for_event(
            api_key, project_id, device_id, event_name, since_iso
        )
        _assert(
            len(rows) == 1,
            f"  exactly 1 event landed (got {len(rows)})",
            failures,
        )
        if not rows:
            continue
        props = rows[0]
        if "source" in requirements:
            _assert(
                props["source"] == requirements["source"],
                f"  source == {requirements['source']!r} (got {props['source']!r})",
                failures,
            )
        if requirements.get("duration_ms"):
            _assert(
                props["duration_ms"] is not None
                and float(props["duration_ms"]) >= 0,
                f"  duration_ms populated (got {props['duration_ms']!r})",
                failures,
            )
        if "dry_run" in requirements:
            _assert(
                bool(props.get("dry_run")) == requirements["dry_run"],
                f"  dry_run == {requirements['dry_run']} (got {props.get('dry_run')!r})",
                failures,
            )

    print()
    if failures:
        print(f"FAILED ({len(failures)} assertion{'s' if len(failures) != 1 else ''}):")
        for f in failures:
            print(f"  - {f.strip()}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
