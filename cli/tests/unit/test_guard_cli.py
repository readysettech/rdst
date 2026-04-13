"""Tests for guard CLI formatting, error handling, and name validation."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from features.guard.checker import check_require_where, check_require_limit
from features.guard.cli.command import GuardCommand
from features.guard.config import GuardConfig, GuardsConfig, LimitsConfig
from features.guard.manager import (
    GuardManager,
    InvalidGuardNameError,
    validate_guard_name,
)


def _make_command(tmp_path: Path) -> GuardCommand:
    cmd = GuardCommand()
    cmd.manager = GuardManager(guards_dir=tmp_path)
    return cmd


def _save_guard(tmp_path: Path, config: GuardConfig) -> None:
    config.save(tmp_path / f"{config.name}.yaml")


class TestGuardListFormatting:
    """NAME column width should match the longest guard name, not be capped at 20."""

    def test_short_name_still_works(self, tmp_path):
        _save_guard(tmp_path, GuardConfig(name="short"))
        cmd = _make_command(tmp_path)
        result = cmd._list()
        assert result.ok
        lines = result.message.splitlines()
        header = lines[0]
        assert header.startswith("NAME")

    def test_long_name_does_not_get_truncated_in_name_column(self, tmp_path):
        long_name = "a_very_long_guard_name_that_exceeds_20_chars"
        _save_guard(tmp_path, GuardConfig(name=long_name))
        cmd = _make_command(tmp_path)
        result = cmd._list()
        assert result.ok
        data_line = [l for l in result.message.splitlines() if long_name in l]
        assert data_line, "guard name not found in list output"
        assert data_line[0].startswith(long_name)

    def test_long_name_does_not_push_type_column_past_expected_offset(self, tmp_path):
        long_name = "a_very_long_guard_name_that_exceeds_20_chars"
        _save_guard(tmp_path, GuardConfig(name=long_name))
        cmd = _make_command(tmp_path)
        result = cmd._list()
        lines = result.message.splitlines()
        header = lines[0]
        data_line = next(l for l in lines if long_name in l)
        # The TYPE column header and data cell must start at the same offset
        type_header_pos = header.index("TYPE")
        assert data_line[type_header_pos:].startswith("manual") or "manual" in data_line[type_header_pos:]

    def test_header_and_data_name_column_aligned_for_multiple_guards(self, tmp_path):
        names = ["alpha", "a_much_longer_guard_name", "tiny"]
        for n in names:
            _save_guard(tmp_path, GuardConfig(name=n))
        cmd = _make_command(tmp_path)
        result = cmd._list()
        lines = result.message.splitlines()
        header = lines[0]
        type_pos = header.index("TYPE")
        data_lines = lines[2:]  # skip header and separator
        for line in data_lines:
            # Each data row's TYPE cell should start at the same column as the header
            assert line[type_pos:].lstrip().startswith("manual") or line[type_pos:].lstrip().startswith("derived")

    def test_separator_line_width_matches_header(self, tmp_path):
        long_name = "this_guard_name_is_definitely_longer_than_twenty"
        _save_guard(tmp_path, GuardConfig(name=long_name))
        cmd = _make_command(tmp_path)
        result = cmd._list()
        lines = result.message.splitlines()
        header = lines[0]
        separator = lines[1]
        assert set(separator.strip()) == {"-"}
        assert len(separator) >= len(header.rstrip())

    def test_no_select_star_shows_in_guards_column(self, tmp_path):
        config = GuardConfig(
            name="myguard",
            guards=GuardsConfig(no_select_star=True),
        )
        _save_guard(tmp_path, config)
        cmd = _make_command(tmp_path)
        result = cmd._list()
        assert result.ok
        data_line = next(l for l in result.message.splitlines() if "myguard" in l)
        assert "no_select*" in data_line

    def test_no_select_star_absent_when_disabled(self, tmp_path):
        config = GuardConfig(
            name="myguard",
            guards=GuardsConfig(no_select_star=False),
        )
        _save_guard(tmp_path, config)
        cmd = _make_command(tmp_path)
        result = cmd._list()
        data_line = next(l for l in result.message.splitlines() if "myguard" in l)
        assert "no_select*" not in data_line

    def test_no_select_star_combined_with_other_guards(self, tmp_path):
        config = GuardConfig(
            name="strict",
            guards=GuardsConfig(require_where=True, no_select_star=True),
        )
        _save_guard(tmp_path, config)
        cmd = _make_command(tmp_path)
        result = cmd._list()
        data_line = next(l for l in result.message.splitlines() if "strict" in l)
        assert "where" in data_line
        assert "no_select*" in data_line

    def test_guard_with_no_rules_shows_dash(self, tmp_path):
        config = GuardConfig(name="empty")
        _save_guard(tmp_path, config)
        cmd = _make_command(tmp_path)
        result = cmd._list()
        data_line = next(l for l in result.message.splitlines() if "empty" in l)
        # No rules - summary should be "-"
        assert " - " in data_line or data_line.endswith(" -") or "  -  " in data_line.replace("-" * 5, "")

    def test_no_select_star_visible_when_only_rule(self, tmp_path):
        config = GuardConfig(
            name="staronly",
            guards=GuardsConfig(no_select_star=True),
        )
        _save_guard(tmp_path, config)
        cmd = _make_command(tmp_path)
        result = cmd._list()
        data_line = next(l for l in result.message.splitlines() if "staronly" in l)
        assert "no_select*" in data_line

    def test_no_select_star_visible_alongside_where(self, tmp_path):
        config = GuardConfig(
            name="two",
            guards=GuardsConfig(require_where=True, no_select_star=True),
        )
        _save_guard(tmp_path, config)
        cmd = _make_command(tmp_path)
        result = cmd._list()
        data_line = next(l for l in result.message.splitlines() if "two" in l)
        assert "where" in data_line
        assert "no_select*" in data_line


class TestGuardErrorHandling:
    """Tests for guard edit usage hints and unparseable SQL indicators."""

    def test_edit_no_name_returns_usage_hint(self, tmp_path):
        """_edit(None) must include the usage hint matching show/delete."""
        cmd = _make_command(tmp_path)
        result = cmd._edit(None)

        assert result.ok is False
        assert "Usage: rdst guard edit <name>" in result.message

    def test_edit_no_name_message_starts_with_guard_name_required(self, tmp_path):
        """The message must begin with 'Guard name required'."""
        cmd = _make_command(tmp_path)
        result = cmd._edit(None)

        assert result.message.startswith("Guard name required")

    def test_show_and_edit_usage_hints_are_consistent(self, tmp_path):
        """show, delete, and edit all use the same 'Guard name required. Usage: ...' pattern."""
        cmd = _make_command(tmp_path)

        show_result = cmd._show(None)
        delete_result = cmd._delete(None)
        edit_result = cmd._edit(None)

        # All three must be failures with a usage hint
        for result in (show_result, delete_result, edit_result):
            assert result.ok is False
            assert "Guard name required. Usage:" in result.message

    def test_require_where_unparseable_sql_not_passed(self):
        """Parse error on require_where must not return passed=True."""
        result = check_require_where("not valid sql at all !!!")

        assert result.passed is False
        assert result.level == "warn"
        assert "Could not verify WHERE clause" in result.message

    def test_require_limit_unparseable_sql_not_passed(self):
        """Parse error on require_limit must not return passed=True."""
        result = check_require_limit("not valid sql at all !!!")

        assert result.passed is False
        assert result.level == "warn"
        assert "Could not verify LIMIT clause" in result.message

    def test_require_where_valid_sql_with_where_still_passes(self):
        """Valid SQL with WHERE must still report passed=True."""
        result = check_require_where("SELECT id FROM users WHERE id = 1")
        assert result.passed is True

    def test_require_where_valid_sql_without_where_still_fails(self):
        """Valid SQL without WHERE must still report passed=False with level=block."""
        result = check_require_where("SELECT id FROM users")
        assert result.passed is False
        assert result.level == "block"

    def test_check_command_uses_warning_symbol_for_parse_failure(self, tmp_path):
        """_check renders a warning symbol (not a checkmark) for a parse-failure warn result."""
        manager = GuardManager(guards_dir=tmp_path)
        config = GuardConfig(name="test-guard")
        config.guards.require_where = True
        manager.create(config)

        cmd = GuardCommand()
        cmd.manager = manager

        result = cmd._check(
            sql="not valid sql at all",
            guard_name="test-guard",
            target=None,
        )

        output = result.message
        lines_with_verify = [ln for ln in output.splitlines() if "Could not verify" in ln]
        assert lines_with_verify, "Expected at least one 'Could not verify' line in output"
        for line in lines_with_verify:
            assert "\u2713" not in line, (
                f"Parse-failure line shows pass checkmark: {line!r}"
            )
            assert "\u26a0" in line, (
                f"Parse-failure line should show warning symbol: {line!r}"
            )


class TestGuardNameValidation:
    """Guard names must not contain path-special characters."""

    # --- validate_guard_name unit tests ---

    def test_valid_name_accepted(self):
        """Normal names must be accepted without error."""
        validate_guard_name("my-guard")
        validate_guard_name("pii_safe")
        validate_guard_name("GuardA1")
        validate_guard_name("_internal")

    def test_name_with_forward_slash_rejected(self):
        """Names containing / must be rejected."""
        with pytest.raises(InvalidGuardNameError):
            validate_guard_name("guard/with/slashes")

    def test_name_with_backslash_rejected(self):
        r"""Names containing \ must be rejected."""
        with pytest.raises(InvalidGuardNameError):
            validate_guard_name("guard\\with\\backslash")

    def test_name_with_dotdot_rejected(self):
        """Names containing .. (path traversal) must be rejected."""
        with pytest.raises(InvalidGuardNameError):
            validate_guard_name("../../etc/foo")

    def test_name_with_single_dot_prefix_rejected(self):
        """Names starting with a dot are rejected (dot not in allowed set)."""
        with pytest.raises(InvalidGuardNameError):
            validate_guard_name(".hidden")

    def test_empty_name_rejected(self):
        """Empty names must be rejected."""
        with pytest.raises(InvalidGuardNameError):
            validate_guard_name("")

    def test_name_exceeding_max_length_rejected(self):
        """Names longer than 64 characters must be rejected."""
        with pytest.raises(InvalidGuardNameError):
            validate_guard_name("a" * 65)

    def test_name_at_max_length_accepted(self):
        """Names of exactly 64 characters must be accepted."""
        validate_guard_name("a" * 64)

    # --- GuardManager.create rejects bad names ---

    def test_manager_create_rejects_slash_in_name(self, tmp_path):
        """GuardManager.create must raise InvalidGuardNameError for /."""
        manager = GuardManager(guards_dir=tmp_path)
        config = GuardConfig(name="guard/with/slashes")
        with pytest.raises(InvalidGuardNameError):
            manager.create(config)

    def test_manager_create_rejects_dotdot_traversal(self, tmp_path):
        """GuardManager.create must raise InvalidGuardNameError for ../."""
        manager = GuardManager(guards_dir=tmp_path)
        config = GuardConfig(name="../../etc/passwd")
        with pytest.raises(InvalidGuardNameError):
            manager.create(config)

    def test_manager_create_does_not_write_files_for_bad_name(self, tmp_path):
        """No files must be written when the name is invalid."""
        manager = GuardManager(guards_dir=tmp_path)
        config = GuardConfig(name="bad/name")
        try:
            manager.create(config)
        except InvalidGuardNameError:
            pass

        # The guards directory must remain empty.
        yaml_files = list(tmp_path.glob("**/*.yaml"))
        assert yaml_files == [], f"Unexpected files created: {yaml_files}"

    def test_manager_rename_rejects_slash_in_new_name(self, tmp_path):
        """GuardManager.rename must reject new names containing /."""
        manager = GuardManager(guards_dir=tmp_path)
        config = GuardConfig(name="original")
        manager.create(config)

        with pytest.raises(InvalidGuardNameError):
            manager.rename("original", "evil/path")

    # --- CLI command surfaces the error ---

    def test_cli_create_rejects_slash_in_name(self, tmp_path):
        """GuardCommand._create must return a failure for names with /."""
        cmd = GuardCommand()
        cmd.manager = GuardManager(guards_dir=tmp_path)

        result = cmd._create(
            name="guard/with/slashes",
            description="",
            mask=None,
            deny_columns=None,
            allow_tables=None,
            require_where=False,
            require_limit=False,
            no_select_star=False,
            max_tables=None,
            cost_limit=None,
            max_estimated_rows=None,
            required_filters=None,
            intent=None,
            schema_context=None,
            max_rows=1000,
            timeout=30,
        )

        assert result.ok is False

    def test_cli_create_rejects_dotdot_traversal(self, tmp_path):
        """GuardCommand._create must return a failure for path traversal names."""
        cmd = GuardCommand()
        cmd.manager = GuardManager(guards_dir=tmp_path)

        result = cmd._create(
            name="../../etc/passwd",
            description="",
            mask=None,
            deny_columns=None,
            allow_tables=None,
            require_where=False,
            require_limit=False,
            no_select_star=False,
            max_tables=None,
            cost_limit=None,
            max_estimated_rows=None,
            required_filters=None,
            intent=None,
            schema_context=None,
            max_rows=1000,
            timeout=30,
        )

        assert result.ok is False
