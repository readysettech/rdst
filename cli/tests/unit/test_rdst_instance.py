"""Desktop flavor instances keep their data, secrets and Docker names apart."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from shared.constants import (
    INSTANCE_ENV_VAR,
    rdst_data_dir,
    rdst_instance,
    rdst_instance_suffix,
    rdst_semantic_layer_dir,
)
from shared.keyservice import (
    DEFAULT_KEYSERVICE_URL,
    KEYSERVICE_URL_ENV_VAR,
    keyservice_base_url,
)
from shared.secret_store_service import SecretStoreService

RDST_ROOT = Path(__file__).resolve().parents[2]
STAGING_KEYSERVICE = "https://keyservice-staging.readyset.io"


@pytest.fixture(autouse=True)
def _production(monkeypatch):
    for variable in (INSTANCE_ENV_VAR, "RDST_DATA_DIR", KEYSERVICE_URL_ENV_VAR):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def frozen_sidecar(monkeypatch, tmp_path):
    """Run as a frozen sidecar in tmp_path, optionally beside an instance.json."""

    def install(marker: dict | str | None) -> Path:
        sidecar_dir = tmp_path / "sidecar"
        sidecar_dir.mkdir(exist_ok=True)
        executable = sidecar_dir / "rdst"
        executable.write_text("", encoding="utf-8")
        if marker is not None:
            text = marker if isinstance(marker, str) else json.dumps(marker)
            (sidecar_dir / "instance.json").write_text(text, encoding="utf-8")
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", str(executable))
        return sidecar_dir

    return install


def test_production_uses_dot_rdst():
    assert rdst_instance() == ""
    assert rdst_instance_suffix() == ""
    assert rdst_data_dir() == Path.home() / ".rdst"
    assert rdst_semantic_layer_dir() == Path.home() / ".rdst" / "semantic-layer"


def test_instance_env_selects_its_own_data_dir(monkeypatch):
    monkeypatch.setenv(INSTANCE_ENV_VAR, "staging")
    assert rdst_instance() == "staging"
    assert rdst_instance_suffix() == "-staging"
    assert rdst_data_dir() == Path.home() / ".rdst-staging"
    assert rdst_semantic_layer_dir() == Path.home() / ".rdst-staging" / "semantic-layer"


def test_data_dir_env_beats_instance(monkeypatch, tmp_path):
    monkeypatch.setenv(INSTANCE_ENV_VAR, "staging")
    monkeypatch.setenv("RDST_DATA_DIR", str(tmp_path / "custom"))
    assert rdst_data_dir() == tmp_path / "custom"


def test_blank_instance_env_is_production(monkeypatch):
    monkeypatch.setenv(INSTANCE_ENV_VAR, "  ")
    assert rdst_instance() == ""
    assert rdst_data_dir() == Path.home() / ".rdst"


@pytest.mark.parametrize(
    "name", ["Staging", "1cl", "-cl", "cl_1", "../prod", "a" * 33, "cl\nx"]
)
def test_invalid_instance_fails_closed(monkeypatch, name):
    monkeypatch.setenv(INSTANCE_ENV_VAR, name)
    with pytest.raises(ValueError, match="Invalid RDST instance"):
        rdst_data_dir()
    with pytest.raises(ValueError):
        SecretStoreService()


def test_frozen_sidecar_marker_names_the_instance(frozen_sidecar):
    frozen_sidecar({"instance": "cl"})
    assert rdst_instance() == "cl"
    assert rdst_data_dir() == Path.home() / ".rdst-cl"


def test_instance_env_beats_marker(frozen_sidecar, monkeypatch):
    frozen_sidecar({"instance": "cl"})
    monkeypatch.setenv(INSTANCE_ENV_VAR, "staging")
    assert rdst_instance() == "staging"


def test_frozen_sidecar_without_marker_is_production(frozen_sidecar):
    frozen_sidecar(None)
    assert rdst_instance() == ""
    assert rdst_data_dir() == Path.home() / ".rdst"


def test_production_marker_names_the_empty_instance(frozen_sidecar):
    frozen_sidecar({"instance": ""})
    assert rdst_instance() == ""
    assert rdst_data_dir() == Path.home() / ".rdst"


def test_invalid_marker_instance_fails_closed(frozen_sidecar):
    frozen_sidecar({"instance": "Not Valid"})
    with pytest.raises(ValueError, match="Invalid RDST instance"):
        rdst_data_dir()


@pytest.mark.parametrize("marker", [{}, {"instance": None}, {"instance": False}, {"instance": " "}])
def test_marker_without_an_instance_fails_closed(frozen_sidecar, marker):
    # A marker must name its instance, production's the empty one.
    frozen_sidecar(marker)
    with pytest.raises(ValueError, match="names no instance"):
        rdst_data_dir()


def test_unreadable_marker_fails_closed(frozen_sidecar):
    frozen_sidecar("{not json")
    with pytest.raises(ValueError):
        rdst_data_dir()


def test_marker_is_ignored_from_source(monkeypatch, tmp_path):
    (tmp_path / "instance.json").write_text('{"instance": "cl"}', encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    assert rdst_instance() == ""


def test_keyservice_marker_beats_baked_url(frozen_sidecar, monkeypatch):
    baked = type(sys)("_version_build")
    baked.KEYSERVICE_URL = "https://keyservice-cl1.readyset.io"
    monkeypatch.setitem(sys.modules, "_version_build", baked)
    assert keyservice_base_url() == "https://keyservice-cl1.readyset.io"

    frozen_sidecar({"instance": "staging", "keyservice": STAGING_KEYSERVICE + "/"})
    assert keyservice_base_url() == STAGING_KEYSERVICE

    monkeypatch.setenv(KEYSERVICE_URL_ENV_VAR, "http://127.0.0.1:8788")
    assert keyservice_base_url() == "http://127.0.0.1:8788"


def test_keyservice_marker_without_url_uses_production(frozen_sidecar, monkeypatch):
    monkeypatch.setitem(sys.modules, "_version_build", None)
    frozen_sidecar({"instance": "cl"})
    assert keyservice_base_url() == DEFAULT_KEYSERVICE_URL


def test_keyring_services_follow_the_instance(monkeypatch):
    from features.slack.config import _secret_store as slack_store

    assert SecretStoreService().service_name == "rdst-web"
    assert slack_store().service_name == "rdst-slack"

    monkeypatch.setenv(INSTANCE_ENV_VAR, "staging")
    assert SecretStoreService().service_name == "rdst-web-staging"
    assert slack_store().service_name == "rdst-slack-staging"
    assert SecretStoreService(service_name="custom").service_name == "custom"
    assert SecretStoreService.SERVICE_NAME == "rdst-web"


def test_instance_secrets_stay_out_of_production(monkeypatch, _isolated_keyring):
    monkeypatch.setenv(INSTANCE_ENV_VAR, "cl")
    store = SecretStoreService()
    store._keyring_call(store._keyring.set_password, store.service_name, "K", "cl-value")
    assert _isolated_keyring.get_password("rdst-web-cl", "K") == "cl-value"
    assert _isolated_keyring.get_password("rdst-web", "K") is None


@pytest.mark.parametrize(
    ("protocol", "scheme"),
    [
        (None, "rdst"),
        ("rdst-staging", "rdst-staging"),
        ("rdst-cl", "rdst-cl"),
        ("rdst-dev", "rdst-dev"),
        ("https", "rdst"),
        ("rdst-Evil", "rdst"),
        ("rdst-cl://x?", "rdst"),
        ("", "rdst"),
    ],
)
def test_provider_return_url_uses_the_desktop_protocol(
    monkeypatch, loopback_request, protocol, scheme
):
    from features.providers.api.routes import _provider_return_url

    monkeypatch.setenv("RDST_DESKTOP", "1")
    if protocol is None:
        monkeypatch.delenv("RDST_DESKTOP_PROTOCOL", raising=False)
    else:
        monkeypatch.setenv("RDST_DESKTOP_PROTOCOL", protocol)
    assert _provider_return_url(loopback_request, "supabase") == (
        f"{scheme}://oauth-complete?provider=supabase"
    )


def _run_claude(action: str) -> list[str]:
    import rdst as rdst_module
    from shared.cli import RdstCLI

    called = {}

    def capture(cmd, **kwargs):
        called["cmd"] = cmd
        return MagicMock(returncode=0, stderr="")

    args = argparse.Namespace(command="claude", action=action, config=None)
    with patch("shutil.which", side_effect=lambda name: f"/bin/{name}"):
        with patch("subprocess.run", side_effect=capture):
            with patch("os.makedirs"), patch("os.remove"):
                with patch("builtins.open", mock.mock_open()):
                    result = rdst_module.execute_command(RdstCLI(), args)
    assert result.ok is True
    return called["cmd"]


def test_claude_slash_commands_belong_to_their_instance(monkeypatch, tmp_path):
    import rdst as rdst_module
    from shared.cli import RdstCLI

    monkeypatch.setenv("HOME", str(tmp_path))
    commands = tmp_path / ".claude" / "commands"

    def claude(action: str) -> None:
        args = argparse.Namespace(command="claude", action=action, config=None)
        with patch("shutil.which", side_effect=lambda name: f"/bin/{name}"):
            with patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")):
                assert rdst_module.execute_command(RdstCLI(), args).ok is True

    claude("add")
    monkeypatch.setenv(INSTANCE_ENV_VAR, "staging")
    claude("add")
    assert sorted(path.name for path in commands.iterdir()) == ["rdst-staging.md", "rdst.md"]
    claude("remove")
    assert sorted(path.name for path in commands.iterdir()) == ["rdst.md"]


@pytest.mark.parametrize("action", ["add", "remove"])
def test_claude_mcp_server_name_follows_the_instance(monkeypatch, action):
    assert _run_claude(action)[2:4] == [action, "rdst"]
    monkeypatch.setenv(INSTANCE_ENV_VAR, "staging")
    assert _run_claude(action)[2:4] == [action, "rdst-staging"]


_DOCKER_NAMES_PROBE = textwrap.dedent(
    """
    import json
    import subprocess
    from pathlib import Path

    from features.demo.cli import command as demo
    from features.qprouter import deploy
    from features.qpdemo import service
    from shared.deploy import local_docker, sandbox_manager, script_generator

    commands = []

    def run(cmd, timeout=300):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    deploy._run = run
    deploy.deploy_readyset("r", 1, 2, 3, service.CREDS)
    deploy.deploy_sqp("s", deploy.Ports(1, 2, 3, 4, 5), Path("c"), Path("d"))
    deploy.deploy_qp_cron("q", Path("cfg"))
    links = [cmd[i + 1] for cmd in commands for i, arg in enumerate(cmd) if arg == "--link"]
    print(json.dumps({
        "names": service.NAMES,
        "workdir": str(service.WORKDIR),
        "demo_dir": str(service.DEMO_DIR),
        "links": links,
        "sandbox": local_docker.MANAGED_SANDBOX_NAME,
        "manager_sandbox": sandbox_manager.SANDBOX_CONTAINER_NAME,
        "target": local_docker.target_container_name("mydb"),
        "deploy_target": script_generator.build_variables("mydb", {}, "")["container_name"],
        "demo": demo.DEMO_CONTAINER,
    }))
    """
)


def _docker_names(tmp_path, instance: str | None, data_dir: str | None = None) -> dict:
    """Import the modules fresh: their Docker names resolve at import time."""
    import os

    env = {
        key: value
        for key, value in os.environ.items()
        if key not in (INSTANCE_ENV_VAR, "RDST_DATA_DIR")
    }
    env["HOME"] = str(tmp_path)
    if instance:
        env[INSTANCE_ENV_VAR] = instance
    if data_dir:
        env["RDST_DATA_DIR"] = data_dir
    result = subprocess.run(
        [sys.executable, "-c", _DOCKER_NAMES_PROBE],
        cwd=RDST_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("data_dir", [None, "custom"])
def test_production_docker_names_are_unchanged(tmp_path, data_dir):
    # qpdemo stays under ~/.rdst even with RDST_DATA_DIR set: running qp-cron mounts it.
    names = _docker_names(tmp_path, None, data_dir and str(tmp_path / data_dir))
    assert names["names"] == {
        "pg": "qpdemo-pg",
        "readyset": "qpdemo-readyset",
        "sqp": "qpdemo-sqp",
        "qp-cron": "qpdemo-qp-cron",
    }
    assert names["workdir"] == str(tmp_path / ".rdst" / "qpdemo")
    assert names["demo_dir"] == str(tmp_path / ".rdst" / "demo")
    assert names["links"] == [
        "qpdemo-pg:qpdemo-pg",
        "qpdemo-pg:qpdemo-pg",
        "qpdemo-readyset:qpdemo-readyset",
        "qpdemo-sqp:qpdemo-sqp",
        "qpdemo-readyset:qpdemo-readyset",
    ]
    assert names["sandbox"] == names["manager_sandbox"] == "rdst-readyset-sandbox"
    assert names["target"] == names["deploy_target"] == "rdst-readyset-mydb"
    assert names["demo"] == "rdst-demo-pg"


def test_instance_docker_names_are_suffixed(tmp_path):
    names = _docker_names(tmp_path, "staging")
    assert names["names"] == {
        "pg": "qpdemo-staging-pg",
        "readyset": "qpdemo-staging-readyset",
        "sqp": "qpdemo-staging-sqp",
        "qp-cron": "qpdemo-staging-qp-cron",
    }
    assert names["workdir"] == str(tmp_path / ".rdst-staging" / "qpdemo")
    assert names["demo_dir"] == str(tmp_path / ".rdst-staging" / "demo")
    # Peers keep their production alias, which the rendered configs resolve.
    assert names["links"] == [
        "qpdemo-staging-pg:qpdemo-pg",
        "qpdemo-staging-pg:qpdemo-pg",
        "qpdemo-staging-readyset:qpdemo-readyset",
        "qpdemo-staging-sqp:qpdemo-sqp",
        "qpdemo-staging-readyset:qpdemo-readyset",
    ]
    assert names["sandbox"] == names["manager_sandbox"] == "rdst-readyset-sandbox-staging"
    assert names["target"] == names["deploy_target"] == "rdst-readyset-mydb-staging"
    assert names["demo"] == "rdst-demo-pg-staging"


@pytest.mark.parametrize(
    ("name", "managed", "removed"),
    [
        # Another instance's managed sandbox is never legacy, whatever its name.
        ("rdst-readyset-sandbox-staging", "true", False),
        ("rdst-readyset-sandbox", "true", False),
        # A target named sandbox-* is legacy, as before.
        ("rdst-readyset-sandbox-orders", "", True),
    ],
)
def test_legacy_cleanup_spares_every_instance_sandbox(monkeypatch, name, managed, removed):
    from shared.deploy import local_docker

    monkeypatch.setattr(
        local_docker,
        "_docker_inspect_state_checked",
        lambda _name: ({"id": "abc", "status": "exited", "running": False, "exit_code": "0"}, None),
    )
    monkeypatch.setattr(
        local_docker, "_inspect_exact_container_checked", lambda _name: ({"managed": managed}, None)
    )
    calls = []
    monkeypatch.setattr(
        local_docker.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or MagicMock(returncode=0, stderr=""),
    )
    monkeypatch.setattr(
        local_docker, "_confirm_legacy_container_removed", lambda n, i: {"success": True, "removed": True}
    )
    result = local_docker.remove_configured_legacy_container(name)
    assert result["success"] is removed
    assert bool(calls) is removed
