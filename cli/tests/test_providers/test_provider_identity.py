from unittest.mock import MagicMock, patch

from features.providers import identity
from shared.config.targets import TargetsConfig
from shared.telemetry_manager import TelemetryManager


def _response(status: int, body=None):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = body
    response.text = ""
    return response


def test_digitalocean_identity_parsing():
    response = _response(
        200,
        {
            "account": {
                "email": "do@example.com",
                "name": "Ada",
                "email_verified": True,
            }
        },
    )
    with patch(
        "features.providers.identity.bearer_get", return_value=response
    ) as get:
        parsed = identity.fetch_digitalocean_identity("oauth-token")

    assert parsed == {
        "email": "do@example.com",
        "name": "Ada",
        "email_verified": True,
    }
    assert get.call_args.args[:2] == (
        "https://api.digitalocean.com/v2/account",
        "oauth-token",
    )


def test_supabase_identity_uses_pat_after_oauth_401(monkeypatch):
    responses = [
        _response(401),
        _response(200, {"primary_email": "sb@example.com", "username": "ada"}),
    ]
    monkeypatch.setattr(identity, "read_secret", lambda name: "pat-token")

    with patch("features.providers.identity.bearer_get", side_effect=responses) as get:
        parsed = identity.fetch_supabase_identity("oauth-token")

    assert parsed == {"email": "sb@example.com", "name": "ada"}
    assert [call.args[1] for call in get.call_args_list] == [
        "oauth-token",
        "pat-token",
    ]


def test_supabase_identity_skips_when_oauth_and_pat_are_forbidden(monkeypatch):
    monkeypatch.setattr(identity, "read_secret", lambda name: "pat-token")
    with patch(
        "features.providers.identity.bearer_get",
        side_effect=[_response(401), _response(403)],
    ):
        assert identity.fetch_supabase_identity("oauth-token") is None


def test_neon_identity_and_organization_key_path():
    with patch(
        "features.providers.identity.bearer_get",
        return_value=_response(200, {"email": "neon@example.com", "name": "Grace"}),
    ):
        assert identity.fetch_neon_identity("personal") == {
            "email": "neon@example.com",
            "name": "Grace",
        }

    with patch(
        "features.providers.identity.bearer_get", return_value=_response(403)
    ):
        assert identity.fetch_neon_identity("organization") is None


def test_aws_identity_keeps_only_email_role_sessions():
    assert identity.aws_email_from_arn(
        "arn:aws:sts::123:assumed-role/Admin/michael.v@readyset.io"
    ) == "michael.v@readyset.io"
    assert identity.aws_email_from_arn(
        "arn:aws:sts::123:assumed-role/Admin/bot-session"
    ) is None


def test_provider_identity_config_shape_and_posthog_properties(tmp_path):
    config_path = tmp_path / "config.toml"
    assert identity.store_provider_identity(
        "digitalocean",
        {"email": "do@example.com", "name": "Ada", "email_verified": True},
        config_path,
    )

    config = TargetsConfig(path=str(config_path))
    config.load()
    assert config.get_provider_identities() == {
        "digitalocean": {
            "email": "do@example.com",
            "name": "Ada",
            "email_verified": True,
        }
    }
    assert "[provider_identity.digitalocean]" in config_path.read_text()
    assert config.get_identity()["email"] is None

    telemetry = TelemetryManager()
    telemetry._rdst_dir = tmp_path
    properties = {}
    telemetry._add_stored_email_properties(properties)
    assert properties == {
        "provider_email_digitalocean": "do@example.com",
        "provider_name_digitalocean": "Ada",
        "provider_email_verified_digitalocean": True,
    }
