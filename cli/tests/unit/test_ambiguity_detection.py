"""Tests for ambiguity detection prompt construction."""

import json

from features.ask.ambiguity_detection import (
    AMBIGUITY_RESPONSE_MAX_TOKENS,
    Ambiguity,
    AmbiguityOption,
    AmbiguityReport,
    clarification_required_for_report,
    detect_ambiguities,
    detect_missing_intent_ambiguities,
    resolve_ranked_ambiguity,
)


class RecordingLLMManager:
    """Return a valid report while retaining the prompt sent to the model."""

    def __init__(self) -> None:
        self.prompt: str | None = None
        self.kwargs: dict | None = None

    def generate_response(self, *, prompt: str, **kwargs) -> dict:
        self.prompt = prompt
        self.kwargs = kwargs
        return {
            "response": json.dumps(
                {
                    "ambiguities": [],
                    "total_ambiguities": 0,
                    "requires_clarification": False,
                    "can_proceed_with_assumptions": True,
                    "overall_confidence": 1.0,
                }
            ),
            "usage": {"total_tokens": 1},
            "model": "test-model",
        }


def test_detect_ambiguities_includes_complete_filtered_schema() -> None:
    manager = RecordingLLMManager()
    terminal_schema = "CREATE TABLE relevant_table (important_column TEXT);"
    filtered_schema = f"{'x' * 10_000}\n{terminal_schema}"

    result = detect_ambiguities(
        nl_question="Use the important column",
        filtered_schema=filtered_schema,
        database_engine="postgresql",
        llm_manager=manager,
    )

    assert result["success"] is True
    assert manager.prompt is not None
    assert filtered_schema in manager.prompt
    assert terminal_schema in manager.prompt
    assert "schema truncated for brevity" not in manager.prompt
    assert manager.kwargs is not None
    assert manager.kwargs["extra"]["response_format"]["type"] == "json_schema"
    assert manager.kwargs["max_tokens"] == AMBIGUITY_RESPONSE_MAX_TOKENS


def test_detector_prompt_distinguishes_missing_intent_from_implementation() -> None:
    manager = RecordingLLMManager()

    result = detect_ambiguities(
        nl_question="Show observatories sorted by average interference",
        filtered_schema="observatories(name, interference)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    assert result["success"] is True
    assert manager.prompt is not None
    assert '"sorted by score" needs ascending versus' in manager.prompt
    assert '"top products" needs a result count' in manager.prompt
    assert "Do not ask the user to\n  choose tables, columns, joins" in manager.prompt
    assert "A safety row cap is\n  not a semantic default" in manager.prompt


def test_detector_adds_missing_sort_direction_when_model_misses_it() -> None:
    manager = RecordingLLMManager()

    result = detect_ambiguities(
        nl_question="Show observatories sorted by average interference.",
        filtered_schema="observatories(name, interference)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    report = result["report"]
    assert report.requires_clarification is True
    assert report.can_proceed_with_assumptions is False
    assert report.overall_confidence == 0.5
    assert len(report.ambiguities) == 1
    ambiguity = report.ambiguities[0]
    assert ambiguity.id == "intent-sort-direction"
    assert ambiguity.term == "sorted by average interference"
    assert [option.id for option in ambiguity.possible_interpretations] == [
        "intent-sort-descending",
        "intent-sort-ascending",
    ]
    assert {option.score for option in ambiguity.possible_interpretations} == {0.5}
    assert {item.get("action") for item in result["normalizations"]} >= {
        "add_deterministic_missing_intent"
    }


def test_missing_sort_direction_respects_explicit_or_implied_direction() -> None:
    assert (
        detect_missing_intent_ambiguities(
            "Show observatories sorted by average interference descending."
        )
        == []
    )
    assert (
        detect_missing_intent_ambiguities("Show the highest scores sorted by score.")
        == []
    )
    assert (
        detect_missing_intent_ambiguities(
            "Show observatories ordered by average interference."
        )[0].term
        == "ordered by average interference"
    )


def test_provided_context_is_authoritative_and_absent_when_empty() -> None:
    manager = RecordingLLMManager()
    context = "The caller defines active as enabled = true."

    result = detect_ambiguities(
        nl_question="Show active users",
        filtered_schema="users(enabled, last_seen)",
        database_engine="postgresql",
        llm_manager=manager,
        provided_context=context,
    )

    assert result["success"] is True
    assert manager.prompt is not None
    assert manager.prompt.count(context) == 1
    assert "AUTHORITATIVE CALLER-PROVIDED CONTEXT:" in manager.prompt
    assert "resolved constraints" in manager.prompt
    assert "Do not report an ambiguity whose SQL effect is determined" in manager.prompt

    no_context_manager = RecordingLLMManager()
    detect_ambiguities(
        nl_question="Show active users",
        filtered_schema="users(enabled, last_seen)",
        database_engine="postgresql",
        llm_manager=no_context_manager,
    )
    assert no_context_manager.prompt is not None
    assert "AUTHORITATIVE CALLER-PROVIDED CONTEXT:" not in no_context_manager.prompt
    assert "resolved constraints" not in no_context_manager.prompt


def test_detector_overrides_internally_inconsistent_proceed_decision() -> None:
    manager = RecordingLLMManager()
    manager.generate_response = lambda **_kwargs: {
        "response": json.dumps(
            {
                "ambiguities": [
                    {
                        "id": "status",
                        "category": "unclear_value_reference",
                        "term": "active",
                        "reason": "Two schema-backed meanings exist.",
                        "possible_interpretations": [
                            {
                                "id": "enabled",
                                "text": "The enabled flag is true.",
                                "score": 0.95,
                                "evidence": ["The table has an enabled column."],
                                "sql_effect": "Filter enabled = true.",
                            },
                            {
                                "id": "recent",
                                "text": "The user was recently active.",
                                "score": 0.4,
                                "evidence": ["The table has a last_seen column."],
                                "sql_effect": "Filter last_seen by time.",
                            },
                        ],
                        "clarifying_question": "Which meaning of active?",
                        "priority": "high",
                    }
                ],
                "total_ambiguities": 0,
                "requires_clarification": False,
                "can_proceed_with_assumptions": False,
                "overall_confidence": 0.8,
            }
        ),
        "usage": {"total_tokens": 1},
        "model": "test-model",
    }

    result = detect_ambiguities(
        nl_question="Show active users",
        filtered_schema="users(enabled, last_seen)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    assert result["success"] is True
    assert result["report"].total_ambiguities == 1
    assert result["report"].requires_clarification is True
    assert {item["field"] for item in result["normalizations"]} == {
        "requires_clarification",
        "total_ambiguities",
    }


def test_cumulative_medium_ambiguities_require_clarification() -> None:
    first = _ambiguity(
        [
            AmbiguityOption("a", "metric a", 0.7),
            AmbiguityOption("b", "metric b", 0.3),
        ]
    )
    second = _ambiguity(
        [
            AmbiguityOption("c", "source c", 0.7),
            AmbiguityOption("d", "source d", 0.3),
        ]
    )
    first.id = "metric"
    second.id = "source"
    report = AmbiguityReport(
        ambiguities=[first, second],
        total_ambiguities=2,
        requires_clarification=False,
        can_proceed_with_assumptions=True,
        overall_confidence=0.72,
    )

    assert clarification_required_for_report(report) is True

    report.ambiguities = [first]
    assert clarification_required_for_report(report) is False


def test_single_weakly_ranked_medium_ambiguity_requires_clarification() -> None:
    ambiguity = _ambiguity(
        [
            AmbiguityOption("school-name", "exact school name", 0.6),
            AmbiguityOption("school-type", "school category", 0.3),
        ]
    )
    report = AmbiguityReport(
        ambiguities=[ambiguity],
        total_ambiguities=1,
        requires_clarification=False,
        can_proceed_with_assumptions=True,
        overall_confidence=0.78,
    )

    assert clarification_required_for_report(report) is True


def test_single_well_ranked_medium_ambiguity_can_proceed() -> None:
    ambiguity = _ambiguity(
        [
            AmbiguityOption("school-type", "school category", 0.75),
            AmbiguityOption("school-name", "exact school name", 0.25),
        ]
    )
    report = AmbiguityReport(
        ambiguities=[ambiguity],
        total_ambiguities=1,
        requires_clarification=False,
        can_proceed_with_assumptions=True,
        overall_confidence=0.78,
    )

    assert clarification_required_for_report(report) is False


def _ambiguity(options: list[AmbiguityOption]) -> Ambiguity:
    return Ambiguity(
        id="status",
        category="unclear_value_reference",
        term="active",
        reason="multiple status meanings",
        possible_interpretations=options,
        clarifying_question="What does active mean?",
    )


def test_ranked_resolver_is_invariant_to_option_order() -> None:
    options = [
        AmbiguityOption("recent", "recent activity", 0.2),
        AmbiguityOption("enabled", "enabled flag", 0.96),
    ]

    forward = resolve_ranked_ambiguity(_ambiguity(options))
    reverse = resolve_ranked_ambiguity(_ambiguity(list(reversed(options))))

    assert forward.action == reverse.action == "select"
    assert forward.selected_option_id == reverse.selected_option_id == "enabled"


def test_ranked_resolver_abstains_on_tie_or_weak_margin() -> None:
    resolution = resolve_ranked_ambiguity(
        _ambiguity(
            [
                AmbiguityOption("enabled", "enabled flag", 0.91),
                AmbiguityOption("recent", "recent activity", 0.90),
            ]
        )
    )

    assert resolution.action == "abstain"
    assert resolution.selected_option_id is None
    assert resolution.reason == "ranking_margin_below_threshold"
