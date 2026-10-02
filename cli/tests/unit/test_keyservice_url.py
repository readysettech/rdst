import sys
import types

import pytest

from shared.keyservice import (
    DEFAULT_KEYSERVICE_URL,
    KEYSERVICE_URL_ENV_VAR,
    keyservice_base_url,
    keyservice_url,
)

CL_URL = "https://keyservice-cl16302.readyset.io"


@pytest.fixture
def baked(monkeypatch):
    """Install a generated _version_build module, optionally carrying a URL."""

    def install(url: str | None) -> None:
        module = types.ModuleType("_version_build")
        module.__version__ = "0.1.42"
        if url is not None:
            module.KEYSERVICE_URL = url
        monkeypatch.setitem(sys.modules, "_version_build", module)

    monkeypatch.delenv(KEYSERVICE_URL_ENV_VAR, raising=False)
    return install


def test_release_build_uses_production(baked):
    baked(None)
    assert keyservice_base_url() == DEFAULT_KEYSERVICE_URL


def test_source_checkout_without_build_metadata_uses_production(monkeypatch):
    monkeypatch.delenv(KEYSERVICE_URL_ENV_VAR, raising=False)
    monkeypatch.setitem(sys.modules, "_version_build", None)
    assert keyservice_base_url() == DEFAULT_KEYSERVICE_URL


def test_cl_build_uses_its_baked_url(baked):
    baked(CL_URL + "/")
    assert keyservice_base_url() == CL_URL
    assert keyservice_url("/register") == f"{CL_URL}/register"


def test_environment_override_beats_baked_url(baked, monkeypatch):
    baked(CL_URL)
    monkeypatch.setenv(KEYSERVICE_URL_ENV_VAR, "http://127.0.0.1:8788")
    assert keyservice_base_url() == "http://127.0.0.1:8788"


def test_version_names_a_non_production_keyservice(baked):
    from shared.cli import RdstCLI

    baked(None)
    assert "Keyservice:" not in RdstCLI().version().message
    baked(CL_URL)
    assert RdstCLI().version().message.endswith(f"\nKeyservice: {CL_URL}")
