import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.unit.test_bird_ci_pipeline import _generated_pipeline

RDST_ROOT = Path(__file__).resolve().parents[2]
KEYSERVICE_CI = RDST_ROOT / "keyservice" / ".buildkite"
APPEND_ONLY_CHECK = KEYSERVICE_CI / "check-migrations-append-only.sh"
DEV_ENV_SCRIPT = RDST_ROOT / "keyservice" / "scripts" / "deploy-dev-env.sh"
CL_BRANCH = "refs/changes/90/14490/5"

# The public mirror publishes tests/unit without the keyservice or its CI scripts.
if not KEYSERVICE_CI.is_dir():
    pytest.skip(
        "keyservice CI scripts are not part of this checkout", allow_module_level=True
    )

_spec = importlib.util.spec_from_file_location(
    "migration_drift", KEYSERVICE_CI / "migration_drift.py"
)
migration_drift = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration_drift)

ON_DISK = ["0001_initial.sql", "0002_logins.sql", "0003_usage.sql"]


@pytest.mark.parametrize(
    "applied",
    [[], ["0001_initial.sql"], ON_DISK[:2], ON_DISK],
)
def test_applied_prefix_of_disk_migrates_in_place(applied):
    assert migration_drift.first_divergence(applied, ON_DISK) is None


@pytest.mark.parametrize(
    ("applied", "on_disk", "reason"),
    [
        (
            ["0001_initial.sql", "0002_old_name.sql"],
            ["0001_initial.sql", "0002_new_name.sql"],
            "0002_old_name.sql is applied but no longer on disk",
        ),
        (
            ON_DISK,
            ON_DISK[:2],
            "0003_usage.sql is applied but no longer on disk",
        ),
        (
            ["0001_initial.sql", "0003_usage.sql"],
            ON_DISK,
            "0002_logins.sql sorts before the already applied 0003_usage.sql",
        ),
    ],
)
def test_rewritten_history_needs_a_rebuild(applied, on_disk, reason):
    assert migration_drift.first_divergence(applied, on_disk) == reason


def test_drift_cli_exit_codes(tmp_path):
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name in ON_DISK:
        (migrations / name).write_text("SELECT 1;\n", encoding="utf-8")
    (migrations / "README.md").write_text("not a migration\n", encoding="utf-8")
    applied = tmp_path / "applied.txt"

    def run(applied_names: list[str]) -> subprocess.CompletedProcess:
        applied.write_text("\n".join(applied_names) + "\n", encoding="utf-8")
        return subprocess.run(
            [
                "python3",
                str(KEYSERVICE_CI / "migration_drift.py"),
                str(applied),
                str(migrations),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    assert run(ON_DISK[:2]).returncode == 0
    rebuild = run(["0002_logins.sql"])
    assert rebuild.returncode == migration_drift.REBUILD_EXIT_CODE
    assert "0001_initial.sql sorts before" in rebuild.stdout


MIGRATIONS = "rdst/keyservice/migrations"
# A non-SQL file sorts after every migration and must not count as one.
BASE_MIGRATIONS = ["0001_initial.sql", "0002_logins.sql", "README.md"]


def _append_only(tmp_path: Path, diff: list[str]) -> subprocess.CompletedProcess:
    """Run the check against a fake git: BASE_MIGRATIONS exist at the base and
    `diff` holds the `git diff --name-status --no-renames` lines."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  ls-tree) printf '%s' \"$MOCK_LS_TREE\" ;;\n"
        "  diff) printf '%s' \"$MOCK_DIFF\" ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "GERRIT_FROM_COMMIT"}
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["MOCK_LS_TREE"] = "".join(f"{MIGRATIONS}/{name}\n" for name in BASE_MIGRATIONS)
    env["MOCK_DIFF"] = "".join(f"{line}\n" for line in diff)
    return subprocess.run(
        ["bash", str(APPEND_ONLY_CHECK)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_new_migration_after_the_last_passes(tmp_path):
    assert _append_only(tmp_path, [f"A\t{MIGRATIONS}/0003_usage.sql"]).returncode == 0


@pytest.mark.parametrize(
    ("diff", "message"),
    [
        ([f"M\t{MIGRATIONS}/0002_logins.sql"], "must not be edited"),
        (
            [f"D\t{MIGRATIONS}/0002_logins.sql", f"A\t{MIGRATIONS}/0003_logins.sql"],
            "must not be edited",
        ),
        ([f"D\t{MIGRATIONS}/0002_logins.sql"], "must not be edited"),
        ([f"A\t{MIGRATIONS}/0001a_late.sql"], "must sort after 0002_logins.sql"),
    ],
)
def test_rewriting_migrations_fails(tmp_path, diff, message):
    result = _append_only(tmp_path, diff)
    assert result.returncode == 1
    assert message in result.stderr


def _pipeline(
    tmp_path: Path, *, changed: str, branch: str = CL_BRANCH, vs_main: str = ""
) -> dict:
    """Generate the pipeline against a fake git: `changed` is this commit's diff
    and `vs_main` the CL tree's diff from its merge-base with main."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        "  fetch) exit 0 ;;\n"
        "  merge-base) echo main-base ;;\n"
        "  diff)\n"
        '    if [ "$3" = main-base ]; then printf \'%s\' "$MOCK_VS_MAIN";\n'
        "    else printf '%s' \"$MOCK_CHANGED_FILES\"; fi ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "MOCK_CHANGED_FILES": changed,
        "MOCK_VS_MAIN": vs_main,
        "BUILDKITE_BRANCH": branch,
        "GERRIT_CHANGE_ID": "14490",
    }
    result = subprocess.run(
        ["bash", str(RDST_ROOT / ".buildkite" / "detect_pipeline_areas.sh")],
        cwd=RDST_ROOT.parent,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return yaml.safe_load(result.stdout)


def _all_steps(pipeline: dict) -> dict:
    steps = {}
    for step in pipeline["steps"]:
        if not isinstance(step, dict):
            continue
        for nested in step.get("steps", [step]):
            if isinstance(nested, dict) and "key" in nested:
                steps[nested["key"]] = nested
    return steps


def test_keyservice_cl_deploys_its_cl_env(tmp_path):
    keyservice_file = "rdst/keyservice/src/index.py\n"
    pipeline = _pipeline(tmp_path, changed=keyservice_file, vs_main=keyservice_file)
    steps = _all_steps(pipeline)

    cl_env = steps["keyservice-cl-env-deploy"]
    assert cl_env["command"] == ".buildkite/deploy-preview.sh cl"
    # The build's binaries point at the CL env, so its deploy must not soft-fail.
    assert "soft_fail" not in cl_env
    assert cl_env["concurrency"] == 1
    assert cl_env["concurrency_group"] == "rdst-keyservice-cl-env-${GERRIT_CHANGE_ID}"
    assert pipeline["env"]["RDST_BUILD_KEYSERVICE_URL"] == (
        "https://keyservice-cl14490.readyset.io"
    )
    assert "keyservice-migrations-append-only" in steps
    assert "keyservice-cl-env-cleanup" not in steps
    # The build preview the E2E suite uses is still emitted alongside.
    assert "keyservice-preview-deploy" in steps


def test_cl_stacked_on_a_keyservice_change_gets_its_own_cl_env(tmp_path):
    pipeline = _pipeline(
        tmp_path,
        changed="rdst/features/ask/service.py\n",
        vs_main="rdst/keyservice/src/index.py\n",
    )
    steps = _all_steps(pipeline)

    assert "keyservice-cl-env-deploy" in steps
    # Its own commit leaves migrations alone, so the append-only check is not needed.
    assert "keyservice-migrations-append-only" not in steps
    assert pipeline["env"]["RDST_BUILD_KEYSERVICE_URL"] == (
        "https://keyservice-cl14490.readyset.io"
    )


def test_cl_whose_tree_matches_main_gets_no_cl_env(tmp_path):
    # The commit touches the keyservice, but only to undo an earlier CL's change.
    pipeline = _pipeline(tmp_path, changed="rdst/keyservice/src/index.py\n", vs_main="")
    steps = _all_steps(pipeline)

    assert "keyservice-cl-env-deploy" not in steps
    assert "keyservice-migrations-append-only" in steps
    assert pipeline["env"]["RDST_BUILD_KEYSERVICE_URL"] == (
        "https://keyservice-staging.readyset.io"
    )


def test_cl_without_keyservice_changes_points_at_staging(tmp_path):
    pipeline = _pipeline(tmp_path, changed="rdst/features/ask/service.py\n")
    steps = _all_steps(pipeline)

    assert "keyservice-cl-env-deploy" not in steps
    assert "keyservice-migrations-append-only" not in steps
    assert "keyservice-preview-deploy" in steps
    assert pipeline["env"]["RDST_BUILD_KEYSERVICE_URL"] == (
        "https://keyservice-staging.readyset.io"
    )
    sidecar = steps["build-rdst-sidecar-linux-amd64"]
    docker = next(
        p["docker#v5.11.0"] for p in sidecar["plugins"] if "docker#v5.11.0" in p
    )
    assert "RDST_BUILD_KEYSERVICE_URL" in docker["environment"]


def test_main_build_bakes_no_keyservice_url(tmp_path):
    pipeline = _pipeline(
        tmp_path, changed="rdst/keyservice/src/index.py\n", branch="main"
    )
    assert "RDST_BUILD_KEYSERVICE_URL" not in pipeline["env"]


@pytest.mark.parametrize(
    "changed", ["rdst/keyservice/src/index.py\n", "rdst/features/ask/service.py\n"]
)
def test_every_main_build_cleans_up_cl_envs(tmp_path, changed):
    pipeline = _generated_pipeline(tmp_path, changed=changed, branch="main")
    steps = {
        step.get("key"): step for step in pipeline["steps"] if isinstance(step, dict)
    }

    cleanup = steps["keyservice-cl-env-cleanup"]
    assert cleanup["command"] == ".buildkite/cleanup-cl-envs.sh"
    assert cleanup["soft_fail"] is True
    assert "keyservice-cl-env-deploy" not in steps


@pytest.mark.parametrize(
    "changed", ["rdst/keyservice/src/index.py\n", "rdst/features/ask/service.py\n"]
)
def test_every_main_build_deploys_the_keyservice(tmp_path, changed):
    # Staging tracks main and a release ships every client change merged since the
    # last one, so a build that touched no keyservice code still deploys both.
    steps = _all_steps(_generated_pipeline(tmp_path, changed=changed, branch="main"))

    staging = steps["keyservice-staging-deploy"]
    assert staging["command"] == ".buildkite/deploy.sh staging"
    assert "depends_on" not in staging
    prod = steps["keyservice-prod-deploy"]
    assert prod["command"] == ".buildkite/deploy.sh prod"
    assert prod["depends_on"] == "release-approval"


@pytest.mark.parametrize("name", ["cl14490", "pr-14490-7", "pr-x"])
def test_personal_env_rejects_ci_preview_names(name):
    result = subprocess.run(
        ["bash", str(DEV_ENV_SCRIPT), name], capture_output=True, text=True, check=False
    )
    assert result.returncode == 2
    assert "is reserved for a shared env" in result.stderr
