"""In-process integration tests for the fleet API endpoints.

Exercises the config-only paths (targets list, CSV import) and the
status stream's error path. Live connectivity checks belong to the
realdb suite.
"""

from __future__ import annotations

from shared.config.targets import TargetsConfig


def _seed_target(name: str, group: str | None = None, tags: list[str] | None = None) -> None:
    cfg = TargetsConfig()
    cfg.load()
    entry = {
        "engine": "postgresql",
        "host": "127.0.0.1",
        "port": 5432,
        "database": "appdb",
        "user": "appuser",
        "password_env": "FLEET_PASS",
    }
    if group:
        entry["group"] = group
    if tags:
        entry["tags"] = tags
    cfg.upsert(name, entry)
    cfg.save()


async def test_targets_empty(client, tmp_rdst_home):
    response = await client.get("/api/fleet/targets")
    assert response.status_code == 200
    assert response.json() == {"members": [], "groups": [], "count": 0}


async def test_targets_list_and_group_filter(client, tmp_rdst_home):
    _seed_target("db1", group="prod", tags=["critical"])
    _seed_target("db2", group="staging")

    response = await client.get("/api/fleet/targets")
    body = response.json()
    assert body["count"] == 2
    assert sorted(body["groups"]) == ["prod", "staging"]

    response = await client.get("/api/fleet/targets?group=prod")
    body = response.json()
    assert body["count"] == 1
    assert body["members"][0]["name"] == "db1"
    assert body["members"][0]["tags"] == ["critical"]


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
    client, tmp_rdst_home, monkeypatch, collect_sse_events
):
    from unittest.mock import patch

    from features.audit.models import AuditMetrics, AuditResult

    _seed_target("db1", group="prod")
    _seed_target("db2", group="prod")
    monkeypatch.setenv("FLEET_PASS", "irrelevant")

    def fake_audit_target(self, name, target_config, on_progress=None):
        return AuditResult(
            target_name=name, engine="postgresql", host="127.0.0.1",
            metrics=AuditMetrics(max_connections=100),
            audited_at="2026-07-07T00:00:00+00:00",
        )

    with patch("features.audit.service.AuditService.audit_target", fake_audit_target):
        events = await collect_sse_events(
            client, "POST", "/api/fleet/audit",
            json_body={"group": "prod", "insights": False, "save_name": "fleet_web_test"},
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
    events = await collect_sse_events(
        client, "POST", "/api/fleet/audit", json_body={"group": "nope"},
    )
    assert events[-1]["event"] == "error"
    assert "No targets found" in events[-1]["data"]["message"]


async def test_discover_imports_and_dedupes(client, tmp_rdst_home, collect_sse_events):
    from unittest.mock import patch

    from features.fleet.models import FleetMember

    # An existing target with the same host as one discovered instance.
    _seed_target("existing-db")
    cfg = TargetsConfig()
    cfg.load()
    cfg.upsert("existing-db", {**cfg.get("existing-db"), "host": "old.abc.us-east-1.rds.amazonaws.com"})
    cfg.save()

    members = [
        FleetMember(
            name="prod-writer", engine="postgresql",
            host="prod.abc.us-east-1.rds.amazonaws.com", port=5432,
            database="app", user="postgres", password_env="FLEET_PASS",
            tags=["writer"], instance_class="db.r6g.large", region="us-east-1",
        ),
        FleetMember(
            name="old-db", engine="mysql",
            host="OLD.abc.us-east-1.rds.amazonaws.com", port=3306,
            database="app", user="admin", password_env="FLEET_PASS",
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
    client, tmp_rdst_home, collect_sse_events
):
    from unittest.mock import patch

    from features.fleet.models import FleetMember

    member = FleetMember(
        name="prod-writer", engine="postgresql",
        host="prod.abc.us-east-1.rds.amazonaws.com", port=5432,
        database="app", user="postgres", password_env="FLEET_PASS",
    )

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


async def test_discover_dry_run_does_not_persist(client, tmp_rdst_home, collect_sse_events):
    from unittest.mock import patch

    from features.fleet.models import FleetMember

    member = FleetMember(
        name="dry-db", engine="postgresql", host="dry.rds.amazonaws.com",
        port=5432, database="app", user="postgres", password_env="FLEET_PASS",
    )
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
