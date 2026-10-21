import json
from datetime import date
from decimal import Decimal

import pytest

from devtools.ask_benchmark.bird_interact_lite import (
    SIMULATOR_POLICY,
    SIMULATOR_SYSTEM_PROMPT,
    case_for,
    follow_up_context,
    knowledge_text,
    load_tasks,
    masked_ids,
    official_sql,
    reward,
    rows_match,
    simulator_message,
    solution_sql,
    user_words,
)
from devtools.ask_benchmark.oracle_user import (
    UNANSWERABLE_ANSWER,
    OracleUserSimulator,
    cache_key,
)
from features.ask.models import AskClarificationQuestion

KNOWLEDGE = [
    {
        "id": 0,
        "knowledge": "SNQI",
        "description": "Signal quality",
        "definition": "SNR - 0.1 x |noise|",
        "type": "calc",
    },
    {
        "id": 1,
        "knowledge": "AOI",
        "description": "Observability",
        "definition": "1 - cloud cover",
        "type": "calc",
    },
]


def _task(**overrides):
    task = {
        "instance_id": "alien_1",
        "selected_database": "alien",
        "category": "Query",
        "amb_user_query": "How does signal quality vary by weather?",
        "query": "How does SNQI vary by weather condition?",
        "sol_sql": ["SELECT 1"],
        "conditions": {"decimal": 2, "distinct": False, "order": True},
        "user_query_ambiguity": {
            "critical_ambiguity": [
                {
                    "term": "signal quality",
                    "sql_snippet": "SnrRatio - 0.1 * ABS(NoiseFloorDbm)",
                    "is_mask": True,
                    "type": "knowledge_linking_ambiguity",
                }
            ],
            "non_critical_ambiguity": [],
        },
        "knowledge_ambiguity": [
            {
                "term": "SNQI",
                "sql_snippet": "SnrRatio - 0.1 * ABS(NoiseFloorDbm)",
                "is_mask": False,
                "type": "knowledge_ambiguity",
                "deleted_knowledge": 0,
            }
        ],
        "follow_up": {
            "query": "And by observatory?",
            "sol_sql": "SELECT 2",
            "conditions": {"order": False},
        },
    }
    task.update(overrides)
    return task


def test_load_tasks_keeps_select_tasks_and_drops_excluded(tmp_path):
    path = tmp_path / "tasks.jsonl"
    rows = [
        _task(),
        _task(instance_id="alien_2"),
        _task(instance_id="alien_M_1", category="Management"),
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    assert [task["instance_id"] for task in load_tasks(path, exclude={"alien_2"})] == [
        "alien_1"
    ]


def test_solution_sql_accepts_string_or_single_statement_list():
    task = _task()
    assert solution_sql(task) == "SELECT 1"
    assert solution_sql(task, "follow_up") == "SELECT 2"
    with pytest.raises(ValueError):
        solution_sql(_task(sol_sql=["SELECT 1", "SELECT 2"]))


def test_masked_knowledge_is_hidden_from_the_assistant():
    task = _task()
    assert masked_ids(task) == {0}
    text = knowledge_text(KNOWLEDGE, masked_ids(task))
    assert "AOI" in text and "SNQI" not in text
    assert '"type"' not in text


def test_simulator_message_carries_labels_hidden_knowledge_and_clear_question():
    message = simulator_message(
        _task(), KNOWLEDGE, "What do you mean by signal quality?", ["SNR", "SNQI"]
    )
    assert "signal quality" in message
    assert "SNR - 0.1 x |noise|" in message
    assert "AOI" not in message
    assert "How does SNQI vary by weather condition?" in message
    assert "1. SNR\n2. SNQI" in message


def test_follow_up_context_lists_the_earlier_exchange():
    context = follow_up_context(
        "KB", "Question one?", [("Which metric?", "SNQI")], "SELECT 1"
    )
    assert context.startswith("KB\n")
    assert "- Q: Which metric? A: SNQI" in context
    assert "SELECT 1" in context


def test_case_for_uses_the_phase_reference_query():
    case = case_for(
        _task(), 3, question="And by observatory?", context="ctx", phase="follow_up"
    )
    assert (case.question_id, case.db_id, case.gold_sql, case.evidence) == (
        3,
        "alien",
        "SELECT 2",
        "ctx",
    )


def test_official_sql_strips_comments_distinct_and_round():
    sql = "/* intent */ SELECT DISTINCT ROUND(AVG(x), 2) -- note\nFROM t"
    assert official_sql(sql) == "SELECT AVG(x) FROM t"


@pytest.mark.parametrize(
    ("pred", "gold", "conditions", "expected"),
    [
        ([(1, Decimal("2.004"))], [(1, 2.0)], {"order": True}, (True, None)),
        ([(2,), (1,)], [(1,), (2,)], {"order": True}, (False, None)),
        ([(2,), (1,)], [(1,), (2,)], {"order": False}, (True, None)),
        ([(date(2024, 1, 2),)], [("2024-01-02",)], None, (True, None)),
        ([], [(1,)], None, (False, "empty result")),
    ],
)
def test_rows_match_follows_the_official_rule(pred, gold, conditions, expected):
    assert rows_match(pred, gold, conditions) == expected


def test_reward_counts_follow_up_only_after_a_solved_first_question():
    assert reward(True, True) == pytest.approx(1.0)
    assert reward(True, False) == pytest.approx(0.7)
    assert reward(False, True) == 0.0


def test_simulator_uses_the_benchmark_prompt_and_reports_unanswerable():
    prompts = []

    def complete(system, user):
        prompts.append((system, user))
        return json.dumps({"option": None, "answer": UNANSWERABLE_ANSWER})

    task = _task()
    simulator = OracleUserSimulator(
        complete,
        policy=SIMULATOR_POLICY,
        system_prompt=SIMULATOR_SYSTEM_PROMPT,
        build_message=lambda case, question, options: simulator_message(
            task, KNOWLEDGE, question, options
        ),
    )
    case = case_for(task, 0, question=task["amb_user_query"], context="KB")
    question = AskClarificationQuestion(
        id="q1", question="Which year?", options=["2023", "2024"]
    )
    answers, records = simulator.answer(case, [question])
    assert answers == {"q1": UNANSWERABLE_ANSWER}
    assert records[0].reason == "unanswerable"
    assert prompts[0][0] == SIMULATOR_SYSTEM_PROMPT
    assert "Which year?" in prompts[0][1]
    assert simulator.describe()["policy"] == SIMULATOR_POLICY
    assert cache_key(
        0, "Which year?", ["2023", "2024"], simulator.model, SIMULATOR_POLICY
    ) != cache_key(0, "Which year?", ["2023", "2024"], simulator.model)


def test_simulator_prompt_forbids_options_that_differ_from_the_notes():
    assert "Never pick an option whose threshold, boundary, unit or category" in (
        SIMULATOR_SYSTEM_PROMPT
    )
    assert SIMULATOR_POLICY != "bird-interact-lite-user-v2"


def test_simulator_free_text_cap_is_configurable():
    definition = "SNQI is the SNR ratio minus a tenth of the absolute noise floor. " * 6
    reply = json.dumps({"option": None, "answer": definition.strip()})
    question = AskClarificationQuestion(id="q1", question="What is SNQI?", options=[])
    case = case_for(_task(), 0, question="q", context="KB")
    default = OracleUserSimulator(lambda system, user: reply)
    wide = OracleUserSimulator(lambda system, user: reply, max_free_text_chars=500)
    assert default.answer(case, [question])[1][0].reason == "too_long_blocked"
    assert wide.answer(case, [question])[1][0].reason == "free_text"


def test_simulator_leak_pattern_allows_formulas_but_not_queries():
    from devtools.ask_benchmark.bird_interact_lite import SIMULATOR_LEAK

    formula = "A usable signal has SNQI above 0, where SNQI = SnrRatio - 0.1 x |NoiseFloorDbm|."
    question = AskClarificationQuestion(id="q1", question="What is usable?", options=[])
    case = case_for(_task(), 0, question="q", context="KB")

    def reason(answer, **kwargs):
        reply = json.dumps({"option": None, "answer": answer})
        simulator = OracleUserSimulator(lambda system, user: reply, **kwargs)
        return simulator.answer(case, [question])[1][0].reason

    assert reason(formula) == "sql_leak_blocked"
    assert reason(formula, leak_pattern=SIMULATOR_LEAK) == "free_text"
    query = "Use SELECT snr FROM signals."
    assert reason(query, leak_pattern=SIMULATOR_LEAK) == "sql_leak_blocked"


def test_simulator_answers_in_business_terms_from_the_users_own_words():
    assert "Never name a table, column or other database identifier" in (
        SIMULATOR_SYSTEM_PROMPT
    )
    assert "naming the columns" not in SIMULATOR_SYSTEM_PROMPT
    words = user_words(_task())
    assert "signal quality" in words and "SNQI vary by weather" in words
