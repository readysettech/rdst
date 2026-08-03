from __future__ import annotations

import hashlib
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


@pytest.fixture
def installer_env(tmp_path: Path):
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    system_bin = tmp_path / "system-bin"
    home.mkdir()
    fake_bin.mkdir()
    system_bin.mkdir()

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

    system = platform.system()
    machine = platform.machine()
    if system == "Darwin":
        target = (
            "aarch64-apple-darwin"
            if machine in ("arm64", "aarch64")
            else "x86_64-apple-darwin"
        )
    else:
        target = (
            "aarch64-unknown-linux-gnu"
            if machine in ("arm64", "aarch64")
            else "x86_64-unknown-linux-gnu"
        )

    archive_root = tmp_path / f"uv-{target}"
    archive_root.mkdir()
    uv = archive_root / "uv"
    uv.write_text(
        """#!/bin/sh
set -eu
if [ -n "${UV_EXTRA_INDEX_URL:-}" ]; then
  echo "ambient UV_EXTRA_INDEX_URL was not cleared" >&2
  exit 98
fi
if [ "${1:-}" = "--version" ]; then
  echo "uv 0.11.23"
  exit 0
fi
command_name="${1:-} ${2:-}"
case "$command_name" in
  "tool install")
    if [ "${FAKE_UV_CANONICALIZE_TOOL_DIR:-0}" = "1" ]; then
      UV_TOOL_DIR=$(cd "$UV_TOOL_DIR" && pwd -P)
      export UV_TOOL_DIR
    fi
    if [ -n "${FAKE_UV_WAIT_FILE:-}" ]; then
      touch "${FAKE_UV_WAIT_FILE}.started"
      while [ ! -f "${FAKE_UV_WAIT_FILE}.release" ]; do sleep 0.05; done
    fi
    if [ "${FAKE_UV_FAIL_INSTALL:-0}" = "1" ]; then
      echo "simulated install failure" >&2
      exit 42
    fi
    package=""
    for argument in "$@"; do package="$argument"; done
    case "$package" in
      rdst==*) version=${package#rdst==} ;;
      *) version=9.9.9 ;;
    esac
    mkdir -p "$UV_TOOL_DIR/rdst/bin" "$UV_TOOL_BIN_DIR"
    cat > "$UV_TOOL_DIR/rdst/bin/rdst" <<EOF
#!/bin/sh
echo "Readyset Data and SQL Toolkit (rdst) version $version"
EOF
    chmod +x "$UV_TOOL_DIR/rdst/bin/rdst"
    ln -sf "$UV_TOOL_DIR/rdst/bin/rdst" "$UV_TOOL_DIR/rdst/bin/rdst-mcp"
    ln -sf "$UV_TOOL_DIR/rdst/bin/rdst" "$UV_TOOL_BIN_DIR/rdst"
    ln -sf "$UV_TOOL_DIR/rdst/bin/rdst-mcp" "$UV_TOOL_BIN_DIR/rdst-mcp"
    if [ "${FAKE_UV_EXTRA_ENTRYPOINT:-0}" = "1" ]; then
      ln -sf "$UV_TOOL_DIR/rdst/bin/rdst" "$UV_TOOL_BIN_DIR/python"
    fi
    if [ "${FAKE_UV_FAIL_AFTER_WRITE:-0}" = "1" ]; then
      echo "simulated partial install failure" >&2
      exit 43
    fi
    ;;
  "tool uninstall")
    rm -rf "$UV_TOOL_DIR/rdst"
    rm -f "$UV_TOOL_BIN_DIR/rdst" "$UV_TOOL_BIN_DIR/rdst-mcp"
    ;;
  *)
    echo "unexpected uv arguments: $*" >&2
    exit 2
    ;;
esac
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    uv_archive = tmp_path / f"uv-{target}.tar.gz"
    with tarfile.open(uv_archive, "w:gz") as archive:
        archive.add(archive_root, arcname=archive_root.name)
    uv_sha256 = hashlib.sha256(uv_archive.read_bytes()).hexdigest()
    installer = tmp_path / "install.sh"
    installer.write_text(
        re.sub(
            r'UV_SHA256="[0-9a-f]{64}"',
            f'UV_SHA256="{uv_sha256}"',
            INSTALLER.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    installer.chmod(0o755)

    curl = fake_bin / "curl"
    curl.write_text(
        """#!/bin/sh
set -eu
destination=""
while [ "$#" -gt 0 ]; do
  if [ "$1" = "--output" ]; then
    destination="$2"
    shift 2
  else
    shift
  fi
done
cp "$FAKE_UV_ARCHIVE" "$destination"
""",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    for command in ("python", "python3", "sudo"):
        executable = fake_bin / command
        executable.write_text(
            f"#!/bin/sh\necho '{command} must not be invoked' >&2\nexit 99\n",
            encoding="utf-8",
        )
        executable.chmod(0o755)

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("RDST_", "UV_", "XDG_"))
    }
    env.update(
        {
            "HOME": str(home),
            "PATH": f"{fake_bin}:{system_bin}",
            "SHELL": "/bin/sh",
            "XDG_BIN_HOME": str(home / "bin"),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "FAKE_UV_ARCHIVE": str(uv_archive),
            "UV_EXTRA_INDEX_URL": "http://malicious.invalid/simple",
            "_RDST_TEST_INSTALLER": str(installer),
        }
    )
    return env, home


def run_installer(env: dict[str, str], *args: str):
    child_env = env.copy()
    installer = child_env.pop("_RDST_TEST_INSTALLER")
    return subprocess.run(
        ["sh", installer, *args],
        env=child_env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def test_embeds_verified_upstream_uv_checksums():
    source = INSTALLER.read_text(encoding="utf-8")

    for checksum in (
        "71ef9de85db820749b3b12b7585624ee279e9c5afcbc6f8236bc3d628c4305b0",
        "7a88155033cc469bba5bd5a24212e355eb92e3e2a276320b669ec576296c1e25",
        "1873a77350f6621279ae1a0d2227f2bd8b67131598f14a7eb0ba2215d3da2c98",
        "e12c4cda2fe8c305510a78380a88f2c32a27e90cdcd123cefd2873388f0ebb5f",
    ):
        assert checksum in source


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
    assert (home / "data" / "rdst" / "bootstrap" / "bin" / "uv").is_file()
    state = (home / ".rdst" / "install-state").read_text(encoding="utf-8")
    assert "method=readyset-uv" in state
    assert "version 9.9.9" in result.stdout


def test_installs_an_exact_version(installer_env):
    env, home = installer_env

    result = run_installer(env, "--version", "1.2.3", "--no-modify-path")

    assert result.returncode == 0, result.stderr
    version = subprocess.run(
        [str(home / "bin" / "rdst"), "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert version.stdout.strip().endswith("1.2.3")


def test_accepts_canonicalized_entrypoint_targets(installer_env):
    env, home = installer_env
    real_data = home / "real-data"
    real_data.mkdir()
    linked_data = home / "linked-data"
    linked_data.symlink_to(real_data)
    env["XDG_DATA_HOME"] = str(linked_data)
    env["FAKE_UV_CANONICALIZE_TOOL_DIR"] = "1"

    result = run_installer(env, "--no-modify-path")

    assert result.returncode == 0, result.stderr
    assert (home / "bin" / "rdst").resolve().is_file()


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


def test_rejects_a_runtime_with_the_wrong_checksum(installer_env):
    env, home = installer_env
    installer = Path(env["_RDST_TEST_INSTALLER"])
    installer.write_text(
        re.sub(
            r'UV_SHA256="[0-9a-f]{64}"',
            'UV_SHA256="0000000000000000000000000000000000000000000000000000000000000000"',
            installer.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "checksum verification failed" in result.stderr
    assert not (home / "bin" / "rdst").exists()


def test_rejects_unexpected_package_entrypoints(installer_env):
    env, home = installer_env
    env["FAKE_UV_EXTRA_ENTRYPOINT"] = "1"

    result = run_installer(env, "--no-modify-path")

    assert result.returncode != 0
    assert "unexpected executable: python" in result.stderr
    assert not (home / "bin" / "python").exists()
    assert not (home / "bin" / "rdst").exists()


def test_failed_install_can_be_uninstalled(installer_env):
    env, home = installer_env
    env["FAKE_UV_FAIL_INSTALL"] = "1"

    failed = run_installer(env, "--no-modify-path")

    assert failed.returncode == 42
    assert (home / ".rdst" / "install-state").is_file()
    assert (home / "data" / "rdst" / ".rdst-managed").is_file()

    env.pop("FAKE_UV_FAIL_INSTALL")
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
    env["FAKE_UV_FAIL_AFTER_WRITE"] = "1"

    failed = run_installer(env, "--no-modify-path")

    assert failed.returncode == 43
    assert active.resolve() == original_generation
    version = subprocess.run(
        [str(home / "bin" / "rdst"), "--version"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert version.stdout.strip().endswith("9.9.9")
    assert list((active.parent).glob(".rdst-generation-*")) == [original_generation]


def test_invalid_rerun_candidate_keeps_the_active_generation(installer_env):
    env, home = installer_env
    installed = run_installer(env, "--no-modify-path")
    assert installed.returncode == 0, installed.stderr
    active = home / "data" / "rdst" / "tools" / "current"
    original_generation = active.resolve()
    env["FAKE_UV_EXTRA_ENTRYPOINT"] = "1"

    failed = run_installer(env, "--no-modify-path")

    assert failed.returncode != 0
    assert "unexpected executable: python" in failed.stderr
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
            "XDG_CACHE_HOME": str(home / "cache"),
        }
    )

    first = run_installer(env, "--no-modify-path")
    second = run_installer(env, "--no-modify-path")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "Installing the RDST runtime manager" not in second.stdout
    assert "version 9.9.9" in second.stdout


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
    first_env = env | {"FAKE_UV_WAIT_FILE": str(wait_file)}
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
        pytest.fail("first installer did not reach the uv operation")

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
