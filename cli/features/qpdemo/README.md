# QueryPilot demo — architecture

Web-only feature (RDST Web `/demo`). User-facing guide:
`rdst/docs/QUERYPILOT_DEMO.md`.

## Topology

Four local docker containers, provisioned with individual
`docker run`/`create` (never compose), mirroring `rdst cache
deploy`:

| Container | Role |
|---|---|
| `qpdemo-pg` | Postgres 16 with a generated Orders dataset (~1.5M orders; deterministic seed at first boot) |
| `qpdemo-readyset` | Readyset in shallow-cache mode |
| `qpdemo-sqp` | SQP router; caching decisions route to Readyset, everything else passes through |
| `qpdemo-qp-cron` | Standalone QueryPilot selector on a short cron cadence |

Host ports are chosen by live bind tests at provision time so
existing deployments (including `cache deploy` stacks) are never
disturbed. Containers reach each other via published localhost
ports and `host.docker.internal`. Missing images are pulled
explicitly during provisioning; container starts use
`--pull=never` so nothing downloads implicitly.

## Behavior map

- `service.py` — orchestration: provision (image pull, health
  gating), dual-path load lifecycle, QueryPilot on/off (cron
  container start/stop; enabling always reopens with the
  most-frequent policy and the default cache budget), policy
  switch (drops QueryPilot-owned caches, keeps manual ones,
  resets comparison counters), container supervisor
  (self-healing), one-hour auto-teardown, telemetry events.
- `load_driver.py` — tiered worker pools send one weighted
  workload to both paths simultaneously; per-query, per-path
  windowed stats.
- `workload.py` — the query catalog (tiers, weights, denylist
  flags) engineered so the two policies select disjoint sets.
- `../qprouter/` — reusable core: deploy, SQP admin client,
  Readyset client, pattern/reason engine. Decision reasons are
  recomputed from the same digest statistics QueryPilot ranks
  with, so every receipt shown in the UI is the selector's own
  arithmetic.
- State lives in a `[demo]` section of `~/.rdst/config.toml`
  plus a mounted config dir for the selector; teardown removes
  containers together with their volumes and clears state.

## Tests

- Fast units: `tests/test_*_unit.py` (no docker).
- Docker-backed: `tests/test_lifecycle.py`,
  `tests/test_integration.py` — provision through teardown on a
  single session-scoped stack, including selection correctness
  per policy and a relative throughput-lift assertion. CI runs
  these in the `RDST demo tests` Buildkite step alongside the
  web tests.
