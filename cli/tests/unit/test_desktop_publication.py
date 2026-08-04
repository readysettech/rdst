from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

UPLOAD = Path(__file__).parents[2] / ".buildkite" / "upload_rdst_desktop_artifacts.sh"


def _write_text(path: Path, contents: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.write(contents)


def _write_executable(path: Path, contents: str) -> None:
    _write_text(path, contents)
    path.chmod(0o755)


def _stage_windows_artifacts(root: Path, version: str, payload: bytes) -> None:
    for child in root.rglob("*"):
        if child.is_file():
            child.unlink()
    update = root / "update"
    update.mkdir(parents=True, exist_ok=True)
    installer = root / f"rdst-desktop-{version}-x64.exe"
    installer.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    _write_text(root / "SHA256SUMS", f"{digest}  {installer.name}\n")
    _write_text(
        update / "latest.yml",
        f"version: {version}\nfiles:\n  - url: {installer.name}\n",
    )
    (update / f"{installer.name}.blockmap").write_bytes(b"blockmap")


@pytest.fixture
def publication_env(tmp_path: Path):
    fake_bin = tmp_path / "bin"
    artifacts = tmp_path / "artifacts"
    store = tmp_path / "s3"
    steps = tmp_path / "buildkite-steps"
    fake_bin.mkdir()
    artifacts.mkdir()
    store.mkdir()
    steps.touch()

    _write_executable(
        fake_bin / "buildkite-agent",
        r"""#!/bin/bash
set -euo pipefail
if [[ "$1 $2" == "artifact download" ]]; then
  destination="$4"
  mkdir -p "$destination"
  if [[ "$3" == *'/update/'* ]]; then
    mkdir -p "$destination/update"
    cp "$FAKE_DESKTOP_ARTIFACTS"/update/* "$destination/update/"
  else
    find "$FAKE_DESKTOP_ARTIFACTS" -maxdepth 1 -type f -exec cp {} "$destination/" \;
  fi
elif [[ "$1" == "annotate" ]]; then
  cat >/dev/null
elif [[ "$1 $2" == "pipeline upload" ]]; then
  pipeline="$(cat "$3")"
  key="$(printf '%s\n' "$pipeline" | awk -F'"' '/^[[:space:]]+key:/ { print $2; exit }')"
  if grep -Fqx "$key" "$FAKE_BUILDKITE_STEPS"; then
    echo "The key \"$key\" has already been used by another step in this build" >&2
    exit 1
  fi
  printf '%s\n' "$key" >> "$FAKE_BUILDKITE_STEPS"
else
  echo "unexpected buildkite-agent command: $*" >&2
  exit 2
fi
""",
    )
    _write_executable(
        fake_bin / "aws",
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
elif [[ "$1 $2" == "s3 cp" ]]; then
  source="$3"
  destination="$4"
  if [[ "$source" == s3://* ]]; then
    key="${source#s3://*/}"
    [[ -f "$store/$key" ]] || { echo NoSuchKey >&2; exit 1; }
    if [[ "$destination" == "-" ]]; then
      cat "$store/$key"
    else
      cp "$store/$key" "$destination"
    fi
  else
    key="${destination#s3://*/}"
    if [[ "${FAKE_AWS_FAIL_UPLOAD_KEY:-}" == "$key" ]]; then
      echo "forced upload failure: $key" >&2
      exit 1
    fi
    mkdir -p "$(dirname "$store/$key")"
    cp "$source" "$store/$key"
  fi
else
  echo "unexpected aws command: $*" >&2
  exit 2
fi
""",
    )

    env = os.environ | {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "BUILDKITE_BUILD_CHECKOUT_PATH": str(tmp_path),
        "FAKE_DESKTOP_ARTIFACTS": str(artifacts),
        "FAKE_S3_STORE": str(store),
        "FAKE_BUILDKITE_STEPS": str(steps),
        "RDST_DESKTOP_ARTIFACT_SOURCE": "desktop/*",
        "RDST_DESKTOP_UPDATE_ARTIFACT_SOURCE": "desktop/update/*",
        "RDST_DESKTOP_DESTINATION_BASE": "s3://bucket/packages/rdst-desktop",
        "RDST_DESKTOP_PUBLIC_BASE_URL": "https://example.test/rdst-desktop",
        "RDST_DESKTOP_IS_MAIN": "true",
    }
    return env, store, artifacts, steps


def _publish(env: dict[str, str], build: int):
    return subprocess.run(
        [os.environ.get("BASH", "bash"), str(UPLOAD), "windows", "*.exe"],
        env=env | {"BUILDKITE_BUILD_NUMBER": str(build)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_older_desktop_build_cannot_rewind_update_channel(publication_env):
    env, store, artifacts, steps = publication_env
    _stage_windows_artifacts(artifacts, "0.1.900", b"newer")
    newer = _publish(env, 900)
    assert newer.returncode == 0, newer.stderr

    repeated = _publish(env, 900)
    assert repeated.returncode == 0, repeated.stderr
    assert "Announcement step already exists" in repeated.stdout
    assert steps.read_text().splitlines() == ["announce-rdst-desktop-windows"]

    latest = store / "packages/rdst-desktop/windows/update/latest.yml"
    marker = store / "packages/rdst-desktop/windows/update/latest-build"
    assert "version: 0.1.900" in latest.read_text(encoding="utf-8")
    assert marker.read_text(encoding="utf-8").strip() == "900"

    _stage_windows_artifacts(artifacts, "0.1.800", b"older")
    older = _publish(env, 800)
    assert older.returncode == 0, older.stderr
    assert "newer windows desktop build (900)" in older.stdout
    assert "version: 0.1.900" in latest.read_text(encoding="utf-8")
    assert marker.read_text(encoding="utf-8").strip() == "900"
    assert steps.read_text().splitlines() == ["announce-rdst-desktop-windows"]


def test_interrupted_channel_publication_can_be_retried(publication_env):
    env, store, artifacts, _ = publication_env
    _stage_windows_artifacts(artifacts, "0.1.700", b"previous")
    previous = _publish(env, 700)
    assert previous.returncode == 0, previous.stderr

    _stage_windows_artifacts(artifacts, "0.1.900", b"current")
    metadata_key = "packages/rdst-desktop/windows/update/latest.yml"
    interrupted = _publish(
        env | {"FAKE_AWS_FAIL_UPLOAD_KEY": metadata_key},
        900,
    )
    assert interrupted.returncode != 0
    marker = store / "packages/rdst-desktop/windows/update/latest-build"
    latest = store / metadata_key
    assert marker.read_text(encoding="utf-8").strip() == "900"
    assert "version: 0.1.700" in latest.read_text(encoding="utf-8")

    retry = _publish(env, 900)
    assert retry.returncode == 0, retry.stderr
    assert "Repairing windows/update/latest.yml" in retry.stdout
    assert "version: 0.1.900" in latest.read_text(encoding="utf-8")


def test_desktop_build_cannot_replace_published_bytes(publication_env):
    env, store, artifacts, _ = publication_env
    _stage_windows_artifacts(artifacts, "0.1.900", b"original")
    first = _publish(env, 900)
    assert first.returncode == 0, first.stderr

    published = (
        store
        / "packages/rdst-desktop/windows/main/build-900/rdst-desktop-0.1.900-x64.exe"
    )
    _stage_windows_artifacts(artifacts, "0.1.900", b"replacement")
    repeated = _publish(env, 900)

    assert repeated.returncode != 0
    assert "Refusing to overwrite immutable desktop object" in repeated.stderr
    assert published.read_bytes() == b"original"


def test_desktop_build_cannot_replace_published_metadata(publication_env):
    env, store, artifacts, _ = publication_env
    _stage_windows_artifacts(artifacts, "0.1.900", b"original")
    first = _publish(env, 900)
    assert first.returncode == 0, first.stderr

    latest = store / "packages/rdst-desktop/windows/update/latest.yml"
    published_metadata = latest.read_bytes()
    with (artifacts / "update/latest.yml").open("a", encoding="utf-8") as metadata:
        metadata.write("releaseNotes: changed\n")
    repeated = _publish(env, 900)

    assert repeated.returncode != 0
    assert "Refusing to replace channel metadata" in repeated.stderr
    assert latest.read_bytes() == published_metadata
