"""API tests for writing modern AWS SSO profiles."""

from __future__ import annotations

import configparser

import pytest

from .conftest import make_fleet_router_client as _client


pytestmark = pytest.mark.usefixtures("run_blocking_inline")


def _profile_body(name: str = "dev") -> dict[str, str]:
    return {
        "name": name,
        "sso_start_url": "https://example.awsapps.com/start",
        "sso_region": "us-east-1",
        "sso_account_id": "123456789012",
        "sso_role_name": "AdministratorAccess",
        "region": "us-west-2",
    }


def test_create_profile_uses_aws_config_file(monkeypatch, tmp_path):
    config_path = tmp_path / "custom" / "aws-config"
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config_path))

    response = _client().post("/api/fleet/aws-profiles", json=_profile_body())

    assert response.status_code == 200
    assert response.json() == {"created": True, "profile": "dev"}
    assert config_path.exists()
    content = config_path.read_text()
    assert "[sso-session dev]" in content
    assert "sso_registration_scopes = sso:account:access" in content
    assert "[profile dev]" in content

    parser = configparser.RawConfigParser()
    parser.read(config_path)
    assert parser["sso-session dev"]["sso_start_url"] == (
        "https://example.awsapps.com/start"
    )
    assert parser["sso-session dev"]["sso_region"] == "us-east-1"
    assert parser["profile dev"]["sso_session"] == "dev"
    assert parser["profile dev"]["sso_account_id"] == "123456789012"
    assert parser["profile dev"]["sso_role_name"] == "AdministratorAccess"
    assert parser["profile dev"]["region"] == "us-west-2"


def test_invalid_profile_name_is_rejected(monkeypatch, tmp_path):
    config_path = tmp_path / "aws-config"
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config_path))

    response = _client().post(
        "/api/fleet/aws-profiles", json=_profile_body("dev; echo injected")
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_profile"
    assert not config_path.exists()


def test_duplicate_profile_is_rejected(monkeypatch, tmp_path):
    config_path = tmp_path / "aws-config"
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config_path))
    client = _client()

    assert client.post("/api/fleet/aws-profiles", json=_profile_body()).status_code == 200
    response = client.post("/api/fleet/aws-profiles", json=_profile_body())

    assert response.status_code == 409
    assert response.json()["code"] == "profile_exists"
