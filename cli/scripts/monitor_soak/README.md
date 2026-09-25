# Local monitor soak

Start the local Postgres container and the backend with
`docs/plans/continuous_analysis/run_dev_server.sh`. Enable monitoring for
target `localdev` and keep the backend signed in to receive Jev verdicts.
The harness connects directly to `127.0.0.1:15433`, database `shopdb`.
It uses `rdst` for traffic (`LOCALDEV_PASSWORD`, default `rdst-localdev`)
and `postgres` for the temporary index (`LOCALDEV_ADMIN_PASSWORD`, default
`localdev`). No password is written to a file.

From `rdst/`:

    scripts/monitor_soak/run_soak.sh start
    scripts/monitor_soak/run_soak.sh status
    scripts/monitor_soak/run_soak.sh stop

The wrapper uses the existing uv environment in offline mode. It starts the
load generator, scenario runner, and checker. Logs, PID files, the atomic
`control.json`, and the timestamped `incidents.jsonl`
are under `docs/plans/continuous_analysis/logs/soak/`. `start` refuses when
any soak process is already running, including a process without a PID file.
`stop` also finds groups without PID files. It waits up to 30 seconds for
each group, then reports and sends SIGKILL if that group is still running.
The checker writes
`soak_report.md` and `soak_summary.json` in the plan directory every 30
seconds for the current run. Pass `--all-runs` to include older ledger runs.
Each cycle runs the six incidents once, in a random order with random gaps
(seeded by the run ID and cycle), after a warmup of 25 to 55 minutes; a
cycle lasts at least three hours. The jitter keeps RDST from learning the
soak's incidents as a routine schedule. The cycle repeats until stopped. SIGTERM ends active phases and restores
the original index state. An unclean SIGKILL may require running `start`
again so the runner can finish restoring the index; `baseline.json` keeps
the original index state across that restart.

The backend and soak wrapper both default to a 600 second development judge
interval. Set `RDST_MONITOR_JUDGE_INTERVAL_S` for both processes when changing
that cadence. The table slowdown phase lasts at least one judge interval plus
a complete five-minute series window and one collector interval. It drops the
temporary `orders(total)` index while two steady query shapes use that index,
then restores it. The checker reads the interval and requires both affected
queries to reach the shortlist and receive qualifying verdicts.

The control file has `rate_multiplier`, `heavy_enabled`, `heavy_cycle`,
`heavy_run_id`, and `steady_enabled`. The heavy phase uses
the run ID and cycle to give pg_stat_statements a new query shape each time.
It can be edited while the load generator runs; write
it via an atomic rename to avoid a partial read. The incident runner owns
these values during a soak. The checker accepts `--api`, `--data-dir`, and
`--judge-interval-s`.
It reports sign-in failures as `auth_required`, since they have no Jev
verdict, and never counts one as a passing assertion.
