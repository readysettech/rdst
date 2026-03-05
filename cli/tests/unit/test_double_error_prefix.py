"""
Unit tests for double 'Error: Error:' prefix bug (rdst-2vr.6).

The ask command's error path must not add an 'Error:' prefix to RdstResult.message
because the main CLI dispatcher (rdst.py) already adds one when displaying errors.
"""

import ast
import inspect
from pathlib import Path


class TestNoDoubleErrorPrefix:
    """RdstResult messages from ask command must not start with 'Error:'."""

    def test_ask_error_result_no_error_prefix(self):
        """The ask command error handler must not prefix messages with 'Error:'."""
        # Read the ask command source and find the error_event handling block
        cli_path = Path(__file__).parent.parent.parent / "lib" / "cli" / "rdst_cli.py"
        source = cli_path.read_text()

        # Find the pattern: message=f"Error: {error_event.message}"
        # This is the bug — the message should NOT have "Error:" prefix
        assert 'message=f"Error: {error_event.message}"' not in source, (
            "ask command wraps error_event.message with 'Error: ' prefix, "
            "but rdst.py:867 also adds 'Error: ' — causing double prefix. "
            "Use the clean message instead: message=error_event.message"
        )
