from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

import pytest

from features.update.cli.command import UpdateCommand
from features.update.models import UpdateOutcome, VersionCheck
from features.update.service import UpdateError, UpdateService

INSTALLER_BASE = "https://downloads.test.invalid/rdst-cli"


@pytest.fixture
def managed_install(tmp_path: Path, monkeypatch):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data" / "rdst"
    bin_dir = tmp_path / "bin"
    tool_dir = data_dir / "tools"
    generation = tool_dir / ".rdst-generation-test"
    runtime_prefix = generation / "rdst"

    config_dir.mkdir()
    runtime_prefix.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    (data_dir / ".rdst-managed").write_text("format=1\n", encoding="utf-8")
    for name in ("rdst", "rdst-mcp"):
        command = runtime_prefix / name
        command.write_text("#!/bin/sh\necho installed\n", encoding="utf-8")
        command.chmod(0o755)
        (bin_dir / name).symlink_to(tool_dir / "current" / "rdst" / name)
    (tool_dir / "current").symlink_to(generation)

    (config_dir / "install-state").write_text(
        "\n".join(
            [
                "format=1",
                "method=readyset-archive",
                f"data_dir={data_dir}",
                f"bin_dir={bin_dir}",
                "platform=linux-x86_64",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("RDST_INSTALLER_BASE_URL", INSTALLER_BASE)

    served: dict[str, bytes] = {}

    def serve(url, body):
        served[url] = body

    def publish(version, body=b"#!/bin/sh\necho installer\n", digest=None):
        url = f"{INSTALLER_BASE}/versions/{version}/install.sh"
        served[url] = body
        checksum = digest if digest is not None else hashlib.sha256(body).hexdigest()
        served[f"{url}.sha256"] = f"{checksum}  install.sh\n".encode()

    publish("2.0.0")

    def fake_urlopen(request, timeout=None):
        url = getattr(request, "full_url", str(request))
        if url not in served:
            raise URLError(f"not published: {url}")
        return io.BytesIO(served[url])

    monkeypatch.setattr("features.update.service.urlopen", fake_urlopen)

    # execv replaces the process, so stand in for that: record the call and
    # stop, the way the real one never returns.
    execs = []

    def fake_execv(program, args):
        execs.append((program, list(args)))
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", fake_execv)

    service = UpdateService(config_dir, runtime_prefix=runtime_prefix)
    service.current_version = lambda: "1.0.0"
    service._latest_version = lambda timeout=5.0: "2.0.0"
    return SimpleNamespace(
        service=service,
        execs=execs,
        publish=publish,
        serve=serve,
        data_dir=data_dir,
        bin_dir=bin_dir,
        tool_dir=tool_dir,
        generation=generation,
    )


def installer_arguments(execs):
    assert len(execs) == 1
    program, args = execs[0]
    assert program == "/bin/sh"
    return args


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


def test_update_hands_off_to_the_published_installer(managed_install):
    fixture = managed_install

    with pytest.raises(SystemExit):
        fixture.service.update()

    args = installer_arguments(fixture.execs)
    assert "--version" in args and "2.0.0" in args
    assert "--force" in args
    # An upgrade replaces the runtime; it must not rewrite the shell profile.
    assert "--no-modify-path" in args


def test_update_installs_a_requested_version(managed_install):
    fixture = managed_install
    fixture.publish("3.1.0")

    with pytest.raises(SystemExit):
        fixture.service.update("3.1.0")

    args = installer_arguments(fixture.execs)
    assert args[args.index("--version") + 1] == "3.1.0"


@pytest.mark.parametrize(
    "base_url",
    ["http://downloads.test.invalid/rdst-cli", "http://localhost.test.invalid/rdst-cli"],
)
def test_update_refuses_an_unverified_installer_source(
    managed_install, monkeypatch, base_url
):
    # The override exists for CI, which points it at loopback or a local file.
    # Anywhere else it is a redirect of the upgrade to an unverified host.
    fixture = managed_install
    monkeypatch.setenv("RDST_INSTALLER_BASE_URL", base_url)

    with pytest.raises(UpdateError, match="must use https"):
        fixture.service.update()

    assert fixture.execs == []


@pytest.mark.parametrize(
    "base_url", ["http://127.0.0.1:8000/rdst-cli", "http://localhost:8000/rdst-cli"]
)
def test_update_allows_a_loopback_installer_source(
    managed_install, monkeypatch, base_url
):
    fixture = managed_install
    monkeypatch.setenv("RDST_INSTALLER_BASE_URL", base_url)
    body = b"#!/bin/sh\necho installer\n"
    url = f"{base_url}/versions/2.0.0/install.sh"
    fixture.serve(url, body)
    fixture.serve(
        f"{url}.sha256", f"{hashlib.sha256(body).hexdigest()}  install.sh\n".encode()
    )

    with pytest.raises(SystemExit):
        fixture.service.update()

    assert installer_arguments(fixture.execs)


def test_a_frozen_build_anchors_to_the_executable_it_runs_from(
    managed_install, monkeypatch, tmp_path
):
    # A frozen build's sys.prefix points inside the PyInstaller bundle, not at
    # the generation the installer activated, so the environment has to be
    # taken from the executable. Nothing else exercises this: unfrozen runs
    # never reach the branch.
    fixture = managed_install
    executable = fixture.generation / "rdst" / "rdst"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "bundle" / "_internal"))

    service = UpdateService(fixture.service.config_dir)

    assert service.runtime_prefix == fixture.generation / "rdst"
    # The generation the installer publishes has to satisfy the check the
    # upgrade runs before handing off, which is what the release smoke covers.
    service.current_version = lambda: "2.0.0"
    service._latest_version = lambda timeout=5.0: "2.0.0"
    assert service.update() == UpdateOutcome(version="2.0.0", changed=False)
    assert fixture.execs == []


def test_update_rejects_a_tampered_installer(managed_install):
    fixture = managed_install
    fixture.publish("2.0.0", digest="0" * 64)

    with pytest.raises(UpdateError, match="checksum verification failed"):
        fixture.service.update()

    assert fixture.execs == []


def test_update_rejects_a_malformed_installer_checksum(managed_install):
    fixture = managed_install
    fixture.publish("2.0.0", digest="not-a-digest")

    with pytest.raises(UpdateError, match="checksum is malformed"):
        fixture.service.update()

    assert fixture.execs == []


def test_update_reports_an_unpublished_version(managed_install):
    fixture = managed_install

    with pytest.raises(UpdateError, match="could not download"):
        fixture.service.update("9.9.9")

    assert fixture.execs == []


def test_plain_update_is_a_noop_when_current(managed_install):
    fixture = managed_install
    fixture.service.current_version = lambda: "2.0.0"

    outcome = fixture.service.update()

    assert outcome == UpdateOutcome(version="2.0.0", changed=False)
    assert fixture.execs == []


def test_plain_update_does_not_downgrade_a_newer_install(managed_install):
    fixture = managed_install
    fixture.service.current_version = lambda: "3.0.0"

    outcome = fixture.service.update()

    assert outcome == UpdateOutcome(version="3.0.0", changed=False)
    assert fixture.execs == []


def test_exact_current_version_is_a_noop(managed_install):
    fixture = managed_install

    outcome = fixture.service.update("1.0.0")

    assert outcome == UpdateOutcome(version="1.0.0", changed=False)
    assert fixture.execs == []


def test_exact_version_allows_a_downgrade(managed_install):
    fixture = managed_install
    fixture.service.current_version = lambda: "3.0.0"
    fixture.publish("2.0.0")

    with pytest.raises(SystemExit):
        fixture.service.update("2.0.0")

    args = installer_arguments(fixture.execs)
    assert args[args.index("--version") + 1] == "2.0.0"


def test_update_rejects_an_invalid_version(managed_install):
    fixture = managed_install

    with pytest.raises(UpdateError, match="invalid version"):
        fixture.service.update("../../etc/passwd")

    assert fixture.execs == []


def test_update_refuses_to_replace_an_external_entrypoint(managed_install):
    fixture = managed_install
    external = fixture.bin_dir / "rdst"
    external.unlink()
    external.symlink_to(fixture.data_dir.parent / "elsewhere")

    with pytest.raises(UpdateError, match="no longer owned"):
        fixture.service.update()

    assert fixture.execs == []


def test_update_refuses_an_unmarked_data_directory(managed_install):
    fixture = managed_install
    (fixture.data_dir / ".rdst-managed").unlink()

    with pytest.raises(UpdateError, match="unmarked"):
        fixture.service.update()

    assert fixture.execs == []


def test_update_rejects_a_non_active_runtime(managed_install):
    fixture = managed_install
    elsewhere = fixture.data_dir.parent / "somewhere-else"
    elsewhere.mkdir()
    fixture.service.runtime_prefix = elsewhere

    with pytest.raises(UpdateError, match="not running from the active"):
        fixture.service.update()

    assert fixture.execs == []


def test_update_rejects_a_dangling_current_generation(managed_install):
    fixture = managed_install
    active = fixture.tool_dir / "current"
    active.unlink()
    active.symlink_to(fixture.tool_dir / ".rdst-generation-missing")

    with pytest.raises(UpdateError, match="active RDST environment is missing"):
        fixture.service.update()

    assert fixture.execs == []


def test_update_rejects_a_superseded_generation(managed_install):
    fixture = managed_install
    active = fixture.tool_dir / "current"
    active.unlink()
    superseded = fixture.tool_dir / "not-a-generation"
    (superseded / "rdst").mkdir(parents=True)
    active.symlink_to(superseded)

    with pytest.raises(UpdateError, match="no longer owned by the installer"):
        fixture.service.update()

    assert fixture.execs == []


def test_update_rejects_duplicate_installer_state(managed_install):
    fixture = managed_install
    state_path = fixture.service.config_dir / "install-state"
    with state_path.open("a", encoding="utf-8") as state:
        state.write(f"data_dir={fixture.data_dir}\n")

    with pytest.raises(UpdateError, match="duplicate data_dir"):
        fixture.service.update()


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
