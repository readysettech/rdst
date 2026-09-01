"""Command-boundary tests for the shared AI access gate."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from shared.ai_access import AIAccessCheck, AIAccessState
from shared.cli.types import RdstResult

MISSING = AIAccessCheck(
    AIAccessState.MISSING,
    message="AI access is required. Run rdst account login.",
    code="LOGIN_REQUIRED",
)
CLAUDE_REQUIRED = AIAccessCheck(
    AIAccessState.CLAUDE_REQUIRED,
    message="Anthropic key required.",
    code="ANTHROPIC_KEY_REQUIRED",
)


def test_ask_blocks_before_constructing_service(monkeypatch):
    from features.ask.cli.command import AskCommand
    from shared.cli import ai_access as cli_ai_access

    monkeypatch.setattr(
        cli_ai_access, "ensure_cli_ai_access", lambda **_kwargs: MISSING
    )

    result = AskCommand().execute(
        question="How many users are there?",
        target="prod",
        no_interactive=True,
    )

    assert not result.ok
    assert result.data == {"code": "LOGIN_REQUIRED", "state": "missing"}


def test_scan_blocks_before_scanning(monkeypatch, tmp_path):
    from features.scan.cli import command as scan_command
    from features.scan.cli.command import ScanCommand

    monkeypatch.setattr(scan_command, "ensure_cli_ai_access", lambda **_kwargs: MISSING)
    (tmp_path / "prod.yaml").write_text("tables: {}\n")
    monkeypatch.setattr(scan_command, "rdst_semantic_layer_dir", lambda: tmp_path)
    command = ScanCommand(console=MagicMock())
    monkeypatch.setattr(
        command,
        "_scan_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("scanned")),
    )

    result = command.execute(directory=".", target="prod")

    assert not result.ok
    assert result.data["code"] == "LOGIN_REQUIRED"


def test_scan_local_validation_precedes_ai_access(monkeypatch):
    from features.scan.cli import command as scan_command
    from features.scan.cli.command import ScanCommand

    gate = MagicMock(return_value=MISSING)
    monkeypatch.setattr(scan_command, "ensure_cli_ai_access", gate)

    result = ScanCommand(console=MagicMock()).execute(directory=".", target=None)

    assert not result.ok
    assert "Target required" in result.message
    gate.assert_not_called()


def test_scan_dry_run_does_not_require_ai(monkeypatch):
    from features.scan.cli import command as scan_command
    from features.scan.cli.command import ScanCommand
    from shared.cli.types import RdstResult

    monkeypatch.setattr(
        scan_command,
        "ensure_cli_ai_access",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("gated")),
    )
    command = ScanCommand(console=MagicMock())
    monkeypatch.setattr(
        command,
        "_scan_directory",
        lambda *_args, **_kwargs: RdstResult(True, "dry run"),
    )

    result = command.execute(directory=".", target="prod", dry_run=True)

    assert result.ok


def test_query_run_analyze_blocks_before_resolving_queries(monkeypatch):
    from features.query_registry.cli.command import QueryCommand
    from shared.cli import ai_access as cli_ai_access

    monkeypatch.setattr(
        cli_ai_access, "ensure_cli_ai_access", lambda **_kwargs: MISSING
    )
    command = QueryCommand()
    monkeypatch.setattr(
        command,
        "_resolve_queries",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resolved")),
    )

    result = command.run(queries=["q1"], analyze=True, quiet=True)

    assert not result.ok
    assert result.data["code"] == "LOGIN_REQUIRED"


def test_intent_guard_blocks_before_derivation(monkeypatch):
    from features.guard.cli.command import GuardCommand
    from shared.cli import ai_access as cli_ai_access

    monkeypatch.setattr(
        cli_ai_access, "ensure_cli_ai_access", lambda **_kwargs: MISSING
    )
    command = GuardCommand()

    result = command._create_from_intent("safe", "mask customer PII", None)

    assert not result.ok
    assert result.data["code"] == "LOGIN_REQUIRED"


def test_agent_chat_reports_claude_specific_requirement(monkeypatch):
    from features.agent.cli.command import AgentCommand
    from shared.cli import ai_access as cli_ai_access

    monkeypatch.setattr(
        cli_ai_access,
        "ensure_cli_ai_access",
        lambda **_kwargs: CLAUDE_REQUIRED,
    )
    command = AgentCommand()
    manager = MagicMock()
    manager.get.return_value = MagicMock()
    command._manager = manager

    result = command._chat(name="sales")

    assert not result.ok
    assert result.data == {
        "code": "ANTHROPIC_KEY_REQUIRED",
        "state": "claude_required",
    }


def test_audit_without_insights_bypasses_ai_gate(monkeypatch):
    from features.audit.cli import command as audit_command
    from features.audit.cli.command import AuditCommand

    monkeypatch.setattr(
        audit_command,
        "ensure_cli_ai_access",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("gated")),
    )
    target_config = {"engine": "postgresql", "host": "localhost"}
    config = MagicMock()
    config.get.return_value = target_config
    monkeypatch.setattr("shared.config.targets.TargetsConfig", lambda: config)
    audit_result = SimpleNamespace(error="stop after gate")
    monkeypatch.setattr(
        audit_command.AuditService,
        "audit_target",
        lambda *_args, **_kwargs: audit_result,
    )
    args = SimpleNamespace(
        target="prod",
        output_json=True,
        no_insights=True,
        no_save=True,
        verbose=False,
        auto_yes=False,
    )

    result = AuditCommand().execute(args)

    assert not result.ok
    assert result.message == "stop after gate"


def test_audit_without_insights_never_runs_health_llm(monkeypatch):
    from features.audit.cli import command as audit_command
    from features.audit.cli.command import AuditCommand
    from features.audit.models import AuditResult

    config = MagicMock()
    config.get.return_value = {"engine": "postgresql", "host": "localhost"}
    monkeypatch.setattr("shared.config.targets.TargetsConfig", lambda: config)
    monkeypatch.setattr(
        audit_command.AuditService,
        "audit_target",
        lambda *_args, **_kwargs: AuditResult(
            target_name="prod",
            engine="postgresql",
            host="localhost",
        ),
    )
    monkeypatch.setattr(
        audit_command.AuditService,
        "run_health_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("used LLM")),
    )
    args = SimpleNamespace(
        target="prod",
        output_json=True,
        no_insights=True,
        no_save=True,
        verbose=False,
        auto_yes=False,
        duration=None,
    )

    result = AuditCommand().execute(args)

    assert result.ok


def test_audit_with_insights_blocks_before_database_work(monkeypatch):
    from features.audit.cli import command as audit_command
    from features.audit.cli.command import AuditCommand

    monkeypatch.setattr(
        audit_command, "ensure_cli_ai_access", lambda **_kwargs: MISSING
    )
    config = MagicMock()
    config.get.return_value = {"engine": "postgresql", "host": "localhost"}
    monkeypatch.setattr("shared.config.targets.TargetsConfig", lambda: config)
    monkeypatch.setattr(
        audit_command.AuditService,
        "audit_target",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("audited")),
    )
    args = SimpleNamespace(
        target="prod",
        output_json=True,
        no_insights=False,
        no_save=True,
        verbose=False,
        auto_yes=False,
    )

    result = AuditCommand().execute(args)

    assert not result.ok
    assert result.data["code"] == "LOGIN_REQUIRED"


def test_fleet_audit_with_insights_blocks_before_connectivity(monkeypatch):
    from features.fleet.cli import command as fleet_command
    from features.fleet.cli.command import FleetCommand

    monkeypatch.setattr(
        fleet_command, "ensure_cli_ai_access", lambda **_kwargs: MISSING
    )
    config = MagicMock()
    config.list_fleet_targets.return_value = ["prod"]
    monkeypatch.setattr(fleet_command, "TargetsConfig", lambda: config)
    args = SimpleNamespace(
        group=None,
        tag=None,
        save_name=None,
        no_save=True,
        output_json=True,
        no_insights=False,
        duration=None,
        verbose=False,
        auto_yes=False,
    )

    result = FleetCommand()._handle_audit(args)

    assert not result.ok
    assert result.data["code"] == "LOGIN_REQUIRED"


def test_llm_schema_annotation_blocks_before_annotator(monkeypatch):
    from features.schema.cli.command import SchemaCommand
    from shared.cli import ai_access as cli_ai_access

    monkeypatch.setattr(
        cli_ai_access, "ensure_cli_ai_access", lambda **_kwargs: MISSING
    )
    manager = MagicMock()
    manager.load_or_create.return_value = SimpleNamespace(tables={"users": object()})
    command = SchemaCommand(manager=manager)

    result = command.annotate(
        "prod",
        use_llm=True,
        auto_accept=True,
        target_config={"engine": "postgresql"},
    )

    assert not result["ok"]
    assert result["data"]["code"] == "LOGIN_REQUIRED"


def test_analyze_review_does_not_require_ai(monkeypatch):
    from features.analyze.cli import command as analyze_command
    from features.analyze.cli.command import AnalyzeCommand, AnalyzeInput

    command = AnalyzeCommand()
    monkeypatch.setattr(
        command,
        "_check_api_key_configured",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("gated")),
    )
    registry = MagicMock()
    registry.conversation_exists.return_value = False
    monkeypatch.setattr(analyze_command, "ConversationRegistry", lambda: registry)
    query = AnalyzeInput(
        sql="SELECT 1",
        normalized_sql="SELECT ?",
        source="inline",
        hash="abc123",
    )

    result = command._execute_analyze_impl(query, review=True)

    assert not result.ok
    assert "No conversation found" in result.message


def test_analyze_blocks_with_structured_login_error(monkeypatch):
    from features.analyze.cli.command import AnalyzeCommand, AnalyzeInput
    from shared.cli import ai_access as cli_ai_access

    monkeypatch.setattr(
        cli_ai_access, "ensure_cli_ai_access", lambda **_kwargs: MISSING
    )
    config = MagicMock()
    config.get.return_value = {"engine": "postgresql"}
    monkeypatch.setattr("shared.config.targets.TargetsConfig", lambda: config)
    query = AnalyzeInput(
        sql="SELECT 1",
        normalized_sql="SELECT ?",
        source="inline",
        hash="abc123",
    )

    result = AnalyzeCommand()._execute_analyze_impl(
        query,
        target="prod",
        output_json=True,
    )

    assert not result.ok
    assert result.data == {"code": "LOGIN_REQUIRED", "state": "missing"}


def test_slack_start_accepts_the_standard_ai_gate(monkeypatch):
    from features.slack.cli import command as slack_command
    from features.slack.cli.command import SlackCommand
    from shared.cli import ai_access as cli_ai_access

    observed = {}

    def gate(**kwargs):
        observed.update(kwargs)
        return CLAUDE_REQUIRED

    monkeypatch.setattr(cli_ai_access, "ensure_cli_ai_access", gate)
    agent = SimpleNamespace(
        name="sales",
        workspace_id="workspace",
        target="prod",
        max_rows=50,
        timeout_seconds=30,
    )
    credential = SimpleNamespace(workspace_name="Readyset", workspace_id="workspace")
    monkeypatch.setattr(slack_command, "load_agent_config", lambda _name: agent)
    monkeypatch.setattr(
        slack_command,
        "load_credentials",
        lambda _workspace=None: {"workspace": credential},
    )

    result = SlackCommand()._start(agent="sales")

    assert not result.ok
    assert result.data["code"] == "ANTHROPIC_KEY_REQUIRED"
    assert observed["require_claude"] is True


def test_json_cli_failure_exposes_stable_access_code(capsys):
    import rdst

    rdst._print_failed_result(
        RdstResult(
            False,
            "AI access is required.",
            data={"code": "LOGIN_REQUIRED", "state": "missing"},
        ),
        SimpleNamespace(json=False, output_json=True, output=None),
    )

    output = json.loads(capsys.readouterr().err)
    assert output == {
        "error": "AI access is required.",
        "code": "LOGIN_REQUIRED",
        "state": "missing",
    }
