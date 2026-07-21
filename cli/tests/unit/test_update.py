from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from features.update.cli.command import UpdateCommand
from features.update.models import UpdateOutcome, VersionCheck
from features.update.service import UpdateError, UpdateService


@pytest.fixture
def managed_install(tmp_path: Path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data" / "rdst"
    bin_dir = tmp_path / "bin"
    cache_dir = tmp_path / "cache" / "rdst"
    uv = data_dir / "bootstrap" / "bin" / "uv"
    log = tmp_path / "uv-log.json"

    config_dir.mkdir()
    uv.parent.mkdir(parents=True)
    (data_dir / ".rdst-managed").write_text("format=1\n", encoding="utf-8")
    uv.write_text(
        """#!/bin/sh
set -eu
if [ "${FAKE_UV_FAIL:-0}" = "1" ]; then
  exit 42
fi
python3 - "$FAKE_UV_LOG" "$@" <<'PY'
import json
import os
import sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "args": sys.argv[2:],
    "tool_dir": os.environ["UV_TOOL_DIR"],
    "bin_dir": os.environ["UV_TOOL_BIN_DIR"],
    "python_dir": os.environ["UV_PYTHON_INSTALL_DIR"],
    "cache_dir": os.environ["UV_CACHE_DIR"],
    "extra_index": os.environ.get("UV_EXTRA_INDEX_URL"),
}))
PY
mkdir -p "$UV_TOOL_BIN_DIR"
cat > "$UV_TOOL_BIN_DIR/rdst" <<'RDST'
#!/bin/sh
echo "Readyset Data and SQL Toolkit (rdst) version 9.9.9"
RDST
chmod +x "$UV_TOOL_BIN_DIR/rdst"
ln -sf "$UV_TOOL_BIN_DIR/rdst" "$UV_TOOL_BIN_DIR/rdst-mcp"
mkdir -p "$UV_TOOL_DIR/rdst/bin"
cp "$UV_TOOL_BIN_DIR/rdst" "$UV_TOOL_DIR/rdst/bin/rdst"
if [ "${FAKE_UV_MISSING_TOOL_MCP:-0}" != "1" ]; then
  cp "$UV_TOOL_BIN_DIR/rdst" "$UV_TOOL_DIR/rdst/bin/rdst-mcp"
fi
if [ "${FAKE_UV_FAIL_AFTER_WRITE:-0}" = "1" ]; then
  exit 43
fi
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    runtime_prefix = data_dir / "tools" / "rdst"
    runtime_bin = runtime_prefix / "bin"
    runtime_bin.mkdir(parents=True)
    for name in ("rdst", "rdst-mcp"):
        command = runtime_bin / name
        command.write_text("#!/bin/sh\necho old-version\n", encoding="utf-8")
        command.chmod(0o755)

    (config_dir / "install-state").write_text(
        "\n".join(
            [
                "format=1",
                "method=readyset-uv",
                f"data_dir={data_dir}",
                f"bin_dir={bin_dir}",
                f"cache_dir={cache_dir}",
                "python=3.12",
                "uv_version=0.11.23",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    service = UpdateService(config_dir, runtime_prefix=runtime_prefix)
    service.current_version = lambda: "1.0.0"
    service._latest_version = lambda timeout=5.0: "2.0.0"
    return service, log, data_dir, bin_dir, cache_dir


def read_log(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_newer_local_version_does_not_report_an_update():
    result = VersionCheck(current="2.0.0", latest="1.9.0")

    assert not result.update_available


def test_check_reports_the_latest_release():
    response = io.BytesIO(json.dumps({"info": {"version": "2.0.0"}}).encode())
    service = UpdateService()

    with (
        patch("features.update.service.urlopen", return_value=response),
        patch.object(service, "current_version", return_value="1.0.0"),
    ):
        result = service.check()

    assert result.current == "1.0.0"
    assert result.latest == "2.0.0"
    assert result.update_available


def test_update_uses_the_private_uv_runtime(managed_install, monkeypatch):
    service, log, data_dir, bin_dir, cache_dir = managed_install
    monkeypatch.setenv("FAKE_UV_LOG", str(log))
    monkeypatch.setenv("UV_EXTRA_INDEX_URL", "http://malicious.invalid/simple")

    service.update()

    invocation = read_log(log)
    assert invocation["args"][:2] == ["tool", "install"]
    assert "--force" in invocation["args"]
    assert invocation["args"][-1] == "rdst==2.0.0"
    generation = Path(invocation["tool_dir"])
    assert generation.parent == data_dir / "tools"
    assert generation.name.startswith(".rdst-generation-")
    assert Path(invocation["bin_dir"]).parent == cache_dir
    assert Path(invocation["bin_dir"]).name.startswith("update-bin-")
    assert (data_dir / "tools" / "current").resolve() == generation
    assert (bin_dir / "rdst").resolve() == generation / "rdst" / "bin" / "rdst"
    assert invocation["python_dir"] == str(data_dir / "python")
    assert invocation["cache_dir"] == str(cache_dir / "uv")
    assert invocation["extra_index"] is None


def test_failed_update_preparation_preserves_existing_command(
    managed_install, monkeypatch
):
    service, log, data_dir, bin_dir, _ = managed_install
    managed_command = data_dir / "tools" / "rdst" / "bin" / "rdst"
    managed_command.parent.mkdir(parents=True, exist_ok=True)
    managed_command.write_text("existing command\n", encoding="utf-8")
    bin_dir.mkdir()
    existing = bin_dir / "rdst"
    existing.symlink_to(managed_command)
    monkeypatch.setenv("FAKE_UV_LOG", str(log))
    monkeypatch.setenv("FAKE_UV_FAIL", "1")

    with pytest.raises(UpdateError, match="existing installation was not changed"):
        service.update()

    assert existing.is_symlink()
    assert existing.read_text(encoding="utf-8") == "existing command\n"
    assert not log.exists()


def test_failed_candidate_install_preserves_previous_environment(
    managed_install, monkeypatch
):
    service, log, data_dir, bin_dir, _ = managed_install
    old_bin = data_dir / "tools" / "rdst" / "bin"
    old_bin.mkdir(parents=True, exist_ok=True)
    for name in ("rdst", "rdst-mcp"):
        command = old_bin / name
        command.write_text("#!/bin/sh\necho old-version\n", encoding="utf-8")
        command.chmod(0o755)
    bin_dir.mkdir()
    for name in ("rdst", "rdst-mcp"):
        (bin_dir / name).symlink_to(old_bin / name)
    monkeypatch.setenv("FAKE_UV_LOG", str(log))
    monkeypatch.setenv("FAKE_UV_FAIL_AFTER_WRITE", "1")

    with pytest.raises(UpdateError, match="existing installation was not changed"):
        service.update()

    assert (bin_dir / "rdst").resolve() == old_bin / "rdst"
    assert (bin_dir / "rdst").read_text(encoding="utf-8").endswith("echo old-version\n")
    assert not list((data_dir / "tools").glob(".rdst-generation-*"))


def test_missing_tool_entrypoint_preserves_previous_environment(
    managed_install, monkeypatch
):
    service, log, data_dir, bin_dir, _ = managed_install
    old_bin = data_dir / "tools" / "rdst" / "bin"
    old_bin.mkdir(parents=True, exist_ok=True)
    for name in ("rdst", "rdst-mcp"):
        command = old_bin / name
        command.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
        command.chmod(0o755)
    bin_dir.mkdir()
    for name in ("rdst", "rdst-mcp"):
        (bin_dir / name).symlink_to(old_bin / name)
    monkeypatch.setenv("FAKE_UV_LOG", str(log))
    monkeypatch.setenv("FAKE_UV_MISSING_TOOL_MCP", "1")

    with pytest.raises(UpdateError, match="environment is missing rdst-mcp"):
        service.update()

    assert (bin_dir / "rdst").resolve() == old_bin / "rdst"
    assert not list((data_dir / "tools").glob(".rdst-generation-*"))


def test_entrypoint_refresh_failure_keeps_the_activated_generation(
    managed_install, monkeypatch
):
    service, log, data_dir, bin_dir, _ = managed_install
    tools = data_dir / "tools"
    old_generation = tools / ".rdst-generation-old"
    old_bin = old_generation / "rdst" / "bin"
    old_bin.mkdir(parents=True)
    for name in ("rdst", "rdst-mcp"):
        command = old_bin / name
        command.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
        command.chmod(0o755)
    (tools / "current").symlink_to(old_generation)
    service.runtime_prefix = old_generation / "rdst"
    bin_dir.mkdir()
    for name in ("rdst", "rdst-mcp"):
        (bin_dir / name).symlink_to(tools / "current" / "rdst" / "bin" / name)
    monkeypatch.setenv("FAKE_UV_LOG", str(log))

    with (
        patch.object(service, "_point_entrypoints", side_effect=OSError("blocked")),
        pytest.raises(OSError, match="blocked"),
    ):
        service.update()

    active = (tools / "current").resolve()
    assert active != old_generation
    assert active.is_dir()
    assert (bin_dir / "rdst").resolve() == active / "rdst" / "bin" / "rdst"


def test_update_rejects_a_dangling_current_generation(managed_install, monkeypatch):
    service, log, data_dir, *_ = managed_install
    tools = data_dir / "tools"
    (tools / "current").symlink_to(tools / ".rdst-generation-missing")
    monkeypatch.setenv("FAKE_UV_LOG", str(log))

    with pytest.raises(UpdateError, match="active RDST environment is missing"):
        service.update()

    assert not log.exists()


def test_update_migrates_a_legacy_tool_environment(managed_install, monkeypatch):
    service, log, data_dir, bin_dir, _ = managed_install
    legacy_bin = data_dir / "tools" / "rdst" / "bin"
    legacy_bin.mkdir(parents=True, exist_ok=True)
    for name in ("rdst", "rdst-mcp"):
        command = legacy_bin / name
        command.write_text("#!/bin/sh\necho legacy\n", encoding="utf-8")
        command.chmod(0o755)
    bin_dir.mkdir()
    for name in ("rdst", "rdst-mcp"):
        (bin_dir / name).symlink_to(legacy_bin / name)
    monkeypatch.setenv("FAKE_UV_LOG", str(log))

    service.update()

    active = data_dir / "tools" / "current"
    assert active.is_symlink()
    assert (data_dir / "tools" / "rdst").is_dir()
    assert (bin_dir / "rdst").readlink() == active / "rdst" / "bin" / "rdst"
    assert (bin_dir / "rdst").resolve().is_file()


def test_update_can_install_an_exact_version(managed_install, monkeypatch):
    service, log, *_ = managed_install
    monkeypatch.setenv("FAKE_UV_LOG", str(log))

    service.update("1.2.3")

    invocation = read_log(log)
    assert invocation["args"][:2] == ["tool", "install"]
    assert "--force" in invocation["args"]
    assert invocation["args"][-1] == "rdst==1.2.3"


def test_plain_update_is_a_noop_when_current(managed_install):
    service, log, data_dir, *_ = managed_install
    service.current_version = lambda: "2.0.0"

    outcome = service.update()

    assert not outcome.changed
    assert outcome.version == "2.0.0"
    assert not log.exists()
    assert not list((data_dir / "tools").glob(".rdst-generation-*"))


def test_plain_update_does_not_downgrade_a_newer_install(managed_install):
    service, log, *_ = managed_install
    service.current_version = lambda: "3.0.0"

    outcome = service.update()

    assert not outcome.changed
    assert outcome.version == "3.0.0"
    assert not log.exists()


def test_exact_current_version_is_a_noop(managed_install):
    service, log, *_ = managed_install
    service.current_version = lambda: "1.2.3"

    outcome = service.update("1.2.3")

    assert not outcome.changed
    assert outcome.version == "1.2.3"
    assert not log.exists()


def test_exact_version_allows_a_downgrade(managed_install, monkeypatch):
    service, log, *_ = managed_install
    service.current_version = lambda: "2.0.0"
    monkeypatch.setenv("FAKE_UV_LOG", str(log))

    outcome = service.update("1.2.3")

    assert outcome.changed
    assert read_log(log)["args"][-1] == "rdst==1.2.3"


def test_update_rejects_an_invalid_version(managed_install):
    service, *_ = managed_install

    with pytest.raises(UpdateError, match="invalid version"):
        service.update("1.2.3; rm -rf")


def test_update_refuses_to_replace_an_external_entrypoint(managed_install):
    service, log, _, bin_dir, _ = managed_install
    bin_dir.mkdir()
    (bin_dir / "rdst").symlink_to(bin_dir / "external-rdst")

    with pytest.raises(UpdateError, match="no longer owned"):
        service.update()

    assert not log.exists()


def test_update_refuses_an_unmarked_data_directory(managed_install):
    service, log, data_dir, *_ = managed_install
    (data_dir / ".rdst-managed").unlink()

    with pytest.raises(UpdateError, match="data directory is unmarked"):
        service.update()

    assert not log.exists()


def test_update_rejects_a_symbolic_link_data_directory(managed_install):
    service, log, data_dir, *_ = managed_install
    victim = data_dir.parent / "victim"
    data_dir.rename(victim)
    data_dir.symlink_to(victim)

    with pytest.raises(UpdateError, match="data directory must not be a symbolic link"):
        service.update()

    assert not log.exists()
    assert (victim / ".rdst-managed").is_file()


def test_update_rejects_a_non_active_runtime(managed_install):
    service, log, data_dir, *_ = managed_install
    external = data_dir.parent / "external-runtime"
    external.mkdir()
    service.runtime_prefix = external

    with pytest.raises(UpdateError, match="not running from the active"):
        service.update()

    assert not log.exists()


def test_update_rejects_a_superseded_generation(managed_install):
    service, log, data_dir, *_ = managed_install
    tools = data_dir / "tools"
    active = tools / ".rdst-generation-active"
    (active / "rdst").mkdir(parents=True)
    (tools / "current").symlink_to(active)

    with pytest.raises(UpdateError, match="not running from the active"):
        service.update()

    assert not log.exists()


def test_update_refuses_an_existing_operation_lock(managed_install):
    service, log, *_ = managed_install
    lock = service.config_dir / ".rdst-operation-lock"
    lock.mkdir()
    (lock / "owner").write_text("1234-foreign-token\n", encoding="utf-8")

    with pytest.raises(UpdateError, match="PID 1234"):
        service.update()

    assert lock.is_dir()
    assert not log.exists()


def test_update_does_not_remove_a_replaced_operation_lock(managed_install):
    service, *_ = managed_install
    state = service.load_installer_state()
    assert state is not None
    lock = service.config_dir / ".rdst-operation-lock"

    with service._operation_lock(state):
        (lock / "owner").write_text("9999-replacement\n", encoding="utf-8")

    assert (lock / "owner").read_text(encoding="utf-8") == "9999-replacement\n"


def test_update_releases_the_operation_lock_after_failure(managed_install):
    service, *_ = managed_install
    service.runtime_prefix = service.config_dir / "missing"

    with pytest.raises(UpdateError):
        service.update()

    state = service.load_installer_state()
    assert state is not None
    assert not (service.config_dir / ".rdst-operation-lock").exists()


def test_update_rejects_duplicate_installer_state(managed_install):
    service, _, data_dir, *_ = managed_install
    state_path = service.config_dir / "install-state"
    with state_path.open("a", encoding="utf-8") as state:
        state.write(f"data_dir={data_dir}\n")

    with pytest.raises(UpdateError, match="duplicate data_dir"):
        service.update()


def test_update_does_not_modify_external_installations(tmp_path: Path):
    service = UpdateService(tmp_path)

    with pytest.raises(UpdateError, match="not managed by the Readyset installer"):
        service.update()


def test_update_command_returns_actionable_check_message():
    service = UpdateService()
    command = UpdateCommand(service)
    response = io.BytesIO(json.dumps({"info": {"version": "2.0.0"}}).encode())

    with (
        patch("features.update.service.urlopen", return_value=response),
        patch.object(service, "current_version", return_value="1.0.0"),
    ):
        result = command.execute(check=True)

    assert result.ok
    assert "RDST 2.0.0 is available" in result.message
    assert result.data == {
        "current": "1.0.0",
        "latest": "2.0.0",
        "update_available": True,
    }


def test_update_command_reports_a_noop():
    service = UpdateService()
    command = UpdateCommand(service)

    with patch.object(
        service,
        "update",
        return_value=UpdateOutcome(version="2.0.0", changed=False),
    ):
        result = command.execute()

    assert result.ok
    assert result.message == "RDST is already up to date (2.0.0)."
    assert result.data == {"version": "2.0.0", "changed": False}


def test_update_command_reports_the_installed_version():
    service = UpdateService()
    command = UpdateCommand(service)

    with patch.object(
        service,
        "update",
        return_value=UpdateOutcome(version="2.0.0", changed=True),
    ):
        result = command.execute()

    assert result.ok
    assert result.message == "RDST 2.0.0 was installed successfully."
    assert result.data == {"version": "2.0.0", "changed": True}


def test_update_command_surfaces_network_errors():
    service = UpdateService()
    command = UpdateCommand(service)

    with patch("features.update.service.urlopen", side_effect=OSError("offline")):
        result = command.execute(check=True)

    assert not result.ok
    assert "could not check for updates" in result.message
