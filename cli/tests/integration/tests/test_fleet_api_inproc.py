"""In-process integration tests for the fleet API endpoints.

Exercises the config-only paths (targets list, CSV import) and the
status stream's error path. Live connectivity checks belong to the
realdb suite.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from shared.config.targets import TargetsConfig
from shared.run_registry import run_registry


pytestmark = pytest.mark.usefixtures("run_blocking_inline")


@pytest.fixture(autouse=True)
async def _isolated_run_registry():
    """Fleet audits are detached background runs; leave none behind."""
    run_registry.reset()
    yield
    run_registry.reset()


async def _start_fleet_audit(client, collect_sse_events, json_body: dict) -> list[dict]:
    """Start the fleet audit background run and drain its event stream."""
    response = await client.post("/api/fleet/audit", json=json_body)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reused"] is False
    events = await collect_sse_events(
        client, "GET", f"/api/runs/{body['run_id']}/events"
    )
    assert events[-1]["event"] == "run_end"
    return events[:-1]


@pytest.fixture
def seeded_target_defaults() -> dict:
    return {"name": "fleettest", "env": "FLEET_PASS"}


async def test_targets_empty(client, tmp_rdst_home):
    response = await client.get("/api/fleet/targets")
    assert response.status_code == 200
    assert response.json() == {"members": [], "groups": [], "count": 0}


async def test_targets_list_and_group_filter(client, tmp_rdst_home, seed_target):
    seed_target("db1", group="prod", tags=["critical"])
    seed_target("db2", group="staging")

    response = await client.get("/api/fleet/targets")
    body = response.json()
    assert body["count"] == 2
    assert sorted(body["groups"]) == ["prod", "staging"]

    response = await client.get("/api/fleet/targets?group=prod")
    body = response.json()
    assert body["count"] == 1
    assert body["members"][0]["name"] == "db1"
    assert body["members"][0]["tags"] == ["critical"]
    assert body["members"][0]["user"] == "appuser"
    assert body["members"][0]["password_env"] == "FLEET_PASS"
    assert body["members"][0]["has_password"] is False


async def test_target_group_patch_sets_and_clears_group(client, tmp_rdst_home, seed_target):
    seed_target("db1", group="staging", tags=["critical"])

    response = await client.patch(
        "/api/fleet/targets/db1", json={"group": "production"}
    )
    assert response.status_code == 200
    member = response.json()
    assert member["name"] == "db1"
    assert member["group"] == "production"
    assert member["tags"] == ["critical"]

    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("db1")["group"] == "production"

    response = await client.patch("/api/fleet/targets/db1", json={"group": None})
    assert response.status_code == 200
    assert response.json()["group"] is None

    cfg = TargetsConfig()
    cfg.load()
    assert "group" not in cfg.get("db1")


async def test_target_group_patch_unknown_target(client, tmp_rdst_home):
    response = await client.patch(
        "/api/fleet/targets/missing", json={"group": "production"}
    )
    assert response.status_code == 404


async def test_import_csv_stream(client, tmp_rdst_home, tmp_path, collect_sse_events):
    csv_path = tmp_path / "fleet.csv"
    csv_path.write_text(
        "name,host,engine,port,database,user\n"
        "fleet-a,db-a.example.com,postgresql,5432,app,svc\n"
        "fleet-b,db-b.example.com,mysql,3306,app,svc\n"
    )

    events = await collect_sse_events(
        client, "POST", "/api/fleet/import",
        json_body={"csv_file": str(csv_path), "group": "imported"},
    )

    types = [e["event"] for e in events]
    assert types.count("import_progress") == 2
    assert types[-1] == "import_complete"
    complete = events[-1]["data"]
    assert complete["success"] is True
    assert complete["imported"] == 2
    assert complete["target_names"] == ["fleet-a", "fleet-b"]

    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("fleet-a")["group"] == "imported"
    assert cfg.get("fleet-b")["engine"] == "mysql"


async def test_import_csv_content_stream(client, tmp_rdst_home, collect_sse_events):
    csv_content = (
        "name,host,engine,port,database,user\n"
        "browser-a,db-a.example.com,postgresql,5432,app,svc_a\n"
        "browser-b,db-b.example.com,mysql,3306,analytics,svc_b\n"
    )

    events = await collect_sse_events(
        client,
        "POST",
        "/api/fleet/import",
        json_body={"csv_content": csv_content, "group": "browser-upload"},
    )

    assert events[-1]["event"] == "import_complete"
    assert events[-1]["data"]["target_names"] == ["browser-a", "browser-b"]
    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("browser-a")["user"] == "svc_a"
    assert cfg.get("browser-a")["group"] == "browser-upload"
    assert cfg.get("browser-b")["engine"] == "mysql"


async def test_import_dry_run_does_not_persist(client, tmp_rdst_home, tmp_path, collect_sse_events):
    csv_path = tmp_path / "fleet.csv"
    csv_path.write_text("name,host,engine\nfleet-dry,db.example.com,postgresql\n")

    events = await collect_sse_events(
        client, "POST", "/api/fleet/import",
        json_body={"csv_file": str(csv_path), "dry_run": True},
    )
    assert events[-1]["data"]["imported"] == 1

    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("fleet-dry") is None


async def test_status_stream_reports_unreachable_target(
    client, tmp_rdst_home, monkeypatch, collect_sse_events
):
    # Port 9 (discard) is closed locally, so the connection fails fast.
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert("unreachable", {
        "engine": "postgresql", "host": "127.0.0.1", "port": 9,
        "database": "appdb", "user": "appuser", "password_env": "FLEET_PASS",
    })
    cfg.save()
    monkeypatch.setenv("FLEET_PASS", "irrelevant")

    events = await collect_sse_events(client, "GET", "/api/fleet/status")
    connectivity = [e["data"] for e in events if e["event"] == "connectivity"]
    assert any(c["status"] == "checking" for c in connectivity)
    failed = [c for c in connectivity if c["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["target_name"] == "unreachable"


async def test_status_stream_errors_with_no_targets(client, tmp_rdst_home, collect_sse_events):
    events = await collect_sse_events(client, "GET", "/api/fleet/status")
    assert events[-1]["event"] == "error"
    assert "No targets found" in events[-1]["data"]["message"]


async def test_status_stream_checks_only_requested_targets(
    client, tmp_rdst_home, monkeypatch, collect_sse_events, seed_target
):
    seed_target("db1")
    seed_target("db2")
    monkeypatch.setenv("FLEET_PASS", "irrelevant")

    checked: list[str] = []

    def fake_check(self, target_config):
        checked.append(target_config["host"] + ":" + target_config["database"])
        return "PostgreSQL 16"

    with patch("features.fleet.service.FleetService._check_connection", fake_check):
        events = await collect_sse_events(
            client,
            "GET",
            "/api/fleet/status?targets=db2",
        )

    connectivity = [e["data"] for e in events if e["event"] == "connectivity"]
    assert {event["target_name"] for event in connectivity} == {"db2"}
    assert len(checked) == 1


def _seed_snapshot(home, snapshot_id: str, results: list[dict]) -> None:
    snapshots = home / "fleet" / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    import json
    (snapshots / f"{snapshot_id}.json").write_text(json.dumps({
        "snapshot_id": snapshot_id,
        "name": snapshot_id,
        "created_at": "2026-07-07T00:00:00+00:00",
        "targets_audited": len(results),
        "results": results,
    }))


async def test_snapshots_list_get_delete(client, tmp_rdst_home):
    _seed_snapshot(tmp_rdst_home, "fleet_a", [
        {"target_name": "db1", "metrics": {"cache_hit_rate": 90.0}, "sizing": {"verdict": "right_sized"}},
    ])

    response = await client.get("/api/fleet/snapshots")
    body = response.json()
    assert body["count"] == 1
    assert body["snapshots"][0]["snapshot_id"] == "fleet_a"
    assert body["snapshots"][0]["targets_audited"] == 1

    response = await client.get("/api/fleet/snapshots/fleet_a")
    assert response.status_code == 200
    assert response.json()["results"][0]["target_name"] == "db1"

    assert (await client.get("/api/fleet/snapshots/nope")).status_code == 404

    assert (await client.delete("/api/fleet/snapshots/fleet_a")).json() == {"success": True}
    assert (await client.delete("/api/fleet/snapshots/fleet_a")).status_code == 404
    assert (await client.get("/api/fleet/snapshots")).json()["count"] == 0


async def test_snapshots_list_excludes_single_target_audit_saves(client, tmp_rdst_home):
    import json

    _seed_snapshot(tmp_rdst_home, "fleet_a", [
        {"target_name": "db1", "metrics": {}, "sizing": {}},
    ])
    # Single-target quick-audit save: raw AuditResult dict, no `results` list.
    snapshots = tmp_rdst_home / "fleet" / "snapshots"
    (snapshots / "audit_db1_20260707_000000.json").write_text(json.dumps({
        "target_name": "db1",
        "audited_at": "2026-07-07T00:00:00+00:00",
        "metrics": {"cache_hit_rate": 90.0},
    }))

    # Default lists fleet snapshots only.
    body = (await client.get("/api/fleet/snapshots")).json()
    assert body["count"] == 1
    assert body["snapshots"][0]["snapshot_id"] == "fleet_a"
    assert body["snapshots"][0]["kind"] == "fleet"

    body = (await client.get("/api/fleet/snapshots?kind=single")).json()
    assert body["count"] == 1
    assert body["snapshots"][0]["kind"] == "single"

    assert (await client.get("/api/fleet/snapshots?kind=all")).json()["count"] == 2


async def test_diff_reports_metric_and_verdict_changes(client, tmp_rdst_home):
    _seed_snapshot(tmp_rdst_home, "fleet_before", [
        {"target_name": "db1", "metrics": {"cache_hit_rate": 90.0, "database_size_mb": 100.0},
         "sizing": {"verdict": "right_sized"}},
        {"target_name": "gone", "metrics": {}, "sizing": {}},
    ])
    _seed_snapshot(tmp_rdst_home, "fleet_after", [
        {"target_name": "db1", "metrics": {"cache_hit_rate": 95.0, "database_size_mb": 100.0},
         "sizing": {"verdict": "oversized"}},
        {"target_name": "fresh", "metrics": {}, "sizing": {}},
    ])

    response = await client.get("/api/fleet/diff?baseline=fleet_before&current=fleet_after")
    assert response.status_code == 200
    body = response.json()
    assert body["new_targets"] == ["fresh"]
    assert body["removed_targets"] == ["gone"]
    fields = {(e["target_name"], e["field_name"]) for e in body["entries"]}
    assert ("db1", "sizing_verdict") in fields
    assert ("db1", "cache_hit_rate") in fields
    assert ("db1", "database_size_mb") not in fields

    assert (await client.get("/api/fleet/diff?baseline=nope&current=fleet_after")).status_code == 404


async def test_fleet_audit_streams_and_saves_snapshot(
    client, tmp_rdst_home, monkeypatch, collect_sse_events, seed_target
):
    from unittest.mock import patch

    from features.audit.models import AuditMetrics, AuditResult

    seed_target("db1", group="prod")
    seed_target("db2", group="prod")
    monkeypatch.setenv("FLEET_PASS", "irrelevant")

    def fake_audit_target(self, name, target_config, on_progress=None):
        return AuditResult(
            target_name=name, engine="postgresql", host="127.0.0.1",
            metrics=AuditMetrics(max_connections=100),
            audited_at="2026-07-07T00:00:00+00:00",
        )

    with patch("features.audit.service.AuditService.audit_target", fake_audit_target):
        events = await _start_fleet_audit(
            client, collect_sse_events,
            {"group": "prod", "insights": False, "save_name": "fleet_web_test"},
        )

    types = [e["event"] for e in events]
    assert types.count("target_start") == 2
    assert types.count("target_complete") == 2
    assert "snapshot_saved" in types
    assert types[-1] == "complete"
    complete = events[-1]["data"]
    assert complete["success"] is True
    assert complete["snapshot_id"] == "fleet_web_test"
    assert complete["summary"]["successes"] == 2

    # The combined snapshot shows up in the snapshots list and diffs cleanly.
    listing = (await client.get("/api/fleet/snapshots")).json()
    ids = [s["snapshot_id"] for s in listing["snapshots"]]
    assert "fleet_web_test" in ids

    response = await client.get("/api/fleet/snapshots/fleet_web_test")
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2


async def test_fleet_audit_no_targets(client, tmp_rdst_home, collect_sse_events):
    events = await _start_fleet_audit(
        client, collect_sse_events, {"group": "nope"},
    )
    assert events[-1]["event"] == "error"
    assert "No targets found" in events[-1]["data"]["message"]


async def test_discover_imports_and_dedupes(client, tmp_rdst_home, collect_sse_events, seed_target, fleet_member):
    from unittest.mock import patch

    # An existing target with the same host as one discovered instance.
    seed_target("existing-db")
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert("existing-db", {**cfg.get("existing-db"), "host": "old.abc.us-east-1.rds.amazonaws.com"})
    cfg.save()

    members = [
        fleet_member(
            "prod-writer",
            tags=["writer"], instance_class="db.r6g.large", region="us-east-1",
        ),
        fleet_member(
            "old-db", engine="mysql", port=3306, user="admin",
            host="OLD.abc.us-east-1.rds.amazonaws.com",
        ),
    ]

    with patch("features.fleet.auth.detect_aws_credentials", return_value=(True, "ok")):
        with patch(
            "features.fleet.discovery.discover_rds_instances",
            return_value=iter(members),
        ):
            events = await collect_sse_events(
                client, "POST", "/api/fleet/discover",
                json_body={"regions": ["us-east-1"], "group": "aws"},
            )

    types = [e["event"] for e in events]
    assert "discover" in types
    discover = next(e["data"] for e in events if e["event"] == "discover")
    assert discover["instances_found"] == 2

    complete = events[-1]["data"]
    assert complete["imported"] == 1
    assert complete["skipped"] == 1
    assert complete["target_names"] == ["prod-writer"]

    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("prod-writer")["engine"] == "postgresql"
    assert cfg.get("old-db") is None


async def test_discover_surfaces_region_errors(client, tmp_rdst_home, collect_sse_events):
    """Expired/failed AWS calls must not masquerade as a clean empty result."""
    from unittest.mock import patch

    boto_error = Exception(
        "An error occurred (ExpiredToken) when calling the "
        "DescribeDBInstances operation: The security token included in "
        "the request is expired"
    )

    with patch("features.fleet.auth.detect_aws_credentials", return_value=(True, "ok")):
        with patch("features.fleet.discovery.get_rds_client", side_effect=boto_error):
            events = await collect_sse_events(
                client, "POST", "/api/fleet/discover",
                json_body={"regions": ["us-east-1", "us-west-2"], "dry_run": True},
            )

    types = [e["event"] for e in events]
    errors = [e["data"]["message"] for e in events if e["event"] == "error"]
    assert len(errors) == 2
    assert "us-east-1: AWS credentials expired. Refresh with: aws sso login" in errors
    assert "us-west-2: AWS credentials expired. Refresh with: aws sso login" in errors

    # No misleading "Found 0 instance(s)" success path.
    assert "discover" not in types
    complete = events[-1]["data"]
    assert complete["success"] is False
    assert complete["errors"] == 2


async def test_discover_partial_region_failure_still_imports(
    client, tmp_rdst_home, collect_sse_events, fleet_member
):
    from unittest.mock import patch

    member = fleet_member("prod-writer")

    def fake_discover(*args, errors=None, **kwargs):
        if errors is not None:
            errors.append("us-west-2: error discovering instances: boom")
        yield member

    with patch("features.fleet.auth.detect_aws_credentials", return_value=(True, "ok")):
        with patch("features.fleet.discovery.discover_rds_instances", fake_discover):
            events = await collect_sse_events(
                client, "POST", "/api/fleet/discover",
                json_body={"regions": ["us-east-1", "us-west-2"], "dry_run": True},
            )

    types = [e["event"] for e in events]
    assert types.count("error") == 1
    assert "discover" in types
    complete = events[-1]["data"]
    assert complete["success"] is True


async def test_discover_without_aws_credentials(client, tmp_rdst_home, collect_sse_events):
    from unittest.mock import patch

    with patch(
        "features.fleet.auth.detect_aws_credentials",
        return_value=(False, "No AWS credentials found. Run: aws sso login"),
    ):
        events = await collect_sse_events(
            client, "POST", "/api/fleet/discover", json_body={"regions": ["us-east-1"]},
        )

    assert events[-1]["event"] == "error"
    assert "No AWS credentials" in events[-1]["data"]["message"]


async def test_discover_dry_run_does_not_persist(client, tmp_rdst_home, collect_sse_events, fleet_member):
    from unittest.mock import patch

    member = fleet_member("dry-db", host="dry.rds.amazonaws.com")
    with patch("features.fleet.auth.detect_aws_credentials", return_value=(True, "ok")):
        with patch(
            "features.fleet.discovery.discover_rds_instances",
            return_value=iter([member]),
        ):
            events = await collect_sse_events(
                client, "POST", "/api/fleet/discover",
                json_body={"regions": ["us-east-1"], "dry_run": True},
            )

    assert events[-1]["data"]["imported"] == 1
    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("dry-db") is None


async def test_discover_preview_is_non_mutating_and_bulk_add_is_selective(
    client, tmp_rdst_home, seed_target, fleet_member
):
    seed_target("existing")
    cfg = TargetsConfig()
    cfg.load()
    existing = cfg.get("existing")
    existing["host"] = "existing.rds.amazonaws.com"
    cfg.upsert("existing", existing)
    cfg.save()

    members = [
        fleet_member(
            "existing-copy", host="EXISTING.rds.amazonaws.com", group="cluster-a"
        ),
        fleet_member(
            "selected", host="selected.rds.amazonaws.com", group="cluster-a"
        ),
        fleet_member(
            "not-selected", host="unselected.rds.amazonaws.com",
            engine="mysql", port=3306, user="admin",
        ),
    ]

    with (
        patch(
            "features.fleet.auth.detect_aws_credentials",
            return_value=(True, "ok"),
        ),
        patch(
            "features.fleet.discovery.discover_rds_instances",
            return_value=iter(members),
        ),
    ):
        preview_response = await client.post(
            "/api/fleet/discover-preview",
            json={"regions": ["us-east-1"], "profile": "dev"},
        )

    assert preview_response.status_code == 200
    preview = preview_response.json()
    by_name = {member["name"]: member for member in preview["members"]}
    assert by_name["existing-copy"]["already_exists"] is True
    assert by_name["selected"]["already_exists"] is False
    assert by_name["not-selected"]["already_exists"] is False

    # Preview must not write any of the discovered targets.
    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("existing-copy") is None
    assert cfg.get("selected") is None
    assert cfg.get("not-selected") is None

    add_response = await client.post(
        "/api/fleet/targets/bulk-add",
        json={"members": [by_name["existing-copy"], by_name["selected"]]},
    )
    assert add_response.status_code == 200
    assert add_response.json() == {
        "imported": 1,
        "skipped": 1,
        "target_names": ["selected"],
    }

    cfg = TargetsConfig()
    cfg.load()
    assert cfg.get("selected")["group"] == "cluster-a"
    assert cfg.get("not-selected") is None
    assert cfg.get("existing-copy") is None


async def test_aws_status_passes_selected_profile(client, tmp_rdst_home):
    status = {
        "has_credentials": True,
        "method": "sso",
        "identity_arn": "arn:aws:sts::123456789012:assumed-role/prod/user",
        "account": "123456789012",
        "active_profile": "production sso",
        "available_profiles": ["production sso"],
        "region": "us-west-2",
    }

    with patch(
        "features.fleet.auth.get_aws_status", return_value=status
    ) as get_status:
        response = await client.get(
            "/api/fleet/aws-status?profile=production%20sso"
        )

    assert response.status_code == 200
    assert response.json()["has_credentials"] is True
    assert response.json()["active_profile"] == "production sso"
    get_status.assert_called_once_with(3.0, "production sso")


async def test_aws_status_without_credentials(client, tmp_rdst_home, monkeypatch):
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    status = {
        "has_credentials": False,
        "method": None,
        "identity_arn": None,
        "account": None,
        "active_profile": None,
        "available_profiles": ["default"],
        "region": "us-east-1",
    }

    with patch("features.fleet.auth.get_aws_status", return_value=status):
        response = await client.get("/api/fleet/aws-status")

    assert response.status_code == 200
    body = response.json()
    assert body["has_credentials"] is False
    assert body["active_profile"] is None
    assert body["available_profiles"] == ["default"]
    assert body["region"] == "us-east-1"


async def _run_mocked_fleet_audit(
    client, collect_sse_events, json_body: dict
) -> tuple[list[dict], list[dict]]:
    from features.audit.events import AuditCompleteEvent

    calls: list[dict] = []

    async def fake_audit_fleet(self, targets, **kwargs):
        calls.append({"targets": targets, **kwargs})
        yield AuditCompleteEvent(type="complete", success=True)

    with patch(
        "features.audit.service.AuditService.audit_fleet",
        fake_audit_fleet,
    ):
        events = await _start_fleet_audit(client, collect_sse_events, json_body)

    return events, calls


async def test_fleet_audit_explicit_targets_are_passed_to_service(
    client, tmp_rdst_home, collect_sse_events, seed_target
):
    seed_target("db1")
    seed_target("db2")

    events, calls = await _run_mocked_fleet_audit(
        client,
        collect_sse_events,
        {"targets": ["db2", "db1"], "insights": False, "save": False},
    )

    assert events[-1]["event"] == "complete"
    assert calls == [{
        "targets": ["db2", "db1"],
        "insights": False,
        "save_name": None,
        "duration_seconds": None,
    }]


async def test_fleet_audit_more_than_eight_targets_streams_error(
    client, tmp_rdst_home, collect_sse_events, seed_target
):
    for index in range(9):
        seed_target(f"db{index}")

    events, calls = await _run_mocked_fleet_audit(
        client, collect_sse_events, {"save": False},
    )

    assert [event["event"] for event in events] == ["error"]
    assert events[0]["data"]["message"] == (
        "Audit up to 8 targets at once — filter by group or tag "
        "(9 targets selected)"
    )
    assert events[0]["data"]["code"] == "too_many_targets"
    assert calls == []


async def test_discover_passes_profile_to_service(
    client, tmp_rdst_home, collect_sse_events
):
    from features.fleet.events import FleetImportCompleteEvent

    calls: list[dict] = []

    async def fake_discover(self, regions, **kwargs):
        calls.append({"regions": regions, **kwargs})
        yield FleetImportCompleteEvent(
            type="import_complete",
            success=True,
            imported=0,
            skipped=0,
            errors=0,
            target_names=[],
        )

    with patch("features.fleet.api.routes.FleetService.discover", fake_discover):
        events = await collect_sse_events(
            client,
            "POST",
            "/api/fleet/discover",
            json_body={
                "regions": ["us-east-1"],
                "profile": "production-sso",
                "dry_run": True,
            },
        )

    assert events[-1]["event"] == "import_complete"
    assert calls[0]["regions"] == ["us-east-1"]
    assert calls[0]["profile"] == "production-sso"
