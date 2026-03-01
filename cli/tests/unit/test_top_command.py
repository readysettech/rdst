"""
Unit tests for TopCommand CLI layer.

Tests the CLI-level behavior including error handling and terminal
management, separate from TopService (which has its own tests).
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from lib.services.types import TopErrorEvent


class TestTopCommandNonexistentTarget:
    """Tests for TopCommand handling of nonexistent targets."""

    def test_realtime_nonexistent_target_returns_error_result(self):
        """Bug rdst-2vr.5: top --target nonexistent should return a clean
        error result instead of crashing with NameError on 'live_started'.

        The TopService correctly yields a TopErrorEvent for invalid targets,
        but the CLI layer has a variable scoping bug where live_started is
        defined inside run_async() but referenced outside it.
        """
        from lib.cli.top import TopCommand

        async def mock_stream_realtime(input_data, options, callback):
            yield TopErrorEvent(
                type="error",
                message="Target 'nonexistent' not found",
                stage="config",
            )

        cmd = TopCommand()

        with (
            patch("lib.services.top_service.TopService") as MockService,
            patch.object(cmd, "_console", MagicMock()),
            patch.object(cmd, "_force_restore_terminal"),
        ):
            mock_service = MockService.return_value
            mock_service.stream_realtime = mock_stream_realtime

            result = cmd.execute(target="nonexistent")

        # Should return a clean error, not crash with NameError
        assert result.ok is False
        assert "not found" in result.message
        # The error should NOT contain Python internals
        assert "NameError" not in result.message
        assert "live_started" not in result.message
