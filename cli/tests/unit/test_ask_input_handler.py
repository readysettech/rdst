from unittest.mock import patch

from features.ask.engine.ask3.input_handler import AskInputHandler
from features.ask.models import AskClarificationQuestion


def test_open_question_takes_a_typed_answer_without_a_menu():
    handler = AskInputHandler()
    question = AskClarificationQuestion(
        id="cutoff", question="What glucose level counts as high?", options=[]
    )

    with (
        patch("builtins.input", return_value=" Above 180 mg/dL "),
        patch("features.ask.engine.ask3.input_handler.IntPrompt.ask") as menu,
    ):
        assert handler._prompt_clarification(question) == "Above 180 mg/dL"

    menu.assert_not_called()


def test_open_question_takes_a_suggested_answer_by_number_or_a_typed_one():
    handler = AskInputHandler()
    question = AskClarificationQuestion(
        id="top", question="How many customers?", options=[], examples=["10", "25"]
    )

    with patch("builtins.input", return_value="2"), patch.object(handler, "_console"):
        assert handler._prompt_clarification(question) == "25"
    with patch("builtins.input", return_value="15"), patch.object(handler, "_console"):
        assert handler._prompt_clarification(question) == "15"


def test_an_open_question_without_input_is_skipped():
    handler = AskInputHandler()
    question = AskClarificationQuestion(id="missing-1", question="Where is QI?", options=[])

    with (
        patch("builtins.input", side_effect=EOFError),
        patch.object(handler, "_console"),
    ):
        assert handler._prompt_clarification(question) == ""


def test_a_choice_question_without_input_is_skipped_like_an_open_one():
    handler = AskInputHandler()
    question = AskClarificationQuestion(
        id="status", question="Which orders count as open?", options=["Open", "Pending"]
    )

    with (
        patch(
            "features.ask.engine.ask3.input_handler.IntPrompt.ask", side_effect=EOFError
        ),
        patch.object(handler, "_console"),
    ):
        assert handler._prompt_clarification(question) == ""


def test_options_show_their_schema_hints_and_a_way_to_describe_the_answer():
    handler = AskInputHandler()
    question = AskClarificationQuestion(
        id="customer",
        question="Which ID should identify each customer?",
        options=["Customer number", "Registry number"],
        hints=["core_record.clientref, e.g. CU241436", ""],
    )

    with (
        patch("features.ask.engine.ask3.input_handler.IntPrompt.ask", return_value=3),
        patch("builtins.input", return_value="The client reference on the income record"),
        patch.object(handler, "_console"),
        patch("features.ask.engine.ask3.input_handler.SelectionTable") as table,
    ):
        answer = handler._prompt_clarification(question)

    assert table.call_args.args[0] == [
        "Customer number (core_record.clientref, e.g. CU241436)",
        "Registry number",
        "None of these (describe what you mean)",
    ]
    assert answer == "The client reference on the income record"


def test_a_question_is_shown_whole():
    handler = AskInputHandler()
    question = AskClarificationQuestion(
        id="missing-1",
        question="I couldn't find payload['region'] in this database. Which table, column "
        "or JSON key holds it, or how should I work it out?",
        options=[],
    )

    with (
        patch("builtins.input", return_value="the region key"),
        patch.object(handler, "_console"),
        patch("features.ask.engine.ask3.input_handler.MessagePanel") as panel,
    ):
        handler._prompt_clarification(question)

    assert panel.call_args.args[0] == question.question


def test_cli_answers_follow_up_questions_from_a_resumed_run(monkeypatch):
    from features.ask import service
    from features.ask.cli.command import AskCommand
    from features.ask.events import AskClarificationNeededEvent, AskResultEvent

    def clarification(session_id):
        question = AskClarificationQuestion(
            id="q", question="What is churn?", options=[]
        )
        return AskClarificationNeededEvent(
            type="clarification_needed",
            session_id=session_id,
            interpretations=[],
            questions=[question],
        )

    resumed = []

    class FakeAskService:
        def __init__(self, **_kwargs):
            pass

        async def ask(self, _input, _options):
            yield clarification("first")

        async def resume(self, session_id, answers):
            resumed.append((session_id, answers))
            if session_id == "first":
                yield clarification("second")
                return
            yield AskResultEvent(
                type="result",
                success=True,
                sql="SELECT 1",
                rows=[],
                columns=[],
                row_count=0,
                execution_time_ms=1.0,
                llm_calls=2,
                total_tokens=10,
            )

    monkeypatch.setattr(service, "AskService", FakeAskService)
    with patch(
        "builtins.input", side_effect=["Cancelled plans", "No order in 90 days"]
    ):
        result = AskCommand()._execute_impl(question="How many churned?", dry_run=True)

    assert result.ok
    assert resumed == [
        ("first", {"q": "Cancelled plans"}),
        ("second", {"q": "No order in 90 days"}),
    ]


def test_a_question_that_allows_several_options_takes_them_all():
    handler = AskInputHandler()
    question = AskClarificationQuestion(
        id="measures",
        question="Which debt measures should the summary show?",
        options=["Debt-to-income ratio", "Total liabilities", "Loan amount"],
        multiple=True,
    )

    with patch("builtins.input", return_value="3, 1"), patch.object(handler, "_console"):
        assert (
            handler._prompt_clarification(question)
            == "Loan amount; Debt-to-income ratio"
        )
    with (
        patch("builtins.input", side_effect=["4", "Median debt-to-income"]),
        patch.object(handler, "_console"),
    ):
        assert handler._prompt_clarification(question) == "Median debt-to-income"
