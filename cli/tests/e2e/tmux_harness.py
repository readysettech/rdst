#!/usr/bin/env python3
"""tmux-based testing harness for interactive CLI testing.

Runs commands inside a tmux session (real PTY), allowing automated
interaction with interactive prompts across multiple invocations.

All actions return JSON to stdout: {"ok": true/false, "data": {...}}

Usage:
    python scripts/tmux_harness.py start --session t1
    python scripts/tmux_harness.py send -s t1 --text "echo hello" --enter
    python scripts/tmux_harness.py wait-for -s t1 --pattern "hello" --timeout 5
    python scripts/tmux_harness.py read -s t1 --last 5
    python scripts/tmux_harness.py send-and-wait -s t1 --text "y" --enter --pattern "\\$"
    python scripts/tmux_harness.py wait-stable -s t1 --settle 2
    python scripts/tmux_harness.py kill -s t1
    python scripts/tmux_harness.py list
"""

import argparse
import json
import re
import subprocess
import sys
import time

SESSION_PREFIX = "rdst-harness-"
DEFAULT_CWD = "/home/gautam/readyset/hacks/gautam/rdst/src"
DEFAULT_WIDTH = 200
DEFAULT_HEIGHT = 50

# Regex to strip ANSI escape sequences that tmux might leave behind
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b\(B")


def session_name(name: str) -> str:
    """Full tmux session name from short name."""
    return f"{SESSION_PREFIX}{name}"


def tmux(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a tmux command and return the result."""
    cmd = ["tmux"] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return ANSI_RE.sub("", text)


def result_ok(data: dict | None = None) -> str:
    """Return a success JSON result."""
    return json.dumps({"ok": True, "data": data or {}})


def result_err(message: str, data: dict | None = None) -> str:
    """Return an error JSON result."""
    payload = {"ok": False, "error": message}
    if data:
        payload["data"] = data
    return json.dumps(payload)


def session_exists(name: str) -> bool:
    """Check if a tmux session exists."""
    r = tmux("has-session", "-t", name, check=False)
    return r.returncode == 0


def capture_pane(sess: str) -> str:
    """Capture the full pane content, ANSI-stripped."""
    r = tmux("capture-pane", "-t", sess, "-p", check=False)
    if r.returncode != 0:
        return ""
    return strip_ansi(r.stdout)


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


HARNESS_PROMPT = "$ "
RCFILE_PATH = "/tmp/rdst_harness_bashrc"


def _write_rcfile() -> None:
    """Write a minimal bashrc for harness sessions.

    Avoids .bashrc issues (Warp hooks, starship, etc.) that corrupt
    tmux pane output.  Forwards key env vars from the parent process.
    """
    import os

    lines = [f'export PS1="{HARNESS_PROMPT}"']
    # Forward important env vars
    for key in sorted(os.environ):
        if any(key.startswith(p) for p in (
            "PATH", "HOME", "USER", "SHELL", "LANG", "LC_",
            "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
            "EDITOR", "XDG_",
        )) or key.endswith("_PASSWORD"):
            val = os.environ[key].replace("'", "'\\''")
            lines.append(f"export {key}='{val}'")

    with open(RCFILE_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")


def action_start(args: argparse.Namespace) -> str:
    """Create a new tmux session with a clean shell."""
    sess = session_name(args.session)
    if session_exists(sess):
        return result_err(f"Session '{args.session}' already exists")

    cwd = args.cwd or DEFAULT_CWD
    width = str(args.width or DEFAULT_WIDTH)
    height = str(args.height or DEFAULT_HEIGHT)

    # Write a clean rcfile that sets a simple prompt and forwards env vars
    _write_rcfile()

    r = tmux(
        "new-session",
        "-d",  # detached
        "-s", sess,
        "-x", width,
        "-y", height,
        "-c", cwd,
        f"bash --rcfile {RCFILE_PATH}",
        check=False,
    )
    if r.returncode != 0:
        return result_err(f"Failed to create session: {r.stderr.strip()}")

    # Wait for the clean prompt to appear
    marker = f"__READY_{int(time.monotonic() * 1000)}__"
    tmux("send-keys", "-t", sess, "-l", "--", f"echo {marker}", check=False)
    tmux("send-keys", "-t", sess, "Enter", check=False)

    deadline = time.monotonic() + 10
    ready = False
    while time.monotonic() < deadline:
        content = capture_pane(sess)
        for line in content.splitlines():
            if line.strip() == marker:
                ready = True
                break
        if ready:
            break
        time.sleep(0.2)

    return result_ok({
        "session": args.session,
        "full_name": sess,
        "cwd": cwd,
        "size": f"{width}x{height}",
        "ready": ready,
    })


def action_send(args: argparse.Namespace) -> str:
    """Send text or keys to the session."""
    sess = session_name(args.session)
    if not session_exists(sess):
        return result_err(f"Session '{args.session}' does not exist")

    if args.text is not None:
        # Use -l (literal) and -- to prevent special char interpretation
        r = tmux("send-keys", "-t", sess, "-l", "--", args.text, check=False)
        if r.returncode != 0:
            return result_err(f"Failed to send text: {r.stderr.strip()}")

    if args.enter:
        r = tmux("send-keys", "-t", sess, "Enter", check=False)
        if r.returncode != 0:
            return result_err(f"Failed to send Enter: {r.stderr.strip()}")

    if args.key:
        for key in args.key:
            r = tmux("send-keys", "-t", sess, key, check=False)
            if r.returncode != 0:
                return result_err(f"Failed to send key '{key}': {r.stderr.strip()}")

    return result_ok({"sent": True})


def action_read(args: argparse.Namespace) -> str:
    """Read the current pane content."""
    sess = session_name(args.session)
    if not session_exists(sess):
        return result_err(f"Session '{args.session}' does not exist")

    content = capture_pane(sess)
    lines = content.splitlines()

    # Strip trailing empty lines before applying --last
    while lines and not lines[-1].strip():
        lines.pop()

    if args.last and args.last > 0:
        lines = lines[-args.last:]

    return result_ok({
        "lines": lines,
        "line_count": len(lines),
        "content": "\n".join(lines),
    })


def action_wait_for(args: argparse.Namespace) -> str:
    """Poll until a regex pattern appears in the pane output."""
    sess = session_name(args.session)
    if not session_exists(sess):
        return result_err(f"Session '{args.session}' does not exist")

    timeout = args.timeout or 10
    interval = args.interval or 0.5
    pattern = re.compile(args.pattern)

    deadline = time.monotonic() + timeout
    last_content = ""
    while time.monotonic() < deadline:
        content = capture_pane(sess)
        last_content = content
        if pattern.search(content):
            # Return the matching lines
            lines = content.splitlines()
            matched = [l for l in lines if pattern.search(l)]
            return result_ok({
                "matched": True,
                "pattern": args.pattern,
                "matched_lines": matched[-3:],  # last 3 matching lines
            })
        time.sleep(interval)

    # Timed out — return last few lines for debugging
    lines = last_content.splitlines()
    tail = lines[-5:] if lines else []
    return result_err(
        f"Pattern '{args.pattern}' not found within {timeout}s",
        {"tail": tail},
    )


def action_wait_stable(args: argparse.Namespace) -> str:
    """Poll until pane output stops changing."""
    sess = session_name(args.session)
    if not session_exists(sess):
        return result_err(f"Session '{args.session}' does not exist")

    settle = args.settle or 2.0
    timeout = args.timeout or 30
    interval = args.interval or 0.5

    deadline = time.monotonic() + timeout
    prev = capture_pane(sess)
    stable_since = time.monotonic()

    while time.monotonic() < deadline:
        time.sleep(interval)
        current = capture_pane(sess)
        if current != prev:
            prev = current
            stable_since = time.monotonic()
        elif time.monotonic() - stable_since >= settle:
            lines = current.splitlines()
            while lines and not lines[-1].strip():
                lines.pop()
            return result_ok({
                "stable": True,
                "settled_for": round(time.monotonic() - stable_since, 1),
                "tail": lines[-5:] if lines else [],
            })

    return result_err(
        f"Output did not stabilize for {settle}s within {timeout}s timeout",
        {"tail": prev.splitlines()[-5:]},
    )


def action_send_and_wait(args: argparse.Namespace) -> str:
    """Send text/keys then wait for a pattern in NEW output only."""
    sess = session_name(args.session)
    if not session_exists(sess):
        return result_err(f"Session '{args.session}' does not exist")

    # Capture baseline before sending so we only match new content
    baseline = capture_pane(sess)
    baseline_lines = baseline.splitlines()
    # Strip trailing blanks to get meaningful line count
    while baseline_lines and not baseline_lines[-1].strip():
        baseline_lines.pop()
    baseline_count = len(baseline_lines)

    # Send phase
    if args.text is not None:
        r = tmux("send-keys", "-t", sess, "-l", "--", args.text, check=False)
        if r.returncode != 0:
            return result_err(f"Failed to send text: {r.stderr.strip()}")

    if args.enter:
        r = tmux("send-keys", "-t", sess, "Enter", check=False)
        if r.returncode != 0:
            return result_err(f"Failed to send Enter: {r.stderr.strip()}")

    if args.key:
        for key in args.key:
            r = tmux("send-keys", "-t", sess, key, check=False)
            if r.returncode != 0:
                return result_err(f"Failed to send key '{key}': {r.stderr.strip()}")

    # Small delay to let the command register
    time.sleep(0.2)

    # Wait phase — only search content AFTER the baseline
    timeout = args.timeout or 10
    interval = args.interval or 0.5
    pattern = re.compile(args.pattern)

    deadline = time.monotonic() + timeout
    last_content = ""
    while time.monotonic() < deadline:
        content = capture_pane(sess)
        last_content = content
        all_lines = content.splitlines()
        # Strip trailing blanks, then look at lines after baseline
        while all_lines and not all_lines[-1].strip():
            all_lines.pop()
        new_lines = all_lines[baseline_count:]
        new_text = "\n".join(new_lines)
        if pattern.search(new_text):
            matched = [l for l in new_lines if pattern.search(l)]
            return result_ok({
                "matched": True,
                "pattern": args.pattern,
                "matched_lines": matched[-3:],
            })
        time.sleep(interval)

    all_lines = last_content.splitlines()
    while all_lines and not all_lines[-1].strip():
        all_lines.pop()
    tail = all_lines[-5:] if all_lines else []
    return result_err(
        f"Pattern '{args.pattern}' not found within {timeout}s",
        {"tail": tail},
    )


def action_kill(args: argparse.Namespace) -> str:
    """Destroy a tmux session."""
    sess = session_name(args.session)
    if not session_exists(sess):
        return result_err(f"Session '{args.session}' does not exist")

    r = tmux("kill-session", "-t", sess, check=False)
    if r.returncode != 0:
        return result_err(f"Failed to kill session: {r.stderr.strip()}")

    return result_ok({"killed": args.session})


def action_list(_args: argparse.Namespace) -> str:
    """List all harness sessions."""
    r = tmux("list-sessions", "-F", "#{session_name}", check=False)
    if r.returncode != 0:
        # No server running or no sessions
        return result_ok({"sessions": []})

    all_sessions = r.stdout.strip().splitlines()
    harness_sessions = [
        s.removeprefix(SESSION_PREFIX)
        for s in all_sessions
        if s.startswith(SESSION_PREFIX)
    ]
    return result_ok({"sessions": harness_sessions})


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="tmux-based testing harness for interactive CLIs",
    )
    sub = parser.add_subparsers(dest="action", required=True)

    # start
    p_start = sub.add_parser("start", help="Create a tmux session")
    p_start.add_argument("--session", "-s", required=True, help="Session short name")
    p_start.add_argument("--cwd", help=f"Working directory (default: {DEFAULT_CWD})")
    p_start.add_argument("--width", type=int, help=f"Pane width (default: {DEFAULT_WIDTH})")
    p_start.add_argument("--height", type=int, help=f"Pane height (default: {DEFAULT_HEIGHT})")

    # send
    p_send = sub.add_parser("send", help="Send text or keys")
    p_send.add_argument("--session", "-s", required=True)
    p_send.add_argument("--text", "-t", help="Text to send (literal)")
    p_send.add_argument("--enter", "-e", action="store_true", help="Send Enter after text")
    p_send.add_argument("--key", "-k", action="append", help="tmux key name (e.g. C-c, Up)")

    # read
    p_read = sub.add_parser("read", help="Read pane content")
    p_read.add_argument("--session", "-s", required=True)
    p_read.add_argument("--last", "-l", type=int, help="Only last N lines")

    # wait-for
    p_wf = sub.add_parser("wait-for", help="Wait for regex pattern in output")
    p_wf.add_argument("--session", "-s", required=True)
    p_wf.add_argument("--pattern", "-p", required=True, help="Regex pattern to match")
    p_wf.add_argument("--timeout", type=float, default=10, help="Timeout in seconds (default: 10)")
    p_wf.add_argument("--interval", type=float, default=0.5, help="Poll interval (default: 0.5s)")

    # wait-stable
    p_ws = sub.add_parser("wait-stable", help="Wait until output stops changing")
    p_ws.add_argument("--session", "-s", required=True)
    p_ws.add_argument("--settle", type=float, default=2.0, help="Seconds of stability required (default: 2)")
    p_ws.add_argument("--timeout", type=float, default=30, help="Timeout in seconds (default: 30)")
    p_ws.add_argument("--interval", type=float, default=0.5, help="Poll interval (default: 0.5s)")

    # send-and-wait
    p_sw = sub.add_parser("send-and-wait", help="Send then wait for pattern")
    p_sw.add_argument("--session", "-s", required=True)
    p_sw.add_argument("--text", "-t", help="Text to send (literal)")
    p_sw.add_argument("--enter", "-e", action="store_true", help="Send Enter after text")
    p_sw.add_argument("--key", "-k", action="append", help="tmux key name")
    p_sw.add_argument("--pattern", "-p", required=True, help="Regex pattern to wait for")
    p_sw.add_argument("--timeout", type=float, default=10, help="Timeout in seconds (default: 10)")
    p_sw.add_argument("--interval", type=float, default=0.5, help="Poll interval (default: 0.5s)")

    # kill
    p_kill = sub.add_parser("kill", help="Destroy a session")
    p_kill.add_argument("--session", "-s", required=True)

    # list
    sub.add_parser("list", help="List harness sessions")

    return parser


ACTIONS = {
    "start": action_start,
    "send": action_send,
    "read": action_read,
    "wait-for": action_wait_for,
    "wait-stable": action_wait_stable,
    "send-and-wait": action_send_and_wait,
    "kill": action_kill,
    "list": action_list,
}


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    action_fn = ACTIONS.get(args.action)
    if not action_fn:
        print(result_err(f"Unknown action: {args.action}"))
        sys.exit(1)

    try:
        output = action_fn(args)
    except Exception as e:
        output = result_err(f"Unexpected error: {e}")

    print(output)

    # Exit with non-zero if not ok (useful for scripting)
    parsed = json.loads(output)
    if not parsed.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
