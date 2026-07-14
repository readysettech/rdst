"""DemoService — orchestrates the whole QueryPilot demo for RDST Web.

Provisions the SQP data plane the rdst-native way — one `docker run`/`create` per
container (Postgres, ReadySet, SQP, QueryPilot cron) via features.qprouter.deploy,
mirroring `rdst cache deploy` (host.docker.internal networking, find-available
ports), NOT docker-compose. Owns the demo run state locally: dual-path load, the
pattern table via qprouter, manual caching, QueryPilot cron lifecycle, and clean
teardown.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from features.qprouter import QPRouter
from features.qprouter import deploy as qdeploy
from features.qprouter.qprouter import MANUAL_OWNER
from features.qprouter.readyset_client import ReadysetClient
from features.qprouter.sqp_client import to_decimal, to_hex
from features.qpdemo.load_driver import DualPathLoadDriver, WindowStat
from features.qpdemo.workload import WORKLOAD, load_sqls, workload_dicts
from shared.telemetry import telemetry

PROJECT = "qpdemo"
ASSETS = Path(__file__).parent / "assets"
WORKDIR = Path.home() / ".rdst" / "qpdemo"
QP_CONFIG_DIR = WORKDIR / "qp-cron"
# Legacy on-disk state; consolidated into config.toml's [demo] section on first
# load, then removed. Retained only so the migration can find and delete them.
STATE_PATH = WORKDIR / "state.json"
DEMO_DIR = Path.home() / ".rdst" / "demo"
HISTORY_PATH = DEMO_DIR / "history.json"
API_KEY = "rdst-demo-key"
CREDS = dict(user="sqp_user", password="sqp_pass", database="sqp_test")
NAMES = {n: f"{PROJECT}-{n}" for n in ("pg", "readyset", "sqp", "qp-cron")}
DISCOVERY_MODES = {"count_star", "sum_time"}
QP_NUMBER_OF_QUERIES = 10
# QueryPilot opens on the most-expensive policy (the tour's first payoff), then
# the visitor switches to most-frequent. Each policy carries its own budget:
# top 10 by total time, top 20 by call count.
QP_DEFAULT_MODE = "sum_time"
MODE_BUDGETS = {"sum_time": 10, "count_star": 20}
QP_CACHE_BUDGET_MIN = 1
QP_CACHE_BUDGET_MAX = 40
QP_MIN_EXECUTION = 5
HISTORY_WINDOW_S = 5 * 60
AUTO_TEARDOWN_S = 60 * 60
QP_INTERVALS = {"15s": 15, "1min": 60}
MANUAL_GUIDE_QUERY_KEYS = ("H03", "H04")
# Preflight estimates surfaced on the start card: total first-run image download
# and the free disk the demo needs while running.
IMAGE_DOWNLOAD_MB = 1100
DISK_REQUIRED_GB = 2


def _norm(sql: str) -> str:
    return re.sub(r"\s+", " ", sql or "").strip().lower()


def _shape(sql: str) -> str:
    text = _norm(sql)
    text = re.sub(r"'(?:''|[^'])*'", "$s", text)
    text = re.sub(r"\$\d+\b", "$n", text)
    text = re.sub(r"\b\d+\b", "$n", text)
    return text


def _now_s() -> float:
    return time.time()


def _round_float(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


class DemoService:
    _instance: "DemoService | None" = None

    def __init__(self) -> None:
        self._driver: DualPathLoadDriver | None = None
        self._drainer: threading.Thread | None = None
        self._history_lock = threading.Lock()
        self._running = False
        self._latest: tuple[WindowStat, WindowStat] | None = None
        # Rolling window + events are in-memory only; a restart starts empty.
        self._history: list[dict] = []
        self._events: list[dict] = []
        self._workers = 8
        self._discovery_mode = "count_star"
        self._cache_budget = QP_NUMBER_OF_QUERIES
        self._qp_cron_fast = True
        self._qp_cron_verified = False
        self._qp_last_tick: float | None = None
        self._last_error: str | None = None
        self._clock = _now_s
        self._auto_teardown_at: float | None = None
        self._pending_notice: str | None = None
        # Fires the `demo_completed` funnel event once per provisioned session.
        self._demo_completed_fired = False
        self._health: dict[str, str] = {n: "absent" for n in NAMES}
        self._supervisor_failures: dict[str, int] = {n: 0 for n in NAMES}
        self._supervisor_stop = threading.Event()
        self._supervisor: threading.Thread | None = None
        self._provisioning = False
        # Serializes provision/teardown: two interleaved provisions (double
        # click, tab retry) force-clean each other's containers mid-deploy and
        # corrupt ports/health state.
        self._lifecycle_lock = threading.Lock()
        self._ports: qdeploy.Ports | None = self._load_state()
        self._qp: QPRouter | None = self._make_qprouter() if self._ports else None
        self._qp_enabled = self._container_state(NAMES["qp-cron"]) == "running"
        self._qp_paused = not self._qp_enabled
        self._catalog = {_norm(q.sql): q for q in WORKLOAD}
        self._catalog_by_key = {q.id: q for q in WORKLOAD}
        self._catalog_by_shape = {_shape(q.sql): q for q in WORKLOAD}
        if self._ports:
            self._ensure_supervisor()

    @classmethod
    def instance(cls) -> "DemoService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ---- rolling demo history (in-memory only) -------------------------------
    def _prune_history_locked(self) -> None:
        cutoff = _now_s() - HISTORY_WINDOW_S
        self._history = [s for s in self._history if float(s.get("t", 0)) >= cutoff]
        self._events = [e for e in self._events if float(e.get("t", 0)) >= cutoff]

    def _record_history_sample(self, d: WindowStat, s: WindowStat) -> None:
        sample = {
            "t": _now_s(),
            "direct": {
                "qps": round(d.qps, 1),
                "p50_ms": round(d.p50_ms, 1),
                "p95_ms": round(d.p95_ms, 1),
            },
            "router": {
                "qps": round(s.qps, 1),
                "p50_ms": round(s.p50_ms, 1),
                "p95_ms": round(s.p95_ms, 1),
            },
        }
        with self._history_lock:
            self._history.append(sample)
            self._prune_history_locked()

    def _record_event(self, event_type: str, label: str) -> None:
        with self._history_lock:
            self._events.append({"t": _now_s(), "type": event_type, "label": label})
            self._prune_history_locked()

    # ---- funnel telemetry ----------------------------------------------------
    # Event names below are frozen — Slack funnel filters key on them exactly.
    def _track_demo_event(self, name: str, display_name: str, **extra) -> None:
        """Emit a demo-lifecycle PostHog event, fire-and-forget. TelemetryManager
        enriches with the stored email/identity and never blocks the caller;
        any failure is swallowed so telemetry can't break the demo."""
        try:
            telemetry.track(name, {"display_name": display_name, "source": "web", **extra})
        except Exception:
            pass

    def _track_first_demo_once(self) -> None:
        """Device-scoped `first_demo` event (frozen name — a Slack destination
        filter keys on it), fired via the shared first-event gate alongside the
        per-run demo_provisioned event. Failures never break the demo."""
        try:
            telemetry.track_first_event_once(
                "first_demo",
                flag="first_demo_fired",
                properties={"display_name": "RDST First Demo", "source": "web"},
            )
        except Exception:
            pass

    def load_history(self) -> dict:
        with self._history_lock:
            self._prune_history_locked()
            return {"samples": list(self._history), "events": list(self._events)}

    # ---- ports / persistent state (config.toml [demo] section) ---------------
    @staticmethod
    def _demo_config():
        from shared.config.targets import TargetsConfig

        cfg = TargetsConfig()
        cfg.load()
        return cfg

    def _apply_state(self, data: dict) -> qdeploy.Ports | None:
        self._discovery_mode = data.get("discovery_mode", "count_star")
        self._cache_budget = self._validate_cache_budget(
            data.get("cache_budget", QP_NUMBER_OF_QUERIES)
        )
        self._qp_cron_fast = bool(data.get("qp_cron_fast", True))
        self._qp_cron_verified = bool(data.get("qp_cron_verified", False))
        at = data.get("auto_teardown_at")
        self._auto_teardown_at = float(at) if at is not None else None
        ports = data.get("ports") or {}
        if not ports:
            return None
        return qdeploy.Ports(
            pg=int(ports["pg"]),
            readyset=int(ports["readyset"]),
            readyset_metrics=int(ports.get("readyset_metrics", 6034)),
            sqp=int(ports["sqp"]),
            metrics=int(ports["metrics"]),
        )

    def _load_state(self) -> qdeploy.Ports | None:
        self._migrate_legacy_state()
        return self._apply_state(self._demo_config().get_demo_state())

    def _state_dict(self) -> dict:
        state = {
            "ports": self._ports.as_dict(),
            "discovery_mode": self._discovery_mode,
            "cache_budget": self._cache_budget,
            "qp_cron_fast": self._qp_cron_fast,
            "qp_cron_verified": self._qp_cron_verified,
        }
        if self._auto_teardown_at is not None:
            state["auto_teardown_at"] = self._auto_teardown_at
        return state

    def _save_state(self) -> None:
        if not self._ports:
            return
        cfg = self._demo_config()
        cfg.set_demo_state(self._state_dict())
        cfg.save()

    def _remove_demo_images(self) -> None:
        """Delete the demo's container images on teardown so nothing is left on
        the machine. They re-download on the next run (public, anonymous).

        Best-effort: a Docker that is gone, an image already absent, or an rmi
        that times out must never fail the teardown or suppress the torn-down
        funnel event. Removing containers and state is what teardown guarantees;
        reclaiming images is a bonus."""
        for image in qdeploy.PINNED_IMAGES.values():
            try:
                self._docker(["rmi", "-f", image], timeout=60)
            except Exception:
                pass

    def _delete_state(self) -> None:
        cfg = self._demo_config()
        if cfg.clear_demo_state():
            cfg.save()

    def _migrate_legacy_state(self) -> None:
        """Absorb the pre-consolidation ~/.rdst/qpdemo/state.json into config.toml
        (only if [demo] is empty), then remove the legacy state file, the stale
        history.json, and the now-unused ~/.rdst/demo directory."""
        if STATE_PATH.exists():
            try:
                legacy = json.loads(STATE_PATH.read_text())
            except (OSError, ValueError):
                legacy = None
            if legacy:
                cfg = self._demo_config()
                if not cfg.get_demo_state():
                    migrated = {
                        "ports": legacy.get("ports") or {},
                        "discovery_mode": legacy.get("discovery_mode", "count_star"),
                        "cache_budget": legacy.get("cache_budget", QP_NUMBER_OF_QUERIES),
                        "qp_cron_fast": bool(legacy.get("qp_cron_fast", True)),
                        "qp_cron_verified": bool(legacy.get("qp_cron_verified", False)),
                    }
                    cfg.set_demo_state(migrated)
                    cfg.save()
            STATE_PATH.unlink(missing_ok=True)
        HISTORY_PATH.unlink(missing_ok=True)
        if DEMO_DIR.exists():
            try:
                DEMO_DIR.rmdir()
            except OSError:
                pass

    def _make_qprouter(self) -> QPRouter:
        p = self._ports
        rs = ReadysetClient("127.0.0.1", p.readyset,
                            metrics_port=p.readyset_metrics, **CREDS)
        return QPRouter("127.0.0.1", p.sqp, p.admin, api_key=API_KEY,
                        readyset=rs, metrics_port=p.metrics, name="demo-qpr")

    # ---- docker helpers ------------------------------------------------------
    @staticmethod
    def docker_ok() -> bool:
        try:
            return subprocess.run(["docker", "info"], capture_output=True,
                                  timeout=8).returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _container_state(self, name: str) -> str:
        try:
            r = subprocess.run(["docker", "inspect", "--format", "{{.State.Status}}", name],
                               capture_output=True, text=True, timeout=5)
            return r.stdout.strip() if r.returncode == 0 else "absent"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return "absent"

    def _force_clean(self) -> None:
        qdeploy.remove_containers(list(NAMES.values()))

    def _docker(self, args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)

    def _stop_container(self, name: str) -> None:
        state = self._container_state(name)
        if state in {"absent", "created", "exited"}:
            return
        r = self._docker(["stop", name], timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"docker stop {name} failed: {r.stderr[:300]}")

    # ---- supervisor ----------------------------------------------------------
    def _ensure_supervisor(self) -> None:
        if self._supervisor and self._supervisor.is_alive():
            return
        self._supervisor_stop.clear()
        self._supervisor = threading.Thread(target=self._supervisor_loop, daemon=True)
        self._supervisor.start()

    def _stop_supervisor(self) -> None:
        self._supervisor_stop.set()
        if self._supervisor and self._supervisor.is_alive():
            self._supervisor.join(timeout=5)
        self._supervisor = None

    def _container_intended_running(self, short: str) -> bool:
        if not self._ports:
            return False
        if short == "qp-cron":
            return self._qp_enabled
        return True

    # ---- one-hour auto-teardown ----------------------------------------------
    def _auto_teardown_due(self) -> bool:
        return bool(
            self._ports
            and self._auto_teardown_at is not None
            and self._clock() >= self._auto_teardown_at
        )

    def _trigger_auto_teardown(self) -> None:
        # Clear the deadline first so the supervisor can't re-fire, then run the
        # teardown off-thread (teardown joins the supervisor thread itself).
        self._auto_teardown_at = None
        threading.Thread(target=self._run_auto_teardown, daemon=True).start()

    def _run_auto_teardown(self) -> None:
        notice = "Demo environment auto-cleaned after 1 hour."
        self.teardown(reason="auto")
        self._pending_notice = notice
        self._record_event("auto_teardown", notice)

    def _supervisor_loop(self) -> None:
        while not self._supervisor_stop.wait(3):
            if not self._ports:
                break
            if self._auto_teardown_due():
                self._trigger_auto_teardown()
                break
            for short, name in NAMES.items():
                if not self._container_intended_running(short):
                    self._health[short] = "running"
                    self._supervisor_failures[short] = 0
                    continue
                if self._health.get(short) == "failed" and self._supervisor_failures.get(short, 0) >= 3:
                    continue
                state = self._container_state(name)
                if state == "running":
                    self._health[short] = "running"
                    self._supervisor_failures[short] = 0
                    continue
                self._health[short] = "recovering"
                self._supervisor_failures[short] += 1
                r = self._docker(["start", name], timeout=30)
                if r.returncode == 0:
                    continue
                if self._supervisor_failures[short] >= 3:
                    msg = f"{short} failed to restart after 3 attempts: {r.stderr[:200]}"
                    self._health[short] = "failed"
                    self._last_error = msg
                    self._record_event("error", msg)

    def _start_qp_cron(self) -> None:
        state = self._container_state(NAMES["qp-cron"])
        if state == "absent":
            r = qdeploy.deploy_qp_cron(NAMES["qp-cron"], QP_CONFIG_DIR, self._qp_cron_fast)
            if r.returncode != 0:
                raise RuntimeError(f"QueryPilot cron create failed: {r.stderr[:300]}")
        self._sync_workload_denylist()
        if self._container_state(NAMES["qp-cron"]) != "running":
            r = self._docker(["start", NAMES["qp-cron"]], timeout=30)
            if r.returncode != 0:
                raise RuntimeError(f"docker start {NAMES['qp-cron']} failed: {r.stderr[:300]}")
        self._qp_enabled = True
        self._qp_paused = False
        if self._qp_cron_fast and not self._qp_cron_verified:
            if self._verify_qp_cron_tick(timeout=25):
                self._qp_cron_verified = True
            else:
                self._fallback_to_baked_crontab()
        self._save_state()

    def _verify_qp_cron_tick(self, timeout: int) -> bool:
        since = str(int(time.time()) - 1)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._container_state(NAMES["qp-cron"]) != "running":
                return False
            logs = self._qp_cron_logs(since=since, tail=200)
            if "sqp-query-pilot" in logs or "Readyset[" in logs or "job succeeded" in logs:
                self._qp_last_tick = _now_s()
                return True
            time.sleep(1)
        return False

    def _fallback_to_baked_crontab(self) -> None:
        self._stop_container(NAMES["qp-cron"])
        r = self._docker(["rm", "-v", NAMES["qp-cron"]], timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"docker rm -v {NAMES['qp-cron']} failed: {r.stderr[:300]}")
        r = qdeploy.deploy_qp_cron(NAMES["qp-cron"], QP_CONFIG_DIR, fast_crontab=False)
        if r.returncode != 0:
            raise RuntimeError(f"QueryPilot cron fallback create failed: {r.stderr[:300]}")
        r = self._docker(["start", NAMES["qp-cron"]], timeout=30)
        if r.returncode != 0:
            raise RuntimeError(f"QueryPilot cron fallback start failed: {r.stderr[:300]}")
        self._qp_cron_fast = False
        self._qp_cron_verified = True
        self._qp_enabled = True
        self._qp_paused = False

    def _qp_cron_logs(self, since: str | None = None, tail: int = 400,
                      timestamps: bool = False) -> str:
        args = ["logs", "--tail", str(tail)]
        if timestamps:
            args.append("--timestamps")
        if since:
            args.extend(["--since", since])
        args.append(NAMES["qp-cron"])
        r = self._docker(args, timeout=10)
        return (r.stdout or "") + (r.stderr or "")

    def _refresh_qp_last_tick(self) -> None:
        if not self._qp_enabled or self._container_state(NAMES["qp-cron"]) != "running":
            return
        logs = self._qp_cron_logs(tail=120, timestamps=True)
        last: float | None = None
        for line in logs.splitlines():
            if not ("sqp-query-pilot" in line or "Readyset[" in line or "job succeeded" in line):
                continue
            ts = line.split(" ", 1)[0]
            try:
                last = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            except ValueError:
                last = _now_s()
        if last is not None:
            self._qp_last_tick = last

    def _qp_schedule(self) -> str:
        return "15s" if self._qp_cron_fast else "1min"

    def _next_qp_eta_s(self) -> int | None:
        if not self._qp_enabled:
            return None
        self._refresh_qp_last_tick()
        interval = QP_INTERVALS[self._qp_schedule()]
        if self._qp_last_tick is None:
            return interval
        return max(0, int(round(self._qp_last_tick + interval - _now_s())))

    def _qp_log_reasons(self) -> dict[int, str]:
        out: dict[int, str] = {}
        logs = self._qp_cron_logs(tail=800)
        pat = re.compile(
            r"digest:\s*(0x[0-9a-fA-F]{16}).*?(selected using query_discovery_mode:\s*\w+)",
            re.I,
        )
        for line in logs.splitlines():
            m = pat.search(line)
            if m:
                out[int(m.group(1), 16)] = m.group(2)
        return out

    def _sync_workload_denylist(self) -> None:
        if not self._qp:
            return
        denylisted = {
            _shape(q.sql): q
            for q in WORKLOAD
            if q.denylist
        }
        lines: list[str] = []
        manual_fps: set[int] = set()
        for rule in self._qp.admin.pattern_rules():
            try:
                meta = json.loads(rule.get("comment") or "{}")
            except ValueError:
                meta = {}
            if meta.get("owner") == MANUAL_OWNER:
                manual_fps.add(to_decimal(rule["fingerprint_hash"]))
        for digest in self._qp.admin.digests(limit=500):
            fp = to_decimal(digest["fingerprint_hash"])
            q = denylisted.get(_shape(digest.get("digest_text", "")))
            if q:
                lines.append(f"digest={to_hex(fp)} # {q.id} {q.title}")
            if fp in manual_fps:
                lines.append(f"digest={to_hex(fp)} # manual cache")
        for fp in manual_fps:
            lines.append(f"digest={to_hex(fp)} # manual cache")
        (QP_CONFIG_DIR / "denylist").write_text(
            "\n".join(sorted(set(lines))) + ("\n" if lines else ""),
            newline="\n",
        )

    # ---- preflight -------------------------------------------------------------
    def preflight(self) -> dict:
        """Live start-card checklist: Docker reachable, demo images cached, and
        enough free disk. Cheap enough to re-run on every card mount / retry."""
        import shutil

        docker_running = self.docker_ok()
        images = list(qdeploy.PINNED_IMAGES.values())
        missing = (
            [i for i in images if not qdeploy.image_present(i)]
            if docker_running
            else list(images)
        )
        download_mb = (
            int(round(IMAGE_DOWNLOAD_MB * len(missing) / len(images))) if missing else 0
        )
        try:
            free_gb = shutil.disk_usage(str(Path.home())).free / 1e9
        except OSError:
            free_gb = 0.0
        return {
            "docker_running": docker_running,
            "images_present": not missing,
            "missing_images": missing,
            "download_mb": download_mb,
            "disk_space_ok": free_gb >= DISK_REQUIRED_GB,
            "disk_free_gb": round(free_gb, 1),
            "disk_required_gb": DISK_REQUIRED_GB,
        }

    # ---- image pull (step 0) -------------------------------------------------
    def _pull_missing_images(self, runner=None) -> Iterator[dict]:
        """Explicitly pull any of the four pinned images that aren't cached yet,
        streaming per-image progress. `run`/`create` stay on --pull=never, so
        this is the demo's only registry access. `runner` is injectable for
        tests; production uses the docker subprocess runner."""
        run = runner or qdeploy._run
        images = list(qdeploy.PINNED_IMAGES.values())
        total = len(images)
        yield {"type": "progress", "stage": "images", "percent": 1,
               "message": "Checking container images..."}
        for idx, image in enumerate(images, start=1):
            pct = 1 + int(idx / total * 3)
            if qdeploy.image_present(image, run):
                yield {"type": "progress", "stage": "images", "percent": pct,
                       "message": f"Image present: {image}"}
                continue
            yield {"type": "progress", "stage": "images", "percent": pct,
                   "message": f"Pulling {image} ({idx}/{total})..."}
            r = qdeploy.pull_image(image, run)
            if r.returncode != 0:
                raise RuntimeError(f"Failed to pull {image}: {(r.stderr or '')[:300]}")
            yield {"type": "progress", "stage": "images", "percent": pct,
                   "message": f"Pulled {image}"}

    # ---- provision -----------------------------------------------------------
    def provision(self) -> Iterator[dict]:
        if not self._lifecycle_lock.acquire(blocking=False):
            yield {"type": "error",
                   "message": "Another provision or teardown is already in progress."}
            return
        self._provisioning = True
        try:
            yield from self._provision_locked()
        finally:
            self._provisioning = False
            self._lifecycle_lock.release()

    def _provision_locked(self) -> Iterator[dict]:
        if not self.docker_ok():
            yield {"type": "error", "message": "Docker isn't running. Start Docker and retry."}
            return
        try:
            self._last_error = None
            self._pending_notice = None
            self._stop_supervisor()
            self._force_clean()  # idempotent
            yield from self._pull_missing_images()
            self._ports = qdeploy.allocate_ports()
            self._discovery_mode = QP_DEFAULT_MODE
            self._cache_budget = MODE_BUDGETS[QP_DEFAULT_MODE]
            self._qp_cron_fast = True
            self._qp_cron_verified = False
            self._qp_last_tick = None
            self._qp_enabled = False
            self._qp_paused = True
            self._demo_completed_fired = False
            self._auto_teardown_at = self._clock() + AUTO_TEARDOWN_S
            self._health = {n: "recovering" for n in NAMES}
            self._supervisor_failures = {n: 0 for n in NAMES}
            self._qp = self._make_qprouter()
            self._save_state()
            p = self._ports
            yield {"type": "progress", "stage": "start", "percent": 5,
                   "message": f"Starting containers (pg:{p.pg} readyset:{p.readyset} sqp:{p.sqp})..."}

            cfg_path, denylist_path = qdeploy.write_sqp_config(
                WORKDIR, p, p.pg, p.readyset, API_KEY, CREDS,
            )
            qdeploy.write_qp_config(
                QP_CONFIG_DIR, p.admin, p.readyset, CREDS,
                self._discovery_mode, self._cache_budget,
            )

            yield {"type": "container", "name": "pg", "label": "Postgres (Orders dataset)",
                   "state": "starting", "percent": 6, "detail": "Starting Postgres..."}
            # Postgres ships pre-built: the container only starts, it never
            # generates data. Ready in a second or two.
            r = qdeploy.deploy_postgres_baked(NAMES["pg"], p.pg)
            if r.returncode != 0:
                raise RuntimeError(f"Postgres failed: {r.stderr[:300]}")
            yield {"type": "progress", "stage": "pg", "percent": 20,
                   "message": "Starting the pre-built Orders database..."}
            if not self._wait_pg_connect(60):
                raise RuntimeError("Postgres did not become ready.")
            yield {"type": "container", "name": "pg", "label": "Postgres (Orders dataset)",
                   "state": "ready", "percent": 30}

            yield {"type": "container", "name": "readyset", "label": "Readyset cache engine",
                   "state": "starting", "percent": 32,
                   "detail": "Waiting for Readyset to connect..."}
            r = qdeploy.deploy_readyset(
                NAMES["readyset"], p.readyset, p.readyset_metrics, p.pg, CREDS,
            )
            if r.returncode != 0:
                raise RuntimeError(f"ReadySet failed: {r.stderr[:300]}")
            if not (yield from self._wait_readyset_events(300)):
                raise RuntimeError("ReadySet did not snapshot in time.")
            yield {"type": "container", "name": "readyset", "label": "Readyset cache engine",
                   "state": "ready", "percent": 60}

            yield {"type": "container", "name": "sqp", "label": "QueryPilot router",
                   "state": "starting", "percent": 62,
                   "detail": "Starting the QueryPilot router..."}
            r = qdeploy.deploy_sqp(NAMES["sqp"], p, cfg_path, denylist_path)
            if r.returncode != 0:
                raise RuntimeError(f"SQP failed: {r.stderr[:300]}")
            if not (yield from self._wait_sqp_events(90)):
                raise RuntimeError("SQP proxy did not become ready.")
            yield {"type": "container", "name": "sqp", "label": "QueryPilot router",
                   "state": "ready", "percent": 85}

            yield {"type": "progress", "stage": "warmup", "percent": 88,
                   "message": "Warming up the database on both paths (parallel)..."}
            warmed = self._warmup_paths(passes=3)["queries"]
            yield {"type": "progress", "stage": "warmup", "percent": 94,
                   "message": f"Warmed {warmed} query shapes on both paths."}

            yield {"type": "container", "name": "qp-cron", "label": "QueryPilot accelerator",
                   "state": "starting", "percent": 94,
                   "detail": "Creating the QueryPilot accelerator..."}

            yield {"type": "progress", "stage": "qp-cron", "percent": 95,
                   "message": "Starting the QueryPilot accelerator..."}
            r = qdeploy.deploy_qp_cron(NAMES["qp-cron"], QP_CONFIG_DIR, fast_crontab=True)
            if r.returncode != 0:
                raise RuntimeError(f"QueryPilot cron failed: {r.stderr[:300]}")
            yield {"type": "container", "name": "qp-cron", "label": "QueryPilot accelerator",
                   "state": "ready", "percent": 97}

            self._save_state()
            self._health = {n: "running" for n in NAMES}
            self._ensure_supervisor()
            self._track_demo_event("demo_provisioned", "RDST Demo Provisioned")
            self._track_first_demo_once()
            yield {"type": "complete", "percent": 100, "targets": [],
                   "message": "Environment ready. QueryPilot is off — cache by hand first."}
        except Exception as e:
            self._last_error = str(e)
            yield {"type": "error", "message": str(e)}

    def _wait_pg_connect(self, timeout: int) -> bool:
        """Readiness wait for the pre-baked image: data already exists, so
        Postgres accepts connections within a second or two of starting."""
        deadline = _now_s() + timeout
        while _now_s() < deadline:
            r = qdeploy._run([
                "docker", "exec", NAMES["pg"],
                "pg_isready", "-U", CREDS["user"], "-d", CREDS["database"],
            ], timeout=10)
            if r.returncode == 0:
                return True
            time.sleep(1)
        return False

    def _wait_readyset_events(self, timeout: int) -> Iterator[dict]:
        deadline = time.monotonic() + timeout
        start = time.monotonic()
        next_event = start + 3
        while time.monotonic() < deadline:
            try:
                st = self._qp.readyset.status()
                if st.get("Status") == "Online" and st.get("Last completed snapshot"):
                    return True
            except Exception:
                pass
            now = time.monotonic()
            if now >= next_event:
                elapsed = int(now - start)
                pct = min(59, 32 + int(27 * elapsed / 45))
                yield {"type": "progress", "stage": "readyset", "percent": pct,
                       "message": f"Waiting for Readyset to connect and snapshot the dataset ({elapsed}s)..."}
                next_event = now + 3
            time.sleep(1)
        return False

    def _wait_sqp_events(self, timeout: int) -> Iterator[dict]:
        deadline = time.monotonic() + timeout
        start = time.monotonic()
        next_event = start + 3
        while time.monotonic() < deadline:
            try:
                self._qp.admin.pools()
                return True
            except Exception:
                pass
            now = time.monotonic()
            if now >= next_event:
                elapsed = int(now - start)
                pct = min(84, 62 + int(22 * elapsed / 30))
                yield {"type": "progress", "stage": "sqp", "percent": pct,
                       "message": f"Starting the QueryPilot router ({elapsed}s)..."}
                next_event = now + 3
            time.sleep(1)
        return False

    def _warmup_paths(self, passes: int = 3, workers: int = 6) -> dict[str, int]:
        """Warm Postgres buffers on both paths before any measurement, so the
        baseline comparison starts honest. Queries run concurrently: the pass
        is I/O-bound on Postgres, and a small pool cuts ~30s to single digits."""
        import psycopg2
        from concurrent.futures import ThreadPoolExecutor

        if not self._ports:
            return {"queries": 0, "executions": 0}
        warm_queries = [
            q.sql for q in WORKLOAD
            if q.weight > 0 and q.category in {"heavy", "moderate"}
        ]
        ports = {"direct": self._ports.pg, "router": self._ports.sqp}

        def _warm_one(job: tuple[int, str]) -> int:
            port, sql = job
            conn = psycopg2.connect(
                host="127.0.0.1", port=port, dbname=CREDS["database"],
                user=CREDS["user"], password=CREDS["password"], connect_timeout=5,
            )
            conn.autocommit = True
            done = 0
            try:
                with conn.cursor() as cur:
                    for _ in range(passes):
                        cur.execute(sql)
                        cur.fetchall()
                        done += 1
            finally:
                conn.close()
            return done

        jobs = [(port, sql) for port in ports.values() for sql in warm_queries]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            executions = sum(pool.map(_warm_one, jobs))
        return {"queries": len(warm_queries), "executions": executions}

    def teardown(self, reason: str = "user") -> dict:
        # Waits for any in-flight provision to finish (or be abandoned) so the
        # two can't interleave docker operations on the same containers.
        with self._lifecycle_lock:
            return self._teardown_locked(reason)

    def _teardown_locked(self, reason: str) -> dict:
        # Only a real, provisioned environment counts as a teardown for the
        # funnel; a no-op teardown on an idle service fires nothing.
        was_provisioned = self._ports is not None
        try:
            self.stop_load()
            self._stop_supervisor()
            self._force_clean()
            self._remove_demo_images()
            self._delete_state()
            self._ports = None
            self._qp = None
            self._qp_enabled = False
            self._qp_paused = True
            self._auto_teardown_at = None
            self._demo_completed_fired = False
            self._health = {n: "absent" for n in NAMES}
            with self._history_lock:
                self._history = []
                self._events = []
            self._supervisor_failures = {n: 0 for n in NAMES}
            self._last_error = None
            if was_provisioned:
                # Frozen, distinct event names: user-initiated vs the one-hour
                # automatic sweep. Slack funnel filters key on the name.
                if reason == "auto":
                    self._track_demo_event(
                        "demo_torn_down_auto", "RDST Demo Auto Torn Down", reason=reason,
                    )
                else:
                    self._track_demo_event(
                        "demo_torn_down", "RDST Demo Torn Down", reason=reason,
                    )
            return {"success": True, "message": "Environment torn down; nothing left behind."}
        except Exception as e:
            self._last_error = str(e)
            return {"success": False, "message": str(e)}

    def status(self) -> dict:
        # Reconcile stale state: if we believe we are provisioned but every
        # container is gone (an out-of-band `docker rm`, a crashed session),
        # clear the demo state so the UI returns to the idle start card
        # instead of a broken ready view.
        if self._ports and not self._provisioning and all(
            self._container_state(cn) == "absent" for cn in NAMES.values()
        ):
            self._stop_supervisor()
            self._delete_state()
            self._ports = None
            self._qp = None
            self._qp_enabled = False
            self._auto_teardown_at = None
            self._health = {n: "absent" for n in NAMES}
            with self._history_lock:
                self._history = []
                self._events = []
        qp_state = self._container_state(NAMES["qp-cron"])
        self._qp_paused = not self._qp_enabled
        try:
            metrics = self._qp.readyset.cache_metrics() if self._qp else {}
        except Exception:
            metrics = {}
        if self._ports and not self._qp_enabled:
            self._health["qp-cron"] = "running"
            self._supervisor_failures["qp-cron"] = 0
        schedule = self._qp_schedule()
        # One-shot: surface (and clear) any server-initiated notice, e.g. the
        # auto-teardown toast, so the UI shows it exactly once.
        notice = self._pending_notice
        self._pending_notice = None
        return {
            "notice": notice,
            "auto_teardown_at": self._auto_teardown_at,
            "provisioned": self._ports is not None,
            "containers": {n: self._container_state(cn) for n, cn in NAMES.items()},
            "load_running": self._running,
            "workers": self._workers,
            "querypilot_paused": self._qp_paused,
            "querypilot_enabled": self._qp_enabled,
            "discovery_mode": self._discovery_mode,
            "querypilot": {
                "enabled": self._qp_enabled,
                "mode": self._discovery_mode,
                "next_pass_eta_s": self._next_qp_eta_s(),
                "schedule": schedule,
                "cache_budget": self._cache_budget,
            },
            "cache_budget": self._cache_budget,
            "health": dict(self._health) if self._ports else {n: "absent" for n in NAMES},
            "cron": {
                "schedule": schedule,
                "fast_crontab_verified": self._qp_cron_verified,
                "state": qp_state,
            },
            "targets": {
                "readyset": {
                    "hit_rate": metrics.get("hit_rate"),
                    "keys_read": metrics.get("keys_read"),
                    "cache_misses": metrics.get("cache_misses"),
                }
            },
            "ports": self._ports.as_dict() if self._ports else None,
            "last_error": self._last_error,
        }

    # ---- load ----------------------------------------------------------------
    def start_load(self, workers: int | None = None) -> None:
        if self._running:
            if workers:
                self.set_intensity(workers)
            return
        if not self._ports:
            return
        self._workers = workers or self._workers
        self._latest = None
        direct = dict(host="127.0.0.1", port=self._ports.pg, dbname=CREDS["database"],
                      user=CREDS["user"], password=CREDS["password"])
        sqp = dict(host="127.0.0.1", port=self._ports.sqp, dbname=CREDS["database"],
                   user=CREDS["user"], password=CREDS["password"])
        self._driver = DualPathLoadDriver(direct, sqp, load_sqls())
        self._reset_comparison_window()
        self._driver.direct.start(self._workers)
        self._driver.sqp.start(self._workers)
        self._running = True
        self._drainer = threading.Thread(target=self._drain_loop, daemon=True)
        self._drainer.start()

    def _drain_loop(self) -> None:
        while self._running:
            drv = self._driver
            if drv is None:
                break
            t0 = time.monotonic()
            time.sleep(1.0)
            el = time.monotonic() - t0
            if not self._running or self._driver is None:
                break
            d = drv.direct.drain_window(el)
            s = drv.sqp.drain_window(el)
            self._latest = (d, s)
            self._record_history_sample(d, s)

    def set_intensity(self, workers: int) -> None:
        if not self._driver or not self._running:
            self._workers = workers
            return
        delta = workers - self._workers
        if delta > 0:
            self._driver.direct.start(delta)
            self._driver.sqp.start(delta)
        self._workers = workers

    def stop_load(self) -> None:
        self._running = False
        if self._driver:
            self._driver.direct.stop()
            self._driver.sqp.stop()
        if self._drainer and self._drainer.is_alive():
            self._drainer.join(timeout=2)
        self._driver = None
        self._drainer = None

    def load_stream(self) -> Iterator[dict]:
        last = None
        while self._running:
            if self._latest is not None and self._latest is not last:
                last = self._latest
                yield {"type": "window", **self._window_dict(*self._latest)}
            time.sleep(0.25)

    @staticmethod
    def _window_dict(d: WindowStat, s: WindowStat) -> dict:
        return {
            "direct": {"qps": round(d.qps), "p50": round(d.p50_ms, 1),
                       "p95": round(d.p95_ms, 1), "p99": round(d.p99_ms, 1),
                       "errors": d.errors, "retried": d.retried,
                       "transient_errors": d.transient_errors},
            "sqp": {"qps": round(s.qps), "p50": round(s.p50_ms, 1),
                    "p95": round(s.p95_ms, 1), "p99": round(s.p99_ms, 1),
                    "errors": s.errors, "retried": s.retried,
                    "transient_errors": s.transient_errors},
        }

    def _reset_comparison_window(self, key: str | None = None) -> None:
        if self._driver:
            self._driver.reset_query_stats(key)

    # ---- patterns / caching --------------------------------------------------
    def _raw_patterns(self):
        if not self._qp:
            return []
        self._sync_workload_denylist()
        return self._qp.get_patterns(
            query_discovery_mode=self._discovery_mode,
            number_of_queries=self._cache_budget,
            min_execution=QP_MIN_EXECUTION,
            denylist_path=QP_CONFIG_DIR / "denylist",
            log_reasons=self._qp_log_reasons(),
        )

    def _query_for_sql(self, sql: str):
        return self._catalog.get(_norm(sql)) or self._catalog_by_shape.get(_shape(sql))

    @staticmethod
    def _contract_reason(decision: dict, q) -> dict:
        kind = decision.get("reason") or "below_rank"
        if q and q.role == "uncacheable" and not q.denylist:
            kind = "unsupported"
        out = {"kind": kind}
        if decision.get("rank") is not None:
            out["rank"] = decision["rank"]
        metric = decision.get("metric_name") or decision.get("metric")
        if metric:
            out["metric"] = "sum_time_us" if metric == "sum_time" else metric
        for key in ("metric_value", "cutoff", "winner_values", "count", "threshold"):
            if decision.get(key) is not None:
                out[key] = decision[key]
        return out

    @staticmethod
    def _contract_status(row, q, reason: dict) -> str:
        if row.cached and row.owner == "manual":
            return "cached_manual"
        if row.cached:
            return "cached_querypilot"
        if q and q.denylist or reason["kind"] == "denylisted":
            return "denylisted"
        if reason["kind"] == "unsupported":
            return "unsupported"
        if reason["kind"] in {"below_min_execution", "not_select_shaped"}:
            return "not_eligible"
        return "pass_through"

    def _current_query_stats(self) -> dict[str, dict[str, dict]]:
        if not self._driver:
            return {"direct": {}, "router": {}}
        return self._driver.query_stats()

    def patterns(self) -> list[dict]:
        stats = self._current_query_stats()
        out = []
        for r in self._raw_patterns():
            q = self._query_for_sql(r.sql)
            key = q.id if q else r.fingerprint_hex
            direct_stat = stats["direct"].get(key, {})
            router_stat = stats["router"].get(key, {})
            direct_hits = int(direct_stat.get("hits", 0) or 0)
            router_hits = int(router_stat.get("hits", 0) or 0)
            reason = self._contract_reason(r.decision, q)
            out.append({
                "key": key,
                "title": q.title if q else r.sql[:80],
                "sql": r.sql,
                "group": q.group if q else "observed",
                "status": self._contract_status(r, q, reason),
                # Windowed per-path hit counts. They diverge once caching speeds
                # one path up; ``hits`` stays as the larger of the two for older
                # clients that predate the split.
                "hits": max(direct_hits, router_hits),
                "postgres_hits": direct_hits,
                "readyset_hits": router_hits,
                "direct_avg_ms": _round_float(direct_stat.get("avg_ms")),
                "router_avg_ms": _round_float(router_stat.get("avg_ms")),
                "reason": reason,
                "log_reason": r.log_reason,
                "alt_rank": r.alt_rank,
                "alt_metric": r.alt_metric,
            })
        self._prefer_manual_guide_mid_tier(out)
        self._maybe_fire_demo_completed(out)
        return out

    def _maybe_fire_demo_completed(self, rows: list[dict]) -> None:
        """The demo's payoff completes when the visitor, having switched to the
        most-frequent policy (count_star, the tour's second beat), sees QueryPilot
        re-select and cache something while traffic flows. Fires on the first
        QueryPilot-owned cache under count_star; once per provisioned session."""
        if self._demo_completed_fired or self._discovery_mode != "count_star" or not self._running:
            return
        if any(r.get("status") == "cached_querypilot" for r in rows):
            self._demo_completed_fired = True
            self._track_demo_event(
                "demo_completed", "RDST Demo Completed", discovery_mode="count_star",
            )

    @staticmethod
    def _prefer_manual_guide_mid_tier(rows: list[dict]) -> None:
        preferred = {key: i for i, key in enumerate(MANUAL_GUIDE_QUERY_KEYS)}
        mid_rows = [row for row in rows if row.get("group") == "mid_tier"]
        if not mid_rows:
            return
        ordered_mid_rows = sorted(
            mid_rows,
            key=lambda row: (
                0 if row.get("key") in preferred else 1,
                preferred.get(row.get("key"), len(preferred)),
            ),
        )
        mid_iter = iter(ordered_mid_rows)
        for i, row in enumerate(rows):
            if row.get("group") == "mid_tier":
                rows[i] = next(mid_iter)

    def _resolve_cache_target(self, identifier: str) -> tuple[str, str]:
        text = str(identifier).strip()
        # Workload keys first: several ("C01", "D02", ...) also parse as hex,
        # so a fingerprint-first order would misread them as hashes.
        q = self._catalog_by_key.get(text)
        if not q:
            try:
                return str(to_decimal(text)), text
            except ValueError:
                pass
            raise ValueError(f"unknown query key or fingerprint: {identifier}")
        for row in self._raw_patterns():
            if _shape(row.sql) == _shape(q.sql):
                return str(row.fingerprint), q.title
        raise ValueError(f"query {text} has not been seen in live traffic yet")

    def cache(self, fingerprint: str) -> dict:
        if not self._qp:
            raise RuntimeError("demo is not provisioned")
        fp, label = self._resolve_cache_target(fingerprint)
        self._qp.manual_cache(fp)
        self._sync_workload_denylist()
        self._record_event("manual_cache", f"you cached {label}")
        # Reset ONLY this query's windowed averages so they rebuild from cached
        # executions and the drop is immediate — the slow pre-cache samples no
        # longer drag the average down. Other queries are untouched.
        q = self._catalog_by_key.get(str(fingerprint).strip())
        if q:
            self._reset_comparison_window(q.id)
        return {"success": True, "fingerprint": fp, "owner": "manual"}

    def uncache(self, fingerprint: str) -> dict:
        if not self._qp:
            raise RuntimeError("demo is not provisioned")
        fp, label = self._resolve_cache_target(fingerprint)
        self._qp.manual_uncache(fp)
        self._sync_workload_denylist()
        self._record_event("manual_uncache", f"you uncached {label}")
        q = self._catalog_by_key.get(str(fingerprint).strip())
        if q:
            self._reset_comparison_window(q.id)
        return {"success": True, "fingerprint": fp}

    def set_querypilot(self, enabled: bool) -> dict:
        if not self._ports:
            raise RuntimeError("demo is not provisioned")
        was_enabled = self._qp_enabled
        reset = None
        if enabled:
            # Enabling always starts from the demo's opening policy: cache the
            # most expensive queries (top 10 by total time), regardless of any
            # prior mode or budget the visitor left behind. The tour then switches
            # to most-frequent.
            self._discovery_mode = QP_DEFAULT_MODE
            self._cache_budget = MODE_BUDGETS[QP_DEFAULT_MODE]
            if self._ports:
                qdeploy.write_qp_config(
                    QP_CONFIG_DIR, self._ports.admin, self._ports.readyset,
                    CREDS, self._discovery_mode, self._cache_budget,
                )
            self._start_qp_cron()
            # Freshly enabled is healthy, not "recovering": the orange state
            # (and its toast) is reserved for genuine post-ready failures,
            # which the supervisor flags on its own.
            self._health["qp-cron"] = "running"
            self._supervisor_failures["qp-cron"] = 0
            if not was_enabled:
                self._record_event("qp_on", "QueryPilot on")
                self._track_demo_event(
                    "querypilot_enabled", "RDST QueryPilot Enabled",
                    mode=self._discovery_mode,
                )
        else:
            self._stop_container(NAMES["qp-cron"])
            reset = self._qp.reset_querypilot_caches() if self._qp else None
            self._qp_enabled = False
            self._qp_paused = True
            self._health["qp-cron"] = "running"
            self._supervisor_failures["qp-cron"] = 0
            self._save_state()
            if was_enabled:
                self._record_event("qp_off", "QueryPilot off")
        self._reset_comparison_window()
        return {"success": True, "querypilot_enabled": self._qp_enabled, "reset": reset}

    def set_discovery_mode(self, mode: str) -> dict:
        if mode not in DISCOVERY_MODES:
            raise ValueError(f"unsupported discovery mode: {mode}")
        if not self._ports or not self._qp:
            raise RuntimeError("demo is not provisioned")
        self._discovery_mode = mode
        # Each policy carries its own budget: most-expensive caches the top 10 by
        # total time, most-frequent the top 20 by call count.
        self._cache_budget = MODE_BUDGETS[mode]
        qdeploy.write_qp_config(
            QP_CONFIG_DIR, self._ports.admin, self._ports.readyset,
            CREDS, self._discovery_mode, self._cache_budget,
        )
        self._sync_workload_denylist()
        reset = self._qp.reset_querypilot_caches()
        self._save_state()
        self._reset_comparison_window()
        label = "Most frequent" if mode == "count_star" else "Most expensive"
        self._record_event("mode_change", f"mode changed to {label}")
        return {"success": True, "mode": mode, "reset": reset}

    @staticmethod
    def _validate_cache_budget(value: int) -> int:
        try:
            budget = int(value)
        except (TypeError, ValueError) as e:
            raise ValueError("cache_budget must be an integer") from e
        if budget < QP_CACHE_BUDGET_MIN or budget > QP_CACHE_BUDGET_MAX:
            raise ValueError(
                f"cache_budget must be between {QP_CACHE_BUDGET_MIN} and {QP_CACHE_BUDGET_MAX}"
            )
        return budget

    def set_settings(self, cache_budget: int) -> dict:
        self._cache_budget = self._validate_cache_budget(cache_budget)
        if self._ports:
            qdeploy.write_qp_config(
                QP_CONFIG_DIR, self._ports.admin, self._ports.readyset,
                CREDS, self._discovery_mode, self._cache_budget,
            )
            self._save_state()
        return {"success": True, "cache_budget": self._cache_budget}

    def workload(self) -> list[dict]:
        return workload_dicts()
