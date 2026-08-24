import pytest

from devtools.ask_benchmark.ci_qualification import (
    EXPECTED_MODEL,
    QualificationError,
    evaluate_direct_ask,
    render_slack_report,
    render_verdict,
)


def _summary(*, direct_correct: int, ask_correct: int, attempts: int = 16):
    def board(track: str, correct: int):
        return {
            "track": track,
            "context_mode": "auto-init",
            "expected_attempts_per_model": attempts,
            "models": [
                {
                    "model_name": EXPECTED_MODEL,
                    "attempt_count": attempts,
                    "scored_attempt_count": attempts,
                    "correct_count": correct,
                    "official_execution_accuracy": correct / attempts,
                }
            ],
        }

    return {
        "leaderboards": [
            board("model-only", direct_correct),
            board("rdst-ask", ask_correct),
        ]
    }


def test_qualification_passes_when_ask_meets_or_beats_direct():
    passed = evaluate_direct_ask(_summary(direct_correct=6, ask_correct=7))
    tied = evaluate_direct_ask(_summary(direct_correct=6, ask_correct=6))
    lost = evaluate_direct_ask(_summary(direct_correct=7, ask_correct=6))

    assert passed["passed"] is True
    assert passed["accuracy_delta"] == pytest.approx(1 / 16)
    assert tied["passed"] is True
    assert lost["passed"] is False
    assert "PASS" in render_verdict(passed)


def test_qualification_rejects_incomplete_coverage():
    summary = _summary(direct_correct=6, ask_correct=7)
    summary["leaderboards"][1]["models"][0]["attempt_count"] = 15

    with pytest.raises(QualificationError, match="coverage is incomplete"):
        evaluate_direct_ask(summary)


def test_qualification_rejects_an_unexpected_model():
    summary = _summary(direct_correct=6, ask_correct=7)
    summary["leaderboards"][0]["models"][0]["model_name"] = "other-model"

    with pytest.raises(QualificationError, match="exact model"):
        evaluate_direct_ask(summary)


def test_slack_report_labels_above_baseline_and_links_the_build():
    verdicts = [
        evaluate_direct_ask(_summary(direct_correct=6, ask_correct=7)),
        {
            **evaluate_direct_ask(_summary(direct_correct=5, ask_correct=8)),
            "context_mode": "bird-curated",
        },
    ]

    report = render_slack_report(
        verdicts,
        suite="smoke",
        build_url="https://buildkite.example/build/42",
        build_number="42",
    )

    assert "AT OR ABOVE BASELINE" in report
    assert "auto-init: Ask 7/16" in report
    assert "bird-curated: Ask 8/16" in report
    assert "<https://buildkite.example/build/42|build #42 and full artifacts>" in report


def test_slack_report_labels_a_lost_context_as_below_baseline():
    verdicts = [
        evaluate_direct_ask(_summary(direct_correct=6, ask_correct=6)),
        {
            **evaluate_direct_ask(_summary(direct_correct=7, ask_correct=6)),
            "context_mode": "llm-enriched",
        },
    ]

    report = render_slack_report(
        verdicts,
        suite="canary",
        build_url="",
        build_number="99",
    )

    assert "BELOW BASELINE" in report
    assert "+0.0%, TIED" in report
    assert "-6.2%, BELOW" in report
    assert report.endswith("Build #99")


def test_slack_report_passes_when_every_context_ties():
    verdicts = [evaluate_direct_ask(_summary(direct_correct=6, ask_correct=6))]

    report = render_slack_report(
        verdicts,
        suite="canary",
        build_url="",
        build_number="",
    )

    assert "AT OR ABOVE BASELINE" in report
    assert "+0.0%, TIED" in report
