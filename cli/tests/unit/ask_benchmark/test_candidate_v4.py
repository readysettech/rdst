from devtools.ask_benchmark.cli import build_parser
from devtools.ask_benchmark.runner import ask_profile_service_flags
from features.ask.service import AskService
from features.ask.sql_validation import validate_filter_literal_provenance


def test_candidate_v4_constructs_service_with_frozen_flags():
    flags = ask_profile_service_flags("candidate-v4")
    assert len(flags) == 40
    assert sum(flags.values()) == 28
    assert flags["projection_contract_enabled"] is True
    assert flags["declared_count_enabled"] is False
    assert flags["dual_candidate_selection_enabled"] is False
    AskService(llm_manager=object(), **flags)
    flags["projection_contract_enabled"] = False
    assert ask_profile_service_flags("candidate-v4")["projection_contract_enabled"]


def test_ci_profile_is_accepted_without_changing_default():
    parser = build_parser()
    base = ["run", "--models", "claude-sonnet-4.6-subscription-medium"]
    assert parser.parse_args(base).ask_accuracy_profile == "baseline"
    args = parser.parse_args([
        *base, "--track", "rdst-ask", "--ask-accuracy-profile", "candidate-v4"
    ])
    assert args.ask_accuracy_profile == "candidate-v4"


def test_candidate_enum_overlap_is_advisory_without_changing_default():
    arguments = {
        "sql": "SELECT name FROM schools WHERE name = 'Special'",
        "question": "Find Special schools",
        "schema_formatted": "Table: schools\n  name (text)\n  kind (enum) [enum: Special]\n",
        "dialect": "mysql",
    }
    assert not validate_filter_literal_provenance(**arguments)["is_valid"]
    result = validate_filter_literal_provenance(
        **arguments, enum_overlap_advisory=True
    )
    assert result["is_valid"]
    assert result["warnings"]
