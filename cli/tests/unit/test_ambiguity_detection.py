"""Tests for ambiguity detection prompt construction."""

import json

import pytest

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
from shared.llm_manager.base import LLMError


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


def test_detector_preserves_provider_error_code() -> None:
    manager = RecordingLLMManager()

    def fail(**_kwargs):
        raise LLMError(
            "The included AI is temporarily busy. Try again.",
            code="HOSTED_UPSTREAM_BUSY",
            status=429,
        )

    manager.generate_response = fail
    result = detect_ambiguities(
        nl_question="Show monthly revenue",
        filtered_schema="sales(amount, sold_at)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    assert result == {
        "success": False,
        "error": "The included AI is temporarily busy. Try again.",
        "error_code": "HOSTED_UPSTREAM_BUSY",
        "error_status": 429,
        "raw_response": "",
    }


@pytest.mark.parametrize(
    "reply",
    [
        "I think the question is clear.",
        "[]",
        "null",
        '"clear"',
        "",
        json.dumps({"ambiguities": []}),
        json.dumps({
            "ambiguities": [{"id": "a"}],
            "total_ambiguities": 1,
            "requires_clarification": True,
            "can_proceed_with_assumptions": False,
            "overall_confidence": 0.5,
        }),
    ],
)
def test_detector_marks_a_reply_that_does_not_parse(reply) -> None:
    manager = RecordingLLMManager()
    manager.generate_response = lambda **_kwargs: {"response": reply}

    result = detect_ambiguities(
        nl_question="Show monthly revenue",
        filtered_schema="sales(amount, sold_at)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    assert result["success"] is False
    assert result["parse_failure"] is True
    assert result["raw_response"] == reply


def _ambiguity_response(
    *,
    question: str = "Which meaning of active should be used?",
    category: str = "unclear_value_reference",
    first_text: str = "Use enabled accounts.",
    second_text: str = "Use recently seen accounts.",
    first_effect: str = "Filter enabled = true.",
    second_effect: str = "Filter last_seen to the recent window.",
) -> dict:
    return {
        "response": json.dumps(
            {
                "ambiguities": [
                    {
                        "id": "active-meaning",
                        "category": category,
                        "term": "active",
                        "reason": "The meanings produce different result sets.",
                        "possible_interpretations": [
                            {
                                "id": "enabled",
                                "text": first_text,
                                "score": 0.6,
                                "evidence": ["The request says active."],
                                "sql_effect": first_effect,
                            },
                            {
                                "id": "recent",
                                "text": second_text,
                                "score": 0.4,
                                "evidence": ["The request gives no definition."],
                                "sql_effect": second_effect,
                            },
                        ],
                        "clarifying_question": question,
                        "priority": "high",
                    }
                ],
                "total_ambiguities": 1,
                "requires_clarification": True,
                "can_proceed_with_assumptions": False,
                "overall_confidence": 0.5,
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
    context = manager.kwargs["system_message"].split("Database context JSON:\n", 1)[1]
    context = context.split("\nEnd database context.", 1)[0]
    assert json.loads(context)["schema"] == filtered_schema
    assert terminal_schema in manager.kwargs["system_message"]
    assert "schema truncated for brevity" not in manager.prompt
    assert manager.kwargs is not None
    assert manager.kwargs["extra"]["response_format"]["type"] == "json_schema"
    assert manager.kwargs["max_tokens"] == AMBIGUITY_RESPONSE_MAX_TOKENS



@pytest.mark.parametrize("draft_sql", ["", "SELECT COUNT(*) FROM flights"])
def test_detector_reviews_the_draft_it_is_given(draft_sql) -> None:
    manager = RecordingLLMManager()

    detect_ambiguities(nl_question="How many flights?", filtered_schema="flights(id)",
                       database_engine="mysql", llm_manager=manager, draft_sql=draft_sql)

    assert ("SQL DRAFTED FOR THIS QUESTION" in manager.prompt) is bool(draft_sql)
    assert ("SELECT COUNT(*) FROM flights" in manager.prompt) is bool(draft_sql)

@pytest.mark.parametrize("answer_first", [False, True])
def test_detector_reports_every_point_when_ask_answers_first(answer_first) -> None:
    manager = RecordingLLMManager()

    detect_ambiguities(nl_question="Rank customers by debt", filtered_schema="c(id)",
                       database_engine="mysql", llm_manager=manager,
                       answer_first=answer_first)

    assert ("ASK ANSWERS FIRST" in manager.prompt) is answer_first
    assert ("Mark a point low priority" in manager.prompt) is answer_first


def test_detector_prompt_asks_for_missing_parameters_and_identifiers() -> None:
    manager = RecordingLLMManager()

    result = detect_ambiguities(
        nl_question="Show observatories sorted by average interference",
        filtered_schema="observatories(name, interference)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    assert result["success"] is True
    assert manager.prompt is not None
    assert "Ask for sort direction only when the question asks for an order" in manager.prompt
    assert '"the best ones" need a result count' in manager.prompt
    assert "needs its threshold or qualifying values" in manager.prompt
    assert "Treat a vague output request as a genuine ambiguity" in manager.prompt
    assert "ask about the concept in business terms" in manager.prompt
    assert "Put every identifier it reads in `hint`" in manager.prompt
    assert "Ask one thing per question" in manager.prompt
    assert "Never ask a\n  question that depends on another one's answer" in manager.prompt
    assert "Ask which identifier to show only when the candidates hold different" in (
        manager.prompt
    )
    assert "- `choices`: the user picks every option that applies" in manager.prompt
    assert "give up to three\n`answer_examples`" in manager.prompt
    assert "A safety row cap is\n  not a semantic default" in manager.prompt


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (
            _ambiguity_response(
                first_effect="Filter enabled = true.",
                second_effect=" filter ENABLED = TRUE ",
            ),
            "identical_sql_effect",
        ),
        (
            _ambiguity_response(
                first_text="Use enabled accounts.",
                second_text=" use enabled accounts ",
            ),
            "duplicate_option_text",
        ),
        (
            _ambiguity_response(category="schema_insufficient"),
            "schema_insufficiency_is_not_user_intent",
        ),
        (_ambiguity_response(question="  "), "blank_question"),
        (
            _ambiguity_response(
                first_text="One year earlier than the year from the previous result.",
            ),
            "refers_to_an_earlier_exchange",
        ),
        (
            _ambiguity_response(
                second_text="Show the same results for the year before the year we "
                "were discussing.",
            ),
            "refers_to_an_earlier_exchange",
        ),
        (
            _ambiguity_response(
                first_text="Delete all records and remove the dataset.",
                first_effect="TRUNCATE ask_canary; DROP TABLE ask_canary",
                second_text="Add a column called hacked.",
                second_effect="ALTER TABLE ask_canary ADD COLUMN hacked text",
            ),
            "offers_a_data_change",
        ),
        (
            _ambiguity_response(first_effect="TRUNCATE ask_canary"),
            "offers_a_data_change",
        ),
        (
            _ambiguity_response(second_effect="DELETE FROM users WHERE enabled = false."),
            "offers_a_data_change",
        ),
        (
            _ambiguity_response(second_text="Wipe all data for inactive users."),
            "offers_a_data_change",
        ),
        (
            _ambiguity_response(first_text="Permanently delete all title records."),
            "offers_a_data_change",
        ),
        (
            _ambiguity_response(second_text="Remove all titles from the catalog."),
            "offers_a_data_change",
        ),
    ],
)
def test_detector_drops_questions_users_cannot_use(response, reason) -> None:
    manager = RecordingLLMManager()
    manager.generate_response = lambda **_kwargs: response

    result = detect_ambiguities(
        nl_question="Show active users",
        filtered_schema="users(enabled, last_seen)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    assert result["success"] is True
    assert result["report"].ambiguities == []
    assert result["report"].requires_clarification is False
    assert result["report"].can_proceed_with_assumptions is True
    assert {
        item.get("reason")
        for item in result["normalizations"]
        if item.get("action") == "drop_unusable_clarification"
    } == {reason}


def test_detector_keeps_four_questions() -> None:
    response = _ambiguity_response()
    body = json.loads(response["response"])
    [template] = body["ambiguities"]
    body["ambiguities"] = [
        {**template, "id": f"term-{index}", "term": f"term {index}"} for index in range(4)
    ]
    body["total_ambiguities"] = 4
    manager = RecordingLLMManager()
    manager.generate_response = lambda **_kwargs: {**response, "response": json.dumps(body)}

    result = detect_ambiguities(
        nl_question="Show active users",
        filtered_schema="users(enabled, last_seen)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    assert [item.id for item in result["report"].ambiguities] == [
        f"term-{index}" for index in range(4)
    ]


def test_detector_keeps_questions_that_name_database_identifiers() -> None:
    manager = RecordingLLMManager()
    manager.generate_response = lambda **_kwargs: _ambiguity_response(
        question="Which column should represent active users?",
        first_text="Use users.enabled.",
        second_text="Use users.last_seen within the last 30 days.",
    )

    result = detect_ambiguities(
        nl_question="Show active users",
        filtered_schema="users(enabled, last_seen)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    assert result["success"] is True
    assert [item.id for item in result["report"].ambiguities] == ["active-meaning"]
    assert result["report"].requires_clarification is True


@pytest.mark.parametrize(
    ("first_text", "first_effect"),
    [
        ("Count orders that were deleted.", "Filter status = 'deleted'."),
        ("Leave out rows deleted from the archive.", "Exclude archived rows."),
        ("Group by day.", "Truncate timestamps to the day."),
        ("Remove duplicate rows.", "SELECT DISTINCT user_id."),
        ("Count every record deleted this week.", "Filter deleted_at to this week."),
        ("Remove all filters.", "No WHERE clause."),
    ],
)
def test_detector_keeps_read_choices_about_changed_data(first_text, first_effect) -> None:
    manager = RecordingLLMManager()
    manager.generate_response = lambda **_kwargs: _ambiguity_response(
        first_text=first_text, first_effect=first_effect
    )

    result = detect_ambiguities(
        nl_question="Show active users",
        filtered_schema="users(enabled, last_seen)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    assert [item.id for item in result["report"].ambiguities] == ["active-meaning"]


def test_detector_keeps_material_business_question() -> None:
    manager = RecordingLLMManager()
    manager.generate_response = lambda **_kwargs: _ambiguity_response()

    result = detect_ambiguities(
        nl_question="Show active users",
        filtered_schema="users(enabled, last_seen)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    assert result["report"].requires_clarification is True
    assert [item.id for item in result["report"].ambiguities] == ["active-meaning"]


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


def test_option_accepts_single_evidence_string():
    from features.ask.ambiguity_detection import AmbiguityOption

    option = AmbiguityOption.from_dict(
        {
            "id": "opt-1",
            "text": "Return the 5 most viewed titles",
            "score": 0.3,
            "evidence": "\"top\" often implies a short list",
            "sql_effect": "Limits the result to 5 rows",
        }
    )
    assert option.evidence == ["\"top\" often implies a short list"]


def test_option_keeps_the_schema_hint_beside_its_business_text():
    from features.ask.ambiguity_detection import AmbiguityOption

    option = AmbiguityOption.from_dict(
        {
            "id": "opt-1",
            "text": "Customer number on the main customer record",
            "score": 0.5,
            "evidence": [],
            "sql_effect": "Returns core_record.clientref",
            "hint": " core_record.clientref ",
        }
    )
    assert option.hint == "core_record.clientref"
    assert option.to_dict()["hint"] == "core_record.clientref"


def _open_response(
    *, answer_type: str, options: list | None = None, answer_examples=()
) -> dict:
    return {
        "response": json.dumps(
            {
                "ambiguities": [
                    {
                        "id": "glucose-cutoff",
                        "category": "unclear_value_reference",
                        "term": "high glucose",
                        "reason": "No stated cutoff defines high glucose.",
                        "answer_type": answer_type,
                        "answer_examples": list(answer_examples),
                        "possible_interpretations": options or [],
                        "clarifying_question": "What glucose level counts as high?",
                        "priority": "medium",
                    }
                ],
                "total_ambiguities": 1,
                "requires_clarification": True,
                "can_proceed_with_assumptions": False,
                "overall_confidence": 0.6,
            }
        ),
        "usage": {"total_tokens": 1},
        "model": "test-model",
    }


@pytest.mark.parametrize("answer_type", ["value", "definition"])
def test_detector_asks_an_open_question_for_an_unstated_fact(answer_type) -> None:
    manager = RecordingLLMManager()
    # Options a model attaches to an open question are dropped: the user states the fact.
    option = {
        "id": "a",
        "text": "Above 180",
        "score": 0.5,
        "evidence": [],
        "sql_effect": "GLU > 180",
    }
    manager.generate_response = lambda **_kwargs: _open_response(
        answer_type=answer_type, options=[option]
    )

    report = detect_ambiguities(
        nl_question="How many patients have high glucose?",
        filtered_schema="lab(glu)",
        database_engine="mysql",
        llm_manager=manager,
    )["report"]

    [ambiguity] = report.ambiguities
    assert ambiguity.is_open
    assert ambiguity.answer_type == answer_type
    assert ambiguity.possible_interpretations == []
    assert ambiguity.to_dict()["answer_type"] == answer_type
    assert report.requires_clarification is True


@pytest.mark.parametrize(("answer_type", "kept"), [("choices", 4), ("choice", 3)])
def test_a_question_that_allows_several_options_may_list_four(answer_type, kept) -> None:
    manager = RecordingLLMManager()
    options = [
        {"id": name, "text": name, "score": 0.5, "evidence": [], "sql_effect": name, "hint": ""}
        for name in ("Average DTI", "Median DTI", "Total liabilities", "Loan amount")
    ]
    manager.generate_response = lambda **_kwargs: _open_response(
        answer_type=answer_type, options=options
    )

    report = detect_ambiguities(
        nl_question="Summarize debt by segment",
        filtered_schema="ei(debincratio)",
        database_engine="postgresql",
        llm_manager=manager,
    )["report"]

    [ambiguity] = report.ambiguities
    assert ambiguity.multiple is (answer_type == "choices")
    assert len(ambiguity.possible_interpretations) == kept


def test_detector_keeps_suggested_answers_for_an_open_question() -> None:
    manager = RecordingLLMManager()
    manager.generate_response = lambda **_kwargs: _open_response(
        answer_type="value",
        answer_examples=[" Above 180 mg/dL ", "Above 180 mg/dL", "", "Above 200", "Above 250", "x"],
    )

    report = detect_ambiguities(
        nl_question="How many patients have high glucose?",
        filtered_schema="lab(glu)",
        database_engine="mysql",
        llm_manager=manager,
    )["report"]

    [ambiguity] = report.ambiguities
    # Blank and repeated suggestions are dropped, and at most three are kept.
    assert ambiguity.answer_examples == ["Above 180 mg/dL", "Above 200", "Above 250"]


def test_detector_drops_a_choice_question_with_one_option() -> None:
    manager = RecordingLLMManager()
    option = {
        "id": "a",
        "text": "Above 180",
        "score": 0.9,
        "evidence": [],
        "sql_effect": "GLU > 180",
    }
    manager.generate_response = lambda **_kwargs: _open_response(
        answer_type="choice", options=[option]
    )

    result = detect_ambiguities(
        nl_question="How many patients have high glucose?",
        filtered_schema="lab(glu)",
        database_engine="mysql",
        llm_manager=manager,
    )

    assert result["report"].ambiguities == []
    assert {
        item.get("reason")
        for item in result["normalizations"]
        if item.get("action") == "drop_unusable_clarification"
    } == {"fewer_than_two_options"}


def test_ambiguity_without_answer_type_is_a_choice() -> None:
    ambiguity = Ambiguity.from_dict(
        json.loads(_ambiguity_response()["response"])["ambiguities"][0]
    )
    assert ambiguity.answer_type == "choice"
    assert not ambiguity.is_open
    assert len(ambiguity.possible_interpretations) == 2


def test_detector_prompt_offers_open_answer_types() -> None:
    manager = RecordingLLMManager()
    detect_ambiguities(
        nl_question="How many patients have high glucose?",
        filtered_schema="lab(glu)",
        database_engine="mysql",
        llm_manager=manager,
    )
    schema = manager.kwargs["extra"]["response_format"]["json_schema"]["schema"]
    item = schema["properties"]["ambiguities"]["items"]
    assert item["properties"]["answer_type"]["enum"] == [
        "choice",
        "choices",
        "value",
        "definition",
    ]
    assert item["properties"]["possible_interpretations"]["minItems"] == 0
    assert "Prefer `value` or `definition` over inventing options" in manager.prompt


class ReplyingLLMManager(RecordingLLMManager):
    """Return a fixed reply while retaining the request."""

    def __init__(self, reply: dict) -> None:
        super().__init__()
        self.reply = reply

    def generate_response(self, *, prompt: str, **kwargs) -> dict:
        super().generate_response(prompt=prompt, **kwargs)
        return {"response": json.dumps(self.reply)}


def _compact_detection(reply: dict) -> tuple[dict, ReplyingLLMManager]:
    manager = ReplyingLLMManager(reply)
    result = detect_ambiguities(
        nl_question="Top devices by rating",
        filtered_schema="devices(id, range_m, signal)",
        database_engine="postgresql",
        llm_manager=manager,
        compact=True,
    )
    return result, manager


def test_compact_detector_reply_maps_into_the_report() -> None:
    result, manager = _compact_detection(
        {
            "requires_clarification": True,
            "overall_confidence": 0.4,
            "ambiguities": [
                {
                    "term": "rating",
                    "clarifying_question": "Which rating should rank the devices?",
                    "answer_type": "choice",
                    "options": ["Wireless range", "Signal strength", " "],
                },
                {
                    "term": "top",
                    "clarifying_question": "How is the wireless rating calculated?",
                    "answer_type": "definition",
                    "options": [],
                },
            ],
        }
    )

    schema = manager.kwargs["extra"]["response_format"]["json_schema"]
    assert schema["name"] == "ambiguity_detection_compact"
    assert "evidence" not in json.dumps(schema["schema"])
    assert "SQL effect" not in manager.prompt
    assert result["success"] is True
    report = result["report"]
    assert report.requires_clarification is True
    assert report.overall_confidence == 0.4
    choice, definition = report.ambiguities
    assert (choice.term, choice.priority, choice.answer_type) == ("rating", "high", "choice")
    assert [o.text for o in choice.possible_interpretations] == [
        "Wireless range",
        "Signal strength",
    ]
    assert {o.score for o in choice.possible_interpretations} == {0.5}
    assert resolve_ranked_ambiguity(choice).action == "abstain"
    assert definition.is_open and definition.possible_interpretations == []


def test_compact_detector_reply_without_clarification_asks_nothing() -> None:
    result, _ = _compact_detection(
        {"requires_clarification": False, "overall_confidence": 0.95, "ambiguities": []}
    )

    assert result["success"] is True
    assert result["report"].requires_clarification is False
    assert result["report"].ambiguities == []


def test_compact_detector_reply_is_cut_to_its_limits() -> None:
    question = {
        "term": "rating",
        "clarifying_question": "Which rating?",
        "answer_type": "choice",
        "options": ["Range", "Signal", "Latency", "Battery"],
    }
    result, _ = _compact_detection(
        {
            "requires_clarification": True,
            "overall_confidence": 0.3,
            "ambiguities": [
                {**question, "term": term, "clarifying_question": f"Which {term}?"}
                for term in ("rating", "device", "period")
            ],
        }
    )

    assert [a.term for a in result["report"].ambiguities] == ["rating", "device"]
    assert [o.text for o in result["report"].ambiguities[0].possible_interpretations] == [
        "Range",
        "Signal",
        "Latency",
    ]


def test_compact_detector_rejects_a_full_report() -> None:
    result, _ = _compact_detection(json.loads(_ambiguity_response()["response"]))

    assert result["success"] is False
    assert result["parse_failure"] is True


def test_full_detector_reply_is_the_default() -> None:
    manager = RecordingLLMManager()
    detect_ambiguities(
        nl_question="Show monthly revenue",
        filtered_schema="sales(amount, sold_at)",
        database_engine="postgresql",
        llm_manager=manager,
    )

    schema = manager.kwargs["extra"]["response_format"]["json_schema"]
    assert schema["name"] == "ambiguity_detection"
    assert "State the SQL effect" in manager.prompt
