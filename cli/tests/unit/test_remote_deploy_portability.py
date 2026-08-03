"""Portability tests for scripts transferred to Unix deployment targets."""

from __future__ import annotations

import tempfile
from subprocess import CompletedProcess
from unittest.mock import patch

from shared.deploy.remote import _scp_script


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
