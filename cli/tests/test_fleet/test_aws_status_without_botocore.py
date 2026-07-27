"""The fleet auth module remains importable without optional AWS packages."""

from __future__ import annotations

import builtins

from features.fleet.auth import get_aws_status


def test_aws_status_without_botocore(monkeypatch):
    real_import = builtins.__import__

    def import_without_botocore(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "botocore" or name.startswith("botocore."):
            raise ImportError("simulated missing botocore")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_without_botocore)

    status = get_aws_status()

    assert status["has_credentials"] is False
    assert status["available_profiles"] == []
    assert status["detail"] == "boto3 is not installed"
