"""Portability tests for scripts transferred to Unix deployment targets."""

from __future__ import annotations

import tempfile
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from shared.deploy.remote import (
    _remote_destination,
    _scp_script,
    _upload_private_script,
    deploy_remote,
)


def test_scp_script_writes_utf8_with_lf_line_endings():
    real_named_temporary_file = tempfile.NamedTemporaryFile
    opened_with = {}
    transferred = {}

    def named_temporary_file(*args, **kwargs):
        opened_with.update(kwargs)
        return real_named_temporary_file(*args, **kwargs)

    def capture_scp(command, **kwargs):
        local_path = command[-2]
        with open(local_path, "rb") as script_file:
            transferred["contents"] = script_file.read()
        return CompletedProcess(command, 0, stdout="", stderr="")

    with (
        patch(
            "shared.deploy.remote.tempfile.NamedTemporaryFile",
            side_effect=named_temporary_file,
        ),
        patch("shared.deploy.remote.subprocess.run", side_effect=capture_scp),
    ):
        result = _scp_script(
            "#!/usr/bin/env bash\necho '東京'\n",
            "deploy@example.com",
            "/tmp/deploy.sh",
            [],
        )

    assert result["success"] is True
    assert opened_with["encoding"] == "utf-8"
    assert opened_with["newline"] == "\n"
    assert transferred["contents"] == "#!/usr/bin/env bash\necho '東京'\n".encode()


def test_private_upload_creates_an_owner_only_remote_file():
    """The uploaded script holds the database password.

    It goes over the SSH channel as bytes so no host rewrites its newlines,
    and the remote shell restricts the mode before the file exists.
    """
    calls = {}

    def capture_ssh(command, **kwargs):
        calls["command"] = command
        calls["input"] = kwargs.get("input")
        assert kwargs.get("text") is not True
        return CompletedProcess(command, 0, stdout=b"", stderr=b"")

    with patch("shared.deploy.remote.subprocess.run", side_effect=capture_ssh):
        result = _upload_private_script(
            "#!/usr/bin/env bash\necho '東京'\n",
            "deploy@example.com",
            "/tmp/rdst-deploy-prod-0123456789abcdef.sh",
            [],
        )

    assert result["success"] is True
    assert calls["input"] == "#!/usr/bin/env bash\necho '東京'\n".encode()
    remote_command = calls["command"][-1]
    assert remote_command.startswith("umask 077 &&")
    assert "/tmp/rdst-deploy-prod-0123456789abcdef.sh" in remote_command


@pytest.mark.parametrize("user, host", [
    ("-oProxyCommand=touch /tmp/pwned", "example.com"),
    ("root", "-oProxyCommand=touch /tmp/pwned"),
    ("root", "host name"),
    ("ro ot", "example.com"),
    ("", "example.com"),
])
def test_a_destination_that_reads_as_an_option_is_refused(user, host):
    with patch("shared.deploy.remote.subprocess.run") as run:
        result = deploy_remote("t", {}, host=host, ssh_user=user)

    assert result["success"] is False
    run.assert_not_called()


@pytest.mark.parametrize("user, host", [
    ("root", "db.example.com"), ("deploy", "prod-bastion"), ("ec2-user", "10.0.0.5"),
    ("root", "2001:db8::1"), ("root", "[2001:db8::1]"), ("alice@corp", "jump"),
])
def test_addresses_aliases_and_ipv6_are_accepted(user, host):
    assert _remote_destination(user, host) == f"{user}@{host}"


def test_ssh_and_scp_end_options_before_the_destination():
    with patch("shared.deploy.remote.subprocess.run",
               return_value=CompletedProcess([], 0, stdout="", stderr="")) as run:
        _upload_private_script("echo", "root@db.example.com", "/tmp/x.sh", ["-o", "A=b"])
        _scp_script("echo", "root@2001:db8::1", "/tmp/x.sh", [])

    ssh_command = run.call_args_list[0].args[0]
    assert ssh_command[:5] == ["ssh", "-o", "A=b", "--", "root@db.example.com"]
    scp_command = run.call_args_list[1].args[0]
    assert scp_command[1] == "--"
    assert scp_command[-1] == "root@[2001:db8::1]:/tmp/x.sh"
