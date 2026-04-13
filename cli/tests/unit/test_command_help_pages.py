"""
Unit tests for CLI command help pages.

Verifies that configure, agent, and guard subcommands each have their own
distinct help page (fix for the flat-parser bug), and that query/schema
continue to work correctly (regression).

Bug context: configure, agent, and guard previously used a flat argparse
parser with 'subcommand' as a positional argument and all flags on the single
parser.  This caused `rdst configure add --help` to show the same output as
`rdst configure --help`.  They were migrated to add_subparsers() following
the pattern used by query and schema.
"""

from __future__ import annotations

import argparse
import io

import pytest

from shared.cli.parser_data import COMMANDS, build_all_subparsers


def _build_parsers() -> dict[str, argparse.ArgumentParser]:
    """Build all subparsers and return the mapping {name: parser}."""
    root = argparse.ArgumentParser()
    subparsers = root.add_subparsers()
    return build_all_subparsers(subparsers)


def _get_help(parser: argparse.ArgumentParser) -> str:
    """Capture the --help output of a parser as a string."""
    buf = io.StringIO()
    try:
        parser.print_help(file=buf)
    except SystemExit:
        pass
    return buf.getvalue()


def _get_subparser(
    parsers: dict[str, argparse.ArgumentParser],
    command: str,
    subcommand: str,
) -> argparse.ArgumentParser:
    """Return the argparse parser for a specific subcommand of a top-level command."""
    parent = parsers[command]
    for action in parent._subparsers._group_actions:
        if isinstance(action, argparse._SubParsersAction):
            sub = action.choices.get(subcommand)
            if sub is not None:
                return sub
    raise KeyError(f"Subcommand '{subcommand}' not found under '{command}'")


def _get_option_strings(parser: argparse.ArgumentParser) -> set[str]:
    """Return all option strings registered on a parser."""
    result: set[str] = set()
    for action in parser._actions:
        result.update(action.option_strings)
    return result


class TestConfigureHelpPages:
    """configure subcommands must each have a distinct, subcommand-specific help page."""

    SUBCOMMANDS = ["add", "list", "edit", "remove", "default", "test"]

    def setup_method(self):
        self.parsers = _build_parsers()
        self.parent_help = _get_help(self.parsers["configure"])

    def test_configure_parent_help_lists_subcommands(self):
        """rdst configure --help must list all available subcommands."""
        for subcmd in self.SUBCOMMANDS:
            assert subcmd in self.parent_help, (
                f"configure --help does not mention subcommand '{subcmd}'"
            )

    def test_configure_parent_has_subparsers(self):
        """configure parser must use add_subparsers (not flat positional argument)."""
        parent = self.parsers["configure"]
        found = False
        for action in parent._subparsers._group_actions:
            if isinstance(action, argparse._SubParsersAction):
                found = True
                break
        assert found, "configure parser has no subparsers action — still using flat parser"

    @pytest.mark.parametrize("subcommand", SUBCOMMANDS)
    def test_subcommand_help_differs_from_parent(self, subcommand):
        """Each subcommand's help text must differ from the parent configure help."""
        sub_parser = _get_subparser(self.parsers, "configure", subcommand)
        sub_help = _get_help(sub_parser)
        assert sub_help != self.parent_help, (
            f"configure {subcommand} --help is identical to configure --help"
        )

    @pytest.mark.parametrize("subcommand", SUBCOMMANDS)
    def test_subcommand_help_mentions_subcommand_name(self, subcommand):
        """Each subcommand's help must reference its own name."""
        sub_parser = _get_subparser(self.parsers, "configure", subcommand)
        sub_help = _get_help(sub_parser)
        assert subcommand in sub_help, (
            f"configure {subcommand} --help does not mention '{subcommand}'"
        )

    def test_add_has_connection_flags(self):
        """configure add must expose connection-related flags."""
        sub_parser = _get_subparser(self.parsers, "configure", "add")
        opts = _get_option_strings(sub_parser)
        for flag in ("--host", "--user", "--database", "--engine", "--password-env"):
            assert flag in opts, (
                f"configure add --help is missing '{flag}'"
            )

    def test_add_does_not_have_confirm_flag(self):
        """configure add must NOT have --confirm (which is remove-only)."""
        sub_parser = _get_subparser(self.parsers, "configure", "add")
        opts = _get_option_strings(sub_parser)
        assert "--confirm" not in opts, (
            "configure add --help incorrectly exposes --confirm (remove-only flag)"
        )

    def test_remove_has_confirm_flag(self):
        """configure remove must have --confirm."""
        sub_parser = _get_subparser(self.parsers, "configure", "remove")
        opts = _get_option_strings(sub_parser)
        assert "--confirm" in opts, (
            "configure remove --help is missing --confirm flag"
        )

    def test_remove_does_not_have_connection_flags(self):
        """configure remove must NOT expose add-only connection flags."""
        sub_parser = _get_subparser(self.parsers, "configure", "remove")
        opts = _get_option_strings(sub_parser)
        for flag in ("--host", "--user", "--database", "--engine"):
            assert flag not in opts, (
                f"configure remove --help incorrectly exposes '{flag}' (add-only flag)"
            )

    def test_list_has_no_connection_flags(self):
        """configure list should have minimal flags (no connection args)."""
        sub_parser = _get_subparser(self.parsers, "configure", "list")
        opts = _get_option_strings(sub_parser)
        for flag in ("--host", "--engine", "--confirm"):
            assert flag not in opts, (
                f"configure list --help incorrectly exposes '{flag}'"
            )

    def test_subcommands_have_distinct_help_from_each_other(self):
        """All configure subcommand help pages must be distinct from one another."""
        help_pages = {}
        for subcmd in self.SUBCOMMANDS:
            sub_parser = _get_subparser(self.parsers, "configure", subcmd)
            help_pages[subcmd] = _get_help(sub_parser)

        for i, sc1 in enumerate(self.SUBCOMMANDS):
            for sc2 in self.SUBCOMMANDS[i + 1:]:
                assert help_pages[sc1] != help_pages[sc2], (
                    f"configure {sc1} --help and configure {sc2} --help are identical"
                )


class TestAgentHelpPages:
    """agent subcommands must each have a distinct, subcommand-specific help page."""

    SUBCOMMANDS = ["create", "list", "show", "delete", "chat", "serve", "mcp", "slack"]

    def setup_method(self):
        self.parsers = _build_parsers()
        self.parent_help = _get_help(self.parsers["agent"])

    def test_agent_parent_help_lists_subcommands(self):
        """rdst agent --help must list all available subcommands."""
        for subcmd in self.SUBCOMMANDS:
            assert subcmd in self.parent_help, (
                f"agent --help does not mention subcommand '{subcmd}'"
            )

    def test_agent_parent_has_subparsers(self):
        """agent parser must use add_subparsers (not flat positional argument)."""
        parent = self.parsers["agent"]
        found = False
        for action in parent._subparsers._group_actions:
            if isinstance(action, argparse._SubParsersAction):
                found = True
                break
        assert found, "agent parser has no subparsers action — still using flat parser"

    @pytest.mark.parametrize("subcommand", SUBCOMMANDS)
    def test_subcommand_help_differs_from_parent(self, subcommand):
        """Each subcommand's help text must differ from the parent agent help."""
        sub_parser = _get_subparser(self.parsers, "agent", subcommand)
        sub_help = _get_help(sub_parser)
        assert sub_help != self.parent_help, (
            f"agent {subcommand} --help is identical to agent --help"
        )

    @pytest.mark.parametrize("subcommand", SUBCOMMANDS)
    def test_subcommand_help_mentions_subcommand_name(self, subcommand):
        """Each subcommand's help must reference its own name."""
        sub_parser = _get_subparser(self.parsers, "agent", subcommand)
        sub_help = _get_help(sub_parser)
        assert subcommand in sub_help, (
            f"agent {subcommand} --help does not mention '{subcommand}'"
        )

    def test_create_has_target_and_guard_flags(self):
        """agent create must expose --target, --guard, --name, --description."""
        sub_parser = _get_subparser(self.parsers, "agent", "create")
        opts = _get_option_strings(sub_parser)
        for flag in ("--target", "--guard", "--name", "--description"):
            assert flag in opts, f"agent create --help is missing '{flag}'"

    def test_create_does_not_have_port_flag(self):
        """agent create must NOT have --port (which is serve-only)."""
        sub_parser = _get_subparser(self.parsers, "agent", "create")
        opts = _get_option_strings(sub_parser)
        assert "--port" not in opts, (
            "agent create --help incorrectly exposes --port (serve-only flag)"
        )

    def test_serve_has_port_flag(self):
        """agent serve must have --port."""
        sub_parser = _get_subparser(self.parsers, "agent", "serve")
        opts = _get_option_strings(sub_parser)
        assert "--port" in opts, "agent serve --help is missing --port flag"

    def test_serve_does_not_have_create_only_flags(self):
        """agent serve must NOT expose create-only flags like --target."""
        sub_parser = _get_subparser(self.parsers, "agent", "serve")
        opts = _get_option_strings(sub_parser)
        for flag in ("--target", "--guard"):
            assert flag not in opts, (
                f"agent serve --help incorrectly exposes '{flag}' (create-only flag)"
            )

    def test_list_has_no_create_flags(self):
        """agent list should have minimal flags."""
        sub_parser = _get_subparser(self.parsers, "agent", "list")
        opts = _get_option_strings(sub_parser)
        for flag in ("--target", "--port", "--guard"):
            assert flag not in opts, (
                f"agent list --help incorrectly exposes '{flag}'"
            )

    def test_chat_has_verbose_flag(self):
        """agent chat must have --verbose."""
        sub_parser = _get_subparser(self.parsers, "agent", "chat")
        opts = _get_option_strings(sub_parser)
        assert "--verbose" in opts, "agent chat --help is missing --verbose flag"


class TestGuardHelpPages:
    """guard subcommands must each have a distinct, subcommand-specific help page."""

    SUBCOMMANDS = ["create", "list", "show", "delete", "edit", "check"]

    def setup_method(self):
        self.parsers = _build_parsers()
        self.parent_help = _get_help(self.parsers["guard"])

    def test_guard_parent_help_lists_subcommands(self):
        """rdst guard --help must list all available subcommands."""
        for subcmd in self.SUBCOMMANDS:
            assert subcmd in self.parent_help, (
                f"guard --help does not mention subcommand '{subcmd}'"
            )

    def test_guard_parent_has_subparsers(self):
        """guard parser must use add_subparsers (not flat positional argument)."""
        parent = self.parsers["guard"]
        found = False
        for action in parent._subparsers._group_actions:
            if isinstance(action, argparse._SubParsersAction):
                found = True
                break
        assert found, "guard parser has no subparsers action — still using flat parser"

    @pytest.mark.parametrize("subcommand", SUBCOMMANDS)
    def test_subcommand_help_differs_from_parent(self, subcommand):
        """Each subcommand's help text must differ from the parent guard help."""
        sub_parser = _get_subparser(self.parsers, "guard", subcommand)
        sub_help = _get_help(sub_parser)
        assert sub_help != self.parent_help, (
            f"guard {subcommand} --help is identical to guard --help"
        )

    @pytest.mark.parametrize("subcommand", SUBCOMMANDS)
    def test_subcommand_help_mentions_subcommand_name(self, subcommand):
        """Each subcommand's help must reference its own name."""
        sub_parser = _get_subparser(self.parsers, "guard", subcommand)
        sub_help = _get_help(sub_parser)
        assert subcommand in sub_help, (
            f"guard {subcommand} --help does not mention '{subcommand}'"
        )

    def test_create_has_policy_flags(self):
        """guard create must expose policy flags like --mask, --require-where."""
        sub_parser = _get_subparser(self.parsers, "guard", "create")
        opts = _get_option_strings(sub_parser)
        for flag in ("--mask", "--require-where", "--require-limit", "--intent", "--name"):
            assert flag in opts, f"guard create --help is missing '{flag}'"

    def test_create_does_not_have_check_flags(self):
        """guard create must NOT have check-only flags like --guard."""
        sub_parser = _get_subparser(self.parsers, "guard", "create")
        opts = _get_option_strings(sub_parser)
        assert "--guard" not in opts, (
            "guard create --help incorrectly exposes --guard (check-only flag)"
        )

    def test_check_has_guard_flag(self):
        """guard check must have --guard."""
        sub_parser = _get_subparser(self.parsers, "guard", "check")
        opts = _get_option_strings(sub_parser)
        assert "--guard" in opts, "guard check --help is missing --guard flag"

    def test_check_does_not_have_create_only_flags(self):
        """guard check must NOT expose create-only flags like --mask."""
        sub_parser = _get_subparser(self.parsers, "guard", "check")
        opts = _get_option_strings(sub_parser)
        for flag in ("--mask", "--require-where", "--intent"):
            assert flag not in opts, (
                f"guard check --help incorrectly exposes '{flag}' (create-only flag)"
            )

    def test_list_has_no_policy_flags(self):
        """guard list should have minimal flags."""
        sub_parser = _get_subparser(self.parsers, "guard", "list")
        opts = _get_option_strings(sub_parser)
        for flag in ("--mask", "--guard", "--intent"):
            assert flag not in opts, (
                f"guard list --help incorrectly exposes '{flag}'"
            )


class TestQueryHelpPagesRegression:
    """Regression tests: query subcommands must still have distinct help pages."""

    SUBCOMMANDS = ["add", "list", "show", "edit", "delete"]

    def setup_method(self):
        self.parsers = _build_parsers()
        self.parent_help = _get_help(self.parsers["query"])

    def test_query_parent_help_lists_subcommands(self):
        """rdst query --help lists available subcommands."""
        for subcmd in self.SUBCOMMANDS:
            assert subcmd in self.parent_help, (
                f"query --help does not mention subcommand '{subcmd}'"
            )

    @pytest.mark.parametrize("subcommand", SUBCOMMANDS)
    def test_subcommand_help_differs_from_parent(self, subcommand):
        """Each query subcommand's help text must differ from parent help."""
        sub_parser = _get_subparser(self.parsers, "query", subcommand)
        sub_help = _get_help(sub_parser)
        assert sub_help != self.parent_help, (
            f"query {subcommand} --help is identical to query --help (regression)"
        )

    def test_query_add_has_query_and_file_flags(self):
        """query add must expose -q/--query and -f/--file flags."""
        sub_parser = _get_subparser(self.parsers, "query", "add")
        opts = _get_option_strings(sub_parser)
        assert "--query" in opts or "-q" in opts, "query add --help missing --query/-q"
        assert "--file" in opts or "-f" in opts, "query add --help missing --file/-f"

    def test_query_list_has_limit_and_filter(self):
        """query list must expose --limit and --filter flags."""
        sub_parser = _get_subparser(self.parsers, "query", "list")
        opts = _get_option_strings(sub_parser)
        assert "--limit" in opts, "query list --help missing --limit"
        assert "--filter" in opts, "query list --help missing --filter"

    def test_query_list_does_not_have_add_only_flags(self):
        """query list must NOT have add-only flags like --query."""
        sub_parser = _get_subparser(self.parsers, "query", "list")
        opts = _get_option_strings(sub_parser)
        assert "--query" not in opts and "-q" not in opts, (
            "query list --help incorrectly exposes --query (add-only flag)"
        )

    def test_query_delete_has_force_flag(self):
        """query delete must have --force."""
        sub_parser = _get_subparser(self.parsers, "query", "delete")
        opts = _get_option_strings(sub_parser)
        assert "--force" in opts, "query delete --help missing --force"


class TestSchemaHelpPagesRegression:
    """Regression tests: schema subcommands must still have distinct help pages."""

    SUBCOMMANDS = ["show", "init", "edit", "annotate", "export", "delete", "list"]

    def setup_method(self):
        self.parsers = _build_parsers()
        self.parent_help = _get_help(self.parsers["schema"])

    def test_schema_parent_help_lists_subcommands(self):
        """rdst schema --help lists available subcommands."""
        for subcmd in self.SUBCOMMANDS:
            assert subcmd in self.parent_help, (
                f"schema --help does not mention subcommand '{subcmd}'"
            )

    @pytest.mark.parametrize("subcommand", SUBCOMMANDS)
    def test_subcommand_help_differs_from_parent(self, subcommand):
        """Each schema subcommand's help text must differ from parent help."""
        sub_parser = _get_subparser(self.parsers, "schema", subcommand)
        sub_help = _get_help(sub_parser)
        assert sub_help != self.parent_help, (
            f"schema {subcommand} --help is identical to schema --help (regression)"
        )

    def test_schema_init_has_force_flag(self):
        """schema init must expose --force."""
        sub_parser = _get_subparser(self.parsers, "schema", "init")
        opts = _get_option_strings(sub_parser)
        assert "--force" in opts, "schema init --help missing --force"

    def test_schema_annotate_has_use_llm_flag(self):
        """schema annotate must expose --use-llm."""
        sub_parser = _get_subparser(self.parsers, "schema", "annotate")
        opts = _get_option_strings(sub_parser)
        assert "--use-llm" in opts, "schema annotate --help missing --use-llm"

    def test_schema_list_has_no_init_flags(self):
        """schema list should not expose --force or --enum-threshold (init-only)."""
        sub_parser = _get_subparser(self.parsers, "schema", "list")
        opts = _get_option_strings(sub_parser)
        for flag in ("--force", "--enum-threshold"):
            assert flag not in opts, (
                f"schema list --help incorrectly exposes '{flag}'"
            )


class TestCommandDefStructure:
    """Structural tests ensuring CommandDef entries are consistent."""

    MIGRATED_COMMANDS = ["configure", "agent", "guard"]

    def test_migrated_commands_have_subcommand_dest(self):
        """configure/agent/guard must have subcommand_dest set for argparse routing."""
        for cmd_name in self.MIGRATED_COMMANDS:
            cmd = COMMANDS[cmd_name]
            assert cmd.subcommand_dest is not None, (
                f"COMMANDS['{cmd_name}'] has no subcommand_dest — dispatch will fail"
            )

    def test_migrated_commands_have_subcommand_defs(self):
        """configure/agent/guard must have subcommand_defs populated."""
        for cmd_name in self.MIGRATED_COMMANDS:
            cmd = COMMANDS[cmd_name]
            assert cmd.subcommand_defs, (
                f"COMMANDS['{cmd_name}'] has empty subcommand_defs"
            )

    def test_migrated_commands_have_no_flat_subcommand_arg(self):
        """configure/agent/guard must not have a flat positional 'subcommand' arg."""
        for cmd_name in self.MIGRATED_COMMANDS:
            cmd = COMMANDS[cmd_name]
            flat_names = [
                a.name for a in cmd.args
                if hasattr(a, "name") and a.name == "subcommand"
            ]
            assert flat_names == [], (
                f"COMMANDS['{cmd_name}'] still has a flat positional 'subcommand' arg "
                f"in cmd.args — migration incomplete"
            )

    def test_subcommand_defs_match_subcommands_list(self):
        """subcommand_defs names must match entries in the subcommands display list."""
        for cmd_name in self.MIGRATED_COMMANDS:
            cmd = COMMANDS[cmd_name]
            def_names = {s.name for s in cmd.subcommand_defs}
            list_names = {name for name, _desc in cmd.subcommands}
            assert def_names == list_names, (
                f"COMMANDS['{cmd_name}'].subcommand_defs names {def_names!r} "
                f"do not match .subcommands display names {list_names!r}"
            )

    @pytest.mark.parametrize("cmd_name", MIGRATED_COMMANDS)
    def test_each_subcommand_def_has_help(self, cmd_name):
        """Every SubcommandDef must have a non-empty help string."""
        cmd = COMMANDS[cmd_name]
        for subcmd in cmd.subcommand_defs:
            assert subcmd.help, (
                f"COMMANDS['{cmd_name}'].subcommand_defs['{subcmd.name}'] has no help text"
            )

    def test_all_commands_with_subcommand_defs_have_subcommand_dest(self):
        """Any CommandDef with subcommand_defs must also have subcommand_dest."""
        for cmd_name, cmd in COMMANDS.items():
            if cmd.subcommand_defs:
                assert cmd.subcommand_dest is not None, (
                    f"COMMANDS['{cmd_name}'] has subcommand_defs but no subcommand_dest"
                )
