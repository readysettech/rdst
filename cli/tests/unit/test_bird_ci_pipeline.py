import os
import subprocess
from pathlib import Path

import yaml


RDST_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DETECTOR = RDST_ROOT / ".buildkite" / "detect_pipeline_areas.sh"


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


def test_python_cl_exposes_nonblocking_bird_gate(tmp_path):
    pipeline = _generated_pipeline(
        tmp_path,
        changed="rdst/features/ask/service.py\n",
        branch="refs/changes/90/14490/5",
    )
    steps = pipeline["steps"]
    block = next(step for step in steps if step.get("key") == "confirm-bird-live")
    qualification = next(
        step for step in steps if step.get("key") == "rdst-bird-live-qualification"
    )

    assert block["blocked_state"] == "passed"
    assert qualification["depends_on"] == "confirm-bird-live"
    assert {field["key"] for field in block["fields"]} >= {
        "bird-live-confirmation",
        "bird-live-license-acceptance",
        "bird-live-suite",
        "bird-live-repetitions",
        "bird-live-max-calls",
        "bird-live-max-cost-usd",
        "bird-live-max-wall-seconds",
    }


def test_keyservice_only_cl_does_not_expose_bird_gate(tmp_path):
    pipeline = _generated_pipeline(
        tmp_path,
        changed="rdst/keyservice/src/index.py\n",
        branch="refs/changes/90/14490/5",
    )

    assert "confirm-bird-live" not in _step_keys(pipeline)


def test_main_build_does_not_expose_bird_gate(tmp_path):
    pipeline = _generated_pipeline(
        tmp_path,
        changed="rdst/features/ask/service.py\n",
        branch="main",
    )

    assert "confirm-bird-live" not in _step_keys(pipeline)
