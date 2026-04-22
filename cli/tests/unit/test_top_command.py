"""
Unit tests for TopCommand CLI layer.

Tests the CLI-level behavior including error handling and terminal
management, separate from TopService (which has its own tests).
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock

from features.top.events import TopCompleteEvent, TopErrorEvent


class TestTopCommandNonexistentTarget:
    """Tests for TopCommand handling of nonexistent targets."""

    def test_realtime_nonexistent_target_returns_error_result(self):
        """Bug rdst-2vr.5: top --target nonexistent should return a clean
        error result instead of crashing with NameError on 'live_started'.

        The TopService correctly yields a TopErrorEvent for invalid targets,
        but the CLI layer has a variable scoping bug where live_started is
        defined inside run_async() but referenced outside it.
        """
        from features.top.cli.command import TopCommand

        async def mock_stream_realtime(input_data, options, callback):
            yield TopErrorEvent(
                type="error",
                message="Target 'nonexistent' not found",
                stage="config",
            )

        cmd = TopCommand()

        with (
            patch("features.top.cli.command.TopService") as MockService,
            patch.object(cmd, "_console", MagicMock()),
            patch.object(cmd, "_force_restore_terminal"),
        ):
            mock_service = MockService.return_value
            mock_service.stream_realtime = mock_stream_realtime

            result = cmd.execute(target="nonexistent")

        # Should return a clean error, not crash with NameError
        assert result.ok is False
        # The renderer already printed the error to the console (MessagePanel via console.print),
        # so result.message must be empty to avoid duplicate output in rdst.py main().
        assert result.message == "", (
            f"top command must return empty message after renderer prints error. Got: {result.message!r}"
        )
        # The error should NOT contain Python internals
        assert "NameError" not in result.message
        assert "live_started" not in result.message


class TestSortFieldLabels:
    """Tests for SORT_FIELD_LABELS correctness."""

    def test_sort_field_labels_does_not_contain_max(self):
        """Bug fix: SORT_FIELD_LABELS should not contain 'max' as a key.

        Previously 'max' was accidentally included but is not a valid
        sort field.
        """
        from features.top.models import SORT_FIELD_LABELS

        assert "max" not in SORT_FIELD_LABELS

    def test_sort_field_labels_covers_all_valid_sort_choices(self):
        """All valid --sort choices from the CLI parser must have labels.

        If a new sort choice is added to parser_data.py but not to
        SORT_FIELD_LABELS, the header/title rendering will fail with
        a KeyError.
        """
        from features.top.models import SORT_FIELD_LABELS
        from shared.cli.parser_data import COMMANDS

        top_def = COMMANDS["top"]
        sort_arg = None
        for arg in top_def.args:
            if arg.name == "--sort":
                sort_arg = arg
                break

        assert sort_arg is not None, "Could not find --sort arg in top command definition"
        assert sort_arg.choices is not None, "--sort arg has no choices defined"

        for choice in sort_arg.choices:
            assert choice in SORT_FIELD_LABELS, (
                f"Sort choice '{choice}' is missing from SORT_FIELD_LABELS"
            )


class TestTopCommandSourceValidation:
    """Tests for source/engine validation in TopCommand.execute()."""

    def test_mysql_source_rejected_on_postgresql_target(self):
        """Bug fix: running top --source slowlog on a PostgreSQL target
        should return an error immediately, not proceed and fail later.
        """
        from features.top.cli.command import TopCommand

        mock_config = {
            "engine": "postgresql",
            "host": "localhost",
            "port": 5432,
            "user": "test",
            "database": "testdb",
        }

        cmd = TopCommand()

        with (
            patch("features.top.cli.command.TopService"),
            patch.object(cmd, "_console", MagicMock()),
            patch.object(cmd, "_force_restore_terminal"),
            patch(
                "shared.config.targets.TargetsConfig.load",
                return_value=None,
            ),
            patch(
                "shared.config.targets.TargetsConfig.get_default",
                return_value="pg-target",
            ),
            patch(
                "shared.config.targets.TargetsConfig.get",
                return_value=mock_config,
            ),
        ):
            result = cmd.execute(target="pg-target", source="slowlog")

        assert result.ok is False
        assert "slowlog" in result.message
        assert "MySQL-only" in result.message


class TestTopCommandSnapshotErrorPropagation:
    """Tests for error propagation in snapshot realtime mode."""

    def test_nonexistent_target_error_uses_ok_attribute(self):
        """Bug fix: _run_snapshot_realtime checks async_result.ok (not
        .success) to detect errors returned from run_async().

        The async generator returns RdstResult(False, ...) when a
        TopErrorEvent is received. The caller must check .ok to
        propagate the error.
        """
        from features.top.cli.command import TopCommand
        from shared.cli.types import RdstResult

        async def mock_stream_realtime(input_data, options, duration):
            yield TopErrorEvent(
                type="error",
                message="Target 'ghost' not found",
                stage="config",
            )

        cmd = TopCommand()

        with (
            patch("features.top.cli.command.TopService") as MockService,
            patch.object(cmd, "_console", MagicMock()),
            patch.object(cmd, "_force_restore_terminal"),
        ):
            mock_service = MockService.return_value
            mock_service.stream_realtime = mock_stream_realtime

            result = cmd.execute(target="ghost", duration=10)

        assert isinstance(result, RdstResult)
        assert result.ok is False
        assert "ghost" in result.message


class TestTopCommandSortPassedToSnapshotMode:
    """Tests that --sort is correctly passed to TopOptions in snapshot mode."""

    def test_sort_freq_passed_to_top_options_in_snapshot_mode(self):
        """Bug fix: when execute() is called with sort='freq' and duration=5
        (snapshot mode), the TopOptions passed to the service must have
        sort='freq', not the default 'total_time'.
        """
        from features.top.cli.command import TopCommand
        from features.top.models import TopOptions

        captured_options = {}

        async def mock_stream_realtime(input_data, options, duration):
            captured_options["sort"] = options.sort
            yield TopCompleteEvent(
                type="complete",
                queries=[],
                source="activity",
                target_name="test-target",
                db_engine="postgresql",
            )

        cmd = TopCommand()

        with (
            patch("features.top.cli.command.TopService") as MockService,
            patch.object(cmd, "_console", MagicMock()),
            patch.object(cmd, "_force_restore_terminal"),
        ):
            mock_service = MockService.return_value
            mock_service.stream_realtime = mock_stream_realtime

            cmd.execute(target="test-target", sort="freq", duration=5)

        assert captured_options["sort"] == "freq", (
            f"Expected sort='freq' in TopOptions, got sort='{captured_options.get('sort')}'"
        )


class TestTopCommandPgStatSnapshotWarning:
    """Tests that pg_stat source in snapshot mode shows a warning."""

    def test_pg_stat_snapshot_mode_shows_warning(self):
        """Bug fix: when --source pg_stat is used with --duration (snapshot mode),
        a NoticePanel warning should be rendered to stderr because pg_stat is
        not available in snapshot mode (it uses pg_stat_activity instead).
        """
        from features.top.cli.command import TopCommand

        async def mock_stream_realtime(input_data, options, duration):
            yield TopCompleteEvent(
                type="complete",
                queries=[],
                source="activity",
                target_name="pg-target",
                db_engine="postgresql",
            )

        cmd = TopCommand()

        with (
            patch("features.top.cli.command.TopService") as MockService,
            patch.object(cmd, "_console", MagicMock()),
            patch.object(cmd, "_force_restore_terminal"),
            patch("features.top.cli.command.create_console") as mock_create_console,
        ):
            mock_stderr_console = MagicMock()
            mock_create_console.return_value = mock_stderr_console

            mock_service = MockService.return_value
            mock_service.stream_realtime = mock_stream_realtime

            cmd.execute(target="pg-target", source="pg_stat", duration=10)

        # create_console(stderr=True) should have been called to render a warning
        mock_create_console.assert_called_with(stderr=True)
        # The stderr console should have had .print() called with the notice
        assert mock_stderr_console.print.called, (
            "Expected stderr console to print a NoticePanel warning for pg_stat in snapshot mode"
        )

    def test_pg_stat_historical_mode_no_warning(self):
        """pg_stat with --historical should NOT show a warning — it's the correct mode."""
        from features.top.cli.command import TopCommand

        async def mock_get_top_queries(input_data, options):
            from features.top.events import TopCompleteEvent

            yield TopCompleteEvent(
                type="complete",
                queries=[],
                source="pg_stat",
                target_name="pg-target",
                db_engine="postgresql",
            )

        cmd = TopCommand()
        stderr_prints = []

        with (
            patch("features.top.cli.command.TopService") as MockService,
            patch.object(cmd, "_console", MagicMock()),
            patch.object(cmd, "_force_restore_terminal"),
            patch("features.top.cli.command.create_console") as mock_create_console,
        ):
            mock_stderr_console = MagicMock()
            mock_stderr_console.print = lambda *a, **kw: stderr_prints.extend(a)
            mock_create_console.return_value = mock_stderr_console

            mock_service = MockService.return_value
            mock_service.get_top_queries = mock_get_top_queries

            cmd.execute(
                target="pg-target", source="pg_stat", historical=True
            )

        from shared.ui import NoticePanel

        notice_panels = [
            p for p in stderr_prints if isinstance(p, NoticePanel)
        ]
        assert len(notice_panels) == 0, (
            "No warning should be shown for pg_stat in historical mode"
        )
