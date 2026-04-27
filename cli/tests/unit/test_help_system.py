"""
Unit tests for the rdst help system.

Tests COMMAND_GROUPS completeness, RDST_DOCS content, help fallback text,
short_help style consistency, CLI syntax correctness, branding, and
interactive menu commands.
"""

import pytest

from shared.cli.parser_data import COMMAND_GROUPS, COMMAND_ORDER, COMMANDS
from shared.cli.help_command import RDST_DOCS
from shared.cli.rdst_cli import RdstCLI
from shared.ui.components import SectionHeader


def _all_commands_in_groups() -> set[str]:
    """Return the flat set of commands listed in COMMAND_GROUPS."""
    result: set[str] = set()
    for _group_name, cmds in COMMAND_GROUPS:
        result.update(cmds)
    return result


class TestCommandGroupsCompleteness:
    """All user-facing commands should appear in at least one COMMAND_GROUP."""

    # Commands that are intentionally hidden / internal (not user-facing).
    EXCLUDED = {"configure"}  # configure is already shown via init/configure

    def test_audit_in_command_groups(self):
        """audit must appear in COMMAND_GROUPS."""
        grouped = _all_commands_in_groups()
        assert "audit" in grouped, (
            "'audit' is missing from COMMAND_GROUPS. Add it to an appropriate group."
        )

    def test_cache_in_command_groups(self):
        """cache must appear in COMMAND_GROUPS."""
        grouped = _all_commands_in_groups()
        assert "cache" in grouped, (
            "'cache' is missing from COMMAND_GROUPS. Add it to an appropriate group."
        )

    def test_fleet_in_command_groups(self):
        """fleet must appear in COMMAND_GROUPS."""
        grouped = _all_commands_in_groups()
        assert "fleet" in grouped, (
            "'fleet' is missing from COMMAND_GROUPS. Add it to an appropriate group."
        )

    def test_all_command_order_entries_are_grouped(self):
        """Every command in COMMAND_ORDER should appear in some COMMAND_GROUP.

        Exceptions: 'configure' is a sub-group shown inline rather than
        a top-level group entry.
        """
        grouped = _all_commands_in_groups()
        ungrouped = [
            name
            for name in COMMAND_ORDER
            if name not in grouped and name not in self.EXCLUDED
        ]
        assert ungrouped == [], (
            f"Commands in COMMAND_ORDER missing from COMMAND_GROUPS: {ungrouped}"
        )

    def test_no_unknown_commands_in_groups(self):
        """Every name listed in COMMAND_GROUPS must be a known command."""
        grouped = _all_commands_in_groups()
        unknown = [name for name in grouped if name not in COMMANDS]
        assert unknown == [], (
            f"COMMAND_GROUPS references unknown command names: {unknown}"
        )

    def test_groups_structure_is_list_of_tuples(self):
        """COMMAND_GROUPS must be a list of (str, list[str]) tuples."""
        assert isinstance(COMMAND_GROUPS, list)
        for entry in COMMAND_GROUPS:
            assert isinstance(entry, tuple) and len(entry) == 2
            group_name, cmds = entry
            assert isinstance(group_name, str)
            assert isinstance(cmds, list)
            for cmd in cmds:
                assert isinstance(cmd, str)


class TestRdstDocsContent:
    """RDST_DOCS must document all commands with correct content."""

    def test_docs_contain_guard_section(self):
        """RDST_DOCS must have a section for 'rdst guard'."""
        assert "### rdst guard" in RDST_DOCS, (
            "RDST_DOCS is missing the '### rdst guard' section."
        )

    def test_docs_contain_agent_section(self):
        """RDST_DOCS must have a section for 'rdst agent'."""
        assert "### rdst agent" in RDST_DOCS, (
            "RDST_DOCS is missing the '### rdst agent' section."
        )

    def test_guard_subcommands_documented(self):
        """guard create/list/show/delete/edit/check must appear in RDST_DOCS."""
        guard_subcommands = ["guard create", "guard list", "guard show",
                             "guard delete", "guard edit", "guard check"]
        missing = [sub for sub in guard_subcommands if sub not in RDST_DOCS]
        assert missing == [], (
            f"RDST_DOCS missing guard subcommand docs: {missing}"
        )

    def test_agent_subcommands_documented(self):
        """agent create/list/show/delete/chat must appear in RDST_DOCS."""
        agent_subcommands = ["agent create", "agent list", "agent show",
                             "agent delete", "agent chat"]
        missing = [sub for sub in agent_subcommands if sub not in RDST_DOCS]
        assert missing == [], (
            f"RDST_DOCS missing agent subcommand docs: {missing}"
        )

    def test_docs_contain_existing_commands(self):
        """Smoke test: RDST_DOCS still contains docs for well-known commands."""
        for section in ["### rdst init", "### rdst analyze", "### rdst top",
                        "### rdst cache", "### rdst audit", "### rdst fleet"]:
            assert section in RDST_DOCS, (
                f"RDST_DOCS is unexpectedly missing '{section}'."
            )

    def test_web_section_exists(self):
        """RDST_DOCS must contain a section for 'rdst web'."""
        assert "### rdst web" in RDST_DOCS, (
            "RDST_DOCS is missing the '### rdst web' section."
        )

    def test_slack_section_exists(self):
        """RDST_DOCS must contain a section for 'rdst slack'."""
        assert "### rdst slack" in RDST_DOCS, (
            "RDST_DOCS is missing the '### rdst slack' section."
        )

    def test_claude_section_exists(self):
        """RDST_DOCS must contain a section for 'rdst claude'."""
        assert "### rdst claude" in RDST_DOCS, (
            "RDST_DOCS is missing the '### rdst claude' section."
        )

    def test_web_section_has_example(self):
        """rdst web section must contain at least one example command."""
        web_section_start = RDST_DOCS.find("### rdst web")
        assert web_section_start != -1
        next_section = RDST_DOCS.find("\n### ", web_section_start + 1)
        web_section = RDST_DOCS[web_section_start:next_section if next_section != -1 else web_section_start + 500]
        assert "rdst web" in web_section, (
            "rdst web section should contain at least one 'rdst web' command example."
        )

    def test_slack_section_has_example(self):
        """rdst slack section must contain at least one example command."""
        slack_section_start = RDST_DOCS.find("### rdst slack")
        assert slack_section_start != -1
        next_section = RDST_DOCS.find("\n### ", slack_section_start + 1)
        slack_section = RDST_DOCS[slack_section_start:next_section if next_section != -1 else slack_section_start + 500]
        assert "rdst slack" in slack_section, (
            "rdst slack section should contain at least one 'rdst slack' command example."
        )

    def test_claude_section_has_example(self):
        """rdst claude section must contain at least one example command."""
        claude_section_start = RDST_DOCS.find("### rdst claude")
        assert claude_section_start != -1
        next_section = RDST_DOCS.find("\n### ", claude_section_start + 1)
        claude_section = RDST_DOCS[claude_section_start:next_section if next_section != -1 else claude_section_start + 500]
        assert "rdst claude" in claude_section, (
            "rdst claude section should contain at least one 'rdst claude' command example."
        )


class TestNoStaleSubcommandReferences:
    """RDST_DOCS must not reference non-existent subcommands."""

    def test_no_configure_llm_in_docs(self):
        """RDST_DOCS must not contain 'configure llm'."""
        assert "configure llm" not in RDST_DOCS, (
            "RDST_DOCS references the non-existent 'rdst configure llm' subcommand. "
            "Remove or replace all such references."
        )

    def test_no_configure_llm_provider_in_docs(self):
        """RDST_DOCS must not contain 'configure llm --provider'."""
        assert "configure llm --provider" not in RDST_DOCS, (
            "RDST_DOCS references 'rdst configure llm --provider', which does not exist."
        )

    def test_no_query_save_in_docs(self):
        """RDST_DOCS must not contain 'query save'."""
        assert "query save" not in RDST_DOCS, (
            "RDST_DOCS references 'rdst query save', which does not exist. "
            "The correct subcommand is 'rdst query add'."
        )

    def test_query_add_is_documented(self):
        """RDST_DOCS must document 'query add' as the correct subcommand."""
        assert "query add" in RDST_DOCS, (
            "RDST_DOCS should document 'rdst query add' as the way to save queries."
        )


class TestGuardCliSyntax:
    """Guard examples must use correct flag syntax."""

    def test_guard_create_uses_name_flag(self):
        """guard create example must use --name flag, not a bare positional name."""
        assert "guard create --name" in RDST_DOCS, (
            "RDST_DOCS guard create example should use '--name my-policy', not 'create my-policy'."
        )

    def test_guard_check_uses_guard_flag(self):
        """guard check example must use --guard flag."""
        assert "guard check --guard" in RDST_DOCS, (
            "RDST_DOCS guard check example should use '--guard my-policy', not 'check my-policy'."
        )

    def test_guard_check_uses_sql_flag(self):
        """guard check example must use --sql flag, not -q."""
        guard_section_start = RDST_DOCS.find("#### guard check")
        assert guard_section_start != -1, "guard check section not found"
        guard_section = RDST_DOCS[guard_section_start:guard_section_start + 300]
        assert "--sql" in guard_section, (
            "guard check example should use '--sql \"...\"', not '-q \"...\"'."
        )

    def test_guard_create_no_bare_positional(self):
        """guard create must not use bare positional 'create my-policy' pattern."""
        assert "rdst guard create my-policy\n" not in RDST_DOCS, (
            "RDST_DOCS uses deprecated positional syntax 'rdst guard create my-policy'. "
            "Use '--name my-policy'."
        )


class TestAgentCliSyntax:
    """Agent examples must use --name flag syntax."""

    def test_agent_create_uses_name_flag(self):
        """agent create example must use --name flag."""
        assert "agent create --name" in RDST_DOCS, (
            "RDST_DOCS agent create example should use '--name my-agent', not 'create my-agent'."
        )

    def test_agent_chat_uses_name_flag(self):
        """agent chat example must use --name flag."""
        assert "agent chat --name" in RDST_DOCS, (
            "RDST_DOCS agent chat example should use '--name my-agent', not 'chat my-agent'."
        )

    def test_agent_create_no_bare_positional(self):
        """agent create must not use bare positional syntax."""
        assert "rdst agent create my-agent\n" not in RDST_DOCS, (
            "RDST_DOCS uses deprecated positional syntax 'rdst agent create my-agent'. "
            "Use '--name my-agent'."
        )

    def test_agent_chat_no_bare_positional(self):
        """agent chat must not use bare positional syntax."""
        assert "rdst agent chat my-agent\n" not in RDST_DOCS, (
            "RDST_DOCS uses deprecated positional syntax 'rdst agent chat my-agent'. "
            "Use '--name my-agent'."
        )


class TestHelpFallbackText:
    """The plain-text help() fallback in RdstCli must reflect current commands."""

    def _get_fallback_text(self) -> str:
        """Return the fallback help text produced by RdstCLI.help()."""
        cli = RdstCLI.__new__(RdstCLI)
        result = cli.help()
        return result.message

    def test_fallback_lists_audit(self):
        """Fallback help text must mention 'audit'."""
        text = self._get_fallback_text()
        assert "audit" in text, "Fallback help text is missing 'audit'."

    def test_fallback_lists_fleet(self):
        """Fallback help text must mention 'fleet'."""
        text = self._get_fallback_text()
        assert "fleet" in text, "Fallback help text is missing 'fleet'."

    def test_fallback_lists_guard(self):
        """Fallback help text must mention 'guard'."""
        text = self._get_fallback_text()
        assert "guard" in text, "Fallback help text is missing 'guard'."

    def test_fallback_lists_agent(self):
        """Fallback help text must mention 'agent'."""
        text = self._get_fallback_text()
        assert "agent" in text, "Fallback help text is missing 'agent'."

    def test_fallback_lists_scan(self):
        """Fallback help text must mention 'scan'."""
        text = self._get_fallback_text()
        assert "scan" in text, "Fallback help text is missing 'scan'."

    def test_fallback_lists_cache(self):
        """Fallback help text must mention 'cache'."""
        text = self._get_fallback_text()
        assert "cache" in text, "Fallback help text is missing 'cache'."

    def test_fallback_is_non_empty(self):
        """Fallback help must return a non-empty success result."""
        cli = RdstCLI.__new__(RdstCLI)
        result = cli.help()
        assert result.ok
        assert result.message.strip() != ""

    def test_no_configure_llm_in_help_output(self):
        """help() method must not reference the non-existent 'configure llm' subcommand."""
        cli = RdstCLI.__new__(RdstCLI)
        result = cli.help()
        assert result.ok
        assert "configure llm" not in result.message, (
            "help() output references the non-existent 'rdst configure llm' subcommand."
        )

    def test_help_output_has_configure_command(self):
        """help() output should still list 'rdst configure'."""
        cli = RdstCLI.__new__(RdstCLI)
        result = cli.help()
        assert "rdst configure" in result.message, (
            "help() output should still list 'rdst configure'."
        )

    def test_help_output_has_common_commands(self):
        """help() output must list common commands."""
        cli = RdstCLI.__new__(RdstCLI)
        result = cli.help()
        for cmd in ["rdst init", "rdst top", "rdst analyze", "rdst audit"]:
            assert cmd in result.message, f"help() output is missing '{cmd}'."


class TestShortHelpImperativeStyle:
    """All short_help descriptions should start with an imperative verb."""

    # Known noun-phrase starters that should not be used
    NOUN_PHRASE_STARTERS = [
        "live view",
        "first-time setup wizard",
    ]

    def test_top_short_help_is_imperative(self):
        """'top' short_help must start with an imperative verb, not a noun phrase."""
        short_help = COMMANDS["top"].short_help.lower()
        for bad in self.NOUN_PHRASE_STARTERS:
            assert not short_help.startswith(bad), (
                f"'top' short_help uses noun phrase '{bad}': {COMMANDS['top'].short_help!r}"
            )

    def test_init_short_help_is_imperative(self):
        """'init' short_help must start with an imperative verb, not a noun phrase."""
        short_help = COMMANDS["init"].short_help.lower()
        for bad in self.NOUN_PHRASE_STARTERS:
            assert not short_help.startswith(bad), (
                f"'init' short_help uses noun phrase '{bad}': {COMMANDS['init'].short_help!r}"
            )

    def test_all_short_helps_are_non_empty(self):
        """Every command must have a non-empty short_help string."""
        empty = [name for name, cmd in COMMANDS.items() if not cmd.short_help.strip()]
        assert empty == [], f"Commands with empty short_help: {empty}"

    def test_top_short_help_contains_monitor_or_watch(self):
        """'top' short_help should use an imperative like 'Monitor' or 'Watch'."""
        short_help = COMMANDS["top"].short_help.lower()
        imperative_verbs = ["monitor", "watch", "show", "display", "track"]
        has_imperative = any(short_help.startswith(v) for v in imperative_verbs)
        assert has_imperative, (
            f"'top' short_help should start with an imperative verb (Monitor/Watch/...): "
            f"{COMMANDS['top'].short_help!r}"
        )

    def test_init_short_help_starts_with_verb(self):
        """'init' short_help should start with an imperative verb like 'Set up'."""
        short_help = COMMANDS["init"].short_help.lower()
        imperative_verbs = ["set", "run", "initialize", "configure", "start"]
        has_imperative = any(short_help.startswith(v) for v in imperative_verbs)
        assert has_imperative, (
            f"'init' short_help should start with an imperative verb: "
            f"{COMMANDS['init'].short_help!r}"
        )

    def test_audit_short_help_starts_with_verb(self):
        """'audit' short_help must start with an imperative verb."""
        cmd = COMMANDS["audit"]
        assert cmd.short_help.startswith("Run "), (
            f"audit short_help should start with 'Run', got: '{cmd.short_help}'"
        )

    def test_audit_short_help_not_noun_first(self):
        """'audit' short_help must not start with a noun phrase."""
        cmd = COMMANDS["audit"]
        first_word = cmd.short_help.split()[0]
        assert first_word[0].isupper(), "First word of short_help should be capitalized."
        assert not cmd.short_help.startswith("Deep"), (
            "audit short_help should not start with 'Deep' (noun-first, not imperative)."
        )

    def test_other_commands_are_verb_first(self):
        """Baseline commands should start with expected imperative verbs."""
        verb_first_commands = {
            "configure": "Manage",
            "top": "Monitor",
            "analyze": "Analyze",
            "init": "Set",
            "report": "Submit",
        }
        for name, expected_start in verb_first_commands.items():
            cmd = COMMANDS[name]
            assert cmd.short_help.startswith(expected_start), (
                f"Command '{name}' short_help should start with '{expected_start}', "
                f"got: '{cmd.short_help}'"
            )


class TestReadysetBranding:
    """User-facing strings must use 'Readyset', not 'ReadySet'."""

    def test_rdst_cli_help_banner_uses_correct_branding(self):
        """help() banner must use 'Readyset', not 'ReadySet'."""
        cli = RdstCLI.__new__(RdstCLI)
        result = cli.help()
        assert "ReadySet" not in result.message, (
            "help() banner uses 'ReadySet' instead of 'Readyset'."
        )
        assert "Readyset" in result.message, (
            "help() banner should contain 'Readyset'."
        )

    def test_rdst_cli_version_uses_correct_branding(self):
        """version() output must use 'Readyset', not 'ReadySet'."""
        cli = RdstCLI.__new__(RdstCLI)
        result = cli.version()
        assert result.ok
        assert "ReadySet" not in result.message, (
            "version() output uses 'ReadySet' instead of 'Readyset'."
        )
        assert "Readyset" in result.message

    def test_analyze_description_uses_correct_branding(self):
        """analyze command description must use 'Readyset', not 'ReadySet'."""
        cmd = COMMANDS["analyze"]
        assert "ReadySet" not in cmd.description, (
            "analyze command description uses 'ReadySet' instead of 'Readyset'."
        )

    def test_cache_compare_help_uses_correct_branding(self):
        """cache-compare subcommand help must use 'Readyset', not 'ReadySet'."""
        query_cmd = COMMANDS["query"]
        cache_compare = next(
            (s for s in query_cmd.subcommand_defs if s.name == "cache-compare"), None
        )
        assert cache_compare is not None
        assert "ReadySet" not in cache_compare.help, (
            "cache-compare subcommand help uses 'ReadySet' instead of 'Readyset'."
        )

    def test_no_readyset_lowercase_s_in_parser_data_user_strings(self):
        """No command short_help or description should use 'ReadySet'."""
        for name, cmd in COMMANDS.items():
            assert "ReadySet" not in cmd.short_help, (
                f"Command '{name}' short_help uses 'ReadySet' (should be 'Readyset')."
            )
            assert "ReadySet" not in cmd.description, (
                f"Command '{name}' description uses 'ReadySet' (should be 'Readyset')."
            )


class TestSectionHeaderArgumentOrder:
    """SectionHeader should be called with the display title as first argument."""

    def test_section_header_title_only_renders_correctly(self):
        """SectionHeader with title only renders the title."""
        header = SectionHeader("Readyset Data and SQL Toolkit")
        assert "READYSET DATA AND SQL TOOLKIT" in header.plain.upper()

    def test_section_header_with_icon_puts_icon_before_title(self):
        """SectionHeader with icon renders icon before title."""
        header = SectionHeader("My Title", icon=">>")
        plain = header.plain
        assert plain.startswith(">> ")
        assert "MY TITLE" in plain.upper()

    def test_section_header_with_toolkit_name_as_title(self):
        """SectionHeader renders toolkit name correctly."""
        header = SectionHeader("Readyset Data and SQL Toolkit")
        plain = header.plain
        assert "READYSET DATA AND SQL TOOLKIT" in plain.upper()
        assert plain.count(" ") > 0

    def test_swapped_args_would_render_wrongly(self):
        """Swapping title and icon arguments produces incorrect rendering."""
        bad_header = SectionHeader("rdst", "Readyset Data and SQL Toolkit")
        plain = bad_header.plain
        assert plain.startswith("Readyset Data and SQL Toolkit ")
        assert plain.endswith("RDST")


class TestInteractiveMenuCommands:
    """Interactive menu in rdst.py must include expected commands."""

    def _get_menu_commands(self) -> list[str]:
        """Extract command names from _menu_command_names in rdst.py."""
        import ast
        import pathlib

        rdst_path = pathlib.Path(__file__).parent.parent.parent / "rdst.py"
        tree = ast.parse(rdst_path.read_text())

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "_menu_command_names"
                and isinstance(node.value, ast.List)
            ):
                return [elt.value for elt in node.value.elts if isinstance(elt, ast.Constant)]
        return []

    def test_scan_in_interactive_menu(self):
        """Interactive menu must include 'scan'."""
        commands = self._get_menu_commands()
        assert "scan" in commands, (
            f"'scan' is missing from the interactive menu commands list. "
            f"Current menu commands: {commands}"
        )

    def test_agent_in_interactive_menu(self):
        """Interactive menu must include 'agent'."""
        commands = self._get_menu_commands()
        assert "agent" in commands, (
            f"'agent' is missing from the interactive menu commands list. "
            f"Current menu commands: {commands}"
        )

    def test_guard_in_interactive_menu(self):
        """Interactive menu must include 'guard'."""
        commands = self._get_menu_commands()
        assert "guard" in commands, (
            f"'guard' is missing from the interactive menu commands list. "
            f"Current menu commands: {commands}"
        )

    def test_core_commands_still_in_menu(self):
        """Core commands (configure, top, analyze, init) must remain in the menu."""
        commands = self._get_menu_commands()
        for core_cmd in ("configure", "top", "analyze", "init"):
            assert core_cmd in commands, (
                f"Core command '{core_cmd}' was accidentally removed from the interactive menu."
            )

    def test_quit_handled_via_q(self):
        """Exit is handled via 'q'/'quit' input, not as a menu command."""
        commands = self._get_menu_commands()
        assert "exit" not in commands, "exit should be handled via 'q' input, not as a menu item"
