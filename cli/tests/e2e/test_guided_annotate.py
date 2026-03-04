"""
E2E test for `rdst schema annotate --use-llm`.

Runs against the real e2e-imdb target provisioned by conftest.py.
Uses a tmux session to interact with the guided annotator's prompts.

Skipped automatically if e2e env vars are missing (see conftest.py).
"""

import time

import pytest


@pytest.fixture(scope="module")
def schema_init(e2e_target):
    """Ensure schema is initialized before guided annotation tests."""
    from tests.e2e.conftest import _run_rdst

    result = _run_rdst("schema", "init", "--target", e2e_target, timeout=60)
    if result.returncode != 0:
        pytest.skip(f"schema init failed: {result.stderr}")
    return e2e_target


class TestGuidedAnnotate:
    """Guided annotation flow against real IMDB database."""

    def test_guided_annotate_single_table(self, schema_init, tmux):
        """Run --use-llm on a single table, accept all defaults."""
        target = schema_init

        # Start guided annotation for title_basics
        tmux.send(
            f"cd {_rdst_dir()} && uv run rdst.py schema annotate "
            f"--target {target} --use-llm title_basics"
        )

        # Wait for profiling phase
        tmux.wait_for("Profiling", timeout=30)
        tmux.wait_for("Analyzing schema with AI", timeout=60)

        # Wait for the table description prompt
        tmux.wait_for("accept/edit/skip", timeout=90)

        # Accept the description
        tmux.send("accept")

        # Wait for completion — answer any questions with defaults (Enter)
        for _ in range(10):
            time.sleep(2)
            pane = tmux.capture()
            if "Saved title_basics" in pane:
                break
            # If there's a prompt waiting, send Enter (accept default)
            if "Answer" in pane or "?" in pane.split("\n")[-3:]:
                tmux.send("")
        else:
            # Check if it finished
            pane = tmux.capture()
            assert "Saved" in pane or "Skipped" in pane, (
                f"Guided annotation didn't complete. Last output:\n{pane[-500:]}"
            )

    def test_help_shows_guided_flag(self):
        """--use-llm appears in help text."""
        from tests.e2e.conftest import _run_rdst

        result = _run_rdst("schema", "annotate", "--help")
        assert "--use-llm" in result.stdout


def _rdst_dir():
    """Return the rdst source directory."""
    from pathlib import Path
    return str(Path(__file__).resolve().parent.parent.parent)
