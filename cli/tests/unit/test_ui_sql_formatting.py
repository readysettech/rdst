"""Tests for shared SQL formatting in the UI layer."""

import sys
from pathlib import Path

from sqlglot.errors import ParseError
from pygments.token import Name

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.ui.console import create_console
import shared.ui.components as ui_components
import shared.ui.theme as ui_theme


def _capture(renderable, width: int = 100) -> str:
    console = create_console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def _clear_theme_env(monkeypatch):
    for name in ("RDST_THEME", "CLITHEME", "COLORFGBG"):
        monkeypatch.delenv(name, raising=False)


class _FakeStdout:
    def __init__(self, is_tty: bool):
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


class TestFormatSqlForDisplay:
    def test_empty_sql_passthrough(self):
        assert ui_components.format_sql_for_display("") == ""

    def test_long_one_line_sql_reformatted(self):
        sql = (
            "select id, name from users where status = 'active' "
            "order by created_at desc limit 10"
        )

        formatted = ui_components.format_sql_for_display(sql)

        assert "\n" in formatted
        assert "SELECT" in formatted
        assert "FROM" in formatted
        assert "ORDER BY" in formatted
        assert "LIMIT 10" in formatted

    def test_parse_failure_fallback_adds_line_breaks(self, monkeypatch):
        def _raise_parse_error(*args, **kwargs):
            raise ParseError("bad sql")

        monkeypatch.setattr(ui_components.sqlglot, "parse_one", _raise_parse_error)

        sql = "select id from users where status = 'active' order by id desc limit 5"
        formatted = ui_components.format_sql_for_display(sql)

        assert "\nFROM" in formatted
        assert "\nWHERE" in formatted
        assert "\nORDER BY" in formatted
        assert formatted.startswith("SELECT")


class TestQueryPanel:
    def test_parameter_tokens_have_distinct_theme_style(self):
        variable_style = ui_components._RDST_SQL_THEME.get_style_for_token(Name.Variable)
        name_style = ui_components._RDST_SQL_THEME.get_style_for_token(Name)

        assert variable_style.color is not None
        assert variable_style.color != name_style.color

    def test_query_panel_renders_formatted_sql_without_box_borders(self):
        sql = (
            "select id, name from users where status = 'active' "
            "order by created_at desc limit 10"
        )

        output = _capture(ui_components.QueryPanel(sql, title="Query"))
        lines = [line for line in output.splitlines() if line.strip()]

        assert "Query" in output
        assert "FROM users" in output
        assert "LIMIT 10" in output
        assert output.count("\n") >= 4
        assert not lines[0].startswith("  ")
        assert lines[1].startswith("  ")
        assert "│" not in output
        assert "╭" not in output
        assert "╰" not in output

    def test_render_sql_block_keeps_placeholder_text_without_box_borders(self):
        sql = (
            "select coalesce(lfc_value, :p1) as count from neon.neon_lfc_stats "
            "where lfc_key = :p2 and other_key = $1 and enabled = ?"
        )

        output = _capture(
            ui_components.render_sql_block(
                sql,
                title="SQL Pattern",
            )
        )
        lines = [line for line in output.splitlines() if line.strip()]

        assert "SQL Pattern" in output
        assert ":p1" in output
        assert ":p2" in output
        assert "$1" in output
        assert "?" in output
        assert not lines[0].startswith("  ")
        assert lines[1].startswith("  ")
        assert "│" not in output
        assert "╭" not in output
        assert "╰" not in output


class TestUiThemeSelection:
    def test_detect_theme_rdtheme_beats_other_hints(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        monkeypatch.setenv("RDST_THEME", "light")
        monkeypatch.setenv("CLITHEME", "dark")
        monkeypatch.setenv("COLORFGBG", "0;0")
        monkeypatch.setattr(ui_theme, "_detect_theme_from_terminal", lambda: "dark")

        assert ui_theme._detect_theme() == "light"

    def test_detect_theme_uses_clitheme_when_rdst_theme_is_unset(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        monkeypatch.setenv("CLITHEME", "light")
        monkeypatch.setenv("COLORFGBG", "0;0")
        monkeypatch.setattr(ui_theme, "_detect_theme_from_terminal", lambda: "dark")

        assert ui_theme._detect_theme() == "light"

    def test_detect_theme_invalid_rdst_theme_falls_through(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        monkeypatch.setenv("RDST_THEME", "sepia")
        monkeypatch.setenv("CLITHEME", "light")
        monkeypatch.setattr(ui_theme, "_detect_theme_from_terminal", lambda: None)

        assert ui_theme._detect_theme() == "light"

    def test_detect_theme_uses_terminal_probe_before_colorfgbg(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        monkeypatch.setenv("COLORFGBG", "0;0")
        monkeypatch.setattr(ui_theme, "_detect_theme_from_terminal", lambda: "light")

        assert ui_theme._detect_theme() == "light"

    def test_detect_theme_falls_back_to_colorfgbg_when_probe_fails(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        monkeypatch.setenv("RDST_THEME", "auto")
        monkeypatch.setenv("COLORFGBG", "0;15")
        monkeypatch.setattr(ui_theme, "_detect_theme_from_terminal", lambda: None)

        assert ui_theme._detect_theme() == "light"

    def test_detect_theme_defaults_to_dark_without_hints(self, monkeypatch):
        _clear_theme_env(monkeypatch)
        monkeypatch.setattr(ui_theme, "_detect_theme_from_terminal", lambda: None)

        assert ui_theme._detect_theme() == "dark"

    def test_detect_theme_from_osc_11_response_detects_dark_background(self):
        response = "\x1b]11;rgb:0000/0000/0000\x1b\\"

        assert ui_theme._detect_theme_from_osc_11_response(response) == "dark"

    def test_detect_theme_from_osc_11_response_detects_light_background(self):
        response = "\x1b]11;rgb:ffff/ffff/ffff\x07"

        assert ui_theme._detect_theme_from_osc_11_response(response) == "light"

    def test_detect_theme_from_terminal_skips_non_tty_stdout(self, monkeypatch):
        monkeypatch.setattr(ui_theme.sys, "stdout", _FakeStdout(is_tty=False))
        monkeypatch.setattr(
            ui_theme,
            "open",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not open /dev/tty")),
            raising=False,
        )

        assert ui_theme._detect_theme_from_terminal() is None

    def test_sql_style_class_matches_theme_name(self):
        assert ui_components._sql_style_class("dark") is ui_components.RdstSqlDarkStyle
        assert ui_components._sql_style_class("light") is ui_components.RdstSqlLightStyle
