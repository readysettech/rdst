import subprocess
import threading
from unittest import mock

import pytest

from features.qpdemo import service as service_mod
from features.qpdemo.service import DemoService, NAMES


class OneLoopStop:
    def __init__(self):
        self.calls = 0

    def wait(self, _seconds):
        self.calls += 1
        return self.calls > 1


def _bare_service(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "DEMO_DIR", tmp_path)
    monkeypatch.setattr(service_mod, "HISTORY_PATH", tmp_path / "history.json")
    svc = DemoService.__new__(DemoService)
    svc._ports = object()
    svc._qp_enabled = False
    svc._health = {n: "running" for n in NAMES}
    svc._supervisor_failures = {n: 0 for n in NAMES}
    svc._supervisor_stop = OneLoopStop()
    svc._history_lock = threading.Lock()
    svc._history = []
    svc._events = []
    svc._last_error = None
    svc._clock = service_mod._now_s
    svc._auto_teardown_at = None
    svc._lifecycle_lock = threading.Lock()
    svc._pending_notice = None
    svc._demo_completed_fired = False
    return svc


def _record_demo_events(svc, monkeypatch):
    """Capture funnel telemetry events fired by the service without touching
    PostHog. Returns the list of (name, properties) tuples."""
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        service_mod.telemetry, "track",
        lambda name, props=None: events.append((name, props or {})),
    )
    return events


def test_history_prunes_in_memory_without_persisting(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    now = service_mod._now_s()
    svc._history = [
        {"t": now - service_mod.HISTORY_WINDOW_S - 1, "direct": {}, "router": {}},
        {"t": now, "direct": {"qps": 1}, "router": {"qps": 2}},
    ]
    svc._events = [
        {"t": now - service_mod.HISTORY_WINDOW_S - 1, "type": "qp_on", "label": "old"},
        {"t": now, "type": "qp_off", "label": "new"},
    ]

    out = svc.load_history()

    assert len(out["samples"]) == 1
    assert len(out["events"]) == 1
    # History lives in memory only: nothing is written under ~/.rdst/demo.
    assert not (tmp_path / "history.json").exists()


def test_status_on_empty_state_reports_unprovisioned(tmp_path, monkeypatch):
    monkeypatch.setattr(service_mod, "DEMO_DIR", tmp_path)
    monkeypatch.setattr(service_mod, "HISTORY_PATH", tmp_path / "history.json")
    svc = DemoService.__new__(DemoService)
    svc._ports = None
    svc._qp = None
    svc._qp_enabled = False
    svc._qp_paused = True
    svc._running = False
    svc._workers = 8
    svc._discovery_mode = "count_star"
    svc._cache_budget = 10
    svc._qp_cron_fast = True
    svc._qp_cron_verified = False
    svc._qp_last_tick = None
    svc._last_error = None
    svc._auto_teardown_at = None
    svc._pending_notice = None
    svc._health = {n: "failed" for n in NAMES}
    svc._supervisor_failures = {n: 0 for n in NAMES}
    svc._container_state = lambda name: "absent"

    out = svc.status()

    assert out["provisioned"] is False
    assert out["ports"] is None
    assert out["auto_teardown_at"] is None
    # With nothing provisioned, health must not force qp-cron to "running"
    # (that forced value made the frontend skip the idle Start card), and any
    # stale "failed" entries must surface as "absent" so the UI never toasts
    # a container failure for a demo that does not exist.
    assert all(state == "absent" for state in out["health"].values())


def test_enabling_querypilot_resets_to_opening_policy(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    svc._qp = None
    svc._qp_paused = True
    svc._ports = type("Ports", (), {"admin": 6035, "readyset": 5433})()
    svc._discovery_mode = "count_star"
    svc._cache_budget = 40
    written = []
    monkeypatch.setattr(
        service_mod.qdeploy, "write_qp_config",
        lambda *a, **k: written.append(a),
    )
    svc._start_qp_cron = lambda: None
    svc._reset_comparison_window = lambda: None

    svc.set_querypilot(True)

    # Enabling always reopens on the most-frequent policy: count_star, top 20.
    assert svc._discovery_mode == "count_star"
    assert svc._cache_budget == service_mod.MODE_BUDGETS["count_star"]
    assert written and written[-1][-2:] == ("count_star", service_mod.MODE_BUDGETS["count_star"])


def test_enabling_querypilot_drops_all_caches_including_manual(tmp_path, monkeypatch):
    # QueryPilot is command-and-control: turning it on clears every cache first,
    # including the ones the visitor made by hand, so it owns caching from there.
    svc = _bare_service(tmp_path, monkeypatch)
    svc._qp_paused = True
    svc._ports = type("Ports", (), {"admin": 6035, "readyset": 5433})()
    reset_calls = []

    class FakeQP:
        def reset_querypilot_caches(self, include_manual=False):
            reset_calls.append(include_manual)
            return {"dropped_rules": 0, "dropped_caches": []}

    svc._qp = FakeQP()
    monkeypatch.setattr(service_mod.qdeploy, "write_qp_config", lambda *a, **k: None)
    svc._start_qp_cron = lambda: None
    svc._reset_comparison_window = lambda: None

    svc.set_querypilot(True)

    assert reset_calls == [True]


def test_disabling_querypilot_flips_flag_before_stopping_cron(tmp_path, monkeypatch):
    # The supervisor keeps qp-cron alive while QueryPilot is enabled, and dropping
    # caches takes a moment. So the enabled flag must go False BEFORE qp-cron is
    # stopped -- otherwise the supervisor revives it mid-drop and it re-caches
    # everything (the caches drop, then instantly reappear).
    svc = _bare_service(tmp_path, monkeypatch)
    svc._qp_enabled = True
    svc._qp_paused = False
    svc._ports = type("Ports", (), {"admin": 6035, "readyset": 5433})()

    class FakeQP:
        def reset_querypilot_caches(self, include_manual=False):
            return {"dropped_rules": 0, "dropped_caches": []}

    svc._qp = FakeQP()
    svc._save_state = lambda: None
    svc._reset_comparison_window = lambda: None
    flag_when_stopped = []
    svc._stop_container = lambda name: flag_when_stopped.append(svc._qp_enabled)

    svc.set_querypilot(False)

    assert flag_when_stopped == [False], "qp-cron stopped while still 'enabled': supervisor would revive it"


def test_supervisor_respects_querypilot_off(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    calls = []
    states = {name: "running" for name in NAMES.values()}
    states[NAMES["qp-cron"]] = "exited"

    svc._container_state = lambda name: states[name]
    svc._docker = lambda args, timeout=30: calls.append(args) or subprocess.CompletedProcess(args, 0)

    svc._supervisor_loop()

    assert svc._health["qp-cron"] == "running"
    assert not [args for args in calls if args[-1] == NAMES["qp-cron"]]


def test_supervisor_marks_failed_after_three_restart_failures(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    svc._qp_enabled = True
    svc._supervisor_failures["pg"] = 2
    states = {name: "running" for name in NAMES.values()}
    states[NAMES["pg"]] = "exited"

    svc._container_state = lambda name: states[name]

    def fail_start(args, timeout=30):
        return subprocess.CompletedProcess(args, 1, stderr="cannot restart")

    svc._docker = fail_start

    svc._supervisor_loop()

    assert svc._health["pg"] == "failed"
    assert svc._last_error and "pg failed to restart" in svc._last_error
    assert svc._events[-1]["type"] == "error"


# ---- one-hour auto-teardown (injectable clock, no sleeping) -----------------
def test_auto_teardown_not_due_before_deadline(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    fake_now = [1000.0]
    svc._clock = lambda: fake_now[0]
    svc._auto_teardown_at = 1000.0 + service_mod.AUTO_TEARDOWN_S - 1
    assert svc._auto_teardown_due() is False


def test_auto_teardown_fires_and_records_notice(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    fake_now = [1000.0]
    svc._clock = lambda: fake_now[0]
    svc._auto_teardown_at = 1000.0 + service_mod.AUTO_TEARDOWN_S

    torn = []
    svc.teardown = lambda reason="user": torn.append(reason) or {"success": True}

    # Advance the injectable clock past the deadline; the supervisor detects it.
    fake_now[0] = 1000.0 + service_mod.AUTO_TEARDOWN_S + 1
    assert svc._auto_teardown_due() is True

    # Run the teardown inline (no background thread) to assert its effects.
    svc._auto_teardown_at = None
    svc._run_auto_teardown()

    assert torn == ["auto"]
    assert svc._pending_notice == "Demo environment auto-cleaned after 1 hour."
    assert svc._events[-1]["label"] == "Demo environment auto-cleaned after 1 hour."
    assert svc._events[-1]["type"] == "auto_teardown"


def test_supervisor_triggers_auto_teardown_when_due(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    svc._clock = lambda: 10_000.0
    svc._auto_teardown_at = 9_000.0
    triggered = []
    svc._trigger_auto_teardown = lambda: triggered.append(True)

    svc._supervisor_loop()

    assert triggered == [True]


# ---- state consolidation isolation ------------------------------------------
def test_demo_touches_only_the_demo_config_section(tmp_path, monkeypatch):
    """Provisioned save + teardown clear must not read or write any rdst feature
    besides the [demo] section (targets, llm, emails, etc. stay untouched)."""
    from shared.config.targets import TargetsConfig

    cfg_path = tmp_path / "config.toml"
    seed = TargetsConfig(str(cfg_path))
    seed.load()
    seed.upsert("prod", {"host": "db.example.com", "engine": "postgresql"})
    seed.set_llm_provider("anthropic")
    seed.set_default("prod")
    seed.save()
    before = cfg_path.read_text()

    monkeypatch.setattr(
        service_mod.DemoService, "_demo_config",
        staticmethod(lambda: _loaded(cfg_path)),
    )

    svc = DemoService.__new__(DemoService)
    svc._ports = service_mod.qdeploy.Ports(
        pg=5432, readyset=5433, readyset_metrics=6034, sqp=6432, metrics=9090,
    )
    svc._discovery_mode = "sum_time"
    svc._cache_budget = 12
    svc._qp_cron_fast = True
    svc._qp_cron_verified = False
    svc._auto_teardown_at = 1234.5

    svc._save_state()

    saved = TargetsConfig(str(cfg_path)); saved.load()
    assert saved.get("prod") == {"host": "db.example.com", "engine": "postgresql"}
    assert saved.get_default() == "prod"
    assert saved.get_llm_provider() == "anthropic"
    demo = saved.get_demo_state()
    assert demo["discovery_mode"] == "sum_time"
    assert demo["cache_budget"] == 12
    assert demo["auto_teardown_at"] == 1234.5
    assert demo["ports"]["pg"] == 5432

    svc._delete_state()
    cleared = TargetsConfig(str(cfg_path)); cleared.load()
    assert cleared.get_demo_state() == {}
    # Every non-demo section survives the demo's write+clear cycle.
    assert cleared.get("prod") == {"host": "db.example.com", "engine": "postgresql"}
    assert cleared.get_default() == "prod"
    assert cleared.get_llm_provider() == "anthropic"
    assert before == _without_demo(cfg_path)


def _loaded(path):
    from shared.config.targets import TargetsConfig

    cfg = TargetsConfig(str(path))
    cfg.load()
    return cfg


def _without_demo(path):
    import toml

    data = toml.load(path)
    data.pop("demo", None)
    return toml.dumps(data)


def test_migration_absorbs_legacy_state_json(tmp_path, monkeypatch):
    """A legacy ~/.rdst/qpdemo/state.json is folded into [demo] and removed,
    along with the stale history.json and the ~/.rdst/demo directory."""
    import json as _json
    from shared.config.targets import TargetsConfig

    workdir = tmp_path / "qpdemo"
    workdir.mkdir()
    state_path = workdir / "state.json"
    demo_dir = tmp_path / "demo"
    demo_dir.mkdir()
    history_path = demo_dir / "history.json"
    history_path.write_text("{}")
    state_path.write_text(_json.dumps({
        "ports": {"pg": 5440, "readyset": 5441, "readyset_metrics": 6034,
                  "sqp": 6440, "metrics": 9099},
        "discovery_mode": "sum_time",
        "cache_budget": 7,
        "qp_cron_fast": False,
        "qp_cron_verified": True,
    }))

    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr(service_mod, "STATE_PATH", state_path)
    monkeypatch.setattr(service_mod, "DEMO_DIR", demo_dir)
    monkeypatch.setattr(service_mod, "HISTORY_PATH", history_path)
    monkeypatch.setattr(
        service_mod.DemoService, "_demo_config",
        staticmethod(lambda: _loaded(cfg_path)),
    )

    svc = DemoService.__new__(DemoService)
    svc._auto_teardown_at = None
    ports = svc._load_state()

    assert ports is not None and ports.pg == 5440 and ports.sqp == 6440
    assert svc._discovery_mode == "sum_time"
    assert svc._cache_budget == 7
    assert svc._qp_cron_fast is False and svc._qp_cron_verified is True

    saved = TargetsConfig(str(cfg_path)); saved.load()
    assert saved.get_demo_state()["cache_budget"] == 7
    assert not state_path.exists()
    assert not history_path.exists()
    assert not demo_dir.exists()


# ---- explicit image pull (stubbed docker runner) ----------------------------
class StubDocker:
    """Records docker invocations; `present` marks which images `inspect` finds."""

    def __init__(self, present):
        self.present = set(present)
        self.calls = []

    def __call__(self, cmd, timeout=None):
        self.calls.append(cmd)
        verb = cmd[1]
        if verb == "image" and cmd[2] == "inspect":
            rc = 0 if cmd[3] in self.present else 1
            return subprocess.CompletedProcess(cmd, rc)
        if verb == "pull":
            return subprocess.CompletedProcess(cmd, 0)
        return subprocess.CompletedProcess(cmd, 0)

    def pulled(self):
        return [c[2] for c in self.calls if c[1] == "pull"]

    def inspected(self):
        return [c[3] for c in self.calls if c[1] == "image" and c[2] == "inspect"]


def _bare_pull_service():
    svc = DemoService.__new__(DemoService)
    return svc


def test_pull_only_missing_images(monkeypatch):
    images = service_mod.qdeploy.PINNED_IMAGES
    missing = images["readyset"]
    present = [img for img in images.values() if img != missing]
    stub = StubDocker(present)

    svc = _bare_pull_service()
    events = list(svc._pull_missing_images(runner=stub))

    # Every image is inspected exactly once; only the missing one is pulled.
    assert sorted(stub.inspected()) == sorted(images.values())
    assert stub.pulled() == [missing]
    messages = [e["message"] for e in events]
    assert any(f"Pulling {missing}" in m for m in messages)
    assert any(f"Pulled {missing}" in m for m in messages)


def test_pull_skipped_when_all_present(monkeypatch):
    images = service_mod.qdeploy.PINNED_IMAGES
    stub = StubDocker(list(images.values()))

    svc = _bare_pull_service()
    events = list(svc._pull_missing_images(runner=stub))

    assert stub.pulled() == []
    assert sorted(stub.inspected()) == sorted(images.values())
    assert all(e["type"] == "progress" for e in events)


def test_pull_failure_raises(monkeypatch):
    images = service_mod.qdeploy.PINNED_IMAGES

    class FailingPull(StubDocker):
        def __call__(self, cmd, timeout=None):
            if cmd[1] == "pull":
                self.calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 1, stderr="network down")
            return super().__call__(cmd, timeout)

    stub = FailingPull([])  # nothing present -> first image pull fails
    svc = _bare_pull_service()
    with pytest.raises(RuntimeError, match="Failed to pull"):
        list(svc._pull_missing_images(runner=stub))


def test_preflight_all_green(monkeypatch):
    svc = _bare_pull_service()
    svc.docker_installed = lambda: True
    svc.docker_ok = lambda: True
    monkeypatch.setattr(
        service_mod.qdeploy, "image_present", lambda image, runner=None: True,
    )
    out = svc.preflight()
    assert out["docker_installed"] is True
    assert out["docker_running"] is True
    assert out["images_present"] is True
    assert out["missing_images"] == []
    assert out["download_mb"] == 0
    assert out["disk_required_gb"] == service_mod.DISK_REQUIRED_GB
    assert isinstance(out["disk_space_ok"], bool)


def test_preflight_docker_installed_but_down_marks_all_images_missing():
    svc = _bare_pull_service()
    svc.docker_installed = lambda: True
    svc.docker_ok = lambda: False
    out = svc.preflight()
    assert out["docker_installed"] is True
    assert out["docker_running"] is False
    assert out["images_present"] is False
    assert out["download_mb"] == service_mod.IMAGE_DOWNLOAD_MB
    assert sorted(out["missing_images"]) == sorted(
        service_mod.qdeploy.PINNED_IMAGES.values()
    )


def test_preflight_docker_not_installed():
    # Not-installed is distinct from installed-but-down: docker_running is forced
    # False without even probing the daemon, so the UI can say "install Docker".
    svc = _bare_pull_service()
    svc.docker_installed = lambda: False
    out = svc.preflight()
    assert out["docker_installed"] is False
    assert out["docker_running"] is False
    assert out["images_present"] is False


def test_preflight_partial_images_scale_download_estimate(monkeypatch):
    svc = _bare_pull_service()
    svc.docker_installed = lambda: True
    svc.docker_ok = lambda: True
    missing = service_mod.qdeploy.PINNED_IMAGES["readyset"]
    monkeypatch.setattr(
        service_mod.qdeploy, "image_present",
        lambda image, runner=None: image != missing,
    )
    out = svc.preflight()
    assert out["missing_images"] == [missing]
    total = len(service_mod.qdeploy.PINNED_IMAGES)
    assert out["download_mb"] == int(round(service_mod.IMAGE_DOWNLOAD_MB / total))


# ---- frozen funnel telemetry events -----------------------------------------
def _stub_successful_provision(svc, monkeypatch):
    """Stub every docker/network touchpoint so provision() runs its success
    path synchronously."""
    import subprocess as sp

    ok = sp.CompletedProcess([], 0, stdout="", stderr="")

    def _gen_true(*_a, **_k):
        return True
        yield  # pragma: no cover - generator shape

    svc.docker_ok = lambda: True
    svc._stop_supervisor = lambda: None
    svc._force_clean = lambda: None
    svc._pull_missing_images = lambda: iter(())
    svc._make_qprouter = lambda: object()
    svc._save_state = lambda: None
    svc._wait_readyset_events = _gen_true
    svc._wait_sqp_events = _gen_true
    svc._warmup_paths = lambda passes=1: {"queries": 0, "executions": 0}
    svc._container_state = lambda name: "created"
    svc._ensure_supervisor = lambda: None
    qd = service_mod.qdeploy
    monkeypatch.setattr(qd, "allocate_ports", lambda exclude=None: qd.Ports(
        pg=5432, readyset=5433, readyset_metrics=6034, sqp=6432, metrics=9090))
    monkeypatch.setattr(qd, "write_sqp_config", lambda *a, **k: (None, None))
    monkeypatch.setattr(qd, "write_qp_config", lambda *a, **k: None)
    for fn in ("deploy_postgres_baked", "deploy_readyset", "deploy_sqp", "deploy_qp_cron"):
        monkeypatch.setattr(qd, fn, lambda *a, **k: ok)
    svc._wait_pg_connect = lambda timeout: True


def test_concurrent_provision_is_rejected(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    _stub_successful_provision(svc, monkeypatch)
    _record_demo_events(svc, monkeypatch)

    gen1 = svc.provision()
    next(gen1)  # in flight: holds the lifecycle lock

    second = list(svc.provision())
    assert second[-1]["type"] == "error"
    assert "already in progress" in second[-1]["message"]

    # Abandoning the in-flight stream (client disconnect) releases the lock,
    # so a fresh provision goes through.
    gen1.close()
    out = list(svc.provision())
    assert out[-1]["type"] == "complete"


def test_first_demo_fires_once_across_provisions(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    _stub_successful_provision(svc, monkeypatch)
    events = _record_demo_events(svc, monkeypatch)
    # Real first-event gate semantics over an in-memory stats dict.
    stats: dict = {}
    monkeypatch.setattr(service_mod.telemetry, "_get_stats", lambda: stats)
    monkeypatch.setattr(
        service_mod.telemetry, "_increment_stat",
        lambda flag, n=1: stats.__setitem__(flag, stats.get(flag, 0) + n),
    )

    for _ in range(2):
        out = list(svc.provision())
        assert out[-1]["type"] == "complete", out[-1]

    provisioned = [n for n, _ in events if n == "demo_provisioned"]
    first = [(n, p) for n, p in events if n == "first_demo"]
    assert len(provisioned) == 2
    assert len(first) == 1
    assert first[0][1]["display_name"] == "RDST First Demo"
    assert first[0][1]["source"] == "web"
    assert stats["first_demo_fired"] == 1



def _teardownable_service(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    svc._ports = object()
    svc._qp = None
    svc._qp_paused = True
    svc.stop_load = lambda: None
    svc._stop_supervisor = lambda: None
    svc._force_clean = lambda: None
    svc._remove_demo_images = lambda: None
    svc._delete_state = lambda: None
    return svc


def test_teardown_fires_demo_torn_down_for_user(tmp_path, monkeypatch):
    svc = _teardownable_service(tmp_path, monkeypatch)
    events = _record_demo_events(svc, monkeypatch)

    svc.teardown()

    names = [n for n, _ in events]
    assert "demo_torn_down" in names
    assert "demo_torn_down_auto" not in names
    props = dict(events)["demo_torn_down"]
    assert props["reason"] == "user"
    assert props["source"] == "web"
    assert props["display_name"] == "RDST Demo Torn Down"


def test_teardown_fires_demo_torn_down_auto_for_auto(tmp_path, monkeypatch):
    svc = _teardownable_service(tmp_path, monkeypatch)
    events = _record_demo_events(svc, monkeypatch)

    svc.teardown(reason="auto")

    names = [n for n, _ in events]
    assert "demo_torn_down_auto" in names
    assert "demo_torn_down" not in names
    assert dict(events)["demo_torn_down_auto"]["reason"] == "auto"


def test_teardown_survives_image_removal_failure(tmp_path, monkeypatch):
    """Image cleanup is best-effort: a Docker that is gone or an rmi that raises
    must not fail the teardown or suppress the torn-down funnel event. Removing
    containers and state is the guarantee; reclaiming images is a bonus."""
    svc = _teardownable_service(tmp_path, monkeypatch)
    # Exercise the real _remove_demo_images against a _docker that always raises,
    # standing in for a unit agent with no Docker binary.
    del svc._remove_demo_images

    def boom(args, timeout=30):
        raise FileNotFoundError("docker: command not found")

    svc._docker = boom
    events = _record_demo_events(svc, monkeypatch)

    result = svc.teardown()

    assert result["success"] is True
    assert svc._ports is None
    assert "demo_torn_down" in [n for n, _ in events]


def test_teardown_on_idle_service_fires_nothing(tmp_path, monkeypatch):
    svc = _teardownable_service(tmp_path, monkeypatch)
    svc._ports = None  # never provisioned
    events = _record_demo_events(svc, monkeypatch)

    svc.teardown()

    assert events == []


def test_enabling_querypilot_fires_event(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    svc._qp = None
    svc._qp_paused = True
    svc._ports = type("Ports", (), {"admin": 6035, "readyset": 5433})()
    svc._discovery_mode = "count_star"
    svc._cache_budget = 40
    monkeypatch.setattr(service_mod.qdeploy, "write_qp_config", lambda *a, **k: None)
    svc._start_qp_cron = lambda: None
    svc._reset_comparison_window = lambda: None
    events = _record_demo_events(svc, monkeypatch)

    svc.set_querypilot(True)

    qp_events = [p for n, p in events if n == "querypilot_enabled"]
    assert len(qp_events) == 1
    # Enabling always reopens on the most-frequent policy; the event carries it.
    assert qp_events[0]["mode"] == "count_star"
    assert qp_events[0]["display_name"] == "RDST QueryPilot Enabled"


def test_demo_completed_fires_once_on_count_star_frequent_cache(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    svc._discovery_mode = "count_star"
    svc._running = True
    events = _record_demo_events(svc, monkeypatch)

    rows = [
        {"status": "pass_through", "group": "cheap_point_lookup"},
        {"status": "cached_querypilot", "group": "expensive_aggregate"},
    ]
    svc._maybe_fire_demo_completed(rows)
    svc._maybe_fire_demo_completed(rows)  # idempotent

    completed = [p for n, p in events if n == "demo_completed"]
    assert len(completed) == 1
    assert completed[0]["discovery_mode"] == "count_star"


def test_demo_completed_silent_when_not_running_or_wrong_mode(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    events = _record_demo_events(svc, monkeypatch)
    rows = [{"status": "cached_querypilot", "group": "cheap_point_lookup"}]

    # Wrong policy (still on the opening most-expensive beat): silent.
    svc._discovery_mode = "sum_time"
    svc._running = True
    svc._maybe_fire_demo_completed(rows)

    # Right policy but load stopped: silent.
    svc._discovery_mode = "count_star"
    svc._running = False
    svc._maybe_fire_demo_completed(rows)

    assert [n for n, _ in events if n == "demo_completed"] == []


# ---- docker resilience -------------------------------------------------------
def test_provision_preflight_reports_docker_down(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    svc.docker_ok = lambda: False

    events = list(svc.provision())

    assert events and events[-1]["type"] == "error"
    assert "docker" in events[-1]["message"].lower()


def test_provision_surfaces_port_exhaustion(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    svc._ports = None
    forced = []
    svc.docker_ok = lambda: True
    svc._stop_supervisor = lambda: None
    svc._force_clean = lambda: forced.append(True)
    svc._pull_missing_images = lambda: iter(())
    monkeypatch.setattr(
        service_mod.qdeploy, "allocate_ports",
        lambda exclude=None: (_ for _ in ()).throw(RuntimeError("No free TCP port available")),
    )

    events = list(svc.provision())

    # Stale containers are force-cleaned before we try to bind ports, and the
    # exhaustion is surfaced as a clean error event (not an unhandled crash).
    assert forced == [True]
    assert events[-1]["type"] == "error"
    assert "no free tcp port" in events[-1]["message"].lower()
    assert svc._last_error and "No free TCP port" in svc._last_error


def test_provision_surfaces_image_pull_failure(tmp_path, monkeypatch):
    svc = _bare_service(tmp_path, monkeypatch)
    svc._ports = None
    svc.docker_ok = lambda: True
    svc._stop_supervisor = lambda: None
    svc._force_clean = lambda: None

    def boom():
        raise RuntimeError("Failed to pull readyset/readyset: network down")
        yield  # pragma: no cover - generator shape

    svc._pull_missing_images = boom

    events = list(svc.provision())

    assert events[-1]["type"] == "error"
    assert "failed to pull" in events[-1]["message"].lower()


def test_next_free_raises_when_port_space_exhausted(monkeypatch):
    monkeypatch.setattr(service_mod.qdeploy, "_port_free", lambda port: False)
    with pytest.raises(RuntimeError, match="No free TCP port"):
        service_mod.qdeploy._next_free(5432, set())


def test_partial_provision_is_teardownable(tmp_path, monkeypatch):
    """A provision that failed with some containers up must still tear down
    cleanly and reset state so the user is never stuck."""
    svc = _teardownable_service(tmp_path, monkeypatch)
    cleaned = []
    svc._force_clean = lambda: cleaned.append(True)
    _record_demo_events(svc, monkeypatch)

    result = svc.teardown()

    assert result["success"] is True
    assert cleaned == [True]
    assert svc._ports is None
    assert all(state == "absent" for state in svc._health.values())


def test_status_reconciles_stale_provisioned_state(tmp_path, monkeypatch):
    """If containers vanish out-of-band (manual docker rm, crashed session),
    status() must clear the stale state and report unprovisioned instead of a
    broken ready view."""
    svc = _bare_service(tmp_path, monkeypatch)
    svc._qp = None
    svc._qp_paused = True
    svc._running = False
    svc._workers = 8
    svc._discovery_mode = "sum_time"
    svc._cache_budget = 10
    svc._qp_cron_fast = True
    svc._qp_cron_verified = False
    svc._qp_last_tick = None
    svc._container_state = lambda name: "absent"
    svc._delete_state = lambda: None
    svc._stop_supervisor = lambda: None
    svc._provisioning = False

    out = svc.status()

    assert out["provisioned"] is False
    assert svc._ports is None
    assert all(state == "absent" for state in out["health"].values())


def test_resolve_cache_target_prefers_workload_key_over_hex(tmp_path, monkeypatch):
    """Keys like C01 also parse as hex; the workload key must win so the UI's
    cache-by-key path never misreads a key as a fingerprint hash."""
    svc = _bare_service(tmp_path, monkeypatch)

    class Q:
        sql = "SELECT id FROM products WHERE id = 1"
        title = "Product tile by id"

    class Row:
        sql = "SELECT id FROM products WHERE id = 2"
        fingerprint = 12345

    svc._catalog_by_key = {"C01": Q()}
    svc._raw_patterns = lambda: [Row()]

    fp, label = svc._resolve_cache_target("C01")
    assert fp == "12345"
    assert label == "Product tile by id"


def test_baked_image_is_pinned_and_pulled(monkeypatch):
    """The pre-baked Orders image is a pinned image (public, anonymous pull):
    the normal pull loop fetches it when missing. There is no seed fallback."""
    monkeypatch.delenv("QPDEMO_FORCE_SEED", raising=False)
    assert service_mod.qdeploy.PINNED_IMAGES["pg"] == service_mod.qdeploy.BAKED_PG_IMAGE
    assert service_mod.qdeploy.BAKED_PG_IMAGE.startswith("public.ecr.aws/")
    stub = StubDocker([])  # nothing cached -> everything, incl. baked pg, is pulled
    svc = _bare_pull_service()
    list(svc._pull_missing_images(runner=stub))
    assert service_mod.qdeploy.BAKED_PG_IMAGE in stub.pulled()


def test_patterns_feeds_open_loop_keys_from_readyset_caches(tmp_path, monkeypatch):
    """The open-loop feed keys off ReadySet cache existence (data plane), not
    SQP rule state: a rule that churns away for one poll must not re-pace a
    query the cache is still serving, and a rule without a live cache must not
    surge a query that is really passing through."""
    svc = _bare_service(tmp_path, monkeypatch)

    class RuleGoneCacheServing:
        sql = "SELECT 1"
        fingerprint_hex = "0xabc"
        cached = False
        has_cache = True
        owner = None
        decision = {"reason": "selected"}
        log_reason = None
        alt_rank = None
        alt_metric = None

    class RuleUpCacheMissing:
        sql = "SELECT 2"
        fingerprint_hex = "0xdef"
        cached = True
        has_cache = False
        owner = "querypilot"
        decision = {"reason": "selected"}
        log_reason = None
        alt_rank = None
        alt_metric = None

    class Driver:
        def __init__(self):
            self.keys = None

        def query_stats(self):
            return {"direct": {}, "router": {}}

        def set_cached_keys(self, keys):
            self.keys = set(keys)

    svc._driver = Driver()
    svc._raw_patterns = lambda: [RuleGoneCacheServing(), RuleUpCacheMissing()]
    svc._catalog = {}
    svc._catalog_by_shape = {}
    svc._catalog_by_key = {}
    svc._discovery_mode = "sum_time"

    rows = svc.patterns()

    assert svc._driver.keys == {"0xabc"}
    by_key = {r["key"]: r for r in rows}
    # UI status still reflects the policy plane (rules) independently.
    assert by_key["0xabc"]["status"] == "pass_through"
    assert by_key["0xdef"]["status"] == "cached_querypilot"
    assert by_key["0xabc"]["has_cache"] is True
    assert by_key["0xdef"]["has_cache"] is False


def test_patterns_preseeds_full_catalog_for_stable_rows(tmp_path, monkeypatch):
    """The pattern row SET is constant from the first poll: every active
    workload query appears immediately (zero stats until traffic reaches
    it), so table rows update in place and never pop in mid-demo."""
    from features.qpdemo.workload import WORKLOAD

    svc = _bare_service(tmp_path, monkeypatch)
    svc._driver = None
    svc._raw_patterns = lambda: []
    svc._catalog = {}
    svc._catalog_by_shape = {}
    svc._catalog_by_key = {}
    svc._discovery_mode = "sum_time"

    rows = svc.patterns()

    active = {q.id for q in WORKLOAD if q.weight > 0}
    inactive = {q.id for q in WORKLOAD if q.weight <= 0}
    keys = {r["key"] for r in rows}
    assert keys == active
    assert not keys & inactive
    stub = rows[0]
    assert stub["hits"] == 0
    assert stub["has_cache"] is False
    assert stub["status"] == "not_eligible"
    assert stub["reason"]["kind"] == "below_min_execution"


def test_provision_rescales_container_percents_after_pull(tmp_path, monkeypatch):
    """When images actually download, the pull stage owns the front of the
    bar and container milestones rescale into the remainder; with cached
    images the authored 5..100 milestones pass through untouched."""
    def events():
        return iter([
            {"type": "progress", "stage": "images", "percent": 2,
             "message": "Pulling img (1/1, ~630 MB)..."},
            {"type": "progress", "stage": "images", "percent": 60,
             "message": "Pulled img"},
            {"type": "progress", "stage": "start", "percent": 5, "message": "go"},
            {"type": "container", "name": "pg", "percent": 30, "state": "ready"},
            {"type": "complete", "percent": 100},
        ])

    svc = _bare_service(tmp_path, monkeypatch)
    svc._provision_locked = events
    out = list(svc.provision())
    assert [e["percent"] for e in out] == [2, 60, 60, 71, 100]

    def cached_events():
        return iter([
            {"type": "progress", "stage": "images", "percent": 3,
             "message": "All container images already cached"},
            {"type": "progress", "stage": "start", "percent": 5, "message": "go"},
            {"type": "container", "name": "pg", "percent": 30, "state": "ready"},
            {"type": "complete", "percent": 100},
        ])

    svc._provision_locked = cached_events
    out = list(svc.provision())
    assert [e["percent"] for e in out] == [3, 5, 30, 100]


def test_provision_retries_on_port_conflict(tmp_path, monkeypatch):
    """The port probe can race another process (and some engines leak
    bindings): a container start that fails with a port-conflict signature
    re-allocates above the burned ports and retries instead of failing the
    provision."""
    svc = _bare_service(tmp_path, monkeypatch)
    svc._ports = None
    svc.docker_ok = lambda: True
    svc._stop_supervisor = lambda: None
    svc._force_clean = lambda: None
    svc._pull_missing_images = lambda: iter(())
    svc._make_qprouter = lambda: None
    svc._save_state = lambda: None
    svc._wait_pg_connect = lambda timeout: True
    svc._warmup_paths = lambda passes: {"queries": 0}
    svc._ensure_supervisor = lambda: None
    svc._track_demo_event = lambda *a, **kw: None

    def _ok_wait(timeout):
        return True
        yield  # unreachable; makes this a generator like the real waiter

    svc._wait_readyset_events = _ok_wait
    svc._wait_sqp_events = _ok_wait

    allocations = []

    def fake_allocate(exclude=None):
        base = 5432 + 10 * len(allocations)
        ports = service_mod.qdeploy.Ports(
            pg=base, readyset=base + 1, readyset_metrics=base + 2,
            sqp=base + 3, metrics=base + 4,
        )
        allocations.append((set(exclude or ()), ports))
        return ports

    ok = mock.Mock(returncode=0, stderr="")
    conflict = mock.Mock(
        returncode=1,
        stderr="driver failed programming external connectivity on endpoint qpdemo-pg",
    )
    pg_results = [conflict, ok]

    monkeypatch.setattr(service_mod.qdeploy, "allocate_ports", fake_allocate)
    monkeypatch.setattr(service_mod.qdeploy, "write_sqp_config",
                        lambda *a, **kw: (tmp_path / "sqp.toml", tmp_path / "deny"))
    monkeypatch.setattr(service_mod.qdeploy, "write_qp_config", lambda *a, **kw: None)
    monkeypatch.setattr(service_mod.qdeploy, "deploy_postgres_baked",
                        lambda name, port: pg_results.pop(0))
    monkeypatch.setattr(service_mod.qdeploy, "deploy_readyset", lambda *a, **kw: ok)
    monkeypatch.setattr(service_mod.qdeploy, "deploy_sqp", lambda *a, **kw: ok)
    monkeypatch.setattr(service_mod.qdeploy, "deploy_qp_cron", lambda *a, **kw: ok)

    events = list(svc.provision())

    assert events[-1]["type"] == "complete"
    # Second allocation excluded every port the first attempt burned.
    assert len(allocations) == 2
    assert allocations[1][0] >= {5432, 5433, 5434, 5435, 5436}
    assert any("retrying on the next free ports" in str(e.get("message", ""))
               for e in events)


class TestAmd64EmulationPreflight:

    def _status(self, tmp_path, monkeypatch, *, plat="darwin", machine="arm64",
                docker_running=True, run_result=None, run_raises=False):
        import platform as platform_mod

        svc = _bare_service(tmp_path, monkeypatch)
        svc._amd64_emulation_ok = False
        monkeypatch.setattr(service_mod.sys, "platform", plat)
        monkeypatch.setattr(platform_mod, "machine", lambda: machine)
        if run_raises:
            def _boom(*a, **kw):
                raise OSError("docker missing")
            monkeypatch.setattr(service_mod.qdeploy, "_run", _boom)
        elif run_result is not None:
            monkeypatch.setattr(service_mod.qdeploy, "_run", lambda *a, **kw: run_result)
        return svc, svc._amd64_emulation_status(docker_running)

    def test_not_applicable_off_mac(self, tmp_path, monkeypatch):
        _, status = self._status(tmp_path, monkeypatch, plat="linux")
        assert status == "not_applicable"

    def test_not_applicable_on_intel_mac(self, tmp_path, monkeypatch):
        _, status = self._status(tmp_path, monkeypatch, machine="x86_64")
        assert status == "not_applicable"

    def test_ok_when_probe_runs_amd64(self, tmp_path, monkeypatch):
        svc, status = self._status(
            tmp_path, monkeypatch,
            run_result=mock.Mock(returncode=0, stdout="x86_64\n", stderr=""),
        )
        assert status == "ok"
        # Sticky: later checks skip the probe entirely.
        assert svc._amd64_emulation_status(True) == "ok"

    def test_unavailable_only_on_definitive_failure(self, tmp_path, monkeypatch):
        _, status = self._status(
            tmp_path, monkeypatch,
            run_result=mock.Mock(returncode=1, stdout="",
                                 stderr="exec format error"),
        )
        assert status == "unavailable"

    def test_ambiguous_failures_never_scare(self, tmp_path, monkeypatch):
        _, status = self._status(
            tmp_path, monkeypatch,
            run_result=mock.Mock(returncode=1, stdout="",
                                 stderr="network timeout while pulling"),
        )
        assert status == "ok"
        _, status = self._status(tmp_path, monkeypatch, run_raises=True)
        assert status == "ok"

    def test_not_applicable_when_docker_down(self, tmp_path, monkeypatch):
        _, status = self._status(tmp_path, monkeypatch, docker_running=False)
        assert status == "not_applicable"
