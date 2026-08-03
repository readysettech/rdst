"""
Unit tests for the _interactive_menu function in rdst.py.

Tests correct behavior of the interactive CLI menu including exit handling,
invalid input error messages, Ctrl-C / EOFError handling, menu item
capitalization consistency, command dispatch, quit variants, query delete
identifier formatting, report menu option, and input re-prompting.
"""

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


def _load_rdst_module():
    """Load rdst.py as a module without executing main()."""
    import importlib.util

    rdst_path = Path(__file__).parent.parent.parent / "rdst.py"
    spec = importlib.util.spec_from_file_location("rdst_main", rdst_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_cached_module = None


def get_rdst_module():
    global _cached_module
    if _cached_module is None:
        _cached_module = _load_rdst_module()
    return _cached_module


def get_menu_func():
    return get_rdst_module()._interactive_menu


def make_cli_mock():
    """Return a MagicMock that mimics RdstCLI."""
    from shared.cli.rdst_cli import RdstResult

    cli = MagicMock()
    cli.help.return_value = RdstResult(True, "Help output")
    cli.version.return_value = RdstResult(True, "version info")
    cli.configure.return_value = RdstResult(True, "configure output")
    cli.top.return_value = RdstResult(True, "top output")
    cli.ask.return_value = RdstResult(True, "ask output")
    cli.schema.return_value = RdstResult(True, "schema output")
    cli.init.return_value = RdstResult(True, "init output")
    return cli


def run_menu_with_input(user_input):
    """Run _interactive_menu with a given raw string as user input."""
    from shared.cli.rdst_cli import RdstResult

    module = get_rdst_module()
    menu_func = module._interactive_menu
    cli = make_cli_mock()

    with patch("sys.stdin") as mock_stdin, \
         patch("builtins.input", return_value=user_input), \
         patch("shared.ui.get_console") as mock_get_console:
        mock_stdin.isatty.return_value = True
        mock_get_console.return_value = MagicMock()
        result = menu_func(cli)

    return result, cli


def run_menu_with_inputs(inputs):
    """Run _interactive_menu with a sequence of inputs (supports re-prompt testing)."""
    module = get_rdst_module()
    menu_func = module._interactive_menu
    cli = make_cli_mock()

    input_iter = iter(inputs)

    with patch("sys.stdin") as mock_stdin, \
         patch("builtins.input", side_effect=input_iter), \
         patch("shared.ui.get_console") as mock_get_console:
        mock_stdin.isatty.return_value = True
        mock_console = MagicMock()
        mock_get_console.return_value = mock_console
        result = menu_func(cli)

    return result, cli, mock_console


EXPECTED_COMMANDS = [
    "configure", "top", "analyze", "ask", "scan", "agent", "guard",
    "init", "query", "schema", "tunnel", "fleet", "audit", "demo",
    "version", "update", "report", "help", "claude", "slack", "web", "exit",
]

PREVIOUSLY_MISSING = ["tunnel", "fleet", "audit", "demo", "claude", "slack", "web"]


def _get_menu_command_names():
    """Get the current menu command names from rdst.py source."""
    rdst_path = Path(__file__).parent.parent.parent / "rdst.py"
    source = rdst_path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_menu_command_names":
                    return ast.literal_eval(node.value)
    return []


def _get_menu_command_names_at_runtime():
    """Get the command names from the actual menu commands list at runtime."""
    captured_rows = []

    class CapturingDataTable:
        def __init__(self, columns, rows, **kwargs):
            captured_rows.extend(rows)

    module = get_rdst_module()
    menu_func = module._interactive_menu
    cli = make_cli_mock()

    with patch("sys.stdin") as mock_stdin, \
         patch("builtins.input", return_value="q"), \
         patch("shared.ui.get_console") as mock_get_console, \
         patch("shared.ui.DataTable", CapturingDataTable), \
         patch("shared.ui.SectionHeader", return_value=""):
        mock_stdin.isatty.return_value = True
        mock_get_console.return_value = MagicMock()
        menu_func(cli)

    return [name for name, _ in captured_rows]


class TestExitOption:
    """Tests that selecting the exit option returns a clean result."""

    def test_exit_returns_ok_result(self):
        """Selecting exit must return RdstResult(ok=True) without calling help."""
        from shared.cli.rdst_cli import RdstResult

        menu_func = get_menu_func()
        cli = make_cli_mock()

        exit_index = "q"

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value=exit_index), \
             patch("shared.ui.get_console") as mock_get_console:
            mock_stdin.isatty.return_value = True
            mock_console = MagicMock()
            mock_get_console.return_value = mock_console

            result = menu_func(cli)

        assert isinstance(result, RdstResult)
        assert result.ok is True
        assert "Goodbye" in result.message
        cli.help.assert_not_called()

    def test_exit_option_label_is_lowercase(self):
        """The exit command tuple must use lowercase 'exit', not 'Exit'."""
        rdst_path = Path(__file__).parent.parent.parent / "rdst.py"
        source = rdst_path.read_text()
        assert '("exit", "Exit rdst")' in source, (
            "Expected lowercase 'exit' in commands list; found title-case 'Exit' instead"
        )
        assert '("Exit", "Exit rdst")' not in source, (
            "Found title-case 'Exit' in commands list; should be lowercase 'exit'"
        )


class TestQuitInput:
    """'q', 'quit', and 'exit' inputs must exit cleanly without showing an error."""

    @pytest.mark.parametrize("quit_input", ["q", "Q", "quit", "QUIT", "exit", "EXIT"])
    def test_quit_variants_return_ok(self, quit_input):
        """Any quit keyword must return RdstResult(ok=True)."""
        from shared.cli.rdst_cli import RdstResult

        result, cli = run_menu_with_input(quit_input)

        assert isinstance(result, RdstResult), f"Expected RdstResult, got {type(result)}"
        assert result.ok is True, f"Expected ok=True for input '{quit_input}', got ok={result.ok}"

    @pytest.mark.parametrize("quit_input", ["q", "Q", "quit", "QUIT", "exit", "EXIT"])
    def test_quit_variants_say_goodbye(self, quit_input):
        """Any quit keyword must return a 'Goodbye' message."""
        result, cli = run_menu_with_input(quit_input)

        assert "Goodbye" in result.message, (
            f"Expected 'Goodbye' in result.message for input '{quit_input}', "
            f"got: '{result.message}'"
        )

    @pytest.mark.parametrize("quit_input", ["q", "Q", "quit", "QUIT", "exit", "EXIT"])
    def test_quit_does_not_call_help(self, quit_input):
        """Quit keywords must NOT fall through to cli.help()."""
        result, cli = run_menu_with_input(quit_input)

        cli.help.assert_not_called()

    def test_q_does_not_trigger_invalid_input_error(self):
        """'q' must NOT produce an 'Invalid option' error message."""
        module = get_rdst_module()
        menu_func = module._interactive_menu
        cli = make_cli_mock()
        printed_calls = []

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value="q"), \
             patch("shared.ui.get_console") as mock_get_console:
            mock_stdin.isatty.return_value = True
            mock_console = MagicMock()
            mock_console.print.side_effect = lambda *a, **kw: printed_calls.append(a)
            mock_get_console.return_value = mock_console
            menu_func(cli)

        error_messages = [
            str(args) for args in printed_calls
            if args and "Invalid option" in str(args[0])
        ]
        assert not error_messages, (
            f"'q' should NOT produce an error message; got: {error_messages}"
        )


class TestInvalidInput:
    """Tests that invalid menu input shows an error message."""

    def _run_with_input(self, user_input):
        """Run the menu with invalid input once, then 'q' to exit the loop."""
        from shared.cli.rdst_cli import RdstResult

        menu_func = get_menu_func()
        cli = make_cli_mock()
        printed_calls = []
        inputs = iter([user_input, "q"])

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", side_effect=inputs), \
             patch("shared.ui.get_console") as mock_get_console:
            mock_stdin.isatty.return_value = True
            mock_console = MagicMock()
            mock_console.print.side_effect = lambda *a, **kw: printed_calls.append(a)
            mock_get_console.return_value = mock_console

            result = menu_func(cli)

        return result, printed_calls

    def test_non_numeric_input_prints_error(self):
        """Entering a non-numeric string must print a red error message."""
        result, printed_calls = self._run_with_input("abc")

        error_messages = [
            str(args) for args in printed_calls
            if args and "Invalid option" in str(args[0])
        ]
        assert error_messages, (
            "Expected an error message to be printed for non-numeric input, "
            f"but console.print calls were: {printed_calls}"
        )

    def test_out_of_range_input_prints_error(self):
        """Entering a number outside 1-N must print a red error message."""
        result, printed_calls = self._run_with_input("999")

        error_messages = [
            str(args) for args in printed_calls
            if args and "Invalid option" in str(args[0])
        ]
        assert error_messages, (
            "Expected an error message for out-of-range input, "
            f"but console.print calls were: {printed_calls}"
        )

    def test_zero_input_prints_error(self):
        """Entering 0 (below valid range) must print a red error message."""
        result, printed_calls = self._run_with_input("0")

        error_messages = [
            str(args) for args in printed_calls
            if args and "Invalid option" in str(args[0])
        ]
        assert error_messages, (
            "Expected an error message for input 0, "
            f"but console.print calls were: {printed_calls}"
        )

    def test_negative_number_prints_error(self):
        """Entering a negative number must print a red error message."""
        result, printed_calls = self._run_with_input("-1")

        error_messages = [
            str(args) for args in printed_calls
            if args and "Invalid option" in str(args[0])
        ]
        assert error_messages, (
            "Expected an error message for negative input, "
            f"but console.print calls were: {printed_calls}"
        )

    def test_empty_string_does_not_print_error(self):
        """Empty input should default to option 1 (configure), not show an error."""
        from shared.cli.rdst_cli import RdstResult

        menu_func = get_menu_func()
        cli = make_cli_mock()
        printed_calls = []
        inputs = iter(["", "q"])  # empty then quit in case it loops

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", side_effect=inputs), \
             patch("shared.ui.get_console") as mock_get_console:
            mock_stdin.isatty.return_value = True
            mock_console = MagicMock()
            mock_console.print.side_effect = lambda *a, **kw: printed_calls.append(a)
            mock_get_console.return_value = mock_console

            cli.configure.return_value = RdstResult(True, "configured")
            result = menu_func(cli)

        error_messages = [
            str(args) for args in printed_calls
            if args and "Invalid option" in str(args[0])
        ]
        assert not error_messages, (
            f"Empty input should NOT trigger an error message; got: {error_messages}"
        )


class TestInvalidInputReprompt:
    """Invalid input must re-prompt instead of dumping help and exiting."""

    def test_invalid_string_then_valid_number_works(self):
        """After a non-numeric input, a valid number on the next prompt should work."""
        from shared.cli.rdst_cli import RdstResult

        result, cli, console = run_menu_with_inputs(["notanumber", "1"])

        assert isinstance(result, RdstResult)
        assert result.ok is True

    def test_out_of_range_then_valid_works(self):
        """After an out-of-range number, a valid choice on the next prompt should work."""
        from shared.cli.rdst_cli import RdstResult

        result, cli, console = run_menu_with_inputs(["999", "1"])

        assert isinstance(result, RdstResult)
        assert result.ok is True

    def test_invalid_input_shows_error_message(self):
        """Invalid input must print an error message before re-prompting."""
        result, cli, console = run_menu_with_inputs(["bad", "q"])

        error_calls = [
            args for args in console.print.call_args_list
            if args and "Invalid option" in str(args)
        ]
        assert error_calls, "No 'Invalid option' error message shown after invalid input"

    def test_invalid_input_does_not_call_cli_help(self):
        """Invalid input followed by 'q' must NOT call cli.help()."""
        result, cli, console = run_menu_with_inputs(["bad", "q"])

        cli.help.assert_not_called()

    def test_multiple_invalid_inputs_before_valid(self):
        """Menu should keep re-prompting through multiple consecutive invalid inputs."""
        from shared.cli.rdst_cli import RdstResult

        result, cli, console = run_menu_with_inputs(["bad", "0", "999", "1"])

        assert isinstance(result, RdstResult)
        assert result.ok is True

    def test_out_of_range_zero_then_quit(self):
        """0 is out of range; should re-prompt and accept 'q' on the next attempt."""
        from shared.cli.rdst_cli import RdstResult

        result, cli, console = run_menu_with_inputs(["0", "q"])

        assert result.ok is True
        assert "Goodbye" in result.message

    def test_invalid_then_quit_exits_cleanly(self):
        """After invalid input, typing 'q' must exit with Goodbye message."""
        from shared.cli.rdst_cli import RdstResult

        result, cli, console = run_menu_with_inputs(["notanumber", "q"])

        assert result.ok is True
        assert "Goodbye" in result.message

    def test_empty_input_defaults_to_first_command(self):
        """Empty input (just Enter) must default to option 1 without looping."""
        from shared.cli.rdst_cli import RdstResult

        result, cli, console = run_menu_with_inputs([""])

        assert isinstance(result, RdstResult)
        assert result.ok is True


class TestKeyboardInterruptHandling:
    """Tests that Ctrl-C and EOFError produce a clean exit."""

    def _run_raising(self, exc_class):
        from shared.cli.rdst_cli import RdstResult

        menu_func = get_menu_func()
        cli = make_cli_mock()
        printed_calls = []

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", side_effect=exc_class()), \
             patch("shared.ui.get_console") as mock_get_console:
            mock_stdin.isatty.return_value = True
            mock_console = MagicMock()
            mock_console.print.side_effect = lambda *a, **kw: printed_calls.append(a)
            mock_get_console.return_value = mock_console

            result = menu_func(cli)

        return result, printed_calls

    def test_keyboard_interrupt_returns_ok_result(self):
        """KeyboardInterrupt must return RdstResult(ok=True), not call cli.help()."""
        from shared.cli.rdst_cli import RdstResult

        result, _ = self._run_raising(KeyboardInterrupt)

        assert isinstance(result, RdstResult)
        assert result.ok is True

    def test_keyboard_interrupt_does_not_call_help(self):
        """KeyboardInterrupt must NOT delegate to cli.help()."""
        menu_func = get_menu_func()
        cli = make_cli_mock()

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", side_effect=KeyboardInterrupt()), \
             patch("shared.ui.get_console") as mock_get_console:
            mock_stdin.isatty.return_value = True
            mock_get_console.return_value = MagicMock()

            menu_func(cli)

        cli.help.assert_not_called()

    def test_keyboard_interrupt_prints_cancelled(self):
        """KeyboardInterrupt must print a cancellation message."""
        result, printed_calls = self._run_raising(KeyboardInterrupt)

        cancelled_messages = [
            str(args) for args in printed_calls
            if args and "Cancelled" in str(args[0])
        ]
        assert cancelled_messages, (
            "Expected a 'Cancelled' message to be printed on KeyboardInterrupt; "
            f"got console.print calls: {printed_calls}"
        )

    def test_eof_error_returns_ok_result(self):
        """EOFError (e.g. piped input exhausted) must return RdstResult(ok=True)."""
        from shared.cli.rdst_cli import RdstResult

        result, _ = self._run_raising(EOFError)

        assert isinstance(result, RdstResult)
        assert result.ok is True

    def test_eof_error_does_not_call_help(self):
        """EOFError must NOT delegate to cli.help()."""
        menu_func = get_menu_func()
        cli = make_cli_mock()

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", side_effect=EOFError()), \
             patch("shared.ui.get_console") as mock_get_console:
            mock_stdin.isatty.return_value = True
            mock_get_console.return_value = MagicMock()

            menu_func(cli)

        cli.help.assert_not_called()


class TestMenuItemCapitalization:
    """Tests that all command names in the commands list are lowercase."""

    def test_all_command_names_are_lowercase(self):
        """Every command name tuple must be entirely lowercase."""
        commands = _get_menu_command_names()
        assert commands, "Could not parse commands list from _interactive_menu"

        non_lowercase = [cmd for cmd in commands if cmd != cmd.lower()]
        assert not non_lowercase, (
            f"The following command names are not lowercase: {non_lowercase}. "
            "All entries should be lowercase for consistency."
        )

    def test_exit_handled_via_q_quit(self):
        """Exit is handled via 'q'/'quit'/'exit' input, not as a menu item."""
        commands = _get_menu_command_names()
        assert "Exit" not in commands, "Title-case 'Exit' should not be a menu item"

    def test_help_command_is_lowercase(self):
        """The help command must be lowercase 'help'."""
        commands = _get_menu_command_names()
        assert "help" in commands
        assert "Help" not in commands

    def test_known_commands_present(self):
        """Verify all expected commands are present in the menu."""
        commands = _get_menu_command_names()
        expected = {
            "configure", "top", "analyze", "ask", "scan", "agent", "guard",
            "init", "query", "schema", "tunnel", "version", "update", "report", "help",
            "fleet", "audit", "demo", "claude", "slack", "web",
        }
        missing = expected - set(commands)
        assert not missing, f"Missing expected commands: {missing}"


class TestMenuCommands:
    """Tests that all expected commands appear in the rendered menu."""

    @pytest.mark.parametrize("cmd_name", PREVIOUSLY_MISSING)
    def test_command_present_in_menu(self, cmd_name):
        """Each expected command must appear in the rendered menu."""
        names = _get_menu_command_names_at_runtime()
        assert cmd_name in names, (
            f"'{cmd_name}' is missing from the interactive menu"
        )

    def test_menu_has_22_entries_including_exit(self):
        """Menu should show 21 real commands + exit = 22 entries."""
        names = _get_menu_command_names_at_runtime()
        assert len(names) == 22, (
            f"Expected 22 menu entries (21 commands + exit), got {len(names)}: {names}"
        )

    @pytest.mark.parametrize("cmd_name", PREVIOUSLY_MISSING)
    def test_command_dispatches_successfully(self, cmd_name):
        """Selecting a command must return a successful RdstResult."""
        module = get_rdst_module()
        menu_func = module._interactive_menu
        cli = make_cli_mock()

        commands = [
            "configure", "top", "analyze", "ask", "scan", "agent", "guard",
            "init", "query", "schema", "tunnel", "fleet", "audit", "demo",
            "version", "update", "report", "help", "claude", "slack", "web", "exit",
        ]
        idx = str(commands.index(cmd_name) + 1)

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value=idx), \
             patch("shared.ui.get_console") as mock_get_console:
            mock_stdin.isatty.return_value = True
            mock_get_console.return_value = MagicMock()
            result = menu_func(cli)

        from shared.cli.rdst_cli import RdstResult
        assert isinstance(result, RdstResult), (
            f"Expected RdstResult when selecting '{cmd_name}', got {type(result)}"
        )
        assert result.ok is True, (
            f"Expected ok=True when selecting '{cmd_name}', got ok={result.ok}, message={result.message!r}"
        )


class TestMenuDescriptionsFromParserData:
    """Menu descriptions must come from parser_data.py, not be hardcoded."""

    def test_rdst_py_does_not_hardcode_top_description(self):
        """The old hardcoded 'Live view of slow queries' must not appear in _interactive_menu."""
        rdst_path = Path(__file__).parent.parent.parent / "rdst.py"
        source = rdst_path.read_text()

        start = source.find("def _interactive_menu(")
        assert start != -1
        end = source.find("\ndef ", start + 1)
        menu_body = source[start:end]

        assert '"Live view of slow queries"' not in menu_body, (
            "Hardcoded description 'Live view of slow queries' still in _interactive_menu. "
            "Descriptions should come from parser_data.COMMANDS."
        )

    def test_rdst_py_imports_parser_commands(self):
        """_interactive_menu must reference COMMANDS from parser_data."""
        rdst_path = Path(__file__).parent.parent.parent / "rdst.py"
        source = rdst_path.read_text()

        start = source.find("def _interactive_menu(")
        assert start != -1
        end = source.find("\ndef ", start + 1)
        menu_body = source[start:end]

        assert "_PARSER_COMMANDS" in menu_body or "COMMANDS" in menu_body, (
            "_interactive_menu does not reference COMMANDS from parser_data"
        )
        assert "parser_data" in menu_body, (
            "_interactive_menu does not import from parser_data"
        )

    def test_descriptions_match_parser_data(self):
        """The descriptions shown in the menu must match parser_data short_help values."""
        from shared.cli.parser_data import COMMANDS

        module = get_rdst_module()
        menu_func = module._interactive_menu
        cli = make_cli_mock()

        captured_rows = []

        class CapturingDataTable:
            def __init__(self, columns, rows, **kwargs):
                captured_rows.extend(rows)

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value="q"), \
             patch("shared.ui.get_console") as mock_get_console, \
             patch("shared.ui.DataTable", CapturingDataTable), \
             patch("shared.ui.SectionHeader", return_value=""):
            mock_stdin.isatty.return_value = True
            mock_get_console.return_value = MagicMock()
            menu_func(cli)

        menu_map = {name: desc for name, desc in captured_rows if name != "exit"}

        for cmd_name, desc in menu_map.items():
            if cmd_name in COMMANDS:
                expected = COMMANDS[cmd_name].short_help
                assert desc == expected, (
                    f"Description for '{cmd_name}' in menu is '{desc}' "
                    f"but parser_data has '{expected}'"
                )

    @pytest.mark.parametrize("cmd_name", [
        "configure", "top", "analyze", "ask", "fleet", "audit",
        "demo", "claude", "slack", "web",
    ])
    def test_specific_command_uses_parser_data_description(self, cmd_name):
        """Each command's menu description must match its parser_data short_help."""
        from shared.cli.parser_data import COMMANDS

        captured_rows = []

        class CapturingDataTable:
            def __init__(self, columns, rows, **kwargs):
                captured_rows.extend(rows)

        module = get_rdst_module()
        menu_func = module._interactive_menu
        cli = make_cli_mock()

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value="q"), \
             patch("shared.ui.get_console") as mock_get_console, \
             patch("shared.ui.DataTable", CapturingDataTable), \
             patch("shared.ui.SectionHeader", return_value=""):
            mock_stdin.isatty.return_value = True
            mock_get_console.return_value = MagicMock()
            menu_func(cli)

        menu_map = {name: desc for name, desc in captured_rows}
        assert cmd_name in menu_map, f"'{cmd_name}' not found in menu rows"
        expected = COMMANDS[cmd_name].short_help
        assert menu_map[cmd_name] == expected, (
            f"'{cmd_name}' menu description '{menu_map[cmd_name]}' != parser_data '{expected}'"
        )


class TestMenuDispatch:
    """Tests that valid selections dispatch to the correct cli method."""

    def test_version_dispatches_to_cli_version(self):
        """Selecting 'version' must call cli.version()."""
        from shared.cli.rdst_cli import RdstResult

        cli = make_cli_mock()
        cli.version.return_value = RdstResult(True, "v1.2.3")

        menu_func = get_menu_func()
        commands = _get_menu_command_names()
        idx = str(commands.index("version") + 1)

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value=idx), \
             patch("shared.ui.get_console") as mock_get_console:
            mock_stdin.isatty.return_value = True
            mock_get_console.return_value = MagicMock()
            result = menu_func(cli)

        cli.version.assert_called_once()
        assert result.ok is True

    def test_help_dispatches_to_cli_help(self):
        """Selecting 'help' must call cli.help()."""
        from shared.cli.rdst_cli import RdstResult

        cli = make_cli_mock()
        cli.help.return_value = RdstResult(True, "Help text")

        menu_func = get_menu_func()
        commands = _get_menu_command_names()
        idx = str(commands.index("help") + 1)

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value=idx), \
             patch("shared.ui.get_console") as mock_get_console:
            mock_stdin.isatty.return_value = True
            mock_get_console.return_value = MagicMock()
            result = menu_func(cli)

        cli.help.assert_called_once()

    def test_non_tty_stdin_falls_back_to_help(self):
        """When stdin is not a TTY the menu should fall back to cli.help()."""
        from shared.cli.rdst_cli import RdstResult

        menu_func = get_menu_func()
        cli = make_cli_mock()
        cli.help.return_value = RdstResult(True, "Help text")

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            result = menu_func(cli)

        cli.help.assert_called_once()


class TestQueryDeleteIdentifier:
    """The query delete identifier for a named query must not duplicate 'query'."""

    def _get_delete_identifier_source(self):
        """Return the source of the query command file."""
        cmd_path = (
            Path(__file__).parent.parent.parent
            / "features" / "query_registry" / "cli" / "command.py"
        )
        return cmd_path.read_text()

    def test_identifier_does_not_start_with_query_prefix(self):
        """identifier must not be set to f\"query '{name}'\" (causes 'Delete query query')."""
        source = self._get_delete_identifier_source()
        assert 'identifier = f"query \'{name}\'"' not in source, (
            "Found the duplicate 'query' bug: identifier = f\"query '{name}'\" — "
            "this causes 'Delete query query name?' messages"
        )

    def test_identifier_uses_bare_name_format(self):
        """identifier should be set to just f\"'{name}'\" (no leading 'query')."""
        source = self._get_delete_identifier_source()
        assert "identifier = f\"'{name}'\"" in source, (
            "Expected identifier = f\"'{name}'\" in delete handler; "
            "the fix for the duplicate 'query' word was not applied"
        )

    def test_no_double_query_in_delete_messages(self):
        """The word 'query' must not appear twice consecutively in confirm messages."""
        source = self._get_delete_identifier_source()
        assert "query query" not in source, (
            "Found 'query query' literal in source — duplicate word bug still present"
        )


class TestReportMenuOption:
    """The 'report' branch in _interactive_menu must use ReportCommand."""

    def _get_report_branch_source(self):
        """Extract the report branch block from rdst.py source."""
        rdst_path = Path(__file__).parent.parent.parent / "rdst.py"
        source = rdst_path.read_text()
        return source

    def test_report_branch_imports_report_command(self):
        """The 'report' branch must import ReportCommand from shared.cli.report_command."""
        source = self._get_report_branch_source()

        report_idx = source.find('elif cmd == "report":')
        assert report_idx != -1, "Could not find 'elif cmd == \"report\":' in rdst.py"

        block = source[report_idx: report_idx + 300]
        assert "ReportCommand" in block, (
            "The 'report' branch in _interactive_menu does not use ReportCommand. "
            "It should import and call ReportCommand().run() instead of cli.report()."
        )

    def test_report_branch_does_not_call_cli_stub(self):
        """The 'report' branch must NOT call the stub cli.report() method."""
        source = self._get_report_branch_source()

        report_idx = source.find('elif cmd == "report":')
        assert report_idx != -1

        block = source[report_idx: report_idx + 300]
        assert "cli.report(" not in block, (
            "The 'report' branch still calls cli.report() stub. "
            "It should use ReportCommand().run() instead."
        )

    def test_report_branch_calls_report_command_run(self):
        """The 'report' branch must call report_cmd.run()."""
        source = self._get_report_branch_source()

        report_idx = source.find('elif cmd == "report":')
        assert report_idx != -1

        block = source[report_idx: report_idx + 300]
        assert "report_cmd.run(" in block, (
            "The 'report' branch should call report_cmd.run() but does not. "
            f"Block found: {block!r}"
        )

    def test_report_menu_option_invokes_report_command(self):
        """Selecting 'report' in the menu invokes ReportCommand, not the stub."""
        from shared.cli.rdst_cli import RdstResult

        module = get_rdst_module()
        menu_func = module._interactive_menu
        cli = make_cli_mock()

        rdst_path = Path(__file__).parent.parent.parent / "rdst.py"
        tree = ast.parse(rdst_path.read_text())
        report_idx = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "_menu_command_names" and isinstance(node.value, ast.List):
                        names = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
                        report_idx = str(names.index("report") + 1)
        assert report_idx, "Could not find 'report' in _menu_command_names"

        mock_run = MagicMock(return_value=True)

        with patch("sys.stdin") as mock_stdin, \
             patch("builtins.input", return_value=report_idx), \
             patch("shared.ui.get_console") as mock_get_console, \
             patch("shared.cli.report_command.ReportCommand.run", mock_run):
            mock_stdin.isatty.return_value = True
            mock_get_console.return_value = MagicMock()
            result = menu_func(cli)

        assert mock_run.called, (
            "ReportCommand.run() was not called when 'report' was selected from the menu"
        )
        cli.report.assert_not_called()


class TestReportCommandCatchesSystemExit:
    """report_command.py run() must catch SystemExit as a safety net for Ctrl-C."""

    def test_run_catches_system_exit(self):
        """ReportCommand.run() must handle SystemExit and return False gracefully."""
        from shared.cli.report_command import ReportCommand

        mock_console = MagicMock()
        cmd = ReportCommand(console=mock_console)

        with patch.object(cmd, "_run_report_flow", side_effect=SystemExit(0)):
            result = cmd.run()

        assert result is False, (
            "ReportCommand.run() should return False when SystemExit is raised, "
            f"but returned: {result}"
        )

    def test_system_exit_exception_in_except_clause(self):
        """Verify SystemExit appears in the except clause of ReportCommand.run()."""
        from shared.cli.report_command import ReportCommand

        source = inspect.getsource(ReportCommand.run)
        assert "SystemExit" in source, (
            "SystemExit is not in the except clause of ReportCommand.run(). "
            "It should be: except (KeyboardInterrupt, EOFError, SystemExit):"
        )
