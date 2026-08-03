"""Provider allowlist read-merge-write and API safety tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from features.allowlist import providers
from features.allowlist.api import routes
from features.allowlist.providers import AddIpResult, AllowlistState
from features.allowlist import service
from shared.config.targets import TargetsConfig

pytestmark = pytest.mark.usefixtures("run_blocking_inline")


@dataclass
class _Response:
    status_code: int
    payload: Any = None
    text: str = ""

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


PROVIDER_CASES = {
    "supabase": {
        "target": {
            "tags": ["provider:supabase", "supabase-ref:project-ref"],
        },
        "read": {
            "config": {
                "dbAllowedCidrs": ["192.0.2.0/24"],
                "dbAllowedCidrsV6": ["2001:db8::/48"],
            }
        },
        "write_method": "post",
    },
    "neon": {
        "target": {
            "tags": ["provider:neon", "neon-project:project-id"],
        },
        "read": {
            "project": {
                "settings": {
                    "allowed_ips": {
                        "ips": ["192.0.2.0/24"],
                        "protected_branches_only": True,
                    }
                }
            }
        },
        "write_method": "patch",
    },
    "digitalocean": {
        "target": {
            "tags": ["provider:digitalocean", "do-cluster:cluster-id"],
        },
        "read": {
            "rules": [
                {"uuid": "one", "type": "tag", "value": "backend"},
                {"uuid": "two", "type": "ip_addr", "value": "192.0.2.0/24"},
            ]
        },
        "write_method": "put",
    },
}


@pytest.mark.parametrize("provider", PROVIDER_CASES)
def test_provider_add_reads_merges_and_writes_full_list(monkeypatch, provider):
    case = PROVIDER_CASES[provider]
    monkeypatch.setattr(
        providers, "credential_for_provider", lambda name: ("token", "test")
    )
    verified_read = case["read"]
    if provider == "supabase":
        verified_read = {
            "config": {
                **case["read"]["config"],
                "dbAllowedCidrs": ["192.0.2.0/24", "198.51.100.7/32"],
            }
        }
    elif provider == "neon":
        verified_read = {
            "project": {
                "settings": {
                    "allowed_ips": {
                        "ips": ["192.0.2.0/24", "198.51.100.7/32"],
                        "protected_branches_only": True,
                    }
                }
            }
        }
    else:
        verified_read = {
            "rules": [
                *case["read"]["rules"],
                {"uuid": "three", "type": "ip_addr", "value": "198.51.100.7/32"},
            ]
        }
    reader = MagicMock(
        side_effect=[_Response(200, case["read"]), _Response(200, verified_read)]
    )
    monkeypatch.setattr(providers.requests, "get", reader)
    writer = MagicMock(
        return_value=_Response(204 if provider == "digitalocean" else 200, {})
    )
    monkeypatch.setattr(providers.requests, case["write_method"], writer)

    result = providers.add_ip(case["target"], "198.51.100.7")

    assert result == AddIpResult(
        provider=provider,
        cidr="198.51.100.7/32",
        wrote=True,
        previous_count=2 if provider != "neon" else 1,
        verified=True,
        credential_method="test" if provider == "digitalocean" else None,
    )
    assert reader.call_count == 2
    payload = writer.call_args.kwargs["json"]
    if provider == "supabase":
        assert payload == {
            "dbAllowedCidrs": ["192.0.2.0/24", "198.51.100.7/32"],
            "dbAllowedCidrsV6": ["2001:db8::/48"],
        }
    elif provider == "neon":
        assert payload["project"]["settings"]["allowed_ips"] == {
            "ips": ["192.0.2.0/24", "198.51.100.7/32"],
            "protected_branches_only": True,
        }
    else:
        assert payload["rules"] == [
            {"type": "tag", "value": "backend"},
            {"type": "ip_addr", "value": "192.0.2.0/24"},
            {"type": "ip_addr", "value": "198.51.100.7/32"},
        ]


@pytest.mark.parametrize("provider", PROVIDER_CASES)
def test_provider_add_aborts_write_when_read_fails(monkeypatch, provider):
    case = PROVIDER_CASES[provider]
    monkeypatch.setattr(
        providers, "credential_for_provider", lambda name: ("token", "test")
    )
    monkeypatch.setattr(
        providers.requests,
        "get",
        MagicMock(return_value=_Response(503, {"message": "temporarily unavailable"})),
    )
    writer = MagicMock()
    monkeypatch.setattr(providers.requests, case["write_method"], writer)

    with pytest.raises(
        providers.ProviderAllowlistError, match="temporarily unavailable"
    ):
        providers.add_ip(case["target"], "198.51.100.7")

    writer.assert_not_called()


def test_provider_add_skips_write_when_existing_range_covers_ip(monkeypatch):
    target = PROVIDER_CASES["neon"]["target"]
    monkeypatch.setattr(
        providers, "credential_for_provider", lambda name: ("token", "test")
    )
    monkeypatch.setattr(
        providers.requests,
        "get",
        MagicMock(return_value=_Response(200, PROVIDER_CASES["neon"]["read"])),
    )
    writer = MagicMock()
    monkeypatch.setattr(providers.requests, "patch", writer)

    result = providers.add_ip(target, "192.0.2.42")

    assert result.wrote is False
    assert result.verified is True
    assert result.cidr == "192.0.2.42/32"
    writer.assert_not_called()


def test_digitalocean_write_uses_full_rules_shape_and_surfaces_provider_error(
    monkeypatch,
):
    case = PROVIDER_CASES["digitalocean"]
    monkeypatch.setattr(
        providers, "credential_for_provider", lambda name: ("token", "oauth")
    )
    monkeypatch.setattr(
        providers.requests, "get", MagicMock(return_value=_Response(200, case["read"]))
    )
    writer = MagicMock(
        return_value=_Response(
            403,
            {"message": "token is missing the database:update scope"},
        )
    )
    monkeypatch.setattr(providers.requests, "put", writer)

    with pytest.raises(
        providers.ProviderAllowlistError,
        match=(
            "token is missing the database:update scope.*"
            "Sign out and sign back in to DigitalOcean"
        ),
    ):
        providers.add_ip(case["target"], "198.51.100.7")

    assert writer.call_count == 1
    assert writer.call_args.kwargs["headers"]["Authorization"] == "Bearer token"
    assert writer.call_args.kwargs["json"] == {
        "rules": [
            {"type": "tag", "value": "backend"},
            {"type": "ip_addr", "value": "192.0.2.0/24"},
            {"type": "ip_addr", "value": "198.51.100.7/32"},
        ]
    }
def test_provider_add_reports_unverified_read_back(monkeypatch):
    case = PROVIDER_CASES["digitalocean"]
    monkeypatch.setattr(
        providers, "credential_for_provider", lambda name: ("token", "oauth")
    )
    monkeypatch.setattr(
        providers.requests,
        "get",
        MagicMock(
            side_effect=[
                _Response(200, case["read"]),
                _Response(200, case["read"]),
            ]
        ),
    )
    monkeypatch.setattr(providers.requests, "put", MagicMock(return_value=_Response(204)))

    result = providers.add_ip(case["target"], "198.51.100.7")

    assert result.wrote is True
    assert result.verified is False


def _stored_target(tmp_rdst_home) -> None:
    config = TargetsConfig()
    config.load()
    config.upsert(
        "prod",
        {
            "engine": "postgresql",
            "host": "db.example.com",
            "tags": ["provider:supabase", "supabase-ref:project-ref"],
        },
    )
    config.save()


async def _request(method: str, path: str, json=None):
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        return await client.request(method, path, json=json)


async def test_context_signed_out_returns_ip_guidance_and_never_writes(
    monkeypatch, tmp_rdst_home
):
    _stored_target(tmp_rdst_home)
    monkeypatch.setattr(service, "get_public_ip", lambda: "198.51.100.7")
    monkeypatch.setattr(
        service, "credential_for_provider", lambda provider: (None, None)
    )
    add = MagicMock()
    monkeypatch.setattr(service, "add_ip", add)
    writes = [MagicMock(), MagicMock(), MagicMock()]
    for name, mock in zip(("post", "patch", "put"), writes):
        monkeypatch.setattr(providers.requests, name, mock)

    response = await _request("GET", "/api/allowlist/context?target=prod")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "supabase",
        "signed_in": False,
        "current_ip": "198.51.100.7",
        "already_allowed": None,
        "entry_count": None,
        "guidance": (
            "Supabase network restrictions may be blocking this machine's "
            "direct Postgres or pooler connection."
        ),
        "error": None,
    }
    add.assert_not_called()
    assert all(not mock.called for mock in writes)


async def test_context_error_uses_context_response_shape(tmp_rdst_home):
    response = await _request("GET", "/api/allowlist/context?target=missing")

    assert response.status_code == 404
    assert response.json() == {
        "provider": "",
        "signed_in": False,
        "current_ip": "",
        "already_allowed": None,
        "entry_count": None,
        "guidance": "",
        "error": "Target 'missing' was not found.",
    }


async def test_context_signed_in_reads_entries_without_writing(
    monkeypatch, tmp_rdst_home
):
    _stored_target(tmp_rdst_home)
    monkeypatch.setattr(service, "get_public_ip", lambda: "198.51.100.7")
    monkeypatch.setattr(
        service, "credential_for_provider", lambda provider: ("token", "oauth")
    )
    read = MagicMock(
        return_value=AllowlistState(
            provider="supabase",
            entries=["198.51.100.0/24"],
            entry_count=3,
            raw={},
        )
    )
    monkeypatch.setattr(service, "read_allowlist", read)
    add = MagicMock()
    monkeypatch.setattr(service, "add_ip", add)

    response = await _request("GET", "/api/allowlist/context?target=prod")

    assert response.status_code == 200
    body = response.json()
    assert body["signed_in"] is True
    assert body["already_allowed"] is True
    assert body["entry_count"] == 3
    read.assert_called_once()
    add.assert_not_called()


async def test_post_records_only_a_successful_provider_add(monkeypatch, tmp_rdst_home):
    _stored_target(tmp_rdst_home)
    monkeypatch.setattr(service, "get_public_ip", lambda: "198.51.100.7")
    monkeypatch.setattr(
        service, "credential_for_provider", lambda provider: ("token", "oauth")
    )
    monkeypatch.setattr(
        service,
        "add_ip",
        lambda target, ip: AddIpResult(
            "supabase", "198.51.100.7/32", True, 2, True
        ),
    )

    response = await _request(
        "POST", "/api/allowlist/add", json={"target": "prod"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["category"] == "allowlist_updated"
    assert body["added_ip"] == "198.51.100.7/32"
    assert body["verified"] is True
    config = TargetsConfig()
    config.load()
    recorded = config.get("prod")["rdst_added_allowlist"]
    assert recorded[0]["ip"] == "198.51.100.7/32"
    assert recorded[0]["provider"] == "supabase"
    assert recorded[0]["added_at"].endswith("Z")


async def test_post_signed_out_is_actionable(monkeypatch, tmp_rdst_home):
    _stored_target(tmp_rdst_home)
    monkeypatch.setattr(
        service, "credential_for_provider", lambda provider: (None, None)
    )

    response = await _request(
        "POST", "/api/allowlist/add", json={"target": "prod"}
    )

    assert response.status_code == 401
    assert response.json()["category"] == "provider_sign_in_required"
    assert "Connect supabase" in response.json()["message"]


async def test_post_aborts_if_ip_changed_after_confirmation(
    monkeypatch, tmp_rdst_home
):
    _stored_target(tmp_rdst_home)
    monkeypatch.setattr(service, "get_public_ip", lambda: "198.51.100.8")
    monkeypatch.setattr(
        service, "credential_for_provider", lambda provider: ("token", "oauth")
    )
    add = MagicMock()
    monkeypatch.setattr(service, "add_ip", add)

    response = await _request(
        "POST",
        "/api/allowlist/add",
        json={"target": "prod", "expected_ip": "198.51.100.7"},
    )

    assert response.status_code == 409
    assert response.json()["category"] == "public_ip_changed"
    add.assert_not_called()


def test_diagnosis_is_provider_network_only():
    provider_target = {"tags": ["provider:neon"]}
    assert service.is_provider_network_failure(
        provider_target, "connection timed out"
    )
    assert not service.is_provider_network_failure(
        provider_target, "password authentication failed"
    )
    assert not service.is_provider_network_failure(
        {"host": "manual.example.com"}, "connection refused"
    )
    assert not service.is_provider_network_failure(
        {**provider_target, "ssh": {"host": "jump"}}, "connection refused"
    )


@pytest.mark.parametrize(
    ("provider", "message"),
    [
        (
            "supabase",
            "FATAL: Address not allowed. Address is not in the allowed list",
        ),
        (
            "supabase",
            "connection to server at \"db.supabase.co\", port 5432 failed: "
            "FATAL:  Address not allowed. Address is not in the allowed list",
        ),
        ("neon", "IP address is not allowed by Neon IP Allow"),
        ("digitalocean", "could not connect to server: Connection timed out"),
        (
            "digitalocean",
            "Host '198.51.100.7' is not allowed to connect to this MySQL server",
        ),
    ],
)
def test_provider_refusal_text_maps_to_allowlist_category(provider, message):
    target = {"tags": [f"provider:{provider}"]}

    assert (
        service.connection_failure_category(target, message)
        == service.PROVIDER_IP_BLOCKED_MAYBE
    )


@pytest.mark.asyncio
async def test_discovery_credentials_status_propagates_supabase_category():
    from features.fleet.service import FleetService

    target = {
        "engine": "postgresql",
        "host": "db.supabase.co",
        "port": 5432,
        "database": "postgres",
        "user": "postgres",
        "password": "test-password",
        "tags": ["provider:supabase"],
    }
    config = MagicMock()
    config.list_fleet_targets.return_value = ["supabase-db"]
    config.get.return_value = target
    service_under_test = FleetService(config=config)
    refusal = (
        'connection to server at "db.supabase.co", port 5432 failed: '
        "FATAL:  Address not allowed. Address is not in the allowed list"
    )

    with patch.object(
        service_under_test,
        "_check_connection",
        side_effect=RuntimeError(refusal),
    ):
        events = [event async for event in service_under_test.check_status()]

    failure = events[-1]
    assert failure.status == "failed"
    assert failure.category == service.PROVIDER_IP_BLOCKED_MAYBE


@pytest.mark.asyncio
async def test_discovery_status_probes_provider_before_missing_password_gate():
    from features.configure.service import ConfigureService
    from features.fleet.service import FleetService

    target = {
        "engine": "postgresql",
        "host": "db.supabase.co",
        "port": 5432,
        "database": "postgres",
        "user": "postgres",
        "password_env": "SUPABASE_DB_PASSWORD",
        "tags": ["provider:supabase"],
    }
    config = MagicMock()
    config.list_fleet_targets.return_value = ["supabase-db"]
    config.get.return_value = target
    service_under_test = FleetService(config=config)
    probe_result = {
        "success": False,
        "category": service.PROVIDER_IP_BLOCKED_MAYBE,
        "message": "Supabase network restrictions may be blocking this connection.",
    }

    with patch.object(
        ConfigureService,
        "perform_connection_test",
        new=AsyncMock(return_value=probe_result),
    ) as probe:
        events = [event async for event in service_under_test.check_status()]

    failure = events[-1]
    assert failure.status == "failed"
    assert failure.category == service.PROVIDER_IP_BLOCKED_MAYBE
    assert failure.code is None
    probe.assert_awaited_once()


async def test_post_verification_failure_returns_ok_false(monkeypatch, tmp_rdst_home):
    _stored_target(tmp_rdst_home)
    monkeypatch.setattr(service, "get_public_ip", lambda: "198.51.100.7")
    monkeypatch.setattr(
        service, "credential_for_provider", lambda provider: ("token", "oauth")
    )
    monkeypatch.setattr(
        service,
        "add_ip",
        lambda target, ip: AddIpResult(
            "supabase", "198.51.100.7/32", True, 2, False
        ),
    )

    response = await _request(
        "POST", "/api/allowlist/add", json={"target": "prod"}
    )

    assert response.status_code == 502
    assert response.json()["ok"] is False
    assert response.json()["verified"] is False
    assert response.json()["category"] == "allowlist_verification_failed"


def test_public_ip_falls_back_and_is_not_cached(monkeypatch):
    get = MagicMock(
        side_effect=[
            requests.Timeout("checkip timed out"),
            _Response(200, text="198.51.100.7\n"),
            _Response(200, text="198.51.100.8\n"),
        ]
    )
    monkeypatch.setattr(service.requests, "get", get)

    assert service.get_public_ip() == "198.51.100.7"
    assert service.get_public_ip() == "198.51.100.8"
    assert [call.args[0] for call in get.call_args_list] == [
        "https://checkip.amazonaws.com",
        "https://api.ipify.org",
        "https://checkip.amazonaws.com",
    ]
    assert all(
        call.kwargs["timeout"] == service.PUBLIC_IP_TIMEOUT
        for call in get.call_args_list
    )
