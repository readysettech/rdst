from __future__ import annotations

import hashlib
import io
import os
import platform
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

INSTALLER = Path(__file__).parents[2] / "install.sh"

ARCHIVE_VERSION = "0.1.1700"
BASE_URL = "https://downloads.test.invalid/rdst-cli"
# What a build smoking its own artifacts serves them over.
LOCAL_BASE_URL = "http://127.0.0.1/rdst-cli"


def platform_slug() -> str:
    machine = platform.machine()
    if platform.system() == "Darwin":
        return "macos-arm64"
    return "linux-arm64" if machine in ("arm64", "aarch64") else "linux-x86_64"


def publish_archive(
    serve: Path,
    workspace: Path,
    *,
    version: str = ARCHIVE_VERSION,
    reported_version: str | None = None,
    root_name: str = "rdst",
    entrypoints: tuple[str, ...] = ("rdst", "rdst-mcp"),
    escaping_link: str | None = None,
    escaping_member: str | None = None,
    noisy: bool = False,
    checksum: str | None = None,
) -> Path:
    """Publish a stub RDST archive the way the build scripts lay one out."""
    # Republishing a version has to start from a clean tree, or leftovers from
    # the previous call survive into the new archive.
    build = workspace / f"build-{version}"
    shutil.rmtree(build, ignore_errors=True)
    root = build / root_name
    (root / "_internal").mkdir(parents=True, exist_ok=True)
    (root / "_internal" / "libpython.so").write_text("lib", encoding="utf-8")

    executable = root / "rdst"
    # A frozen build shares stdout and stderr with its libraries, so noisy=True
    # stands in for one that prints around its own version line.
    noise = 'echo "[telemetry] api_key is empty" >&2\n' if noisy else ""
    executable.write_text(
        "#!/bin/sh\n"
        + noise
        + f'echo "Readyset Data and SQL Toolkit (rdst) version {reported_version or version}"\n'
        + noise,
        encoding="utf-8",
    )
    executable.chmod(0o755)
    if "rdst" not in entrypoints:
        executable.unlink()
    if "rdst-mcp" in entrypoints:
        (root / "rdst-mcp").symlink_to("rdst")
    if escaping_link is not None:
        (root / "escape").symlink_to(escaping_link)

    archive_name = f"rdst-{version}-{platform_slug()}.tar.gz"
    destination = serve / "versions" / version
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / archive_name
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(root, arcname=root_name)
        if escaping_member is not None:
            # Written through the member name, which is the one route the
            # post-extraction symlink check cannot see.
            payload = b"owned"
            info = tarfile.TarInfo(escaping_member)
            info.size = len(payload)
            handle.addfile(info, io.BytesIO(payload))
    digest = checksum or hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / f"{archive_name}.sha256").write_text(
        f"{digest}  {archive_name}\n", encoding="utf-8"
    )
    return archive


@pytest.fixture
def installer_env(tmp_path: Path):
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    system_bin = tmp_path / "system-bin"
    serve = tmp_path / "serve"
    home.mkdir()
    fake_bin.mkdir()
    system_bin.mkdir()
    serve.mkdir()

    # Keep the installer subprocess isolated from an rdst already installed on
    # the developer or CI host while retaining the ordinary POSIX utilities it
    # exercises. Tests that need an existing rdst add one explicitly.
    for directory in (Path("/usr/bin"), Path("/bin"), Path("/usr/sbin"), Path("/sbin")):
        if not directory.is_dir():
            continue
        for executable in directory.iterdir():
            destination = system_bin / executable.name
            if (
                executable.name in {"rdst", "rdst-mcp"}
                or destination.exists()
                or destination.is_symlink()
            ):
                continue
            destination.symlink_to(executable)

    publish_archive(serve, tmp_path)

    # Stamp the version the way prepare_installer.sh does, so these tests run
    # against the published shape of the script rather than the repo copy.
    installer = tmp_path / "install.sh"
    installer.write_text(
        re.sub(
            r'^DEFAULT_RDST_VERSION="latest"$',
            f'DEFAULT_RDST_VERSION="{ARCHIVE_VERSION}"',
            INSTALLER.read_text(encoding="utf-8"),
            count=1,
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    installer.chmod(0o755)

    # Resolve the request against the published tree instead of the network.
    # The installer still passes its real transport flags, so --proto and the
    # rest stay exercised.
    curl = fake_bin / "curl"
    curl.write_text(
        """#!/bin/sh
set -eu
destination=""
url=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --output) destination="$2"; shift 2 ;;
    --proto) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
if [ -n "${FAKE_DOWNLOAD_WAIT_FILE:-}" ]; then
  touch "${FAKE_DOWNLOAD_WAIT_FILE}.started"
  while [ ! -f "${FAKE_DOWNLOAD_WAIT_FILE}.release" ]; do sleep 0.05; done
fi
if [ "${FAKE_DOWNLOAD_FAIL:-0}" = "1" ]; then
  echo "curl: (22) simulated download failure" >&2
  exit 22
fi
path=${url#*://}
path=${path#*/}
source="$FAKE_SERVE_DIR/${path#rdst-cli/}"
[ -f "$source" ] || { echo "curl: (22) not found: $url" >&2; exit 22; }
cp "$source" "$destination"
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    # The archive is a stub, so stand in for Apple's tooling. Real signature
    # and team verification is covered against a genuinely signed tree.
    # FAKE_CODESIGN_FOREIGN_FILE signs one file in the tree as somebody else,
    # which is what a tampered library looks like to the installer.
    codesign = fake_bin / "codesign"
    codesign.write_text(
        """#!/bin/sh
set -eu
if [ "${FAKE_CODESIGN_INVALID:-0}" = "1" ]; then
  echo "test-stub: signature invalid" >&2
  exit 1
fi
target=""
for argument in "$@"; do
  case "$argument" in
    -*) ;;
    *) target="$argument" ;;
  esac
done
team="${FAKE_CODESIGN_TEAM-MK994N7JPH}"
if [ -n "${FAKE_CODESIGN_FOREIGN_FILE:-}" ] \
  && [ "${target##*/}" = "$FAKE_CODESIGN_FOREIGN_FILE" ]; then
  team="0THERTEAM1"
fi
for argument in "$@"; do
  if [ "$argument" = "-dv" ]; then
    echo "Identifier=rdst" >&2
    # An ad-hoc signature reports no team at all, which FAKE_CODESIGN_TEAM= asks for.
    [ -z "$team" ] || echo "TeamIdentifier=$team" >&2
    exit 0
  fi
done
exit 0
""",
        encoding="utf-8",
    )
    codesign.chmod(0o755)

    # The installer picks the files to verify by asking file(1) which are
    # Mach-O. The stub archive holds shell scripts, so report its executable
    # and its stub library as the loadable code they stand in for.
    file_command = fake_bin / "file"
    file_command.write_text(
        """#!/bin/sh
set -eu
for path in "$@"; do
  if [ "${FAKE_FILE_NO_MACH_O:-0}" = "1" ]; then
    printf '%s: ASCII text\n' "$path"
    continue
  fi
  case "${path##*/}" in
    rdst|*.so) printf '%s: Mach-O 64-bit executable arm64\n' "$path" ;;
    *) printf '%s: ASCII text\n' "$path" ;;
  esac
done
""",
        encoding="utf-8",
    )
    file_command.chmod(0o755)

    for command in ("python", "python3", "sudo", "uv"):
        executable = fake_bin / command
        executable.write_text(
            f"#!/bin/sh\necho '{command} must not be invoked' >&2\nexit 99\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("RDST_", "UV_", "XDG_", "FAKE_"))
    }
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{system_bin}",
            "SHELL": "/bin/sh",
            "XDG_BIN_HOME": str(home / "bin"),
            "XDG_DATA_HOME": str(home / "data"),
            "RDST_INSTALLER_BASE_URL": BASE_URL,
            "FAKE_SERVE_DIR": str(serve),
            "_RDST_TEST_INSTALLER": str(installer),
            "_RDST_TEST_SERVE": str(serve),
            "_RDST_TEST_WORKSPACE": str(tmp_path),
        }
    )
    return env, home


def run_installer(env: dict[str, str], *args: str):
    child_env = env.copy()
    installer = child_env.pop("_RDST_TEST_INSTALLER")
    child_env.pop("_RDST_TEST_SERVE", None)
    child_env.pop("_RDST_TEST_WORKSPACE", None)
    return subprocess.run(
        ["sh", installer, *args],
        env=child_env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def test_pins_the_readyset_signing_team():
    # The signing team is what makes a macOS archive verifiable beyond its
    # checksum, which travels from the same host as the archive itself.
    assert 'APPLE_TEAM_ID="MK994N7JPH"' in INSTALLER.read_text(encoding="utf-8")


def test_installs_without_python_pip_or_sudo(installer_env):
    env, home = installer_env

    result = run_installer(env, "--no-modify-path")

    assert result.returncode == 0, result.stderr
    assert (home / "bin" / "rdst").is_symlink()
    assert (home / "bin" / "rdst-mcp").is_symlink()
    assert (home / "bin" / "rdst-mcp").resolve().is_file()
    active = home / "data" / "rdst" / "tools" / "current"
    assert active.is_symlink()
    assert active.resolve().name.startswith(".rdst-generation-")
    # No interpreter is provisioned any more: the archive is self-contained.
    assert not (home / "data" / "rdst" / "bootstrap").exists()
    assert not (home / "data" / "rdst" / "python").exists()
    state = (home / ".rdst" / "install-state").read_text(encoding="utf-8")
    assert "method=readyset-archive" in state
    assert f"platform={platform_slug()}" in state
    assert f"version {ARCHIVE_VERSION}" in result.stdout


def test_installs_an_exact_version(installer_env):
    env, home = installer_env
    publish_archive(
        Path(env["_RDST_TEST_SERVE"]), Path(env["_RDST_TEST_WORKSPACE"]), version="1.2.3"
    )

    result = run_installer(env, "--version", "1.2.3", "--no-modify-path")

    assert result.returncode == 0, result.stderr
    version = subprocess.run(
        [str(home / "bin" / "rdst"), "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert version.stdout.strip().endswith("1.2.3")


def test_rejects_an_archive_built_for_another_version(installer_env):
    env, home = installer_env
    publish_archive(
        Path(env["_RDST_TEST_SERVE"]),
        Path(env["_RDST_TEST_WORKSPACE"]),
        version="1.2.3",
        reported_version="6.6.6",
    )

    result = run_installer(env, "--version", "1.2.3", "--no-modify-path")

    assert result.returncode != 0
    assert "reports 6.6.6, expected 1.2.3" in result.stderr
    assert not (home / "bin" / "rdst").exists()


def test_rejects_a_flag_as_the_version(installer_env):
    env, home = installer_env

    result = run_installer(env, "--version", "--force", "--no-modify-path")

    assert result.returncode != 0
    assert "invalid version: --force" in result.stderr
    assert not (home / "data" / "rdst").exists()


def test_refuses_to_replace_an_unmanaged_executable(installer_env):
    env, home = installer_env
    bin_dir = home / "bin"
    bin_dir.mkdir()
    existing = bin_dir / "rdst"
    existing.write_text("#!/bin/sh\n", encoding="utf-8")

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "is not managed by this installer" in result.stderr
    assert not (home / ".rdst" / "install-state").exists()


def test_force_does_not_replace_an_unmanaged_target(installer_env):
    env, home = installer_env
    bin_dir = home / "bin"
    bin_dir.mkdir()
    existing = bin_dir / "rdst"
    existing.write_text("#!/bin/sh\n", encoding="utf-8")

    result = run_installer(env, "--force", "--no-modify-path")

    assert result.returncode != 0
    assert "Remove it with its package manager" in result.stderr
    assert existing.read_text(encoding="utf-8") == "#!/bin/sh\n"


def test_refuses_to_shadow_an_existing_path_command(installer_env):
    env, home = installer_env
    path_bin = Path(env["PATH"].split(os.pathsep)[0])
    existing = path_bin / "rdst"
    existing.write_text("#!/bin/sh\n", encoding="utf-8")
    existing.chmod(0o755)

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert f"rdst is already available at {existing}" in result.stderr
    assert not (home / ".rdst" / "install-state").exists()


def test_refuses_to_take_over_an_existing_data_directory(installer_env):
    env, home = installer_env
    data_dir = home / "data" / "rdst"
    data_dir.mkdir(parents=True)
    sentinel = data_dir / "important.txt"
    sentinel.write_text("preserve me", encoding="utf-8")

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "contains data and is not managed" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def test_refuses_a_symbolic_link_data_directory(installer_env):
    env, home = installer_env
    data_home = home / "data"
    data_home.mkdir()
    victim = home / "victim"
    victim.mkdir()
    (data_home / "rdst").symlink_to(victim)

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "must not be a symbolic link" in result.stderr
    assert list(victim.iterdir()) == []


def test_rejects_an_archive_with_the_wrong_checksum(installer_env):
    env, home = installer_env
    publish_archive(
        Path(env["_RDST_TEST_SERVE"]),
        Path(env["_RDST_TEST_WORKSPACE"]),
        version="1.2.3",
        checksum="0" * 64,
    )

    result = run_installer(env, "--version", "1.2.3", "--no-modify-path")

    assert result.returncode != 0
    assert "checksum verification failed" in result.stderr
    assert not (home / "bin" / "rdst").exists()


def test_rejects_a_malformed_published_checksum(installer_env):
    env, home = installer_env
    publish_archive(
        Path(env["_RDST_TEST_SERVE"]),
        Path(env["_RDST_TEST_WORKSPACE"]),
        version="1.2.3",
        checksum="not-a-real-digest",
    )

    result = run_installer(env, "--version", "1.2.3", "--no-modify-path")

    assert result.returncode != 0
    assert "checksum is malformed" in result.stderr
    assert not (home / "bin" / "rdst").exists()


def test_rejects_an_unexpected_archive_layout(installer_env):
    env, home = installer_env
    publish_archive(
        Path(env["_RDST_TEST_SERVE"]),
        Path(env["_RDST_TEST_WORKSPACE"]),
        version="1.2.3",
        root_name="somethingelse",
    )

    result = run_installer(env, "--version", "1.2.3", "--no-modify-path")

    assert result.returncode != 0
    assert "unexpected layout" in result.stderr
    assert not (home / "bin" / "rdst").exists()


def test_rejects_an_archive_missing_the_mcp_entrypoint(installer_env):
    env, home = installer_env
    publish_archive(
        Path(env["_RDST_TEST_SERVE"]),
        Path(env["_RDST_TEST_WORKSPACE"]),
        version="1.2.3",
        entrypoints=("rdst",),
    )

    result = run_installer(env, "--version", "1.2.3", "--no-modify-path")

    assert result.returncode != 0
    assert "missing the rdst-mcp entrypoint" in result.stderr
    assert not (home / "bin" / "rdst").exists()


@pytest.mark.parametrize("target", ["/etc/passwd", "../../../../../../etc/passwd"])
def test_rejects_an_archive_whose_symlink_escapes(installer_env, target):
    env, home = installer_env
    publish_archive(
        Path(env["_RDST_TEST_SERVE"]),
        Path(env["_RDST_TEST_WORKSPACE"]),
        version="1.2.3",
        escaping_link=target,
    )

    result = run_installer(env, "--version", "1.2.3", "--no-modify-path")

    assert result.returncode != 0
    assert "do not stay inside the install" in result.stderr
    assert not (home / "bin" / "rdst").exists()


@pytest.mark.parametrize(
    "member", ["../owned", "rdst/../../owned", "/tmp/rdst-installer-owned"]
)
def test_rejects_an_archive_whose_member_leaves_the_install(installer_env, member, tmp_path):
    # Unpacking is what would put the file there, and the signature can only be
    # checked once the tree exists, so the names have to be refused first.
    env, home = installer_env
    publish_archive(
        Path(env["_RDST_TEST_SERVE"]),
        Path(env["_RDST_TEST_WORKSPACE"]),
        version="1.2.3",
        escaping_member=member,
    )

    result = run_installer(env, "--version", "1.2.3", "--no-modify-path")

    assert result.returncode != 0
    assert "leaves the install" in result.stderr
    assert not (home / "bin" / "rdst").exists()
    # Nothing was unpacked, so the generation directory was never created.
    assert not list((home / "data" / "rdst" / "tools").glob(".rdst-generation-*"))


def test_reports_the_staged_version_around_unrelated_output(installer_env):
    # The executable shares stdout and stderr with its libraries, so the check
    # cannot assume the version is the last thing printed.
    env, home = installer_env
    publish_archive(
        Path(env["_RDST_TEST_SERVE"]),
        Path(env["_RDST_TEST_WORKSPACE"]),
        version="1.2.3",
        noisy=True,
    )

    result = run_installer(env, "--version", "1.2.3", "--no-modify-path")

    assert result.returncode == 0, result.stderr
    assert (home / "bin" / "rdst").exists()


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="signatures are only checked on macOS"
)
def test_rejects_an_archive_whose_signature_does_not_verify(installer_env):
    env, home = installer_env
    env["FAKE_CODESIGN_INVALID"] = "1"

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "signature verification failed" in result.stderr
    assert not (home / "bin" / "rdst").exists()


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="signatures are only checked on macOS"
)
def test_rejects_an_archive_signed_by_another_team(installer_env):
    env, home = installer_env
    env["FAKE_CODESIGN_TEAM"] = "0000000000"

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "not signed by Readyset" in result.stderr
    assert not (home / "bin" / "rdst").exists()


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="signatures are only checked on macOS"
)
def test_installs_an_adhoc_archive_when_a_build_verifies_its_own_artifacts(installer_env):
    # A change build signs ad-hoc, and its smoke serves the archive from the
    # host it is running on. That pairing is what lets the smoke cover this
    # script before a release signs anything.
    env, home = installer_env
    env["RDST_INSTALLER_BASE_URL"] = LOCAL_BASE_URL
    env["FAKE_CODESIGN_TEAM"] = ""
    env["RDST_ALLOW_ADHOC_SIGNATURE"] = "1"

    result = run_installer(env, "--no-modify-path")

    assert result.returncode == 0, result.stderr
    assert "Readyset has not signed" in result.stderr
    assert (home / "bin" / "rdst").exists()


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="signatures are only checked on macOS"
)
def test_an_adhoc_archive_still_verifies_against_its_own_signature(installer_env):
    # Whose signature the tree carries is the only thing the opt-out relaxes.
    env, home = installer_env
    env["RDST_INSTALLER_BASE_URL"] = LOCAL_BASE_URL
    env["RDST_ALLOW_ADHOC_SIGNATURE"] = "1"
    env["FAKE_CODESIGN_INVALID"] = "1"

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "signature verification failed" in result.stderr
    assert not (home / "bin" / "rdst").exists()


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="signatures are only checked on macOS"
)
def test_the_adhoc_opt_out_cannot_reach_a_published_install(installer_env):
    # The opt-out is reachable only from a transport a published install never
    # uses, so asking for it over https changes nothing.
    env, home = installer_env
    env["FAKE_CODESIGN_TEAM"] = ""
    env["RDST_ALLOW_ADHOC_SIGNATURE"] = "1"

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "not signed by Readyset" in result.stderr
    assert not (home / "bin" / "rdst").exists()


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="signatures are only checked on macOS"
)
def test_rejects_an_archive_whose_library_is_signed_by_another_team(installer_env):
    # The frozen CLI loads these from its own tree with library validation
    # disabled, so a swapped library is never re-checked at run time. Verifying
    # only the entrypoint would leave it resting on the checksum, which travels
    # from the same host as the archive.
    env, home = installer_env
    env["FAKE_CODESIGN_FOREIGN_FILE"] = "libpython.so"

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "not signed by Readyset" in result.stderr
    assert "libpython.so" in result.stderr
    assert not (home / "bin" / "rdst").exists()


@pytest.mark.skipif(
    platform.system() != "Darwin", reason="signatures are only checked on macOS"
)
def test_rejects_an_archive_whose_executable_is_not_signed_code(installer_env):
    # An archive whose entrypoint is not Mach-O has nothing for codesign to
    # check, so the whole signature gate would pass vacuously.
    env, home = installer_env
    env["FAKE_FILE_NO_MACH_O"] = "1"

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "not signed code" in result.stderr
    assert not (home / "bin" / "rdst").exists()


def test_failed_install_can_be_uninstalled(installer_env):
    env, home = installer_env
    env["FAKE_DOWNLOAD_FAIL"] = "1"

    failed = run_installer(env, "--no-modify-path")

    assert failed.returncode != 0
    assert (home / ".rdst" / "install-state").is_file()
    assert (home / "data" / "rdst" / ".rdst-managed").is_file()

    env.pop("FAKE_DOWNLOAD_FAIL")
    uninstalled = run_installer(env, "--uninstall")
    assert uninstalled.returncode == 0, uninstalled.stderr
    assert not (home / "data" / "rdst").exists()


def test_rerun_is_idempotent(installer_env):
    env, home = installer_env

    first = run_installer(env, "--no-modify-path")
    second = run_installer(env, "--no-modify-path")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert (home / "bin" / "rdst").is_symlink()


def test_failed_rerun_keeps_the_active_generation(installer_env):
    env, home = installer_env
    installed = run_installer(env, "--no-modify-path")
    assert installed.returncode == 0, installed.stderr
    active = home / "data" / "rdst" / "tools" / "current"
    original_generation = active.resolve()
    # Republish the same version as an archive that unpacks but fails
    # verification, so the failure lands after the new generation exists.
    publish_archive(
        Path(env["_RDST_TEST_SERVE"]),
        Path(env["_RDST_TEST_WORKSPACE"]),
        entrypoints=("rdst",),
    )

    failed = run_installer(env, "--no-modify-path")

    assert failed.returncode != 0
    assert active.resolve() == original_generation
    version = subprocess.run(
        [str(home / "bin" / "rdst"), "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert version.stdout.strip().endswith(ARCHIVE_VERSION)
    # The abandoned generation is cleaned up, leaving only the active one.
    assert list((active.parent).glob(".rdst-generation-*")) == [original_generation]


def test_invalid_rerun_candidate_keeps_the_active_generation(installer_env):
    env, home = installer_env
    installed = run_installer(env, "--no-modify-path")
    assert installed.returncode == 0, installed.stderr
    active = home / "data" / "rdst" / "tools" / "current"
    original_generation = active.resolve()
    publish_archive(
        Path(env["_RDST_TEST_SERVE"]),
        Path(env["_RDST_TEST_WORKSPACE"]),
        escaping_link="/etc/passwd",
    )

    failed = run_installer(env, "--no-modify-path")

    assert failed.returncode != 0
    assert "do not stay inside the install" in failed.stderr
    assert active.resolve() == original_generation
    assert (home / "bin" / "rdst").resolve().is_file()


def test_rerun_repairs_a_dangling_current_generation(installer_env):
    env, home = installer_env
    installed = run_installer(env, "--no-modify-path")
    assert installed.returncode == 0, installed.stderr
    active = home / "data" / "rdst" / "tools" / "current"
    generation = active.resolve()
    shutil.rmtree(generation)

    repaired = run_installer(env, "--no-modify-path")

    assert repaired.returncode == 0, repaired.stderr
    assert active.resolve().is_dir()
    assert (home / "bin" / "rdst").resolve().is_file()


def test_rerun_rejects_a_traversing_generation_link(installer_env):
    env, home = installer_env
    installed = run_installer(env, "--no-modify-path")
    assert installed.returncode == 0, installed.stderr
    tools = home / "data" / "rdst" / "tools"
    active = tools / "current"
    active.unlink()
    active.symlink_to(tools / ".rdst-generation-fake" / ".." / ".." / "victim")
    victim = home / "victim"
    victim.mkdir()
    sentinel = victim / "important.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    failed = run_installer(env, "--no-modify-path")

    assert failed.returncode != 0
    assert "points outside the managed generation directory" in failed.stderr
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_paths_with_spaces_are_supported(installer_env):
    env, _ = installer_env
    home = Path(env["HOME"]).parent / "home with spaces"
    home.mkdir()
    env.update(
        {
            "HOME": str(home),
            "XDG_BIN_HOME": str(home / "bin"),
            "XDG_DATA_HOME": str(home / "data"),
        }
    )

    first = run_installer(env, "--no-modify-path")
    second = run_installer(env, "--no-modify-path")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert f"version {ARCHIVE_VERSION}" in second.stdout
    assert (home / "bin" / "rdst").is_symlink()
    assert (home / "bin" / "rdst-mcp").resolve().is_file()


def test_path_profile_update_is_idempotent(installer_env):
    env, home = installer_env
    env["SHELL"] = "/bin/zsh"

    first = run_installer(env)
    second = run_installer(env)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    profile = (home / ".zshrc").read_text(encoding="utf-8")
    assert profile.count("# >>> rdst >>>") == 1
    assert str(home / "bin") in profile


def test_reinstall_keeps_the_recorded_path_profile(installer_env):
    env, home = installer_env
    installed = run_installer(env | {"SHELL": "/bin/zsh"})
    assert installed.returncode == 0, installed.stderr

    reinstalled = run_installer(env | {"SHELL": "/bin/bash"})
    assert reinstalled.returncode == 0, reinstalled.stderr
    state = (home / ".rdst" / "install-state").read_text(encoding="utf-8")
    assert f"path_profile={home}/.zshrc" in state

    uninstalled = run_installer(env | {"SHELL": "/bin/bash"}, "--uninstall")
    assert uninstalled.returncode == 0, uninstalled.stderr
    assert "# >>> rdst >>>" not in (home / ".zshrc").read_text(encoding="utf-8")


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS Bash behavior")
def test_macos_bash_uses_an_existing_profile(installer_env):
    env, home = installer_env
    env["SHELL"] = "/bin/bash"
    profile = home / ".profile"
    profile.write_text("existing\n", encoding="utf-8")

    result = run_installer(env)

    assert result.returncode == 0, result.stderr
    assert "# >>> rdst >>>" in profile.read_text(encoding="utf-8")
    assert not (home / ".bash_profile").exists()


def test_uninstall_removes_only_the_recorded_path_block(installer_env):
    env, home = installer_env
    env["SHELL"] = "/bin/zsh"
    profile = home / ".zshrc"
    profile.write_text("before\n", encoding="utf-8")
    installed = run_installer(env)
    assert installed.returncode == 0, installed.stderr
    with profile.open("a", encoding="utf-8") as output:
        output.write("after\n")

    result = run_installer(env, "--uninstall")

    assert result.returncode == 0, result.stderr
    contents = profile.read_text(encoding="utf-8")
    assert "# >>> rdst >>>" not in contents
    assert "before\n" in contents
    assert "after\n" in contents


def test_uninstall_preserves_path_blocks_when_path_was_not_modified(installer_env):
    env, home = installer_env
    installed = run_installer(env, "--no-modify-path")
    assert installed.returncode == 0, installed.stderr
    profile = home / ".zshrc"
    original = (
        "user-content\n"
        "# >>> rdst >>>\n"
        "export PATH='/opt/other/bin':\"$PATH\"\n"
        "# <<< rdst <<<\n"
    )
    profile.write_text(original, encoding="utf-8")

    result = run_installer(env, "--uninstall")

    assert result.returncode == 0, result.stderr
    assert profile.read_text(encoding="utf-8") == original


def test_uninstall_preserves_a_symlinked_profile(installer_env):
    env, home = installer_env
    env["SHELL"] = "/bin/zsh"
    dotfiles = home / "dotfiles"
    dotfiles.mkdir()
    target = dotfiles / "zshrc"
    target.write_text("user-content\n", encoding="utf-8")
    profile = home / ".zshrc"
    profile.symlink_to(target)
    installed = run_installer(env)
    assert installed.returncode == 0, installed.stderr

    result = run_installer(env, "--uninstall")

    assert result.returncode == 0, result.stderr
    assert profile.is_symlink()
    assert target.read_text(encoding="utf-8").strip() == "user-content"


def test_signal_after_lock_acquisition_releases_lock(installer_env):
    env, home = installer_env
    installer = Path(env["_RDST_TEST_INSTALLER"])
    source = installer.read_text(encoding="utf-8")
    source = source.replace(
        "acquire_operation_lock\ntmp_dir=$(mktemp -d",
        "acquire_operation_lock\nkill -INT $$\ntmp_dir=$(mktemp -d",
        1,
    )
    installer.write_text(source, encoding="utf-8")

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert not (home / ".rdst" / ".rdst-operation-lock").exists()


def test_existing_operation_lock_blocks_install(installer_env):
    env, home = installer_env
    lock = home / ".rdst" / ".rdst-operation-lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text("4321-foreign\n", encoding="utf-8")

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "PID 4321" in result.stderr
    assert (lock / "owner").read_text(encoding="utf-8") == "4321-foreign\n"


def test_concurrent_install_is_rejected_without_disturbing_the_owner(installer_env):
    env, home = installer_env
    wait_file = home / "wait"
    first_env = env | {"FAKE_DOWNLOAD_WAIT_FILE": str(wait_file)}
    first = subprocess.Popen(
        ["sh", env["_RDST_TEST_INSTALLER"], "--no-modify-path"],
        env=first_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    for _ in range(200):
        if Path(f"{wait_file}.started").exists():
            break
        time.sleep(0.01)
    else:
        first.kill()
        pytest.fail("first installer did not reach the download")

    second = run_installer(env, "--no-modify-path")
    Path(f"{wait_file}.release").touch()
    first_stdout, first_stderr = first.communicate(timeout=10)

    assert second.returncode != 0
    assert "another RDST install" in second.stderr
    assert first.returncode == 0, f"{first_stdout}\n{first_stderr}"
    assert (home / "bin" / "rdst").is_symlink()
    assert not (home / ".rdst" / ".rdst-operation-lock").exists()


def test_uninstall_uses_saved_paths_when_environment_changes(installer_env):
    env, home = installer_env
    installed = run_installer(env, "--no-modify-path")
    assert installed.returncode == 0, installed.stderr
    original_data_home = env["XDG_DATA_HOME"]
    env["XDG_DATA_HOME"] = str(home / "other-data")

    result = run_installer(env, "--uninstall")

    assert result.returncode == 0, result.stderr
    assert not (home / "bin" / "rdst").exists()
    assert not (home / "bin" / "rdst").is_symlink()
    assert not Path(original_data_home, "rdst").exists()
    env["XDG_DATA_HOME"] = original_data_home
    reinstalled = run_installer(env, "--no-modify-path")
    assert reinstalled.returncode == 0, reinstalled.stderr


def test_uninstall_refuses_an_unmarked_data_directory(installer_env):
    env, home = installer_env
    installed = run_installer(env, "--no-modify-path")
    assert installed.returncode == 0, installed.stderr
    data_dir = home / "data" / "rdst"
    (data_dir / ".rdst-managed").unlink()

    result = run_installer(env, "--uninstall")

    assert result.returncode != 0
    assert "unmarked data directory" in result.stderr
    assert data_dir.is_dir()
    assert (home / "bin" / "rdst").exists()


def test_uninstall_does_not_remove_a_replaced_executable(installer_env):
    env, home = installer_env
    installed = run_installer(env, "--no-modify-path")
    assert installed.returncode == 0, installed.stderr
    executable = home / "bin" / "rdst"
    executable.unlink()
    executable.symlink_to(home / "external-rdst")

    result = run_installer(env, "--uninstall")

    assert result.returncode == 0, result.stderr
    assert executable.is_symlink()
    assert os.readlink(executable) == str(home / "external-rdst")
    assert not (home / "data" / "rdst").exists()


def test_uninstall_preserves_user_configuration(installer_env):
    env, home = installer_env
    installed = run_installer(env, "--no-modify-path")
    assert installed.returncode == 0, installed.stderr
    config = home / ".rdst" / "config.toml"
    config.write_text("[test]\nvalue = true\n", encoding="utf-8")

    result = run_installer(env, "--uninstall")

    assert result.returncode == 0, result.stderr
    assert not (home / "bin" / "rdst").exists()
    assert not (home / "data" / "rdst").exists()
    assert config.is_file()
    assert not (home / ".rdst" / "install-state").exists()
