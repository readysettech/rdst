# Onboarding and agent connect telemetry

Server-side PostHog events for adding a database, turning on monitoring and connecting a
coding agent. They go through `shared/product_telemetry.py`, so they follow the same gating
(RDST_TELEMETRY, the config opt-out, RDST_TESTING) and the same privacy rules as every other
product event: no hostnames, users, database or target names, URLs, file paths or SQL.
Every event also carries the surface properties (`surface`, `app_version`, ...).

## Shared values

- `via`: how the database was added or checked. From the `X-RDST-Via` request header, one of
  `url`, `fields`, `provider`, `agent`, `cli`, `mcp`; null when the request didn't say
  (the app's own dialogs without the header). MCP tool calls are always `mcp`.
- `engine`: `postgresql`, `mysql`, `mariadb` or `unknown`.
- `error_class` for connections: `auth_failed`, `host_unreachable`, `timeout`, `tls`,
  `db_not_found`, `permission_denied`, `ssh_failed`, `unknown`.
- `attempt_id`: ties one copied agent prompt to the agent's connection and the tool calls
  after it. Each `POST /api/agent-connect/prompt` issues a random attempt nonce, and the
  prompt's MCP URL carries it as a query parameter (`<url>/mcp?a=<nonce>`), so it survives
  `claude mcp add` and curl alike. attempt_id is the first 16 hex characters of SHA-256 of
  `rdst-agent-attempt-v1:<nonce>`, computed on the server (and the same way by the app for
  `agent_prompt_shown`); the nonce itself is never sent. A /mcp request naming a prompt from
  the last 2 hours joins that prompt's attempt; a request without one (an agent registered
  from Settings, or from before a restart) joins the latest prompt while no agent has
  answered it yet. Tool calls more than 2 hours after their prompt was issued carry null.
- Prerequisite `check` ids: PostgreSQL `pg_stat_statements_preload`, `pg_stat_statements`,
  `read_all_stats`, `query_text_size`, `visibility_probe`; MySQL `performance_schema`,
  `read_all_stats`, `statement_consumers`, `statement_history`, `query_text_size`,
  `visibility_probe`. Anything else is sent as `other`.

## agent_connect_step

One step of a coding agent connecting through the prompt from the Add database dialog.
Sent at most once per (attempt_id, step, tool, ok) per RDST process.

| step | When it fires | Properties |
| --- | --- | --- |
| `prompt_issued` | `POST /api/agent-connect/prompt` returns a prompt with a new attempt nonce | `attempt_id` |
| `connected` | The first `/mcp` request after a prompt was issued (the UI's "Agent connected") | `attempt_id`, `ok: true` |
| `tool_called` | An MCP `tools/call` on `/mcp` finished | `attempt_id`, `tool` (an `rdst_*` tool name), `ok`, `error_class` when `ok` is false |

`tool_called` counts `ok: false` when the tool returned an MCP error, when
`rdst_test_database` reported a failed connection (`ok: false` in its result), or when the
result says `ready_to_monitor: false`. `error_class` is a connection class from the list
above or one of `needs_setup` (query statistics aren't available: monitoring is blocked),
`invalid_arguments`, `unknown_target`, `tool_error`, `unexpected`.

Funnel: prompt_issued -> connected -> tool_called(rdst_add_database, ok) ->
tool_called(rdst_check_prerequisites or rdst_test_database, ok) ->
tool_called(rdst_enable_monitoring, ok). A `needs_setup` followed later by an `ok: true` for
the same tool and attempt means the user fixed the prerequisite.

## database_add_prereq_result

One monitoring prerequisite's outcome. Fires from the agent path (`rdst_check_prerequisites`,
`rdst_test_database`, a refused `rdst_enable_monitoring`, and `POST
/api/agent-connect/recheck`) and from the manual path
(`GET /api/configure/targets/{name}/prerequisites`, which the Turn on monitoring dialog
polls), so both paths compare check by check. Sent once per check and outcome per attempt
(agent path) or per database for the life of the process (manual path), so polling doesn't
repeat it; a check that later passes sends again with `ok: true`.

Properties: `engine`, `check`, `ok`, `via`, and `attempt_id` on the agent path.

## Other onboarding events

| Event | When it fires | Properties |
| --- | --- | --- |
| `database_add_test_completed` | A connection test from the Add database flow finished | `engine`, `via`, `ok`, `error_class` when it failed |
| `database_add_saved` | `POST /api/configure/targets` saved a database | `engine`, `via`, `is_first_database` |
| `database_add_failed` | Saving a database failed | `engine`, `via`, `error_class` |
| `monitoring_enable_failed` | Turning on monitoring was refused (the 409/422 paths) | `reason`: `unavailable`, `local_limit`, `invalid`, `unknown` |
| `account_anonymous_created` | A new anonymous session was created for hosted AI | `trial_days`, `machine_age_days` |
| `account_linked` | An anonymous user signed in | `from_anonymous`, `days_since_first_seen` |
| `signin_prompt_state_changed` | The sign-in prompt moved to a new state | `state`: `none`, `soft`, `budget`, `required` |

## The agent-connect progress feed (not telemetry)

`GET /api/agent-connect/events` (local only) streams the same steps to the open app as SSE
frames of `{step, target?, message?, ts, ...}`. `step` is `connected` (the first `/mcp`
request after a prompt), `database_added`,
`tested` (prerequisites pass), `needs_setup`, `monitoring_enabled` or `error`. A
`needs_setup` frame carries `check: "query_stats"`, `source`, `ready_to_monitor: false`,
`blocking_reason`, `next_step`, `instructions`, `sql`, `needs_restart` and `prerequisites`
(the same result `GET /api/configure/targets/{name}/prerequisites` returns, each check with
`fix_kind`). `POST /api/agent-connect/recheck {target}` runs the checks again and publishes
`tested` or `needs_setup`.
