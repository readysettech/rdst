import os
import subprocess
from pathlib import Path

import yaml

from devtools.ask_benchmark.claude_subscription_adapter import (
    PINNED_CLAUDE_CODE_VERSION,
)

RDST_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DETECTOR = RDST_ROOT / ".buildkite" / "detect_pipeline_areas.sh"
BIRD_LIVE_RUNNER = RDST_ROOT / ".buildkite" / "run_bird_live_qualification.sh"
ENSURE_NODE = RDST_ROOT / ".buildkite" / "ensure_node.sh"
UV_LOCK = RDST_ROOT / "uv.lock"
BIRD_ONLY_TRIGGER = "rdst/.buildkite/bird_live_test_trigger"


def _generated_pipeline(tmp_path: Path, *, changed: str, branch: str) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "diff" ]; then\n'
        "  printf '%s' \"$MOCK_CHANGED_FILES\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 2\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "MOCK_CHANGED_FILES": changed,
        "BUILDKITE_BRANCH": branch,
    }
    result = subprocess.run(
        ["bash", str(PIPELINE_DETECTOR)],
        cwd=RDST_ROOT.parent,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return yaml.safe_load(result.stdout)


def _step_keys(pipeline: dict) -> set[str]:
    return {
        step["key"]
        for step in pipeline["steps"]
        if isinstance(step, dict) and "key" in step
    }


def test_marker_only_cl_emits_only_bird_qualification(tmp_path):
    pipeline = _generated_pipeline(
        tmp_path,
        changed=f"{BIRD_ONLY_TRIGGER}\n",
        branch="refs/changes/30/14530/9",
    )

    steps = pipeline["steps"]
    assert _step_keys(pipeline) == {"rdst-bird-live-qualification"}
    assert len(steps) == 1
    assert all("group" not in step for step in steps)
    assert steps[0]["soft_fail"] is True
    assert "depends_on" not in steps[0]


def test_marker_on_main_retains_normal_rdst_pipeline(tmp_path):
    pipeline = _generated_pipeline(
        tmp_path,
        changed=f"{BIRD_ONLY_TRIGGER}\n",
        branch="main",
    )

    assert "rdst-bird-live-qualification" in _step_keys(pipeline)
    assert "build-package" in _step_keys(pipeline)
    assert "rdst-cli" in _step_keys(pipeline)


def test_python_cl_does_not_run_post_merge_bird_qualification(tmp_path):
    pipeline = _generated_pipeline(
        tmp_path,
        changed="rdst/features/ask/service.py\n",
        branch="refs/changes/90/14490/5",
    )
    assert "rdst-bird-live-qualification" not in _step_keys(pipeline)


def test_keyservice_only_cl_does_not_expose_bird_gate(tmp_path):
    pipeline = _generated_pipeline(
        tmp_path,
        changed="rdst/keyservice/src/index.py\n",
        branch="refs/changes/90/14490/5",
    )

    assert "rdst-bird-live-qualification" not in _step_keys(pipeline)


def test_python_main_build_runs_informational_bird_qualification(tmp_path):
    pipeline = _generated_pipeline(
        tmp_path,
        changed="rdst/features/ask/service.py\n",
        branch="main",
    )

    steps = pipeline["steps"]
    qualification = next(
        step for step in steps if step.get("key") == "rdst-bird-live-qualification"
    )
    release = next(step for step in steps if step.get("key") == "release-approval")

    assert qualification["soft_fail"] is True
    assert "depends_on" not in qualification
    assert "run_bird_live_qualification.sh" in qualification["command"]
    assert "rdst-bird-live-qualification" not in release["depends_on"]


def test_keyservice_main_build_also_runs_bird_qualification(tmp_path):
    pipeline = _generated_pipeline(
        tmp_path,
        changed="rdst/keyservice/src/index.py\n",
        branch="main",
    )

    qualification = next(
        step
        for step in pipeline["steps"]
        if step.get("key") == "rdst-bird-live-qualification"
    )
    assert qualification["soft_fail"] is True
    assert "depends_on" not in qualification


def test_bird_live_runner_uses_pinned_subscription_provider():
    script = BIRD_LIVE_RUNNER.read_text(encoding="utf-8")
    ensure_node = ENSURE_NODE.read_text(encoding="utf-8")

    assert UV_LOCK.is_file()
    assert "uv sync --python 3.10 --frozen --group dev --group eval" in script
    assert "--models claude-sonnet-4.6-subscription-medium" in script
    assert f'CLAUDE_CODE_VERSION="{PINNED_CLAUDE_CODE_VERSION}"' in script
    assert 'source "${SCRIPT_DIR}/ensure_node.sh"' in script
    assert "\n  ensure_node\n" in script
    assert script.index("\n  ensure_node\n") < script.index("npm install")
    assert "&& command -v npm >/dev/null 2>&1" in ensure_node
    assert 'SUITE="canary"' in script
    assert 'REPETITIONS="1"' in script
    assert 'MAX_CALLS="350"' in script
    assert 'MAX_COST="5.00"' in script
    assert 'MAX_WALL="7200"' in script
    assert '__version__ = "0.0.0.dev0"' in script
    assert 'mkdir -p "${RDST_ROOT}/web_dist"' in script
    assert 'run_context "${CONTEXT}" &' in script
    assert "bird-live-confirmation" not in script
    assert "bird-live-license-acceptance" not in script
    assert "bird-live-suite" not in script
    assert "buildkite/rdst/claude-oauth-token" in script
    assert "ANTHROPIC_API_KEY is required" not in script
    assert "devtools.ask_benchmark.ci_qualification" in script
    assert "QUALIFICATION_FAILED" in script
    assert 'channels: ["#builds-rdst"]' in script
    assert "slack-report" in script
    assert "allow_dependency_failure: true" in script
