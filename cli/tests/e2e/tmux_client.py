"""Python wrapper around scripts/tmux_harness.py for e2e tests.

Provides a clean API for test code to drive tmux sessions without
dealing with subprocess calls or JSON parsing directly.
"""

import json
import subprocess
import sys
from pathlib import Path


class TmuxError(Exception):
    """Raised when a tmux harness command fails."""

    def __init__(self, message: str, data: dict | None = None):
        super().__init__(message)
        self.data = data or {}


# Resolve harness script — same directory as this file.
_HARNESS = Path(__file__).resolve().parent / "tmux_harness.py"


def _run(*args: str) -> dict:
    """Run a tmux harness command and return the parsed data dict.

    Raises TmuxError if the command fails (ok=false).
    """
    cmd = [sys.executable, str(_HARNESS)] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

    # Parse JSON from stdout
    try:
        parsed = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise TmuxError(
            f"Harness returned invalid JSON: {result.stdout[:200]}",
            {"stderr": result.stderr, "returncode": result.returncode},
        ) from exc

    if not parsed.get("ok"):
        raise TmuxError(
            parsed.get("error", "Unknown harness error"),
            parsed.get("data", {}),
        )

    return parsed.get("data", {})


class TmuxClient:
    """Drives a tmux harness session for e2e testing."""

    def __init__(self, session: str):
        self.session = session

    def start(self, cwd: str | None = None) -> dict:
        """Create the tmux session."""
        args = ["start", "-s", self.session]
        if cwd:
            args += ["--cwd", cwd]
        return _run(*args)

    def kill(self) -> dict:
        """Destroy the tmux session."""
        return _run("kill", "-s", self.session)

    def send(self, text: str | None = None, enter: bool = True) -> dict:
        """Send text/keys to the session."""
        args = ["send", "-s", self.session]
        if text is not None:
            args += ["-t", text]
        if enter:
            args.append("-e")
        return _run(*args)

    def read(self, last: int | None = None) -> dict:
        """Read pane content."""
        args = ["read", "-s", self.session]
        if last is not None:
            args += ["-l", str(last)]
        return _run(*args)

    def wait_for(self, pattern: str, timeout: float = 30) -> dict:
        """Wait for a regex pattern to appear in pane output."""
        return _run(
            "wait-for", "-s", self.session,
            "-p", pattern,
            "--timeout", str(timeout),
        )

    def wait_stable(self, settle: float = 3.0, timeout: float = 60) -> dict:
        """Wait until pane output stops changing."""
        return _run(
            "wait-stable", "-s", self.session,
            "--settle", str(settle),
            "--timeout", str(timeout),
        )

    def send_and_wait(
        self,
        text: str,
        pattern: str,
        enter: bool = True,
        timeout: float = 30,
    ) -> dict:
        """Send text then wait for a pattern in new output."""
        args = [
            "send-and-wait", "-s", self.session,
            "-t", text,
            "-p", pattern,
            "--timeout", str(timeout),
        ]
        if enter:
            args.append("-e")
        return _run(*args)

    def run_rdst(self, args: str, timeout: float = 90) -> str:
        """Run an rdst command and return recent output after it completes.

        Sends ``uv run rdst.py {args}``, waits for the shell prompt to
        reappear (indicating the command finished), then reads the last
        200 lines of pane content to avoid unbounded scrollback.

        Note: the prompt pattern uses ``\\n\\$`` (newline then dollar) rather
        than ``\\$ `` because tmux capture-pane strips trailing whitespace
        from each line, turning ``$ `` into ``$``.
        """
        cmd = f"uv run rdst.py {args}"
        self.send_and_wait(cmd, r"\n\$", timeout=timeout)
        data = self.read(last=200)
        return data.get("content", "")
