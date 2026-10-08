from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

RDST_DIR = Path(__file__).parents[2]
DETECT_PIPELINE = RDST_DIR / ".buildkite" / "detect_pipeline_areas.sh"
DESKTOP_BUILD_LIB = RDST_DIR / ".buildkite" / "desktop_build_lib.sh"
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


def desktop_pipeline(tmp_path: Path, branch: str, **env: str) -> dict:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    git = fake_bin / "git"
    git.write_text(
        "#!/bin/sh\nprintf '%s\\n' web-apps/apps/rdst-desktop/electron-builder.yml\n",
        encoding="utf-8",
    )
    git.chmod(0o755)
    env = os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "BUILDKITE_BRANCH": branch} | env
    result = run_script(DETECT_PIPELINE, env)
    assert result.returncode == 0, result.stderr
    pipeline = yaml.safe_load(result.stdout)
    return {step.get("key"): step for step in flatten_steps(pipeline["steps"]) if "key" in step}


PROMOTION = {"RDST_PIPELINE_MODE": "promote-staging"}


def test_desktop_pipeline_builds_smokes_and_publishes_windows(tmp_path: Path):
    steps = desktop_pipeline(tmp_path, "main")
    windows_git_env = {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.longpaths",
        "GIT_CONFIG_VALUE_0": "true",
    }
    for key, mode in (
        ("prepare-rdst-desktop-windows", "-Mode Prepare"),
        ("package-rdst-desktop-windows-release", "-Mode Package -Flavor release"),
        ("package-rdst-desktop-windows-staging", "-Mode Package -Flavor staging"),
    ):
        step = steps[key]
        assert step["agents"]["queue"] == "windows-c6a-2xlarge"
        assert step["env"] == windows_git_env
        assert "artifact_paths" not in step
        assert step["command"].endswith(f"build_rdst_desktop_windows.ps1 {mode}")

    smoke = steps["smoke-rdst-desktop-windows"]
    assert smoke["depends_on"] == "package-rdst-desktop-windows-release"
    assert smoke["agents"]["queue"] == "windows-c6a-2xlarge"
    assert smoke["env"] == windows_git_env
    assert smoke["command"].endswith("-Flavor release")

    publish = steps["publish-rdst-desktop-release"]
    assert "smoke-rdst-desktop-windows" in publish["depends_on"]
    assert publish["env"] == {"RDST_DESKTOP_FLAVOR": "release"}
    assert publish["command"].endswith("upload_rdst_desktop_artifacts.sh all")
    assert "smoke-rdst-desktop-windows" in steps["release-approval"]["depends_on"]


@pytest.mark.parametrize(
    ("branch", "env", "flavors"),
    [
        ("main", {}, ["release", "staging"]),
        ("refs/changes/52/16352/6", {}, ["cl"]),
        ("refs/changes/52/16352/6", PROMOTION, ["staging"]),
    ],
)
def test_every_flavor_is_packaged_from_one_prepared_app(tmp_path: Path, branch, env, flavors):
    steps = desktop_pipeline(tmp_path, branch, **env)
    packaged = sorted(key.rsplit("-", 1)[1] for key in steps if key.startswith("package-rdst-desktop-linux-"))
    assert packaged == sorted(flavors)
    for flavor in flavors:
        for platform in ("linux", "macos", "windows"):
            package = steps[f"package-rdst-desktop-{platform}-{flavor}"]
            assert package["depends_on"] == f"prepare-rdst-desktop-{platform}"
            if platform != "windows":  # The Windows script uploads its own artifacts.
                # Each job uploads only its own flavor's packages.
                assert package["artifact_paths"] == [
                    f"rdst/.buildkite-artifacts/rdst-desktop-{platform}-{flavor}/*",
                    f"rdst/.buildkite-artifacts/rdst-desktop-{platform}-{flavor}/update/*",
                ]
                assert package["command"].endswith(f"package_rdst_desktop.sh {platform} {flavor}")
        notarize = steps[f"notarize-rdst-desktop-macos-{flavor}"]
        assert notarize["depends_on"] == f"package-rdst-desktop-macos-{flavor}"
    for platform in ("linux", "macos"):
        assert steps[f"prepare-rdst-desktop-{platform}"]["artifact_paths"] == [
            f"rdst/.buildkite-artifacts/rdst-desktop-{platform}-prepared/*"
        ]
    # The smokes install the first flavor.
    assert steps["smoke-rdst-desktop-linux"]["command"].endswith(f"smoke_rdst_desktop_linux.sh {flavors[0]}")


def test_nothing_on_the_release_path_waits_for_staging(tmp_path: Path):
    steps = desktop_pipeline(tmp_path, "main")

    def upstream(key, seen):
        dependencies = steps[key].get("depends_on") or []
        for dependency in [dependencies] if isinstance(dependencies, str) else dependencies:
            if dependency not in seen:
                seen.add(dependency)
                upstream(dependency, seen)
        return seen

    for key in ("release-approval", "announce-release-candidate", "publish-rdst-desktop-release"):
        assert not [dep for dep in upstream(key, set()) if "staging" in dep], key
    # Staging shares agent queues with the release path and never fails the build.
    for key in (
        "package-rdst-desktop-linux-staging",
        "package-rdst-desktop-macos-staging",
        "package-rdst-desktop-windows-staging",
    ):
        assert steps[key]["priority"] == -1
        assert steps[key]["soft_fail"] is True
        assert steps[key.replace("staging", "release")]["soft_fail"] is False
    assert steps["notarize-rdst-desktop-macos-staging"]["soft_fail"] is True


def test_main_builds_publish_the_staging_channel_off_the_release_path(tmp_path: Path):
    steps = desktop_pipeline(tmp_path, "main")

    staging = steps["publish-rdst-desktop-staging"]
    assert staging["env"] == {"RDST_DESKTOP_FLAVOR": "staging"}
    assert "release-approval" not in staging["depends_on"]
    assert "notarize-rdst-desktop-macos-staging" in staging["depends_on"]
    assert staging["soft_fail"] is True
    release = steps["publish-rdst-desktop-release"]
    assert "release-approval" in release["depends_on"]
    assert "notarize-rdst-desktop-macos-release" in release["depends_on"]
    assert release["soft_fail"] is False
    assert release["concurrency_group"] != staging["concurrency_group"]


def test_every_main_build_republishes_staging(tmp_path: Path):
    # A keyservice-only merge still replaces a staging promotion's desktop app.
    pipeline = generate_pipeline(tmp_path, branch="main", changed=("rdst/keyservice/src/index.py",))
    steps = {step.get("key"): step for step in flatten_steps(pipeline["steps"]) if "key" in step}
    assert "publish-rdst-desktop-staging" in steps
    assert "keyservice-staging-deploy" in steps


def test_desktop_groups_hold_no_release_gated_step(tmp_path: Path):
    # Canvas draws a group after its latest dependency, so one release-gated
    # step inside a build group would draw every build after the Release button.
    steps = desktop_pipeline(tmp_path, "main")
    for key in ("rdst-desktop", "rdst-desktop-release", "rdst-desktop-staging", "smoke-rdst-desktop"):
        for step in steps[key]["steps"]:
            depends_on = step.get("depends_on", [])
            depends_on = [depends_on] if isinstance(depends_on, str) else depends_on
            assert "release-approval" not in depends_on, (key, step["key"])


def test_change_builds_package_only_their_own_flavor(tmp_path: Path):
    steps = desktop_pipeline(tmp_path, "refs/changes/90/14490/5")
    assert [key for key in steps if key.startswith("publish-rdst-desktop")] == ["publish-rdst-desktop-cl"]
    assert "keyservice-staging-deploy" not in steps


def test_a_staging_promotion_builds_signs_and_publishes_only_staging(tmp_path: Path):
    steps = desktop_pipeline(tmp_path, "refs/changes/52/16352/6", **PROMOTION)
    assert set(steps) == {
        "rdst-cli",
        "build-rdst-sidecar-linux",
        "rdst-desktop",
        "prepare-rdst-desktop-linux",
        "prepare-rdst-desktop-macos",
        "prepare-rdst-desktop-windows",
        "rdst-desktop-staging",
        "package-rdst-desktop-linux-staging",
        "package-rdst-desktop-macos-staging",
        "package-rdst-desktop-windows-staging",
        "notarize-rdst-desktop-macos-staging",
        "smoke-rdst-desktop",
        "smoke-rdst-desktop-linux",
        "smoke-rdst-desktop-macos",
        "smoke-rdst-desktop-windows",
        "publish-rdst-desktop-staging",
        "keyservice-promotion-migrations",
        "keyservice-staging-deploy",
    }
    # A promotion's staging packages are the build itself: they fail it.
    assert steps["package-rdst-desktop-macos-staging"]["soft_fail"] is False
    # Main builds and promotions take turns deploying staging, a promotion
    # deploys only main's migrations, and a refused deploy publishes nothing.
    deploy = steps["keyservice-staging-deploy"]
    assert deploy["concurrency_group"] == "rdst-keyservice-staging-deploy"
    assert deploy["depends_on"] == "keyservice-promotion-migrations"
    assert "keyservice-staging-deploy" in steps["publish-rdst-desktop-staging"]["depends_on"]


def test_a_promotion_deploys_only_mains_migrations(tmp_path: Path):
    guard = desktop_pipeline(tmp_path, "refs/changes/52/16352/6", **PROMOTION)[
        "keyservice-promotion-migrations"
    ]["command"]
    # The guard only fetches main and asks whether the migrations differ from it.
    fake_bin = tmp_path / "guard-bin"
    fake_bin.mkdir()
    (fake_bin / "git").write_text(
        """#!/bin/sh
echo "$*" >> "$GIT_LOG"
case "$1 $2" in
  "fetch --quiet") exit 0 ;;
  "diff --quiet") exit "$MIGRATIONS_DIFFER" ;;
  "diff --stat") echo " rdst/keyservice/migrations/0002_change.sql | 1 +" ;;
  *) exit 2 ;;
esac
""",
        encoding="utf-8",
    )
    (fake_bin / "git").chmod(0o755)

    def promote(differ: bool) -> subprocess.CompletedProcess:
        env = os.environ | {
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GIT_LOG": str(tmp_path / "git.log"),
            "MIGRATIONS_DIFFER": "1" if differ else "0",
        }
        return subprocess.run(["bash", "-euc", guard], env=env, capture_output=True, text=True)

    assert promote(differ=False).returncode == 0
    # Even an appended migration would stay in staging's one database under main.
    refused = promote(differ=True)
    assert refused.returncode == 1
    assert "only a change with main's keyservice migrations can be promoted" in refused.stderr
    assert "diff --quiet FETCH_HEAD HEAD -- rdst/keyservice/migrations" in (
        tmp_path / "git.log"
    ).read_text()


@pytest.mark.parametrize("branch", ["refs/changes/52/16352", "refs/changes/52/16352/6"])
def test_a_promotion_builds_a_changes_ref(tmp_path: Path, branch):
    steps = desktop_pipeline(tmp_path, branch, **PROMOTION)
    assert "publish-rdst-desktop-staging" in steps


@pytest.mark.parametrize(
    ("branch", "env"),
    [
        ("main", PROMOTION),
        ("refs/heads/feature", PROMOTION),
        ("refs/changes/52/16352/6/x", PROMOTION),
        ("refs/changes/52/16352/6", {"RDST_PIPELINE_MODE": "promote-prod"}),
    ],
)
def test_a_promotion_refuses_anything_but_a_change(tmp_path: Path, branch, env):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "git").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (fake_bin / "git").chmod(0o755)
    result = run_script(
        DETECT_PIPELINE,
        os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}", "BUILDKITE_BRANCH": branch} | env,
    )
    assert result.returncode == 1
    assert result.stdout == ""


def test_staging_restores_exactly_the_release_candidates_prepared_app(tmp_path: Path):
    release_app = tmp_path / "release"
    (release_app / "out" / "renderer").mkdir(parents=True)
    (release_app / "out" / "renderer" / "index.html").write_text("<html>", encoding="utf-8")
    sidecar = release_app / "sidecar" / "linux-x64" / "rdst"
    sidecar.mkdir(parents=True)
    (sidecar / "rdst").write_bytes(b"frozen sidecar")
    (sidecar / "lib.so.1").write_bytes(b"library")
    (sidecar / "lib.so").symlink_to("lib.so.1")
    prepared = tmp_path / "prepared"
    staged = run_build_lib("stage_prepared_tree", str(release_app), str(prepared))
    assert staged.returncode == 0, staged.stderr

    staging_app = tmp_path / "staging"
    (staging_app / "out").mkdir(parents=True)
    (staging_app / "out" / "stale.js").write_text("old", encoding="utf-8")
    restored = run_build_lib("restore_prepared_tree", str(staging_app), str(prepared))
    assert restored.returncode == 0, restored.stderr
    assert not (staging_app / "out" / "stale.js").exists()
    assert (staging_app / "out" / "renderer" / "index.html").read_text(encoding="utf-8") == "<html>"
    assert os.readlink(staging_app / "sidecar" / "linux-x64" / "rdst" / "lib.so") == "lib.so.1"

    # A sidecar that is not the one the release packaged is refused.
    (prepared / "sidecar.sha256").write_text("0" * 64 + "\n", encoding="utf-8")
    refused = run_build_lib("restore_prepared_tree", str(staging_app), str(prepared))
    assert refused.returncode == 1
    assert "is not the prepared" in refused.stderr


def test_update_metadata_and_feeds_are_checked(tmp_path: Path):
    dist = tmp_path / "dist"
    artifacts = tmp_path / "artifacts"
    dist.mkdir()
    artifacts.mkdir()
    (dist / "latest-linux.yml").write_text(
        "version: 0.1.1\nfiles:\n  - url: app.AppImage\n", encoding="utf-8"
    )
    (dist / "app.AppImage.blockmap").write_bytes(b"blockmap")
    missing = run_build_lib("stage_update_metadata", str(dist), str(artifacts), "latest-linux.yml")
    assert missing.returncode == 1
    assert "references missing file: app.AppImage" in missing.stderr
    (artifacts / "app.AppImage").write_bytes(b"app")
    staged = run_build_lib("stage_update_metadata", str(dist), str(artifacts), "latest-linux.yml")
    assert staged.returncode == 0, staged.stderr
    assert sorted(path.name for path in (artifacts / "update").iterdir()) == [
        "app.AppImage.blockmap",
        "latest-linux.yml",
    ]

    staging = "https://downloads.readyset.io/packages/rdst-desktop-staging/linux/update"
    root = tmp_path / "package"
    (root / "resources").mkdir(parents=True)
    config = root / "resources" / "app-update.yml"
    config.write_text(f"provider: generic\nurl: {staging}\n", encoding="utf-8")
    assert run_build_lib("verify_update_feed", str(root), "pkg", staging).returncode == 0
    production = staging.replace("rdst-desktop-staging", "rdst-desktop")
    wrong = run_build_lib("verify_update_feed", str(root), "pkg", production)
    assert wrong.returncode == 1
    assert f"expected '{production}'" in wrong.stderr
    # A macOS bundle keeps it in Contents/Resources.
    bundle = tmp_path / "RDST Desktop Staging.app" / "Contents" / "Resources"
    bundle.mkdir(parents=True)
    (bundle / "app-update.yml").write_text(f"provider: generic\nurl: {staging}\n", encoding="utf-8")
    macos = run_build_lib("verify_update_feed", str(bundle.parent.parent), "bundle", staging)
    assert macos.returncode == 0, macos.stderr


def package_root(tmp_path: Path, metadata: dict, marker: dict | None = None) -> Path:
    """Lay out an unpacked package: app.asar plus the sidecar's instance marker."""
    resources = tmp_path / "app" / "resources"
    resources.mkdir(parents=True)
    # asar stores package.json uncompressed between its header and other files.
    (resources / "app.asar").write_bytes(
        b"\x04\x00\x00\x00header"
        + json.dumps({"name": "rdst-desktop", "version": "0.1.1", **metadata}).encode()
        + b"\x00more files"
    )
    if marker is not None:
        sidecar = tmp_path / "app" / "sidecar" / "rdst"
        sidecar.mkdir(parents=True)
        (sidecar / "instance.json").write_text(json.dumps(marker), encoding="utf-8")
    return tmp_path / "app"


def run_build_lib(function: str, *args: str):
    return subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail; source "$1"; shift; {function} "$@"',
            "bash",
            str(DESKTOP_BUILD_LIB),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_staging_variant_must_carry_the_staging_keyservice(tmp_path: Path):
    staging = "https://keyservice-staging.readyset.io"

    ok = package_root(tmp_path / "ok", {"rdstKeyservice": staging})
    result = run_build_lib(
        "verify_packaged_metadata", str(ok), "pkg", "rdstKeyservice", staging
    )
    assert result.returncode == 0, result.stderr

    wrong = {"prod": "", "other": "https://keyservice.readyset.io", "none": None}
    for case, packaged in wrong.items():
        metadata = {} if packaged is None else {"rdstKeyservice": packaged}
        root = package_root(tmp_path / case, metadata)
        result = run_build_lib(
            "verify_packaged_metadata", str(root), "pkg", "rdstKeyservice", staging
        )
        assert result.returncode == 1, case
        assert f"expected '{staging}'" in result.stderr


def test_packages_carry_exactly_their_own_instance(tmp_path: Path):
    staging = package_root(
        tmp_path / "staging", {"rdstInstance": "staging"}, {"instance": "staging"}
    )
    production = package_root(tmp_path / "production", {"rdstInstance": ""}, {"instance": ""})

    def verify(root: Path, instance: str, *keyservice: str) -> int:
        return run_build_lib(
            "verify_packaged_instance", str(root), "pkg", instance, *keyservice
        ).returncode

    assert verify(staging, "staging") == 0
    assert verify(production, "") == 0
    keyed = package_root(
        tmp_path / "keyed",
        {"rdstInstance": "staging"},
        {"instance": "staging", "keyservice": "https://keyservice-staging.readyset.io"},
    )
    assert verify(keyed, "staging", "https://keyservice-staging.readyset.io") == 0
    assert verify(keyed, "staging", "https://keyservice.readyset.io") == 1
    assert verify(staging, "staging", "https://keyservice-staging.readyset.io") == 1

    mismatched = {
        "production checked as staging": (production, "staging"),
        "staging checked as production": (staging, ""),
        "staging checked as cl": (staging, "cl"),
        "metadata without a marker": (
            package_root(tmp_path / "no-marker", {"rdstInstance": "staging"}),
            "staging",
        ),
        "production with a staging marker": (
            package_root(tmp_path / "stray-marker", {}, {"instance": "staging"}),
            "",
        ),
        "production without a marker": (package_root(tmp_path / "unmarked", {}), ""),
    }
    for case, (root, instance) in mismatched.items():
        assert verify(root, instance) == 1, case


def test_windows_build_uploads_normalized_artifact_paths():
    script = WINDOWS_DESKTOP_BUILD.read_text(encoding="utf-8")

    assert "'artifact', 'upload', '--experiment', 'normalised-upload-paths'" in script
    assert '"rdst/.buildkite-artifacts/rdst-desktop-windows-$Flavor/*"' in script
    assert '"rdst/.buildkite-artifacts/rdst-desktop-windows-$Flavor/update/*"' in script
    assert "'rdst/.buildkite-artifacts/rdst-desktop-windows-prepared/*'" in script


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
    # Put over the flavor's own rendered config.
    assert "extends" not in signing_config
    assert '"extends: ./$($facts.RDST_DESKTOP_BUILDER_CONFIG)`n$signingConfig"' in script
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


def generate_pipeline(
    tmp_path: Path,
    *,
    branch: str,
    changed: tuple[str, ...] = (),
):
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


def test_live_hosted_inference_e2e_runs_against_the_preview(tmp_path: Path):
    """The live account and hosted-inference suite gates every CL that deploys a
    preview Worker, and the preview is torn down only after it has run."""
    pipeline = generate_pipeline(
        tmp_path,
        branch="cl/hosted-inference",
        changed=("rdst/keyservice/src/account_auth.py",),
    )
    steps = {
        step.get("key"): step
        for step in flatten_steps(pipeline["steps"])
        if "key" in step
    }
    assert "keyservice-preview-deploy" in steps
    assert steps["rdst-web-keyservice-e2e"]["depends_on"] == "keyservice-preview-deploy"
    assert "run-web-e2e-ci.sh" in steps["rdst-web-keyservice-e2e"]["command"]
    assert steps["annotate-rdst-web-keyservice-e2e"]["depends_on"] == [
        "rdst-web-keyservice-e2e"
    ]
    for engine in ("postgresql", "mysql"):
        step = steps[f"rdst-continuous-monitoring-e2e-{engine}"]
        assert step["depends_on"] == "keyservice-preview-deploy"
        assert "run-continuous-monitoring-ci.sh" in step["command"]
    assert steps["keyservice-preview-teardown"]["depends_on"] == [
        "keyservice-preview-deploy",
        "rdst-web-keyservice-e2e",
        "rdst-continuous-monitoring-e2e-postgresql",
        "rdst-continuous-monitoring-e2e-mysql",
    ]


def test_publish_installer_downloads_every_platform_archive(tmp_path: Path):
    pipeline = generate_pipeline(tmp_path, branch="main")
    steps = {step.get("key"): step for step in pipeline["steps"] if "key" in step}
    publish = steps["publish-installer"]

    assert "rdst-sidecar-linux-*" in publish["command"]
    assert "rdst-cli-macos" in publish["command"]
    for dependency in ("build-rdst-sidecar-linux", "build-rdst-cli-macos"):
        assert dependency in publish["depends_on"]


def test_the_macos_cli_archive_comes_from_its_own_signing_step(tmp_path: Path):
    # The CLI signs itself in a dedicated step so the archive stays independent
    # of the desktop packages; only that step may publish it.
    pipeline = generate_pipeline(tmp_path, branch="main")
    steps = {step.get("key"): step for step in flatten_steps(pipeline["steps"]) if "key" in step}

    assert "rdst/.buildkite-artifacts/rdst-cli-macos/*" in steps["build-rdst-cli-macos"]["artifact_paths"]
    for key in ("prepare-rdst-desktop-macos", "package-rdst-desktop-macos-release"):
        assert not any("rdst-cli-macos" in path for path in steps[key]["artifact_paths"])
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
    for key in ("smoke-rdst-installer-macos", "smoke-rdst-installer-ubuntu"):
        assert "rdst-installer/install.sh" in steps[key]["command"]
    # basic mode: a change build has no Developer ID certificate to sign with,
    # so its archive is ad-hoc and the update check a release runs is skipped.
    assert smoke["command"].rstrip().endswith("basic")
    ubuntu = steps["smoke-rdst-installer-ubuntu"]
    assert ubuntu["command"].rstrip().endswith("basic")
    # Both architectures, each on its own queue.
    assert ubuntu["matrix"] == ["c7a-xlarge", "c7g-xlarge"]
    assert ubuntu["agents"]["queue"] == "{{matrix}}"


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

    smokes = ["release-smoke-installer-linux", "release-smoke-installer-macos"]
    for smoke in smokes:
        assert smoke in steps["release-approval"]["depends_on"]
        assert "publish-release" not in upstream(smoke)
    # Publishing stays behind the button, which it used to reach only through
    # the chain that ran the smokes.
    assert "release-approval" in steps["publish-installer"]["depends_on"]


def test_linux_release_smokes_cover_every_image_and_architecture(tmp_path: Path):
    pipeline = generate_pipeline(tmp_path, branch="main")
    steps = {step.get("key"): step for step in flatten_steps(pipeline["steps"]) if "key" in step}
    smoke = steps["release-smoke-installer-linux"]
    assert smoke["matrix"]["setup"] == {
        "image": [
            "${PUBLIC_REPO_BASE:-public.ecr.aws}/docker/library/ubuntu:24.04",
            "quay.io/fedora/fedora-minimal:42",
        ],
        "queue": ["c7a-xlarge", "c7g-xlarge"],
    }
    docker = smoke["plugins"][0]["docker#v5.11.0"]
    assert docker["image"] == "{{matrix.image}}"
    assert docker["mount-buildkite-agent"] is True
    assert smoke["agents"]["queue"] == "{{matrix.queue}}"
    # Ubuntu trusts no certificate authority until it installs them.
    command = smoke["command"]
    assert command.index("apt-get install") < command.index("buildkite-agent artifact download")


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
