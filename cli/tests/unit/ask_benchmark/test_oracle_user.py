import json
from pathlib import Path

import pytest

from devtools.ask_benchmark.models import BenchmarkCase
from devtools.ask_benchmark.oracle_user import (
    ANY_OPTION_ANSWER,
    DONT_KNOW_ANSWER,
    ORACLE_SYSTEM_PROMPT,
    ORACLE_USER_KNOWLEDGE,
    ORACLE_USER_POLICY,
    OracleUserSimulator,
    build_hint_only_message,
    build_user_message,
    named_identifiers,
    oracle_report,
    parse_reply,
    schema_names,
)
from features.ask.models import AskClarificationQuestion


def _case(question_id=7):
    return BenchmarkCase(
        question_id=question_id,
        db_id="financial",
        question="How many districts had more crimes in 1996 than in 1995?",
        evidence="A15 refers to crimes in 1995; A16 refers to crimes in 1996",
        gold_sql="SELECT COUNT(*) FROM district WHERE A16 > A15",
        difficulty="simple",
        dialect="mysql",
    )


def _question(question_id="year", options=("A15", "A16")):
    return AskClarificationQuestion(
        id=question_id,
        question="Which column holds 1995 crimes?",
        options=list(options),
    )


class _Model:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, system, user):
        self.prompts.append((system, user))
        return self.replies.pop(0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"option": 1, "answer": ""}', ("A15", 1, "option")),
        ('Sure: {"option": 2}', ("A16", 2, "option")),
        (
            '{"option": null, "answer": "The 1995 crime count."}',
            ("The 1995 crime count.", None, "free_text"),
        ),
        (
            json.dumps({"option": None, "answer": ANY_OPTION_ANSWER}),
            (ANY_OPTION_ANSWER, None, "any_option"),
        ),
        ('{"option": 5, "answer": ""}', (None, None, "empty_reply")),
        (
            '{"option": null, "answer": "Use SELECT A15 FROM district."}',
            (None, None, "sql_leak_blocked"),
        ),
        (
            '{"option": null, "answer": "Use the `A15` column."}',
            (None, None, "sql_leak_blocked"),
        ),
        ("no json here", (None, None, "unparseable_reply")),
        ('{"option": true}', (None, None, "empty_reply")),
        (
            json.dumps({"option": None, "answer": DONT_KNOW_ANSWER}),
            (None, None, "dont_know"),
        ),
    ],
)
def test_parse_reply_maps_options_and_blocks_leaks(raw, expected):
    assert parse_reply(raw, ["A15", "A16"]) == expected


def test_user_message_carries_hint_gold_and_numbered_options():
    message = build_user_message(_case(), "Which column?", ["A15", "A16"])

    assert "A15 refers to crimes in 1995" in message
    assert "SELECT COUNT(*) FROM district WHERE A16 > A15" in message
    assert "1. A15\n2. A16" in message


def test_hint_only_user_sees_the_hint_but_not_the_gold_sql():
    message = build_hint_only_message(_case(), "Which column?", ["A15", "A16"])

    assert "A15 refers to crimes in 1995" in message
    assert "SELECT" not in message
    assert "1. A15\n2. A16" in message
    policies = {policy for policy, _, _ in ORACLE_USER_KNOWLEDGE.values()}
    assert len(policies) == len(ORACLE_USER_KNOWLEDGE)


def test_hint_only_user_leaves_undecided_questions_unanswered():
    policy, system_prompt, build_message = ORACLE_USER_KNOWLEDGE["hint-only"]
    model = _Model([json.dumps({"option": None, "answer": DONT_KNOW_ANSWER})])
    simulator = OracleUserSimulator(
        model, policy=policy, system_prompt=system_prompt, build_message=build_message
    )

    answers, records = simulator.answer(_case(), [_question()])

    assert answers == {}
    assert records[0].reason == "dont_know"
    assert "correct SQL" not in model.prompts[0][1]


def test_simulator_states_the_exact_rule_instead_of_a_close_option():
    # A close option ("65 and older" for "older than 65") would change the rows, so the
    # prompt forbids it and asks for the exact comparison, boundary, value and category.
    assert "Never pick an option whose threshold, boundary, unit or category" in (
        ORACLE_SYSTEM_PROMPT
    )
    assert "whether the boundary counts" in ORACLE_SYSTEM_PROMPT
    assert ORACLE_USER_POLICY != "oracle-user-v1"

    model = _Model(['{"option": null, "answer": "Strictly older than 65."}'])
    question = AskClarificationQuestion(
        id="age", question="Which patients count as elderly?", options=["65 and older"]
    )
    answers, records = OracleUserSimulator(model).answer(_case(), [question])
    assert answers == {"age": "Strictly older than 65."}
    assert records[0].reason == "free_text"


def test_simulator_answers_and_reuses_cache_across_instances(tmp_path: Path):
    cache = tmp_path / "cache.jsonl"
    model = _Model(['{"option": 1}'])
    simulator = OracleUserSimulator(model, cache_path=cache)

    answers, records = simulator.answer(_case(), [_question()])
    assert answers == {"year": "A15"}
    assert records[0].cached is False

    # Case and whitespace differences in the clarification text hit the same entry.
    again = AskClarificationQuestion(
        id="year",
        question="  which COLUMN holds 1995 crimes? ",
        options=["a15", "A16 "],
    )
    answers, records = simulator.answer(_case(), [again])
    assert answers == {"year": "a15"}
    assert records[0].cached is True

    reloaded = OracleUserSimulator(_Model([]), cache_path=cache)
    answers, records = reloaded.answer(_case(), [_question()])
    assert answers == {"year": "A15"}
    assert records[0].cached is True
    assert len(model.prompts) == 1


def test_runs_sharing_a_cache_give_every_model_the_first_answer(tmp_path: Path):
    cache = tmp_path / "cache.jsonl"
    first = OracleUserSimulator(_Model(['{"option": 1}']), cache_path=cache)
    # Both runs start before either has answered; a torn last line is skipped.
    cache.write_text('{"key": "cut short', encoding="utf-8")
    second = OracleUserSimulator(_Model([]), cache_path=cache)

    assert first.answer(_case(), [_question()])[0] == {"year": "A15"}
    answers, records = second.answer(_case(), [_question()])

    assert answers == {"year": "A15"}
    assert records[0].cached is True


def test_an_answer_written_while_this_run_waited_on_the_model_wins(tmp_path: Path):
    cache = tmp_path / "cache.jsonl"
    other = OracleUserSimulator(_Model(['{"option": 2}']), cache_path=cache)

    class AnswersAfterTheOtherRun:
        prompts = []

        def __call__(self, system, user):
            # The other run answers the same question while this one waits on its model.
            other.answer(_case(), [_question()])
            return '{"option": 1}'

    answers, records = OracleUserSimulator(
        AnswersAfterTheOtherRun(), cache_path=cache
    ).answer(_case(), [_question()])

    assert answers == {"year": "A16"}
    assert records[0].cached is True
    assert len(cache.read_text(encoding="utf-8").splitlines()) == 1


def test_simulator_cache_is_per_case():
    model = _Model(['{"option": 1}', '{"option": 2}'])
    simulator = OracleUserSimulator(model)

    assert simulator.answer(_case(1), [_question()])[0] == {"year": "A15"}
    assert simulator.answer(_case(2), [_question()])[0] == {"year": "A16"}


def test_simulator_skips_questions_past_its_budget_and_blocked_replies():
    model = _Model(['{"option": null, "answer": "SELECT A15 FROM district"}'])
    simulator = OracleUserSimulator(model, max_answers=1)

    answers, records = simulator.answer(_case(), [_question("a"), _question("b")])

    assert answers == {}
    assert [record.reason for record in records] == [
        "sql_leak_blocked",
        "over_question_budget",
    ]
    assert len(model.prompts) == 1


def test_simulator_rejects_a_nonpositive_budget():
    with pytest.raises(ValueError):
        OracleUserSimulator(_Model([]), max_answers=0)


def _write_run(
    directory: Path, correct: dict[int, bool], asked: dict[int, int] | None = None
):
    directory.mkdir(parents=True)
    lines = []
    for question_id, ok in correct.items():
        diagnostics = {}
        if asked and asked.get(question_id):
            exchanges = [{"reason": "option"}] * asked[question_id]
            diagnostics["clarification"] = {"oracle_rounds": [{"exchanges": exchanges}]}
        lines.append(
            json.dumps(
                {
                    "question_id": question_id,
                    "scored": True,
                    "execution_correct": ok,
                    "diagnostics": diagnostics,
                }
            )
        )
    (directory / "attempts.jsonl").write_text("\n".join(lines) + "\n")


def test_oracle_report_refuses_runs_it_cannot_compare_one_to_one(tmp_path: Path):
    _write_run(tmp_path / "none", {1: False})
    _write_run(tmp_path / "hints", {1: True})
    _write_run(tmp_path / "oracle", {1: True})
    second = tmp_path / "oracle" / "repetition-2"
    _write_run(second, {1: False})

    with pytest.raises(ValueError, match="more than one scored attempt of question 1"):
        oracle_report(tmp_path / "oracle", tmp_path / "none", tmp_path / "hints")

    other = tmp_path / "other-model"
    _write_run(other, {1: True})
    attempts = other / "attempts.jsonl"
    attempts.write_text(
        attempts.read_text().replace('"scored": true', '"scored": true, "model_name": "x"')
    )
    with pytest.raises(ValueError, match="different models"):
        oracle_report(other, tmp_path / "none", tmp_path / "hints")


def test_oracle_report_leaves_out_cases_the_leaderboard_leaves_out(tmp_path: Path):
    for name, ok in (("none", False), ("hints", True), ("oracle", True)):
        _write_run(tmp_path / name, {1: ok, 2: ok})
        attempts = tmp_path / name / "attempts.jsonl"
        lines = attempts.read_text().splitlines()
        unstable = json.loads(lines[1])
        unstable["diagnostics"]["headline_eligible"] = False
        attempts.write_text(lines[0] + "\n" + json.dumps(unstable) + "\n")

    report = oracle_report(tmp_path / "oracle", tmp_path / "none", tmp_path / "hints")

    assert report["questions"] == 1


def test_oracle_report_measures_gap_recovery_and_ask_quality(tmp_path: Path):
    _write_run(tmp_path / "none", {1: False, 2: False, 3: True, 4: False})
    _write_run(tmp_path / "hints", {1: True, 2: True, 3: True, 4: False})
    _write_run(
        tmp_path / "oracle", {1: True, 2: False, 3: False, 4: False}, asked={1: 1, 3: 2}
    )

    report = oracle_report(tmp_path / "oracle", tmp_path / "none", tmp_path / "hints")

    assert report["questions"] == 4
    assert report["accuracy"] == {
        "oracle": 0.25,
        "no_hints": 0.25,
        "hints": 0.75,
        "oracle_hint_capped": 0.25,
    }
    assert report["gap_recovered"] == 0.0
    assert report["ask_rate"] == 0.5
    assert report["questions_asked_per_task"] == 0.75
    assert report["ask_recall"] == 0.5
    assert report["ask_precision"] == 0.5
    assert report["fixed_by_asking"] == 1
    assert report["broken_by_asking"] == 1


def test_oracle_report_counts_asks_the_hints_cannot_fix_against_need_precision(
    tmp_path: Path,
):
    _write_run(tmp_path / "none", {1: False, 2: False})
    _write_run(tmp_path / "hints", {1: True, 2: False})
    _write_run(tmp_path / "oracle", {1: False, 2: False}, asked={2: 1})

    report = oracle_report(tmp_path / "oracle", tmp_path / "none", tmp_path / "hints")

    assert report["ask_precision"] == 1.0
    assert report["ask_precision_need"] == 0.0


def test_oracle_report_caps_oracle_fixes_at_what_the_hints_fix(tmp_path: Path):
    # Question 2 is right only with oracle answers, so the answers carried more than the
    # hint; the capped accuracy leaves it out.
    _write_run(tmp_path / "none", {1: False, 2: False, 3: True})
    _write_run(tmp_path / "hints", {1: True, 2: False, 3: True})
    _write_run(tmp_path / "oracle", {1: True, 2: True, 3: True}, asked={1: 1, 2: 1})

    report = oracle_report(tmp_path / "oracle", tmp_path / "none", tmp_path / "hints")

    assert report["accuracy"]["oracle"] == 1.0
    assert report["accuracy"]["oracle_hint_capped"] == pytest.approx(2 / 3)


def _scan_case():
    return BenchmarkCase(
        question_id=3,
        db_id="archeology",
        question="Which sites have the best scans?",
        evidence="",
        gold_sql=(
            "SELECT s.zoneref FROM scans s JOIN scanpointcloud p ON p.arcref = s.arcref "
            "WHERE s.presstat IN ('Poor', 'Critical')"
        ),
        difficulty="",
        dialect="postgresql",
    )


def test_schema_names_are_the_tables_and_columns_of_the_gold_sql():
    assert schema_names(_scan_case().gold_sql, "postgresql") == {
        "zoneref",
        "scans",
        "scanpointcloud",
        "arcref",
        "presstat",
    }
    assert schema_names("not sql at all (", "mysql") == frozenset()


@pytest.mark.parametrize(
    ("answer", "named"),
    [
        ("Link each scan to its point cloud through arcref.", ["arcref"]),
        ("A site is at risk when its PresStat is 'Poor'.", ["presstat"]),
        ("The preservation status (presstat) is 'Poor' or 'Critical'.", ["presstat"]),
        ("Count the rows of the scans table.", ["scans"]),
        ("Use each scan's zone and its preservation status.", []),
        ("Only sites whose status is 'Poor' or 'Critical'.", []),
    ],
)
def test_named_identifiers_flags_schema_names_but_not_business_words(answer, named):
    case = _scan_case()
    names = schema_names(case.gold_sql, case.dialect)
    assert named_identifiers(answer, names, case.question) == named


def test_named_identifiers_accepts_words_and_acronyms_the_user_uses():
    names = {"Age", "RPI"}
    words = "How many elderly users? elder refers to Age > 65"
    assert named_identifiers("Users strictly older than 65 by age.", names, words) == []
    assert named_identifiers("An RPI above 3.", names, words) == []
    assert named_identifiers("The Research Priority Index (RPI).", names, words) == []


def test_simulator_rephrases_an_answer_that_names_identifiers():
    model = _Model(
        [
            '{"option": null, "answer": "Join the point cloud through arcref."}',
            '{"option": null, "answer": "Match each point cloud to its scan."}',
        ]
    )
    question = AskClarificationQuestion(
        id="k", question="How are they linked?", options=[]
    )

    answers, records = OracleUserSimulator(model).answer(_scan_case(), [question])

    assert answers == {"k": "Match each point cloud to its scan."}
    assert records[0].reason == "rephrased"
    assert "named database identifiers (arcref)" in model.prompts[1][1]


def test_simulator_blocks_an_answer_that_keeps_naming_identifiers():
    reply = '{"option": null, "answer": "Use the arcref column."}'
    question = AskClarificationQuestion(
        id="k", question="How are they linked?", options=[]
    )

    answers, records = OracleUserSimulator(_Model([reply, reply])).answer(
        _scan_case(), [question]
    )

    assert answers == {}
    assert (records[0].answer, records[0].reason) == (None, "identifier_blocked")
