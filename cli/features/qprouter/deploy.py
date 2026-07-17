"""Deploy an SQP + standalone QueryPilot data plane via plain `docker run`.

Models the `rdst cache deploy` mechanism (shared/deploy/local_docker.py): one
`docker run` per container, `--add-host=host.docker.internal:host-gateway` so the
containers reach each other through the host's published ports (no compose file,
no user-defined network), find-available ports so a second deploy can't collide.

Four containers make the plane:
  Postgres  (the data)         -> published on pg_port
  ReadySet  (cache engine)     -> UPSTREAM = host.docker.internal:pg_port
  SQP       (the router)       -> primary pool = host:pg_port, readyset pool = host:rs_port
  QP cron   (control loop)     -> calls SQP admin API and ReadySet

This is reusable: the demo is the first consumer, but the same primitives back a
future `cache deploy` that puts squeepy in front of ReadySet.
"""

from __future__ import annotations

import os
import socket
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

READYSET_IMAGE = "public.ecr.aws/readyset/readyset:latest-stable"
SQP_IMAGE = "public.ecr.aws/i9r8k6s4/sqp:20260709-1722"
QP_CRON_IMAGE = (
    "public.ecr.aws/i9r8k6s4/sqp-qp-cron:demo"
    "@sha256:b4fbf9a47928e570eacb774f416ffe63fb9625328c392199d787afdc48d92cd7"
)
# Base image the pre-baked Orders image is built FROM (build time only; never
# pulled at runtime).
POSTGRES_IMAGE = "public.ecr.aws/docker/library/postgres:16"
# Pre-baked Postgres: the Orders dataset is generated at image-build time, so a
# user's machine only ever DOWNLOADS this image — it never generates data. Ready
# in under a second. Public and anonymously pullable (no login of any kind).
BAKED_PG_IMAGE = os.environ.get(
    "QPDEMO_BAKED_PG_IMAGE",
    "public.ecr.aws/i9r8k6s4/qpdemo-orders-pg:v1",
)

# The four pinned images the demo data plane runs (all public, anonymous pull).
# `run`/`create` always use `--pull=never`; provisioning explicitly pulls any
# that are missing first. Postgres ships pre-built — there is no seed path.
PINNED_IMAGES: dict[str, str] = {
    "pg": BAKED_PG_IMAGE,
    "readyset": READYSET_IMAGE,
    "sqp": SQP_IMAGE,
    "qp-cron": QP_CRON_IMAGE,
}

# Per-container resource caps, fixed at a laptop floor. The demo is designed to
# run identically on a small machine (~2 cores / 2 GB available to Docker), so
# the numbers are consistent on every system rather than scaling with the host.
# The stability lever is the OFFERED LOAD (few workers, see load_driver), not the
# resource ceiling: a small workload stays comfortably within these caps, and the
# caps stop any container from monopolizing the machine. `--cpus` is the ceiling
# that matters; memory is generous so a cache is never OOM-killed mid-demo. All
# overridable via env for tuning without a code change.
RESOURCE_LIMITS: dict[str, dict[str, str]] = {
    "pg": {
        "cpus": os.environ.get("QPDEMO_PG_CPUS", "2"),
        "memory": os.environ.get("QPDEMO_PG_MEM", "2g"),
    },
    "readyset": {
        "cpus": os.environ.get("QPDEMO_READYSET_CPUS", "2"),
        "memory": os.environ.get("QPDEMO_READYSET_MEM", "2g"),
    },
    "sqp": {
        "cpus": os.environ.get("QPDEMO_SQP_CPUS", "1"),
        "memory": os.environ.get("QPDEMO_SQP_MEM", "512m"),
    },
    "qp-cron": {
        "cpus": os.environ.get("QPDEMO_QP_CRON_CPUS", "0.5"),
        "memory": os.environ.get("QPDEMO_QP_CRON_MEM", "256m"),
    },
}


def _limit_flags(component: str) -> list[str]:
    lim = RESOURCE_LIMITS[component]
    return ["--cpus", lim["cpus"], "--memory", lim["memory"]]


# Base ports; each is bumped to the next free one at deploy time.
BASE_PORTS = {
    "pg": 5432,
    "readyset": 5433,
    "readyset_metrics": 6034,
    "sqp": 6432,
    "metrics": 9090,
}


@dataclass
class Ports:
    pg: int
    readyset: int
    readyset_metrics: int
    sqp: int
    metrics: int

    @property
    def admin(self) -> int:  # SQP admin API auto-binds at metrics + 1
        return self.metrics + 1

    def as_dict(self) -> dict:
        d = asdict(self)
        d["admin"] = self.admin
        return d


def _port_free(port: int) -> bool:
    """Whether Docker could publish `port` on this host.

    Probes a wildcard bind on BOTH stacks with no SO_REUSEADDR: on
    macOS/BSD, a reuse-enabled loopback bind succeeds even while another
    process (a local Postgres, another engine's port forwarder) holds the
    port on 0.0.0.0 or ::, which made the demo pick busy ports on Macs and
    fail at container start."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s4:
        try:
            s4.bind(("", port))
        except OSError:
            return False
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s6:
            s6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            try:
                s6.bind(("::", port))
            except OSError:
                return False
    except OSError:
        # Host without IPv6 support: the IPv4 probe already answered.
        pass
    return True


def _next_free(base: int, taken: set[int]) -> int:
    p = base
    # metrics needs its +1 admin sibling free too; harmless for the others.
    # Bounded so an exhausted range raises a friendly error instead of looping
    # off the end of the port space (bind past 65535 would raise OverflowError).
    while p <= 65534 and (p in taken or not _port_free(p) or not _port_free(p + 1)):
        p += 1
    if p > 65534:
        raise RuntimeError(
            f"No free TCP port available at or above {base}. Close some listeners "
            "or stop other containers, then retry."
        )
    taken.add(p)
    return p


# docker/engine error signatures that mean "the host port was taken after
# all" - the probe can race another process, and some engines leak bindings.
_PORT_CONFLICT_MARKERS = (
    "port is already allocated",
    "address already in use",
    "failed programming external connectivity",
    "failed to set up container networking",
)


def is_port_conflict(error_text: str) -> bool:
    """Whether a container-start error is a host-port conflict (retry on new
    ports) rather than a real failure."""
    text = (error_text or "").lower()
    return any(marker in text for marker in _PORT_CONFLICT_MARKERS)


def allocate_ports(exclude: set[int] | None = None) -> Ports:
    """Find a free, non-colliding port for each component (increment on
    conflict). `exclude` blacklists ports a previous attempt already failed
    on."""
    taken: set[int] = set(exclude or ())
    return Ports(
        pg=_next_free(BASE_PORTS["pg"], taken),
        readyset=_next_free(BASE_PORTS["readyset"], taken),
        readyset_metrics=_next_free(BASE_PORTS["readyset_metrics"], taken),
        sqp=_next_free(BASE_PORTS["sqp"], taken),
        metrics=_next_free(BASE_PORTS["metrics"], taken),
    )


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def image_present(image: str, runner=_run) -> bool:
    """True when `image` is already in the local docker cache (no network)."""
    return runner(["docker", "image", "inspect", image], timeout=30).returncode == 0


def pull_image(image: str, runner=_run) -> subprocess.CompletedProcess:
    """Explicitly pull `image`. The only place the demo reaches the registry."""
    return runner(["docker", "pull", image], timeout=1800)


def deploy_postgres_baked(name: str, port: int) -> subprocess.CompletedProcess:
    """Run the pre-baked Orders image: credentials and data ship in the image,
    runtime flags ship in its CMD — only the name and port vary."""
    return _run([
        "docker", "run", "--pull=never", "-d", "--name", name, "--restart=unless-stopped",
        "--shm-size=1g",
        *_limit_flags("pg"),
        "-p", f"{port}:5432",
        BAKED_PG_IMAGE,
    ])


def deploy_readyset(name: str, port: int, metrics_port: int, pg_port: int,
                    creds: dict) -> subprocess.CompletedProcess:
    db = (f"postgresql://{creds['user']}:{creds['password']}"
          f"@host.docker.internal:{pg_port}/{creds['database']}")
    return _run([
        "docker", "run", "--pull=never", "-d", "--name", name, "--restart=unless-stopped",
        "--add-host=host.docker.internal:host-gateway",
        *_limit_flags("readyset"),
        "-e", f"UPSTREAM_DB_URL={db}",
        "-e", f"LISTEN_ADDRESS=0.0.0.0:{port}",
        "-e", "QUERY_LOG_MODE=enabled",
        "-e", "PROMETHEUS_METRICS=true",
        "-e", f"METRICS_ADDRESS=0.0.0.0:{metrics_port}",
        "-e", "DISABLE_TELEMETRY=true",
        "-e", "ALLOW_UNAUTHENTICATED_CONNECTIONS=true",
        # Shallow caches default to a 10s TTL, so every cache re-runs upstream
        # every few seconds; with ~20 caches created together those refreshes
        # land in lockstep and the ReadySet throughput line sawtooths. A 3-minute
        # TTL makes refreshes rare and the line steady for the length of a demo.
        "-e", f"DEFAULT_TTL_MS={os.environ.get('QPDEMO_SHALLOW_TTL_MS', '180000')}",
        "-p", f"{port}:{port}",
        "-p", f"{metrics_port}:{metrics_port}",
        READYSET_IMAGE,
    ])


def deploy_sqp(name: str, ports: Ports, config_path: Path, denylist_path: Path
               ) -> subprocess.CompletedProcess:
    return _run([
        "docker", "run", "--pull=never", "-d", "--name", name, "--restart=unless-stopped",
        "--add-host=host.docker.internal:host-gateway",
        *_limit_flags("sqp"),
        "-v", f"{config_path}:/etc/sqp/sqp.toml:ro",
        "-v", f"{denylist_path}:/etc/sqp/denylist:ro",
        "-p", f"{ports.sqp}:{ports.sqp}",
        "-p", f"{ports.metrics}:{ports.metrics}",
        "-p", f"{ports.admin}:{ports.admin}",
        SQP_IMAGE,
    ])


def deploy_qp_cron(name: str, config_dir: Path, fast_crontab: bool = True
                   ) -> subprocess.CompletedProcess:
    """Create the standalone QueryPilot cron container in the stopped state."""
    cmd = [
        "docker", "create", "--pull=never", "--name", name, "--restart=unless-stopped",
        "--add-host=host.docker.internal:host-gateway",
        *_limit_flags("qp-cron"),
        "-v", f"{config_dir}:/etc/sqp:ro",
        QP_CRON_IMAGE,
    ]
    if fast_crontab:
        cmd.append("/etc/sqp/crontab")
    return _run(cmd)


def write_sqp_config(out_dir: Path, ports: Ports, pg_port: int, rs_port: int,
                     api_key: str, creds: dict) -> tuple[Path, Path]:
    """Render sqp.toml with resolved ports + host.docker.internal backends.

    Returns (config_path, denylist_path). The embedded QueryPilot plugin is
    explicitly disabled; the demo uses the standalone qp-cron container instead.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    denylist = out_dir / "denylist"
    denylist.write_text("")
    cfg = out_dir / "sqp.toml"
    cfg.write_text(f"""[general]
pool_mode = "transaction"
worker_threads = 0

[[listeners]]
protocol = "postgres"
bind = "0.0.0.0:{ports.sqp}"
tls_mode = "disable"

[pools.primary]
role = "primary"
min_connections = 2
max_connections = 50
username = "{creds['user']}"
password = "{creds['password']}"
database = "{creds['database']}"
backends = [{{ host = "host.docker.internal", port = {pg_port}, weight = 1 }}]

[pools.readyset]
role = "primary"
min_connections = 1
max_connections = 25
username = "{creds['user']}"
password = "{creds['password']}"
database = "{creds['database']}"
backends = [{{ host = "host.docker.internal", port = {rs_port}, weight = 1 }}]

[routing]
default_pool = "primary"
read_pool = "primary"
write_pool = "primary"

[metrics]
enabled = true
bind = "0.0.0.0:{ports.metrics}"

[logging]
level = "info"
format = "pretty"

[plugins.query_pilot]
enabled = false
""")
    return cfg, denylist


def write_qp_config(out_dir: Path, admin_port: int, readyset_pg_port: int, creds: dict,
                    query_discovery_mode: str = "count_star",
                    number_of_queries: int = 10) -> tuple[Path, Path, Path]:
    """Render the standalone QueryPilot config directory mounted at /etc/sqp."""
    if query_discovery_mode not in {"count_star", "sum_time"}:
        raise ValueError(f"unsupported query_discovery_mode: {query_discovery_mode}")
    if number_of_queries < 1 or number_of_queries > 40:
        raise ValueError("number_of_queries must be between 1 and 40")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o755)
    denylist = out_dir / "denylist"
    denylist.write_text("", newline="\n")
    denylist.chmod(0o644)
    crontab = out_dir / "crontab"
    crontab.write_text(
        "*/15 * * * * * * /usr/bin/sqp-query-pilot --config /etc/sqp/qp.cnf\n",
        newline="\n",
    )
    crontab.chmod(0o644)
    cfg = out_dir / "qp.cnf"
    cfg.write_text(f"""backend_type = "sqp"
database_type = "postgresql"
sqp_api_url = "http://host.docker.internal:{admin_port}"
sqp_readyset_pool = "readyset"
sqp_readyset_servers = [
    {{ host = "host.docker.internal", port = {readyset_pg_port} }},
]

warmup_time_s = 0
operation_health_check = true
operation_query_discovery = true
operation_rules_from_caches = false
operation_proxysql_cache = false
number_of_queries = {number_of_queries}
query_discovery_mode = "{query_discovery_mode}"
query_discovery_min_execution = 5
query_discovery_wait_s = 0
log_verbosity = "debug"
denylist_path = "/etc/sqp/denylist"

[[user]]
readyset_user = "{creds['user']}"
readyset_password = "{creds['password']}"
readyset_database = "{creds['database']}"
""", newline="\n")
    cfg.chmod(0o644)
    return cfg, denylist, crontab


def remove_containers(names: list[str]) -> None:
    for name in names:
        r = _run(["docker", "rm", "-f", "-v", name], timeout=60)
        if r.returncode != 0 and "No such container" not in r.stderr:
            raise RuntimeError(f"docker rm -f -v {name} failed: {r.stderr[:300]}")
