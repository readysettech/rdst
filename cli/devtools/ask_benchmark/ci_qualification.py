from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from shared.persistence import write_json, write_text

EXPECTED_MODEL = "claude-sonnet-4.6-subscription-medium"
VERDICT_SCHEMA_VERSION = 1


class QualificationError(ValueError):
    pass


def evaluate_direct_ask(
    summary: dict[str, Any],
    *,
    expected_model: str = EXPECTED_MODEL,
) -> dict[str, Any]:
    boards = summary.get("leaderboards")
    if not isinstance(boards, list):
        raise QualificationError("Leaderboard summary has no leaderboards")

    selected: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for board in boards:
        if not isinstance(board, dict):
            continue
        track = str(board.get("track"))
        if track not in {"model-only", "rdst-ask"}:
            continue
        models = board.get("models")
        if not isinstance(models, list) or len(models) != 1:
            raise QualificationError(f"{track} must contain exactly one model")
        model = models[0]
        if not isinstance(model, dict) or model.get("model_name") != expected_model:
            raise QualificationError(
                f"{track} must use the exact model {expected_model!r}"
            )
        if track in selected:
            raise QualificationError(f"Leaderboard contains duplicate {track} boards")
        selected[track] = (board, model)

    if set(selected) != {"model-only", "rdst-ask"}:
        raise QualificationError(
            "Qualification requires one model-only and one rdst-ask leaderboard"
        )

    direct_board, direct = selected["model-only"]
    ask_board, ask = selected["rdst-ask"]
    direct_context = str(direct_board.get("context_mode"))
    ask_context = str(ask_board.get("context_mode"))
    if direct_context != ask_context:
        raise QualificationError(
            f"Track contexts differ: direct={direct_context}, ask={ask_context}"
        )

    expected_attempts = int(direct_board.get("expected_attempts_per_model", 0))
    if (
        expected_attempts <= 0
        or int(ask_board.get("expected_attempts_per_model", 0)) != expected_attempts
    ):
        raise QualificationError("Track expected-attempt counts differ or are invalid")

    for track, (_, model) in selected.items():
        attempt_count = int(model.get("attempt_count", 0))
        scored_count = int(model.get("scored_attempt_count", 0))
        if attempt_count != expected_attempts or scored_count != expected_attempts:
            raise QualificationError(
                f"{track} coverage is incomplete: "
                f"attempts={attempt_count}, scored={scored_count}, "
                f"expected={expected_attempts}"
            )

    direct_correct = int(direct.get("correct_count", -1))
    ask_correct = int(ask.get("correct_count", -1))
    if min(direct_correct, ask_correct) < 0:
        raise QualificationError("Track correct counts are missing")

    direct_accuracy = float(direct["official_execution_accuracy"])
    ask_accuracy = float(ask["official_execution_accuracy"])
    passed = ask_correct >= direct_correct
    return {
        "schema_version": VERDICT_SCHEMA_VERSION,
        "context_mode": direct_context,
        "model_name": expected_model,
        "metric": "official_execution_accuracy",
        "rule": "rdst-ask >= model-only",
        "expected_attempts_per_track": expected_attempts,
        "direct_correct": direct_correct,
        "rdst_ask_correct": ask_correct,
        "direct_accuracy": direct_accuracy,
        "rdst_ask_accuracy": ask_accuracy,
        "accuracy_delta": ask_accuracy - direct_accuracy,
        "passed": passed,
    }


def render_verdict(verdict: dict[str, Any]) -> str:
    status = "PASS" if verdict["passed"] else "FAIL"
    return (
        f"**Qualification verdict:** {status}  \n"
        f"RDST Ask: {verdict['rdst_ask_correct']}/"
        f"{verdict['expected_attempts_per_track']} "
        f"({verdict['rdst_ask_accuracy']:.1%}); direct Sonnet 4.6: "
        f"{verdict['direct_correct']}/{verdict['expected_attempts_per_track']} "
        f"({verdict['direct_accuracy']:.1%}); delta: "
        f"{verdict['accuracy_delta']:+.1%}.\n"
    )


def render_slack_report(
    verdicts: list[dict[str, Any]],
    *,
    suite: str,
    build_url: str,
    build_number: str,
) -> str:
    if not verdicts:
        raise QualificationError("Slack report requires at least one verdict")
    contexts = [str(verdict.get("context_mode", "")) for verdict in verdicts]
    if any(not context for context in contexts) or len(set(contexts)) != len(contexts):
        raise QualificationError("Slack report contexts must be nonempty and unique")
    models = {verdict.get("model_name") for verdict in verdicts}
    if models != {EXPECTED_MODEL}:
        raise QualificationError(
            f"Slack report must use the exact model {EXPECTED_MODEL!r}"
        )

    passed = all(bool(verdict.get("passed")) for verdict in verdicts)
    overall = "AT OR ABOVE BASELINE" if passed else "BELOW BASELINE"
    emoji = ":white_check_mark:" if passed else ":x:"
    lines = [
        f"{emoji} *RDST Ask BIRD {suite}: {overall}*",
        "Model: Claude Sonnet 4.6 subscription",
    ]
    for verdict in verdicts:
        delta = float(verdict["accuracy_delta"])
        if delta > 0:
            relation = "ABOVE"
        elif delta < 0:
            relation = "BELOW"
        else:
            relation = "TIED"
        lines.append(
            f"- {verdict['context_mode']}: Ask "
            f"{verdict['rdst_ask_correct']}/"
            f"{verdict['expected_attempts_per_track']} "
            f"({float(verdict['rdst_ask_accuracy']):.1%}) vs direct "
            f"{verdict['direct_correct']}/"
            f"{verdict['expected_attempts_per_track']} "
            f"({float(verdict['direct_accuracy']):.1%}), "
            f"{delta:+.1%}, {relation}"
        )
    if build_url:
        label = f"build #{build_number}" if build_number else "Buildkite report"
        lines.append(f"<{build_url}|{label} and full artifacts>")
    elif build_number:
        lines.append(f"Build #{build_number}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate and report canonical Ask qualification."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser(
        "evaluate", help="Evaluate Ask against direct Sonnet."
    )
    evaluate.add_argument("leaderboard", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    slack_report = subparsers.add_parser(
        "slack-report", help="Render the completed context verdicts for Slack."
    )
    slack_report.add_argument("verdicts", nargs="+", type=Path)
    slack_report.add_argument("--suite", required=True)
    slack_report.add_argument("--build-url", default="")
    slack_report.add_argument("--build-number", default="")
    slack_report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "slack-report":
        try:
            verdicts = [
                json.loads(path.read_text(encoding="utf-8")) for path in args.verdicts
            ]
            report = render_slack_report(
                verdicts,
                suite=args.suite,
                build_url=args.build_url,
                build_number=args.build_number,
            )
        except (OSError, json.JSONDecodeError, QualificationError, KeyError) as exc:
            parser.error(str(exc))
        write_text(args.output, report + "\n")
        print(report)
        return 0

    try:
        summary = json.loads(args.leaderboard.read_text(encoding="utf-8"))
        verdict = evaluate_direct_ask(summary)
    except (OSError, json.JSONDecodeError, QualificationError, KeyError) as exc:
        parser.error(str(exc))

    write_json(args.output, verdict)
    write_text(args.output.with_suffix(".md"), render_verdict(verdict))
    print(render_verdict(verdict).strip())
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
