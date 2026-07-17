"""
Unit tests for error handling in rdst.

Covers:
- No duplicate error output for ask/schema/top/configure/query commands
- User-friendly error messages for missing modules in ask
- Correct error messages for nonexistent targets in schema show
- Query subcommands return user-friendly errors for missing required arguments
- Correct import paths in schema.py
- Schema annotate validates target before entering wizard
- Analyze validates target before showing EXPLAIN ANALYZE prompt
- Consistent "target not found" error message format across commands
- Schema delete validates before prompting
- Error output routed to stderr
- Schema export validates target existence
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest


RDST_ROOT = Path(__file__).parent.parent.parent
RDST_CLI_PATH = RDST_ROOT / "shared" / "cli" / "rdst_cli.py"
PARSER_DATA_PATH = RDST_ROOT / "shared" / "cli" / "parser_data.py"
SCHEMA_PY_PATH = RDST_ROOT / "features" / "ask" / "engine" / "ask3" / "phases" / "schema.py"
TOP_COMMAND_PATH = RDST_ROOT / "features" / "top" / "cli" / "command.py"
TOP_RENDERER_PATH = RDST_ROOT / "features" / "top" / "cli" / "renderer.py"
TOP_SERVICE_PATH = RDST_ROOT / "features" / "top" / "service.py"
CONFIGURE_COMMAND_PATH = RDST_ROOT / "features" / "configure" / "cli" / "command.py"
ANALYZE_COMMAND_PATH = RDST_ROOT / "features" / "analyze" / "cli" / "command.py"
ANALYZE_SERVICE_PATH = RDST_ROOT / "features" / "analyze" / "service.py"
QUERY_COMMAND_PATH = RDST_ROOT / "features" / "query_registry" / "cli" / "command.py"
QUERY_SERVICE_PATH = RDST_ROOT / "features" / "query_registry" / "service.py"
ASK_RENDERER_PATH = RDST_ROOT / "features" / "ask" / "engine" / "ask3" / "renderer.py"
ASK_COMMAND_PATH = RDST_ROOT / "features" / "ask" / "cli" / "command.py"

STANDARD_FORMAT = "not found. Run 'rdst configure add' to set one up."


class TestNoDuplicateErrorOutputAsk:
    """
    rdst ask error messages must not be returned in RdstResult.message when they
    have already been rendered to the console by AskRenderer.

    Background: rdst.py main() prints `result.message` to stderr when ok=False.
    If the renderer already printed the error to stdout/console, the user sees
    the same message twice. Returning an empty message avoids this.
    """

    def test_ask_error_returns_empty_message_not_raw_event_message(self):
        """
        When ask returns an error event that was already rendered,
        RdstResult.message must be empty so rdst.py does not print it again.

        The ask flow lives in `features/ask/cli/command.py:AskCommand`
        (per-feature CLI layer); the dispatcher in `rdst_cli.py` is a
        thin pass-through.
        """
        source = ASK_COMMAND_PATH.read_text()

        assert 'message=error_event.message' not in source, (
            "ask error handler returns error_event.message directly in RdstResult, "
            "causing duplicate output: renderer prints it AND rdst.py main() prints it. "
            "Return message='' instead when the renderer has already displayed the error."
        )

    def test_ask_empty_message_on_error_path(self):
        """
        Verify the ask error path returns an empty message string.
        Inspects the AST to confirm the pattern used.
        """
        source = ASK_COMMAND_PATH.read_text()
        tree = ast.parse(source)

        found_empty_on_error = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else (func.id if isinstance(func, ast.Name) else "")
                )
                if name == "RdstResult":
                    kwargs = {kw.arg: kw.value for kw in node.keywords}
                    if "ok" in kwargs and "message" in kwargs:
                        ok_val = kwargs["ok"]
                        msg_val = kwargs["message"]
                        if (
                            isinstance(ok_val, ast.Constant) and ok_val.value is False
                            and isinstance(msg_val, ast.Constant) and msg_val.value == ""
                        ):
                            found_empty_on_error = True
                            break
        assert found_empty_on_error, (
            "Could not find RdstResult(ok=False, message='') in rdst_cli.py. "
            "The ask/schema error path should return empty message to prevent duplicate output."
        )


class TestNoDuplicateErrorOutputSchema:
    """
    rdst schema error messages must not be returned in RdstResult.message when
    they have already been rendered to the console by SchemaRenderer.
    """

    def test_schema_error_event_returns_empty_message(self):
        """
        After schema service yields an error event and the renderer prints it,
        the schema() method must return RdstResult(False, '') not RdstResult(False, error_event.message).
        """
        import inspect
        from shared.cli.rdst_cli import RdstCLI

        source = inspect.getsource(RdstCLI.schema)

        assert 'RdstResult(False, error_event.message)' not in source, (
            "schema() returns RdstResult(False, error_event.message) which causes duplicate "
            "error output: SchemaRenderer prints it AND rdst.py main() prints it to stderr. "
            "Return RdstResult(False, '') instead since the renderer already displayed it."
        )


class TestNoDuplicateSchemaEndToEnd:
    """
    Integration-level test: schema() method should not propagate the error
    message when error_event was rendered.
    """

    def test_schema_show_nonexistent_target_returns_empty_message(self):
        """
        When schema show is called with a configured target that has no semantic
        layer, the returned RdstResult.message must be empty (renderer handles output).
        """
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()

        with (
            patch.object(cli, "_get_target_config", return_value={"engine": "postgresql"}),
            patch.object(cli, "_get_default_target", return_value="testtarget"),
            patch(
                "features.schema.service.SemanticLayerManager.exists",
                return_value=False,
            ),
            patch(
                "features.schema.service.SemanticLayerManager.get_summary",
                return_value={},
            ),
        ):
            result = cli.schema(subcommand="show", target="testtarget")

        assert result.ok is False
        assert result.message == "", (
            f"Expected empty message but got: {result.message!r}. "
            "Renderer already displays the error; rdst.py should not print it again."
        )


class TestAskImportErrorUserFriendly:
    """
    When ask command fails to import a required module, the user should see a
    friendly error message, not a raw ImportError traceback.
    """

    def test_ask_catches_import_error_and_returns_friendly_message(self):
        """
        If ask's module imports fail, RdstResult should contain a user-friendly
        message, not expose the raw import error.
        """
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()

        with patch(
            "shared.cli.rdst_cli.RdstCLI.ask",
            side_effect=None,
        ):
            pass  # just confirm we can import

        # Simulate ImportError in the ask imports block
        with patch.dict(
            sys.modules,
            {
                "features.ask.engine.ask3.input_handler": None,
                "features.ask.engine.ask3.renderer": None,
                "features.ask.events": None,
                "features.ask.models": None,
                "features.ask.service": None,
            },
        ):
            result = cli.ask(question="test question", target=None)

        assert result.ok is False
        raw_module_patterns = [
            "No module named",
            "ModuleNotFoundError",
            "ImportError",
        ]
        for pattern in raw_module_patterns:
            assert pattern not in result.message, (
                f"Error message exposes raw import error ({pattern!r}): {result.message!r}. "
                "Should show a user-friendly message instead."
            )
        assert len(result.message) > 0, "Error message should not be empty"

    def test_ask_import_error_caught_in_code(self):
        """
        The ask() source code must have an explicit ImportError catch to provide
        user-friendly messages when modules are unavailable.

        Source lives in `features/ask/cli/command.py:AskCommand` after
        the per-feature CLI layer extraction.
        """
        source = ASK_COMMAND_PATH.read_text()

        assert "ImportError" in source, (
            "AskCommand must catch ImportError to give users a friendly message when "
            "required modules (like features.ask.semantic_layer) are not available."
        )

    def test_ask_error_message_mentions_ask_command(self):
        """
        When ask's imports fail, the error message should mention the ask command
        so the user knows which command failed.
        """
        source = RDST_CLI_PATH.read_text()
        tree = ast.parse(source)

        found_friendly_ask_error = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else (func.id if isinstance(func, ast.Name) else "")
                )
                if name == "RdstResult":
                    args = node.args
                    if args and isinstance(args[0], ast.Constant) and args[0].value is False:
                        msg_node = (
                            args[1]
                            if len(args) > 1
                            else next(
                                (kw.value for kw in node.keywords if kw.arg == "message"),
                                None,
                            )
                        )
                        if msg_node and isinstance(msg_node, ast.Constant):
                            msg = str(msg_node.value).lower()
                            if "ask" in msg or "command" in msg or "not available" in msg:
                                found_friendly_ask_error = True
                                break
        assert found_friendly_ask_error, (
            "ask() does not have a user-friendly error message for ImportError. "
            "Should return a message mentioning 'ask' or that the command is unavailable."
        )


class TestSchemaShowCorrectTargetError:
    """
    rdst schema show --target nonexistent should say 'Target not found'
    rather than 'No semantic layer found'.
    """

    def test_schema_show_unconfigured_target_says_target_not_found(self):
        """
        When both the target config AND the semantic layer file are missing,
        schema show must report 'Target not found', not 'No semantic layer'.
        """
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()

        with (
            patch.object(cli, "_get_target_config", return_value={}),
            patch(
                "features.schema.service.SemanticLayerManager.exists",
                return_value=False,
            ),
        ):
            result = cli.schema(subcommand="show", target="nonexistent_target")

        assert result.ok is False
        msg_lower = result.message.lower()
        assert "not found" in msg_lower or "target" in msg_lower, (
            f"Expected 'target not found' style message, got: {result.message!r}"
        )
        assert "no semantic layer" not in msg_lower, (
            f"Schema show says 'no semantic layer' for completely unknown target: {result.message!r}. "
            "Should say 'Target not found' when the target is not configured."
        )

    def test_schema_show_configured_target_no_layer_says_no_semantic_layer(self):
        """
        When target IS in config but has no semantic layer, report the semantic layer error.
        """
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()

        with (
            patch.object(
                cli,
                "_get_target_config",
                return_value={"engine": "postgresql", "host": "localhost"},
            ),
            patch(
                "features.schema.service.SemanticLayerManager.exists",
                return_value=False,
            ),
        ):
            result = cli.schema(subcommand="show", target="configured_but_no_layer")

        assert result.ok is False
        if result.message:
            assert "target not found" not in result.message.lower(), (
                f"Configured target with no semantic layer should not say 'Target not found': {result.message!r}"
            )

    def test_schema_show_target_check_is_in_source(self):
        """
        The schema() source code must perform a target existence check before
        calling the schema service for the 'show' subcommand.
        """
        source = RDST_CLI_PATH.read_text()

        assert "_get_target_config" in source, (
            "schema() must call _get_target_config to check if target is configured."
        )
        assert "Target" in source and "not found" in source, (
            "schema() must include a 'Target not found' error path for schema show."
        )


class TestQuerySubcommandsUserFriendlyErrors:
    """
    Query subcommands (show, delete, edit, add, import) must return user-friendly
    error messages when required arguments are missing, NOT raw argparse errors.
    """

    def test_query_show_missing_args_returns_friendly_error(self):
        """
        rdst query show without name or hash must return a friendly error,
        not crash or show a raw argparse message.
        """
        from features.query_registry.cli.command import QueryCommand

        cmd = QueryCommand()
        result = cmd.show(name=None, query_name=None, hash=None)

        assert result.ok is False
        assert len(result.message) > 0
        assert "error: one of the arguments" not in result.message.lower(), (
            f"show returned raw argparse error: {result.message!r}"
        )
        assert "name" in result.message.lower() or "hash" in result.message.lower(), (
            f"show error must mention 'name' or 'hash': {result.message!r}"
        )

    def test_query_edit_missing_args_returns_friendly_error(self):
        """
        rdst query edit without name or hash must return a friendly error.
        """
        from features.query_registry.cli.command import QueryCommand

        cmd = QueryCommand()
        result = cmd.edit(name=None, hash=None)

        assert result.ok is False
        assert len(result.message) > 0
        assert "error: one of the arguments" not in result.message.lower(), (
            f"edit returned raw argparse error: {result.message!r}"
        )
        assert "name" in result.message.lower() or "hash" in result.message.lower(), (
            f"edit error must mention 'name' or 'hash': {result.message!r}"
        )

    def test_query_delete_missing_args_returns_friendly_error(self):
        """
        rdst query delete without name or hash must return a friendly error.
        """
        from features.query_registry.cli.command import QueryCommand

        cmd = QueryCommand()
        result = cmd.delete(name=None, hash=None)

        assert result.ok is False
        assert len(result.message) > 0
        assert "error: one of the arguments" not in result.message.lower(), (
            f"delete returned raw argparse error: {result.message!r}"
        )
        assert "name" in result.message.lower() or "hash" in result.message.lower(), (
            f"delete error must mention 'name' or 'hash': {result.message!r}"
        )

    def test_query_add_missing_name_returns_friendly_error(self):
        """
        rdst query add without a name must return a friendly error.
        """
        from features.query_registry.cli.command import QueryCommand

        cmd = QueryCommand()
        result = cmd.add(name=None)

        assert result.ok is False
        assert len(result.message) > 0
        assert "error: the following arguments" not in result.message.lower(), (
            f"add returned raw argparse error: {result.message!r}"
        )
        assert "name" in result.message.lower() or "required" in result.message.lower(), (
            f"add error must mention 'name' or 'required': {result.message!r}"
        )

    def test_query_import_missing_file_returns_friendly_error(self):
        """
        rdst query import without a file must return a friendly error.
        """
        from features.query_registry.cli.command import QueryCommand

        cmd = QueryCommand()
        result = cmd.import_queries(file=None)

        assert result.ok is False
        assert len(result.message) > 0
        assert "error: the following arguments" not in result.message.lower(), (
            f"import returned raw argparse error: {result.message!r}"
        )
        assert "file" in result.message.lower() or "required" in result.message.lower(), (
            f"import error must mention 'file' or 'required': {result.message!r}"
        )


class TestQueryParserDataRequiredFalse:
    """
    Verify that parser_data.py defines query subcommand MutuallyExclusiveGroups
    with required=False so argparse does not call sys.exit() on missing args.
    """

    def _get_query_subcommand_groups(self):
        """Parse parser_data.py and collect MutuallyExclusiveGroup required values."""
        source = PARSER_DATA_PATH.read_text()
        tree = ast.parse(source)

        required_true_groups = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else (func.id if isinstance(func, ast.Name) else "")
                )
                if name == "MutuallyExclusiveGroup":
                    for kw in node.keywords:
                        if kw.arg == "required":
                            if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                                required_true_groups.append(node)
        return required_true_groups

    def test_no_required_true_in_query_subcommand_groups(self):
        """
        No MutuallyExclusiveGroup in the query subcommand definitions should
        have required=True, since that causes raw argparse errors on missing args.
        """
        required_true_groups = self._get_query_subcommand_groups()

        assert len(required_true_groups) == 0, (
            f"Found {len(required_true_groups)} MutuallyExclusiveGroup(required=True) in parser_data.py. "
            "These cause raw argparse errors on missing args. "
            "Change to required=False and validate in the command handlers."
        )

    def test_import_file_arg_is_optional(self):
        """
        The 'import' subcommand's 'file' positional argument must have nargs='?'
        so argparse does not error when it's missing.
        """
        source = PARSER_DATA_PATH.read_text()

        import_block_start = source.find('"import"')
        assert import_block_start != -1, "Could not find 'import' subcommand in parser_data.py"

        import_block = source[import_block_start:import_block_start + 600]

        assert '"file"' in import_block, (
            "Could not find 'file' argument in import subcommand block"
        )

        file_pos = import_block.find('"file"')
        file_region = import_block[file_pos:file_pos + 200]
        assert 'nargs="?"' in file_region or "nargs='?'" in file_region, (
            f"import subcommand 'file' arg must have nargs='?' to allow missing value. "
            f"Region around 'file' in import block: {file_region!r}"
        )

    def test_add_query_name_arg_is_optional(self):
        """
        The 'add' subcommand's 'query_name' positional argument must have nargs='?'
        so argparse does not error when it's missing.
        """
        source = PARSER_DATA_PATH.read_text()

        add_block_start = source.find('"add",\n')
        assert add_block_start != -1 or '"add",' in source, (
            "Could not find 'add' subcommand in parser_data.py"
        )

        query_name_pos = source.find('ArgDef("query_name"')
        assert query_name_pos != -1, "Could not find ArgDef('query_name') in parser_data.py"

        snippet = source[query_name_pos:query_name_pos + 100]
        assert 'nargs="?"' in snippet or "nargs='?'" in snippet, (
            f"'add' subcommand 'query_name' arg must have nargs='?' to allow missing value. "
            f"Snippet: {snippet!r}"
        )


class TestQueryShowGuardInCode:
    """
    Verify the QueryCommand.show() method has an explicit guard for missing
    name and hash arguments.
    """

    def test_show_has_guard_for_missing_name_and_hash(self):
        """
        QueryCommand.show() must check for missing name/hash and return
        a friendly error, not crash or show unhelpful output.
        """
        import inspect
        from features.query_registry.cli.command import QueryCommand

        source = inspect.getsource(QueryCommand.show)

        assert "not name" in source or "if not name" in source, (
            "show() must check if name is missing"
        )
        assert "not hash" in source, (
            "show() must check if hash is missing"
        )

    def test_show_returns_rdst_result_on_missing_args(self):
        """
        QueryCommand.show() must return an RdstResult (not raise) when
        both name and hash are None.
        """
        from features.query_registry.cli.command import QueryCommand
        from shared.cli.types import RdstResult

        cmd = QueryCommand()
        result = cmd.show(name=None, query_name=None, hash=None)

        assert isinstance(result, RdstResult), (
            f"show() must return RdstResult, got {type(result)}"
        )
        assert result.ok is False


class TestParserDoesNotExitOnMissingQueryArgs:
    """
    Verify that the argparse configuration for query subcommands does NOT
    call sys.exit() when required args are missing.
    """

    def _build_query_parser(self):
        """Build the query subparser for testing."""
        import argparse
        from shared.cli.parser_data import build_subparser

        top_parser = argparse.ArgumentParser(exit_on_error=False)
        subparsers = top_parser.add_subparsers(dest="command")
        build_subparser(subparsers, "query")
        return top_parser

    def test_query_show_no_args_does_not_raise_system_exit(self):
        """
        'rdst query show' without args should not trigger argparse SystemExit.
        """
        parser = self._build_query_parser()
        try:
            parser.parse_args(["query", "show"])
        except SystemExit:
            pytest.fail(
                "argparse called sys.exit() for 'query show' with no args. "
                "Set required=False in MutuallyExclusiveGroup and validate in command handler."
            )

    def test_query_delete_no_args_does_not_raise_system_exit(self):
        """
        'rdst query delete' without args should not trigger argparse SystemExit.
        """
        parser = self._build_query_parser()
        try:
            parser.parse_args(["query", "delete"])
        except SystemExit:
            pytest.fail(
                "argparse called sys.exit() for 'query delete' with no args. "
                "Set required=False in MutuallyExclusiveGroup."
            )

    def test_query_edit_no_args_does_not_raise_system_exit(self):
        """
        'rdst query edit' without args should not trigger argparse SystemExit.
        """
        parser = self._build_query_parser()
        try:
            parser.parse_args(["query", "edit"])
        except SystemExit:
            pytest.fail(
                "argparse called sys.exit() for 'query edit' with no args. "
                "Set required=False in MutuallyExclusiveGroup."
            )

    def test_query_import_no_args_does_not_raise_system_exit(self):
        """
        'rdst query import' without a file should not trigger argparse SystemExit.
        """
        parser = self._build_query_parser()
        try:
            parser.parse_args(["query", "import"])
        except SystemExit:
            pytest.fail(
                "argparse called sys.exit() for 'query import' with no file arg. "
                "Use nargs='?' for the file positional argument."
            )


class TestAskSchemaImportPath:
    """
    features/ask/engine/ask3/phases/schema.py must import SemanticLayerManager
    from features.schema.semantic_layer.manager, not from a nonexistent
    features.semantic_layer.manager module.
    """

    def test_schema_py_uses_correct_import_path(self):
        """
        The import in schema.py must reference features.schema.semantic_layer.manager,
        not a relative path that points to a nonexistent module.
        """
        source = SCHEMA_PY_PATH.read_text()

        assert "from ....semantic_layer.manager import SemanticLayerManager" not in source, (
            "schema.py uses wrong relative import '....semantic_layer.manager'. "
            "The module lives at features/schema/semantic_layer/manager.py. "
            "Use 'from features.schema.semantic_layer.manager import SemanticLayerManager'."
        )

    def test_schema_py_has_correct_absolute_import(self):
        """
        The schema.py file must use the absolute import path for SemanticLayerManager.
        """
        source = SCHEMA_PY_PATH.read_text()

        assert "from features.schema.semantic_layer.manager import SemanticLayerManager" in source, (
            "schema.py must import SemanticLayerManager from "
            "'features.schema.semantic_layer.manager'. "
            f"Current imports in schema.py don't include this path."
        )

    def test_semantic_layer_manager_module_exists(self):
        """
        The target module must actually exist at features/schema/semantic_layer/manager.py.
        """
        manager_path = RDST_ROOT / "features" / "schema" / "semantic_layer" / "manager.py"
        assert manager_path.exists(), (
            f"Module not found: {manager_path}. "
            "The SemanticLayerManager must live at features/schema/semantic_layer/manager.py."
        )

    def test_semantic_layer_manager_importable(self):
        """
        SemanticLayerManager should be importable from the correct path.
        """
        try:
            from features.schema.semantic_layer.manager import SemanticLayerManager
        except ImportError as e:
            pytest.fail(
                f"Cannot import SemanticLayerManager from features.schema.semantic_layer.manager: {e}"
            )


class TestSchemaAnnotateTargetValidation:
    """
    rdst schema annotate --target nonexistent must fail immediately with an error,
    not enter the annotation wizard and create/save a YAML file for a target
    that doesn't exist in the config.
    """

    def test_schema_annotate_rejects_nonexistent_target(self):
        """
        When schema annotate is called with a target not in config, it must
        return an error before entering the annotation wizard.
        """
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()

        with patch.object(cli, "_get_target_config", return_value={}):
            result = cli.schema(subcommand="annotate", target="nonexistent_target")

        assert result.ok is False, (
            "schema annotate with unknown target should return ok=False"
        )
        assert result.message, (
            "schema annotate with unknown target must return an error message"
        )
        assert "nonexistent_target" in result.message or "not found" in result.message.lower(), (
            f"Error message should mention the target or 'not found': {result.message!r}"
        )

    def test_schema_annotate_validates_target_before_wizard(self):
        """
        The schema annotate path in rdst_cli.py must perform target validation
        before calling the annotation wizard.
        """
        source = RDST_CLI_PATH.read_text()

        assert "not target_config" in source or "if not target_config" in source, (
            "schema annotate flow must validate target config before entering wizard."
        )

    def test_schema_annotate_no_target_config_returns_configure_hint(self):
        """
        When target is missing, the error should hint at rdst configure add.
        """
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()

        with patch.object(cli, "_get_target_config", return_value={}):
            result = cli.schema(subcommand="annotate", target="phantom_target")

        assert result.ok is False
        msg = result.message.lower()
        assert "configure" in msg or "set one up" in msg, (
            f"Error message should hint at 'rdst configure add': {result.message!r}"
        )


class TestAnalyzeTargetValidationBeforePrompt:
    """
    rdst analyze must validate that the target exists BEFORE showing the
    EXPLAIN ANALYZE warning prompt. Showing an interactive prompt for an
    invalid target is confusing.
    """

    def test_analyze_rejects_nonexistent_target_without_prompting(self):
        """
        When analyze is called with a nonexistent target, it must return an
        error immediately without prompting for EXPLAIN ANALYZE confirmation.
        """
        from features.analyze.cli.command import AnalyzeCommand, AnalyzeInput
        from shared.query_registry import hash_sql, normalize_sql

        cmd = AnalyzeCommand()
        query = "SELECT 1"
        resolved_input = AnalyzeInput(
            sql=query,
            normalized_sql=normalize_sql(query),
            source="inline",
            hash=hash_sql(query),
        )

        with patch("shared.config.targets.TargetsConfig") as mock_cfg_cls:
            mock_cfg = MagicMock()
            mock_cfg.get.return_value = None  # target not found
            mock_cfg_cls.return_value = mock_cfg

            with patch.object(cmd, "_check_api_key_configured", return_value=None):
                result = cmd.execute_analyze(
                    resolved_input,
                    target="nonexistent_target",
                    skip_warning=False,
                )

        assert result.ok is False, (
            "analyze with nonexistent target should fail"
        )
        assert "nonexistent_target" in result.message or "not found" in result.message.lower(), (
            f"Error should mention the target: {result.message!r}"
        )

    def test_analyze_reports_missing_target_before_missing_api_key(self):
        from features.analyze.cli.command import AnalyzeCommand, AnalyzeInput
        from shared.query_registry import hash_sql, normalize_sql

        query = "SELECT 1"
        cmd = AnalyzeCommand()
        resolved_input = AnalyzeInput(
            sql=query,
            normalized_sql=normalize_sql(query),
            source="inline",
            hash=hash_sql(query),
        )

        with patch.object(
            cmd,
            "_check_api_key_configured",
            return_value="No LLM API key configured",
        ):
            result = cmd.execute_analyze(resolved_input, target=None)

        assert result.ok is False
        assert "No target specified" in result.message
        assert "rdst configure add" in result.message
        assert "LLM API key" not in result.message

    def test_analyze_target_validation_before_explain_prompt_in_source(self):
        """
        In execute_analyze(), target existence must be checked before the
        EXPLAIN ANALYZE safety warning block.
        """
        source = ANALYZE_COMMAND_PATH.read_text()

        target_validation_pos = source.find("Target '")
        explain_prompt_pos = source.find("EXPLAIN ANALYZE Warning")

        assert target_validation_pos != -1, (
            "execute_analyze() must contain a 'Target not found' validation check."
        )
        assert explain_prompt_pos != -1, (
            "execute_analyze() must contain the EXPLAIN ANALYZE warning block."
        )
        assert target_validation_pos < explain_prompt_pos, (
            "Target validation must appear BEFORE the EXPLAIN ANALYZE warning in execute_analyze(). "
            f"Target check at pos {target_validation_pos}, EXPLAIN ANALYZE warning at pos {explain_prompt_pos}."
        )

    def test_analyze_target_validation_returns_correct_message_format(self):
        """
        Target not found error in analyze must follow the standard format.
        """
        source = ANALYZE_COMMAND_PATH.read_text()
        assert "Run 'rdst configure add' to set one up." in source, (
            "analyze target validation must use standard message: "
            "\"Target 'X' not found. Run 'rdst configure add' to set one up.\""
        )


class TestNoDuplicateErrorOutputTop:
    """
    rdst top error messages must not be returned in RdstResult.message when
    they have already been rendered to the console by TopRenderer / console.print.
    """

    def test_top_error_returns_empty_message_not_raw_event_message(self):
        """
        After the renderer or console.print displays an error from a TopErrorEvent,
        the returned RdstResult must have an empty message to avoid double printing.
        """
        source = TOP_COMMAND_PATH.read_text()

        assert 'return RdstResult(False, error_event.message)' not in source, (
            "top command returns error_event.message in RdstResult after the renderer "
            "already printed it, causing duplicate output. "
            "Return RdstResult(False, '') instead."
        )

    def test_top_error_path_returns_empty_message(self):
        """
        The _run_realtime_with_service error path must return RdstResult(False, "").
        """
        source = TOP_COMMAND_PATH.read_text()

        assert 'RdstResult(False, "")' in source or "RdstResult(False, '')" in source, (
            "top command error path must return RdstResult(False, '') to avoid duplicate output."
        )


class TestNoDuplicateErrorOutputConfigureTest:
    """
    rdst configure test errors must not be printed twice: once by the renderer
    and again by rdst.py main() printing result.message.
    """

    def test_configure_test_error_returns_empty_message(self):
        """
        When configure test fails and the renderer already printed the error,
        the returned RdstResult must have an empty message.
        """
        source = CONFIGURE_COMMAND_PATH.read_text()

        assert '"test"' in source, "configure command must handle 'test' subcmd"

        assert 'subcmd in ("test"' in source or "subcmd == \"test\"" in source, (
            "configure command must have special handling for 'test' subcmd error message."
        )

    def test_configure_test_renderer_already_prints_error(self):
        """
        ConfigureRenderer already prints the failure message. The command should
        return empty message to avoid duplication.
        """
        renderer_path = RDST_ROOT / "features" / "configure" / "cli" / "renderer.py"
        source = renderer_path.read_text()

        assert "status == \"failed\"" in source or 'status == "failed"' in source, (
            "ConfigureRenderer must handle connection test failure status"
        )
        assert "MessagePanel" in source, (
            "ConfigureRenderer must use MessagePanel to display errors"
        )

    def test_configure_test_integration_returns_empty_message_on_failure(self):
        """
        End-to-end: when configure test encounters a connection failure rendered
        by ConfigureRenderer, the RdstResult.message must be empty.
        """
        from features.configure.cli.command import ConfigureCommand

        cmd = ConfigureCommand()

        from features.configure.events import ConfigureConnectionTestEvent

        failure_event = ConfigureConnectionTestEvent(
            type="connection_test",
            target_name="mydb",
            status="failed",
            message="Connection refused",
        )

        async def _mock_test_connection(target_name):
            yield failure_event

        with (
            patch("features.configure.cli.command.TargetsConfig") as mock_cfg_cls,
            patch("features.configure.cli.command.ConfigureService") as mock_svc_cls,
            patch("features.configure.cli.command.ConfigureRenderer") as mock_renderer_cls,
        ):
            mock_cfg = MagicMock()
            mock_cfg.get_default.return_value = "mydb"
            mock_cfg_cls.return_value = mock_cfg

            mock_svc = MagicMock()
            mock_svc.test_connection = _mock_test_connection
            mock_svc_cls.return_value = mock_svc

            mock_renderer = MagicMock()
            mock_renderer_cls.return_value = mock_renderer

            result = cmd.execute(subcommand="test", target="mydb")

        assert result.ok is False
        assert result.message == "", (
            f"configure test must return empty message after renderer prints error. "
            f"Got: {result.message!r}"
        )


class TestConsistentTargetNotFoundMessages:
    """
    All 'target not found' errors across rdst commands must use the same
    canonical format: "Target 'X' not found. Run 'rdst configure add' to set one up."
    """

    CANONICAL_HINT = "Run 'rdst configure add' to set one up."
    OLD_FORMATS = [
        "Run 'rdst configure' first.",
        "Run 'rdst configure' to add it.",
        "Run 'rdst configure'.",
    ]

    def test_no_old_format_target_not_found_in_rdst_cli(self):
        """
        rdst_cli.py must not use any of the old inconsistent 'target not found'
        message formats.
        """
        source = RDST_CLI_PATH.read_text()

        for old_fmt in self.OLD_FORMATS:
            assert old_fmt not in source, (
                f"rdst_cli.py still uses old format: {old_fmt!r}. "
                f"Standardize to: '{self.CANONICAL_HINT}'"
            )

    def test_canonical_message_used_consistently(self):
        """
        All schema-related target-not-found errors in rdst_cli.py must use
        the canonical hint.
        """
        source = RDST_CLI_PATH.read_text()

        count = source.count(self.CANONICAL_HINT)
        assert count >= 3, (
            f"Expected at least 3 uses of canonical hint in rdst_cli.py, found {count}. "
            f"Hint: {self.CANONICAL_HINT!r}"
        )

    def test_schema_annotate_uses_canonical_message(self):
        """
        schema annotate target validation must use the canonical error format.
        """
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()
        with patch.object(cli, "_get_target_config", return_value={}):
            result = cli.schema(subcommand="annotate", target="mydb")

        assert result.ok is False
        assert self.CANONICAL_HINT in result.message, (
            f"schema annotate error must contain canonical hint. Got: {result.message!r}"
        )

    def test_schema_init_uses_canonical_message(self):
        """
        schema init target validation must use the canonical error format.
        """
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()
        with patch.object(cli, "_get_target_config", return_value={}):
            result = cli.schema(subcommand="init", target="mydb")

        assert result.ok is False
        assert self.CANONICAL_HINT in result.message, (
            f"schema init error must contain canonical hint. Got: {result.message!r}"
        )

    def test_analyze_uses_canonical_message(self):
        """
        analyze target validation must use the canonical error format.
        """
        source = ANALYZE_COMMAND_PATH.read_text()
        assert self.CANONICAL_HINT in source, (
            f"analyze target validation must use canonical hint: {self.CANONICAL_HINT!r}"
        )

    def test_top_service_uses_standard_format(self):
        source = TOP_SERVICE_PATH.read_text()
        assert STANDARD_FORMAT in source, (
            f"features/top/service.py does not use the standard 'target not found' format. "
            f"Expected to find: \"{STANDARD_FORMAT}\""
        )

    def test_top_service_no_bare_not_found(self):
        source = TOP_SERVICE_PATH.read_text()
        assert "' not found\"\n" not in source and "' not found'" not in source, (
            "features/top/service.py still has a bare 'not found' message without the "
            "hint to run 'rdst configure add'."
        )

    def test_analyze_service_uses_standard_format(self):
        source = ANALYZE_SERVICE_PATH.read_text()
        assert STANDARD_FORMAT in source, (
            f"features/analyze/service.py does not use the standard 'target not found' format. "
            f"Expected to find: \"{STANDARD_FORMAT}\""
        )

    def test_query_run_uses_standard_format(self):
        source = QUERY_COMMAND_PATH.read_text()
        assert STANDARD_FORMAT in source, (
            f"features/query_registry/cli/command.py does not use the standard "
            f"'target not found' format. Expected: \"{STANDARD_FORMAT}\""
        )

    def test_query_run_no_in_configuration_wording(self):
        source = QUERY_COMMAND_PATH.read_text()
        assert "not found in configuration" not in source, (
            "features/query_registry/cli/command.py still uses 'not found in configuration'. "
            "Standardize to: \"not found. Run 'rdst configure add' to set one up.\""
        )

    def test_rdst_cli_schema_subcommands_use_standard_format(self):
        source = RDST_CLI_PATH.read_text()
        occurrences = source.count(STANDARD_FORMAT)
        assert occurrences >= 3, (
            f"shared/cli/rdst_cli.py should contain the standard 'target not found' format "
            f"in multiple schema subcommands (init, annotate, delete, refresh, profile, export). "
            f"Found {occurrences} occurrence(s), expected at least 3."
        )


class TestSchemaDeleteTargetValidation:
    """
    rdst schema delete --target nonexistent must fail immediately with an error,
    not prompt "Delete semantic layer for 'X'? [y/N]" before discovering
    the target/schema doesn't exist.
    """

    def test_schema_delete_rejects_nonexistent_target_before_prompt(self):
        """
        When schema delete is called with a target not in config, it must
        return an error without asking for confirmation.
        """
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()

        with (
            patch.object(cli, "_get_target_config", return_value={}),
            patch("builtins.input") as mock_input,
        ):
            result = cli.schema(subcommand="delete", target="nonexistent_target")

        assert result.ok is False, (
            "schema delete with unknown target should return ok=False"
        )
        mock_input.assert_not_called(), (
            "schema delete must NOT prompt for confirmation when target doesn't exist"
        )
        assert "nonexistent_target" in result.message or "not found" in result.message.lower(), (
            f"Error must mention the target or 'not found': {result.message!r}"
        )

    def test_schema_delete_rejects_missing_semantic_layer_before_prompt(self):
        """
        When schema delete is called for a configured target that has no
        semantic layer, it must return an error without prompting.
        """
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()

        with (
            patch.object(cli, "_get_target_config", return_value={"engine": "postgresql"}),
            patch("features.schema.service.SemanticLayerManager.exists", return_value=False),
            patch("builtins.input") as mock_input,
        ):
            result = cli.schema(subcommand="delete", target="mydb")

        assert result.ok is False
        mock_input.assert_not_called(), (
            "schema delete must NOT prompt when semantic layer doesn't exist"
        )
        msg = result.message.lower()
        assert "no semantic layer" in msg or "not found" in msg, (
            f"Error should mention missing semantic layer: {result.message!r}"
        )

    def test_schema_delete_prompts_only_when_valid(self):
        """
        When target and semantic layer both exist, schema delete SHOULD prompt.
        """
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()

        with (
            patch.object(cli, "_get_target_config", return_value={"engine": "postgresql"}),
            patch("features.schema.service.SemanticLayerManager.exists", return_value=True),
            patch("builtins.input", return_value="n"),  # user says no
        ):
            result = cli.schema(subcommand="delete", target="mydb")

        assert result.ok is False
        assert "Cancelled" in result.message, (
            f"When user declines delete prompt, should return 'Cancelled': {result.message!r}"
        )

    def test_schema_delete_validation_order_in_source(self):
        """
        In rdst_cli.py, the 'delete' subcommand block must perform target and
        schema existence checks BEFORE the confirmation prompt.
        """
        source = RDST_CLI_PATH.read_text()

        delete_block_start = source.find("elif subcommand == \"delete\":")
        assert delete_block_start != -1, "delete subcommand block not found in rdst_cli.py"

        delete_block = source[delete_block_start:delete_block_start + 800]

        target_check_pos = delete_block.find("not delete_target_config")
        layer_check_pos = delete_block.find("not service._manager.exists")
        prompt_pos = delete_block.find("input(")

        assert target_check_pos != -1, (
            "delete block must check target config existence before prompting"
        )
        assert layer_check_pos != -1, (
            "delete block must check semantic layer existence before prompting"
        )
        assert prompt_pos != -1, (
            "delete block must still have a confirmation prompt"
        )
        assert target_check_pos < prompt_pos, (
            "Target existence check must come BEFORE the confirmation prompt"
        )
        assert layer_check_pos < prompt_pos, (
            "Semantic layer existence check must come BEFORE the confirmation prompt"
        )


class TestAskErrorsRouteToStderr:
    """
    ask errors must be printed to stderr, not stdout.

    The AskRenderer._render_error method should use a stderr console rather
    than the default stdout console so error output goes to the correct stream.
    """

    def test_ask_renderer_uses_stderr_console_for_errors(self):
        source = ASK_RENDERER_PATH.read_text()
        assert "stderr=True" in source, (
            "features/ask/engine/ask3/renderer.py does not use a stderr console for errors. "
            "AskRenderer._render_error must create a console with stderr=True to route "
            "error output to stderr instead of stdout."
        )

    def test_ask_renderer_render_error_creates_stderr_console(self):
        source = ASK_RENDERER_PATH.read_text()
        tree = ast.parse(source)

        found_stderr_in_render_error = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_render_error":
                func_src = ast.unparse(node)
                if "stderr=True" in func_src or "stderr_console" in func_src:
                    found_stderr_in_render_error = True
                    break

        assert found_stderr_in_render_error, (
            "AskRenderer._render_error does not use a stderr console. "
            "It should create a console with stderr=True before printing the error."
        )


class TestTopErrorsRouteToStderr:
    """
    top errors must be printed to stderr, not stdout.

    TopRenderer._render_error and the inline error print in command.py
    should use a stderr console.
    """

    def test_top_renderer_uses_stderr_console_for_errors(self):
        source = TOP_RENDERER_PATH.read_text()
        assert "stderr=True" in source, (
            "features/top/cli/renderer.py does not use a stderr console for errors. "
            "TopRenderer._render_error must print errors to stderr, not stdout."
        )

    def test_top_renderer_render_error_uses_stderr(self):
        source = TOP_RENDERER_PATH.read_text()
        tree = ast.parse(source)

        found_stderr_in_render_error = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_render_error":
                func_src = ast.unparse(node)
                if "stderr=True" in func_src or "stderr_console" in func_src:
                    found_stderr_in_render_error = True
                    break

        assert found_stderr_in_render_error, (
            "TopRenderer._render_error does not use a stderr console. "
            "It should create a console with stderr=True before printing the error panel."
        )

    def test_top_command_early_error_uses_stderr_console(self):
        source = TOP_COMMAND_PATH.read_text()
        assert "stderr=True" in source, (
            "features/top/cli/command.py does not use a stderr console for early errors "
            "(errors before the Live display starts). Create a console with stderr=True "
            "when displaying pre-Live errors."
        )

    def test_top_command_early_error_not_stdout_console(self):
        source = TOP_COMMAND_PATH.read_text()
        assert "self._console.print(\n                    MessagePanel(error_event.message" not in source, (
            "features/top/cli/command.py still uses self._console (stdout) to print "
            "the error panel before Live starts. Switch to a stderr console."
        )


class TestSchemaExportTargetCheck:
    """
    schema export must check whether the target exists before attempting
    to read the semantic layer. A nonexistent target should produce
    "Target 'X' not found" rather than "No semantic layer found".
    """

    def test_schema_export_branch_has_target_check(self):
        source = RDST_CLI_PATH.read_text()
        tree = ast.parse(source)

        export_target_check_found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "schema":
                func_src = ast.unparse(node)
                if "export_target_config" in func_src or (
                    "subcommand == 'export'" in func_src
                    and "_get_target_config" in func_src
                ):
                    export_target_check_found = True
                    break

        assert export_target_check_found, (
            "RdstCLI.schema() has no target existence check in the 'export' branch. "
            "Add a _get_target_config() call before service.export_events() so that "
            "a nonexistent target returns 'Target not found' instead of 'No semantic layer found'."
        )

    def test_schema_export_returns_target_not_found_for_missing_target(self):
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()

        with patch.object(cli, "_get_target_config", return_value={}):
            with patch.object(cli, "_get_default_target", return_value="ghost"):
                result = cli.schema(subcommand="export", target="ghost")

        assert not result.ok
        assert "not found" in result.message.lower(), (
            f"schema export with missing target should say 'not found', got: {result.message!r}"
        )
        assert "configure add" in result.message, (
            f"schema export error should include 'rdst configure add' hint, got: {result.message!r}"
        )

    def test_schema_export_error_not_semantic_layer_message(self):
        from shared.cli.rdst_cli import RdstCLI

        cli = RdstCLI()

        with patch.object(cli, "_get_target_config", return_value={}):
            with patch.object(cli, "_get_default_target", return_value="ghost"):
                result = cli.schema(subcommand="export", target="ghost")

        assert "semantic layer" not in result.message.lower(), (
            f"schema export with missing target should not say 'No semantic layer found'. "
            f"Got: {result.message!r}"
        )


class TestQueryRunDuplicateError:
    """
    When query run encounters an error event from the service, the QueryRenderer
    already prints it to the console. RdstResult should have an empty message so
    rdst.py main() does not print it a second time.
    """

    def test_rdst_cli_query_error_returns_empty_message(self):
        source = RDST_CLI_PATH.read_text()
        assert 'RdstResult(False, error_event.message)' not in source, (
            "shared/cli/rdst_cli.py query() returns error_event.message directly in "
            "RdstResult after QueryRenderer has already printed it, causing duplicate "
            "output. Return RdstResult(False, '') instead."
        )

    def test_rdst_cli_query_error_uses_empty_string(self):
        source = RDST_CLI_PATH.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "query":
                func_src = ast.unparse(node)
                if "error_event" in func_src:
                    assert "RdstResult(False, '')" in func_src or 'message=""' in func_src, (
                        "RdstCLI.query() handles error_event but does not return an empty "
                        "message. The QueryRenderer printed the error; return '' to avoid "
                        "a duplicate print in rdst.py."
                    )
                    return

        pytest.skip("Could not find query() method with error_event handling in rdst_cli.py")

    def test_query_renderer_prints_error_to_console(self):
        renderer_path = RDST_ROOT / "features" / "query_registry" / "cli" / "renderer.py"
        source = renderer_path.read_text()
        assert "QueryErrorEvent" in source, (
            "QueryRenderer must handle QueryErrorEvent and print it to the console. "
            "If the renderer does not print the error, the empty-message fix in rdst_cli.py "
            "would silently swallow errors."
        )
        assert "console.print" in source or "_console.print" in source, (
            "QueryRenderer does not appear to call console.print for errors."
        )
