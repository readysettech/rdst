from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
import yaml

RDST_DIR = Path(__file__).parents[2]
DETECT_PIPELINE = RDST_DIR / ".buildkite" / "detect_pipeline_areas.sh"
WINDOWS_DESKTOP_BUILD = RDST_DIR / ".buildkite" / "build_rdst_desktop_windows.ps1"
WINDOWS_DESKTOP_SIGNING_CONFIG = (
    RDST_DIR / ".buildkite" / "electron-builder-windows-signed.yml"
)
PREPARE = RDST_DIR / ".buildkite" / "prepare_installer.sh"
PUBLISH = RDST_DIR / ".buildkite" / "publish_installer.sh"


@pytest.fixture
def publication_env(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    store = tmp_path / "s3"
    artifacts = tmp_path / "artifacts"
    fake_bin.mkdir()
    store.mkdir()

    aws = fake_bin / "aws"
    aws.write_text(
        """#!/bin/bash
set -euo pipefail
store="$FAKE_S3_STORE"
if [[ "$1 $2" == "s3api head-object" ]]; then
  shift 2
  while [[ $# -gt 0 ]]; do
    case "$1" in --key) key="$2"; shift 2;; *) shift;; esac
  done
  [[ -f "$store/$key" ]] || { echo 404 >&2; exit 1; }
elif [[ "$1 $2" == "s3api get-object" ]]; then
  shift 2
  destination="${!#}"
  while [[ $# -gt 1 ]]; do
    case "$1" in --key) key="$2"; shift 2;; *) shift;; esac
  done
  cp "$store/$key" "$destination"
elif [[ "$1 $2" == "s3api put-object" ]]; then
  shift 2
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --key) key="$2"; shift 2;;
      --body) body="$2"; shift 2;;
      *) shift;;
    esac
  done
  [[ ! -e "$store/$key" ]] || exit 1
  mkdir -p "$(dirname "$store/$key")"
  cp "$body" "$store/$key"
  echo "$key" >> "$FAKE_AWS_LOG"
elif [[ "$1 $2" == "s3 cp" ]]; then
  source="$3"
  destination="$4"
  if [[ "$source" == s3://* ]]; then
    key="${source#s3://*/}"
    [[ -f "$store/$key" ]] || { echo NoSuchKey >&2; exit 1; }
    if [[ "$destination" == "-" ]]; then cat "$store/$key"; else cp "$store/$key" "$destination"; fi
  else
    key="${destination#s3://*/}"
    if [[ "${FAKE_AWS_FAIL_UPLOAD_KEY:-}" == "$key" ]]; then
      echo "forced upload failure: $key" >&2
      exit 1
    fi
    mkdir -p "$(dirname "$store/$key")"
    cp "$source" "$store/$key"
    echo "$key" >> "$FAKE_AWS_LOG"
  fi
else
  echo "unexpected aws command: $*" >&2
  exit 2
fi
""",
        encoding="utf-8",
    )
    aws.chmod(0o755)

    curl = fake_bin / "curl"
    curl.write_text(
        """#!/bin/bash
set -euo pipefail
destination=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o) destination="$2"; shift 2;;
    http*) url="$1"; shift;;
    *) shift;;
  esac
done
path="${url#*://*/}"
path="${path%%\\?*}"
[[ -f "$FAKE_S3_STORE/$path" ]] || exit 22
# -fsI asks only whether the object is reachable, so there is nowhere to write.
[[ -n "$destination" ]] || exit 0
cp "$FAKE_S3_STORE/$path" "$destination"
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_S3_STORE": str(store),
        "FAKE_AWS_LOG": str(tmp_path / "aws.log"),
        "RDST_INSTALLER_ARTIFACT_DIR": str(artifacts),
        "RDST_INSTALLER_DESTINATION_BASE": "s3://bucket/packages/rdst-cli",
        "RDST_INSTALLER_PUBLIC_BASE_URL": "https://example.test/packages/rdst-cli",
        "PUBLISH_PUBLIC": "true",
    }
    return env, store, artifacts


def run_script(script: Path, env: dict[str, str]):
    return subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, check=False
    )


PLATFORMS = ("macos-arm64", "linux-x86_64", "linux-arm64")


def stage_archives(artifacts: Path, version: str, *, signing: str = "developer-id"):
    """Lay out the platform archives the way the build scripts upload them."""
    for platform in PLATFORMS:
        name = "rdst-cli-macos" if platform.startswith("macos") else f"rdst-sidecar-{platform}"
        directory = artifacts.parent / name
        directory.mkdir(parents=True, exist_ok=True)
        archive = directory / f"rdst-{version}-{platform}.tar.gz"
        archive.write_bytes(f"archive for {platform} {version}".encode())
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (directory / f"{archive.name}.sha256").write_text(
            f"{digest}  {archive.name}\n", encoding="utf-8"
        )
        (directory / "version").write_text(f"{version}\n", encoding="utf-8")
        (directory / "platform").write_text(f"{platform}\n", encoding="utf-8")
        if platform.startswith("macos"):
            (directory / "signing").write_text(f"{signing}\n", encoding="utf-8")


def prepare(env: dict[str, str], build: int, **archive_options):
    prepared_env = env | {"BUILDKITE_BUILD_NUMBER": str(build)}
    result = run_script(PREPARE, prepared_env)
    assert result.returncode == 0, result.stderr
    artifacts = Path(prepared_env["RDST_INSTALLER_ARTIFACT_DIR"])
    version = (artifacts / "version").read_text(encoding="utf-8").strip()
    stage_archives(artifacts, version, **archive_options)
    return prepared_env


def test_desktop_pipeline_builds_smokes_and_publishes_windows(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git = fake_bin / "git"
    git.write_text(
        "#!/bin/sh\nprintf '%s\\n' web-apps/apps/rdst-desktop/electron-builder.yml\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "BUILDKITE_BRANCH": "main",
        "BUILDKITE_BUILD_NUMBER": "123",
    }

    result = run_script(DETECT_PIPELINE, env)

    assert result.returncode == 0, result.stderr
    pipeline = yaml.safe_load(result.stdout)
    steps = {step.get("key"): step for step in flatten_steps(pipeline["steps"]) if "key" in step}
    windows_git_env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.longpaths",
        "GIT_CONFIG_VALUE_0": "true",
    }
    build = steps["build-rdst-desktop-windows"]
    assert build["agents"]["queue"] == "windows-c6a-2xlarge"
    assert build["env"] == windows_git_env
    assert "artifact_paths" not in build
    assert "build_rdst_desktop_windows.ps1" in build["command"]

    smoke_group = steps["smoke-rdst-desktop"]
    smoke_steps = {step["key"]: step for step in smoke_group["steps"]}
    smoke = smoke_steps["smoke-rdst-desktop-windows"]
    assert smoke["depends_on"] == "build-rdst-desktop-windows"
    assert smoke["agents"]["queue"] == "windows-c6a-2xlarge"
    assert smoke["env"] == windows_git_env

    publish = steps["publish-rdst-desktop"]
    assert "smoke-rdst-desktop-windows" in publish["depends_on"]
    assert "windows '*.exe'" in publish["command"]
    assert "smoke-rdst-desktop-windows" in steps["release-approval"]["depends_on"]


def test_windows_build_uploads_normalized_artifact_paths():
    script = WINDOWS_DESKTOP_BUILD.read_text(encoding="utf-8")

    assert "'artifact', 'upload', '--experiment', 'normalised-upload-paths'" in script
    assert "rdst/.buildkite-artifacts/rdst-desktop-windows/*" in script
    assert "rdst/.buildkite-artifacts/rdst-desktop-windows/update/*" in script


def test_windows_main_build_inherits_azure_signing_credentials():
    script = WINDOWS_DESKTOP_BUILD.read_text(encoding="utf-8")

    for name in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        assert name in script
    assert "Main build requires Azure Trusted Signing credentials" in script
    assert "Main build requires WIN_CSC_LINK" not in script
    assert "Install-ChocolateyPackage 'dotnet-8.0-sdk'" in script
    assert "electron-builder-windows-signed.yml" in script

    signing_config = yaml.safe_load(
        WINDOWS_DESKTOP_SIGNING_CONFIG.read_text(encoding="utf-8")
    )
    assert signing_config["extends"] == "./electron-builder.yml"
    assert signing_config["win"]["azureSignOptions"] == {
        "publisherName": (
            'CN="READYSET TECHNOLOGY, INC.", O="READYSET TECHNOLOGY, INC.", '
            "L=Beverly Hills, S=California, C=US"
        ),
        "endpoint": "https://eus.codesigning.azure.net/",
        "codeSigningAccountName": "readysetsigning",
        "certificateProfileName": "rdstapp-public",
    }


def flatten_steps(steps):
    """Every step in the pipeline, including those nested inside group steps."""
    flattened = []
    for step in steps:
        flattened.append(step)
        flattened.extend(flatten_steps(step.get("steps") or []))
    return flattened


def generate_pipeline(tmp_path: Path, *, branch: str, changed: tuple[str, ...] = ()):
    """Run the pipeline generator against a synthetic diff."""
    fake_bin = tmp_path / f"bin-{branch.replace('/', '-')}-{len(changed)}"
    fake_bin.mkdir(parents=True, exist_ok=True)
    git = fake_bin / "git"
    listing = "".join(f"echo '{path}'\n" for path in changed)
    git.write_text(f"#!/bin/sh\n{listing}exit 0\n", encoding="utf-8")
    git.chmod(0o755)
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "BUILDKITE_BRANCH": branch,
        "BUILDKITE_BUILD_NUMBER": "123",
    }

    result = run_script(DETECT_PIPELINE, env)

    assert result.returncode == 0, result.stderr
    return yaml.safe_load(result.stdout)


@pytest.mark.parametrize(
    ("branch", "changed"),
    [
        ("main", ()),
        # The fail-open branch: a diff that produced nothing still has to emit
        # a pipeline whose dependencies resolve.
        ("cl/some-change", ()),
        ("cl/some-change", ("rdst/install.sh",)),
        ("cl/some-change", ("rdst/features/update/service.py",)),
        ("cl/some-change", ("web-apps/apps/rdst-desktop/package.json",)),
        ("cl/some-change", ("rdst/keyservice/app.py",)),
    ],
)
def test_every_step_dependency_is_emitted(tmp_path: Path, branch, changed):
    # Buildkite rejects the whole upload when a step depends on a key that is
    # not in the pipeline, so a surface gated differently from its dependency
    # takes down every build that hits it.
    pipeline = generate_pipeline(tmp_path, branch=branch, changed=changed)
    steps = flatten_steps(pipeline["steps"])
    keys = {step["key"] for step in steps if "key" in step}

    for step in steps:
        dependencies = step.get("depends_on") or []
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        for dependency in dependencies:
            name = dependency["step"] if isinstance(dependency, dict) else dependency
            assert name in keys, f"{step.get('key')} depends on missing {name}"


def test_publish_installer_downloads_every_platform_archive(tmp_path: Path):
    pipeline = generate_pipeline(tmp_path, branch="main")
    steps = {step.get("key"): step for step in pipeline["steps"] if "key" in step}
    publish = steps["publish-installer"]

    assert "rdst-sidecar-linux-*" in publish["command"]
    assert "rdst-cli-macos" in publish["command"]
    for dependency in (
        "build-rdst-sidecar-linux-amd64",
        "build-rdst-sidecar-linux-arm64",
        "build-rdst-cli-macos",
    ):
        assert dependency in publish["depends_on"]


def test_the_macos_cli_archive_comes_from_its_own_signing_step(tmp_path: Path):
    # The CLI signs itself in a dedicated step so the archive stays independent
    # of the desktop packages; only that step may publish it.
    pipeline = generate_pipeline(tmp_path, branch="main")
    steps = {step.get("key"): step for step in flatten_steps(pipeline["steps"]) if "key" in step}

    assert "rdst/.buildkite-artifacts/rdst-cli-macos/*" in steps["build-rdst-cli-macos"]["artifact_paths"]
    assert not any(
        "rdst-cli-macos" in path for path in steps["build-rdst-desktop-macos"]["artifact_paths"]
    )
    assert "build-rdst-cli-macos" in steps["release-smoke-installer-macos"]["depends_on"]


def test_a_change_build_smokes_the_installer_on_every_platform(tmp_path: Path):
    # The installer is what these smokes are for, so a change to it is checked
    # on every platform it installs, against the archives that build produced.
    pipeline = generate_pipeline(
        tmp_path, branch="cl/installer", changed=("rdst/install.sh",)
    )
    steps = {step.get("key"): step for step in flatten_steps(pipeline["steps"]) if "key" in step}

    # The installer a change build smokes is stamped and uploaded the way a
    # release stamps and uploads it, so the build page carries the script that
    # was actually exercised.
    assert "rdst/.buildkite-artifacts/rdst-installer/*" in steps["prepare-installer"]["artifact_paths"]

    smoke = steps["smoke-rdst-installer-macos"]
    assert smoke["depends_on"] == ["prepare-installer", "build-rdst-cli-macos"]
    assert smoke["agents"]["queue"] == "macos"
    for key in ("smoke-rdst-installer-macos", "smoke-rdst-installer-ubuntu-amd64"):
        assert "rdst-installer/install.sh" in steps[key]["command"]
    # basic mode: a change build has no Developer ID certificate to sign with,
    # so its archive is ad-hoc and the update check a release runs is skipped.
    assert smoke["command"].rstrip().endswith("basic")
    for key in ("smoke-rdst-installer-ubuntu-amd64", "smoke-rdst-installer-ubuntu-arm64"):
        assert steps[key]["command"].rstrip().endswith("basic")


def test_the_installer_is_proven_before_the_release_button(tmp_path: Path):
    # The installer no longer takes anything from the package index, so its
    # smokes have no reason to trail the PyPI publish. Running them ahead of the
    # button means an irreversible publish never happens on an installer that
    # was never installed.
    pipeline = generate_pipeline(tmp_path, branch="main")
    steps = {step.get("key"): step for step in flatten_steps(pipeline["steps"]) if "key" in step}

    def upstream(key, seen=None):
        seen = set() if seen is None else seen
        dependencies = steps.get(key, {}).get("depends_on") or []
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        for dependency in dependencies:
            name = dependency["step"] if isinstance(dependency, dict) else dependency
            if name not in seen:
                seen.add(name)
                upstream(name, seen)
        return seen

    smokes = [f"release-smoke-installer-{platform}" for platform in
              ("ubuntu-amd64", "ubuntu-arm64", "fedora-amd64", "fedora-arm64", "macos")]
    for smoke in smokes:
        assert smoke in steps["release-approval"]["depends_on"]
        assert "publish-release" not in upstream(smoke)
    # Publishing stays behind the button, which it used to reach only through
    # the chain that ran the smokes.
    assert "release-approval" in steps["publish-installer"]["depends_on"]


def test_linux_release_smokes_mount_buildkite_agent(tmp_path: Path):
    pipeline = generate_pipeline(tmp_path, branch="main")
    steps = {step.get("key"): step for step in flatten_steps(pipeline["steps"]) if "key" in step}
    smoke_keys = [
        "release-smoke-installer-ubuntu-amd64",
        "release-smoke-installer-ubuntu-arm64",
        "release-smoke-installer-fedora-amd64",
        "release-smoke-installer-fedora-arm64",
    ]
    for key in smoke_keys:
        docker = steps[key]["plugins"][0]["docker#v5.11.0"]
        assert docker["mount-buildkite-agent"] is True
    for key in smoke_keys[:2]:
        command = steps[key]["command"]
        assert command.index("apt-get install") < command.index(
            "buildkite-agent artifact download"
        )


def test_publishes_one_pinned_artifact_to_versioned_and_latest_paths(
    publication_env,
):
    env, store, artifacts = publication_env
    env = prepare(env, 123)

    result = run_script(PUBLISH, env)

    assert result.returncode == 0, result.stderr
    version = (artifacts / "version").read_text(encoding="utf-8").strip()
    immutable = store / f"packages/rdst-cli/versions/{version}/install.sh"
    latest = store / "packages/rdst-cli/install.sh"
    assert immutable.read_bytes() == latest.read_bytes()
    assert f'DEFAULT_RDST_VERSION="{version}"' in latest.read_text(encoding="utf-8")
    assert (store / "packages/rdst-cli/latest-build").read_text().strip() == "123"
    upload_log = Path(env["FAKE_AWS_LOG"]).read_text().splitlines()
    marker_index = upload_log.index("packages/rdst-cli/latest-build")
    checksum_index = upload_log.index("packages/rdst-cli/install.sh.sha256")
    installer_index = upload_log.index("packages/rdst-cli/install.sh")
    assert marker_index < checksum_index < installer_index

    repeated = run_script(PUBLISH, env)
    assert repeated.returncode == 0, repeated.stderr
    assert "identical bytes" in repeated.stdout


@pytest.mark.parametrize("lever", ["PUBLISH_PUBLIC", "PUBLISH_TO_PYPI"])
def test_withholding_public_distribution_publishes_nothing(publication_env, lever):
    # One lever governs the public release: the index upload in
    # deploy_release.sh and the CLI archives here. Withholding it has to leave
    # the published installer exactly as it was, under either name it carries.
    env, store, artifacts = publication_env
    env = prepare(env, 123)
    env.pop("PUBLISH_PUBLIC", None)
    env[lever] = "false"

    result = run_script(PUBLISH, env)

    assert result.returncode == 0, result.stderr
    assert "PUBLISH_PUBLIC is not true" in result.stdout
    assert not (store / "packages/rdst-cli/install.sh").exists()
    assert not (store / "packages/rdst-cli/latest-build").exists()
    assert not Path(env["FAKE_AWS_LOG"]).exists()


def test_the_new_lever_wins_over_the_name_it_replaced(publication_env):
    # The old name is honoured so an operator's existing runbook still works,
    # but it cannot withhold a release the new name asked for.
    env, store, artifacts = publication_env
    env = prepare(env, 123)
    env["PUBLISH_PUBLIC"] = "true"
    env["PUBLISH_TO_PYPI"] = "false"

    result = run_script(PUBLISH, env)

    assert result.returncode == 0, result.stderr
    assert (store / "packages/rdst-cli/install.sh").exists()


def test_publishes_every_platform_archive_before_the_installer(publication_env):
    # install.sh downloads these, so an installer that goes public without them
    # is a script that 404s for everyone who runs it.
    env, store, artifacts = publication_env
    env = prepare(env, 123)

    result = run_script(PUBLISH, env)

    assert result.returncode == 0, result.stderr
    version = (artifacts / "version").read_text(encoding="utf-8").strip()
    upload_log = Path(env["FAKE_AWS_LOG"]).read_text().splitlines()
    installer_index = upload_log.index(
        f"packages/rdst-cli/versions/{version}/install.sh"
    )
    for platform in PLATFORMS:
        archive_key = (
            f"packages/rdst-cli/versions/{version}/rdst-{version}-{platform}.tar.gz"
        )
        assert (store / archive_key).is_file()
        assert (store / f"{archive_key}.sha256").is_file()
        assert upload_log.index(archive_key) < installer_index


@pytest.mark.parametrize("missing", PLATFORMS)
def test_a_missing_platform_archive_publishes_nothing(publication_env, missing):
    env, store, artifacts = publication_env
    env = prepare(env, 123)
    version = (artifacts / "version").read_text(encoding="utf-8").strip()
    name = "rdst-cli-macos" if missing.startswith("macos") else f"rdst-sidecar-{missing}"
    (artifacts.parent / name / f"rdst-{version}-{missing}.tar.gz").unlink()

    result = run_script(PUBLISH, env)

    assert result.returncode != 0
    assert f"Missing {missing} archive artifact" in result.stderr
    assert not (store / "packages/rdst-cli/install.sh").exists()
    assert not (store / f"packages/rdst-cli/versions/{version}").exists()


def test_a_macos_archive_without_a_signing_record_is_not_published(publication_env):
    # An archive from a build that predates the record is as unpublishable as
    # an ad-hoc one: there is nothing saying it carries a Developer ID.
    env, store, artifacts = publication_env
    env = prepare(env, 123)
    (artifacts.parent / "rdst-cli-macos" / "signing").unlink()

    result = run_script(PUBLISH, env)

    assert result.returncode != 0
    assert "Missing macos-arm64 archive artifact" in result.stderr
    assert not (store / "packages/rdst-cli/install.sh").exists()


def test_an_adhoc_signed_macos_archive_is_not_published(publication_env):
    # Change builds sign ad-hoc so the install flow stays testable. Such an
    # archive resolves to a different designated requirement than the desktop
    # app, which is the divergence this distribution exists to close.
    env, store, _ = publication_env
    env = prepare(env, 123, signing="adhoc")

    result = run_script(PUBLISH, env)

    assert result.returncode != 0
    assert "not Developer ID signed" in result.stderr
    assert not (store / "packages/rdst-cli/install.sh").exists()


def test_refuses_to_overwrite_an_immutable_version(publication_env):
    env, store, artifacts = publication_env
    env = prepare(env, 123)
    first = run_script(PUBLISH, env)
    assert first.returncode == 0, first.stderr
    with (artifacts / "install.sh").open("a", encoding="utf-8") as installer:
        installer.write("\n# changed bytes\n")
    digest = hashlib.sha256((artifacts / "install.sh").read_bytes()).hexdigest()
    (artifacts / "install.sh.sha256").write_text(
        f"{digest}  install.sh\n", encoding="utf-8"
    )

    result = run_script(PUBLISH, env)

    assert result.returncode != 0
    assert "Refusing to overwrite immutable" in result.stderr
    published = store / "packages/rdst-cli/versions/0.1.123/install.sh"
    assert "changed bytes" not in published.read_text(encoding="utf-8")


def test_marker_failure_does_not_publish_assets(publication_env):
    env, store, _ = publication_env
    env = prepare(env, 100)
    first = run_script(PUBLISH, env)
    assert first.returncode == 0, first.stderr
    published_installer = store / "packages/rdst-cli/install.sh"
    published_checksum = store / "packages/rdst-cli/install.sh.sha256"
    previous_bytes = published_installer.read_bytes()
    previous_checksum = published_checksum.read_bytes()

    env = prepare(env, 300)
    failed = run_script(
        PUBLISH,
        env | {"FAKE_AWS_FAIL_UPLOAD_KEY": "packages/rdst-cli/latest-build"},
    )

    assert failed.returncode != 0
    assert published_installer.read_bytes() == previous_bytes
    assert (store / "packages/rdst-cli/latest-build").read_text().strip() == "100"
    assert published_checksum.read_bytes() == previous_checksum

    older_env = prepare(env, 200)
    older = run_script(PUBLISH, older_env)
    assert older.returncode == 0, older.stderr
    assert (store / "packages/rdst-cli/latest-build").read_text().strip() == "200"
    assert 'DEFAULT_RDST_VERSION="0.1.200"' in published_installer.read_text()


def test_asset_failure_reserves_channel_against_older_build(publication_env):
    env, store, _ = publication_env
    env = prepare(env, 100)
    first = run_script(PUBLISH, env)
    assert first.returncode == 0, first.stderr
    published_installer = store / "packages/rdst-cli/install.sh"
    previous_bytes = published_installer.read_bytes()

    env = prepare(env, 300)
    failed = run_script(
        PUBLISH,
        env | {"FAKE_AWS_FAIL_UPLOAD_KEY": "packages/rdst-cli/install.sh"},
    )
    assert failed.returncode != 0
    assert published_installer.read_bytes() == previous_bytes
    assert (store / "packages/rdst-cli/latest-build").read_text().strip() == "300"

    older_env = prepare(env, 200)
    older = run_script(PUBLISH, older_env)
    assert older.returncode == 0, older.stderr
    assert "newer installer build (300)" in older.stdout
    assert published_installer.read_bytes() == previous_bytes

    retry_env = prepare(env, 300)
    retry = run_script(PUBLISH, retry_env)
    assert retry.returncode == 0, retry.stderr
    assert 'DEFAULT_RDST_VERSION="0.1.300"' in published_installer.read_text()


def test_older_build_cannot_rewind_latest(publication_env):
    env, store, _ = publication_env
    latest_build = store / "packages/rdst-cli/latest-build"
    latest_build.parent.mkdir(parents=True)
    latest_build.write_text("999\n", encoding="utf-8")
    current = store / "packages/rdst-cli/install.sh"
    current.write_text("newer installer\n", encoding="utf-8")
    env = prepare(env, 123)

    result = run_script(PUBLISH, env)

    assert result.returncode == 0, result.stderr
    assert "newer installer build" in result.stdout
    assert current.read_text(encoding="utf-8") == "newer installer\n"
    assert latest_build.read_text(encoding="utf-8") == "999\n"
