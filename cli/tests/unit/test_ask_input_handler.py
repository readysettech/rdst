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
