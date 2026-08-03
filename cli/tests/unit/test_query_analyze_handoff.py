"""Process handoff regression for query-to-analyze navigation."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import rdst
from shared.cli.types import RdstResult


def test_query_analyze_handoff_waits_for_child():
    cli = MagicMock()
    cli.query.return_value = RdstResult(
        True,
        "",
        data={
            "action": "analyze",
            "selected_hash": "abc123",
            "selected_target": "prod",
        },
    )
    args = argparse.Namespace(
        command="query",
        query_subcommand="list",
        names=[],
        limit=10,
        target=None,
        filter=None,
        interactive=True,
    )
    completed = SimpleNamespace(returncode=0)

    with (
        patch("subprocess.run", return_value=completed) as run,
        patch("rdst.os.execv") as execv,
    ):
        result = rdst.execute_command(cli, args)

    execv.assert_not_called()
    run.assert_called_once()
    command = run.call_args.args[0]
    assert command[-5:] == ["analyze", "--hash", "abc123", "--target", "prod"]
    assert run.call_args.kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
    assert result.ok is True
