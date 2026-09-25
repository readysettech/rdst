"""Compare against Readyset: run stages, the run list, and Docker Desktop networking."""

from __future__ import annotations

from features.cache.api import routes
from shared.deploy import docker_topology
from shared.deploy.docker_topology import DockerTopology


def _run(status, events, metadata=None):
    return {"run_id": "r1", "kind": "cache_compare", "target": "db", "status": status,
            "metadata": metadata or {"query_hash": "abc", "label": "COUNT on orders"},
            "events": [{"seq": index + 1, "event": name, "data": data, "ts": f"t{index}"}
                       for index, (name, data) in enumerate(events)]}


def test_queued_run_says_what_it_waits_for():
    run = _run("running", [("progress", {"stage": "queued", "message": "Queued", "percent": 0})])
    sandbox = {"lease_owner": "prewarm-1", "lease_purpose": "prewarm", "phase": "provisioning",
               "readiness_note": "Readyset isn't answering yet after 45 s: Connection refused"}
    summary = routes.summarize_compare_run(run, sandbox)
    assert (summary.stage, summary.stage_label) == ("queued", "Waiting for the Readyset sandbox")
    assert summary.waiting_for == ("The sandbox is busy (prewarm). Readyset isn't answering yet "
                                   "after 45 s: Connection refused")


def test_stages_map_to_real_steps_and_results_carry_speedup():
    for raw, stage in (("downloading_readyset", "image_pull"), ("waiting_for_readyset", "cache_start"),
                       ("creating_test_cache", "cache_create"), ("warming", "warm"),
                       ("benchmarking_readyset", "run")):
        summary = routes.summarize_compare_run(_run("running", [("progress", {"stage": raw})]), None)
        assert summary.stage == stage
    done = routes.summarize_compare_run(_run("done", [
        ("progress", {"stage": "benchmarking_readyset"}),
        ("cache_compare_complete", {"speedup_mean": 4.0, "winner": "readyset",
                                    "origin": {"mean_ms": 8.0}, "readyset": {"mean_ms": 2.0}})]),
        None)
    assert done.stage == "done" and done.result.speedup_label == "4.0x faster"
    assert (done.result.origin_ms, done.result.readyset_ms) == (8.0, 2.0)
    failed = routes.summarize_compare_run(_run("failed", [
        ("progress", {"stage": "waiting_for_readyset"}),
        ("error", {"message": "Readyset sandbox readiness timed out: Connection refused"})]), None)
    assert failed.stage == "failed" and "Connection refused" in failed.error


def test_docker_desktop_behind_the_default_socket_uses_bridge_networking(monkeypatch):
    monkeypatch.delenv("DOCKER_HOST", raising=False)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setattr(docker_topology, "_active_context", lambda env: "default")
    monkeypatch.setattr(docker_topology, "_daemon_is_desktop", lambda endpoint: True)
    topology = DockerTopology.from_environment()
    plan = topology.container_network_for("localhost", platform_name="linux")
    assert topology.desktop and not plan.host_network
    assert plan.upstream_host == "host.docker.internal"
    monkeypatch.setattr(docker_topology, "_daemon_is_desktop", lambda endpoint: False)
    assert DockerTopology.from_environment().container_network_for(
        "localhost", platform_name="linux").host_network


def test_run_list_orders_the_queue_across_targets(monkeypatch):
    import asyncio

    class Registry:
        def list_runs(self, kinds, target=None):
            runs = [_run("running", [("progress", {"stage": "queued"})]),
                    _run("running", [("progress", {"stage": "queued"})]),
                    _run("running", [("progress", {"stage": "warming"})])]
            for index, (run, run_target) in enumerate(zip(runs, ("other", "db", "db"))):
                run.update(run_id=f"r{index}", target=run_target)
                run["events"][0]["ts"] = f"2026-09-28T00:0{index}"
            return runs

    class Manager:
        async def diagnostics(self):
            return {"phase": "ready", "generation": 1, "queued_requests": 2,
                    "container_name": "rdst-readyset-sandbox", "healthy": True,
                    "lease_owner": "r2", "lease_purpose": "compare"}

    async def runtime():
        return {"installed": True, "running": True}

    monkeypatch.setattr(routes, "_run_registry", Registry())
    monkeypatch.setattr(routes, "_docker_runtime_status", runtime)
    monkeypatch.setattr("shared.deploy.sandbox_manager.sandbox_manager", Manager())
    page = asyncio.run(routes.list_compare_runs("db"))
    positions = {run.run_id: run.queue_position for run in page.runs}
    assert positions == {"r1": 1, "r2": None}
