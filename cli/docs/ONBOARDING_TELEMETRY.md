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
| `search_started` | RDST started a coding agent searching the user's recent sessions for databases | `tool` (`rdst_search_claude` or `rdst_search_codex`) |
| `search_finished` | That search ended | `tool`, `ok` (true when the agent proposed databases, even none) |
| `setup_started` | RDST resumed that agent's session to set up the databases the user picked | `tool` (`rdst_setup_claude` or `rdst_setup_codex`) |
| `setup_finished` | That setup ended | `tool`, `ok` (true when the agent added a database) |
| `scan_finished` | RDST looked for databases on this computer itself, without an agent | `ok` (true when it found one) |
| `app_opened` | RDST opened a new conversation in the user's Claude app with the paste prompt typed in | `tool` (`rdst_app_claude`) |
| `reply_sent` | The user answered the agent's question in RDST, continuing its conversation | `tool` (`rdst_setup_claude`...) |
| `agent_registered` | The user connected a coding agent to RDST from a database's page (RDST added itself to the agent's MCP servers) | `tool` (`rdst_connect_claude`...) |
| `found_connected` | RDST added a database its scan found by itself, reading the URL or password where the scan saw it | `tool` (`rdst_connect_supabase`...), `ok` (whether RDST reached it) |

`tool_called` counts `ok: false` when the tool returned an MCP error, when
`rdst_test_database` reported a failed connection (`ok: false` in its result), or when the
result says `ready_to_monitor: false`. `error_class` is a connection class from the list
above or one of `needs_setup` (query statistics aren't available: monitoring is blocked),
`awaiting_user` (the user turns monitoring on in the app the first time), `signin_required`
(the same, and the user isn't signed in, so the app asks them to sign up or sign in),
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

## Turning on monitoring signed out

Monitoring runs on a Readyset account, so the Turn on monitoring dialog has a signed-out
user sign up (Google first, or email) before monitoring turns on; there's no way past it
without an account. The app sends these events; `monitoring_enabled` comes from the server.

| Event | When it fires | Properties |
| --- | --- | --- |
| `monitoring_dialog_shown` | The Turn on monitoring dialog opened | `via` (how the database arrived, or `page`) |
| `monitoring_signin_gate_shown` | The dialog asked a signed-out user to sign in | `via` |
| `monitoring_signin_gate_choice` | The user went on | `choice`: `sign_in` (chose Get started with Readyset), `signed_in_after` (signed up or in, so monitoring turned on); `without_ai` only from builds that offered it |
| `signin_started`, `signin_completed` | A sign-in began or finished | `method` (started only), `source`: `monitoring_gate` or `sign_in_page`, absent elsewhere |
| `monitoring_enabled` (server) | Monitoring turned on for a database | `ai_monitoring`: `on` (signed in, AI running), `signed_out` (turned on without AI), `local_only` (the account's AI databases are full), `pending` (signed in, the Key Service hasn't answered) |

Funnel: monitoring_dialog_shown -> monitoring_signin_gate_shown ->
monitoring_signin_gate_choice(sign_in) -> signin_completed(source monitoring_gate) ->
monitoring_signin_gate_choice(signed_in_after) -> monitoring_enabled(ai_monitoring on).
`ai_monitoring: signed_out` now only comes from older builds or the CLI.

## Finding a database

The Add a database dialog's find view. Every property is a count, a kind or an id: never a
path, host or database name. `run_id` joins the server's `agent_connect_step` events (its
`attempt_id`) for that scan, search or setup.

| Event | When it fires | Properties |
| --- | --- | --- |
| `find_view_shown` | The find view opened | `agents` (how many agents RDST can run), `agent_keys` (`claude:local,codex:wsl`...), `apps` (desktop apps installed) |
| `find_option_chosen` | A way in was taken: the scan RDST starts on opening, or what the user picked | `option`: `scan` (`started`: `auto` when RDST looks as the view opens, `click` when the user looks again), `sign_in` (`provider`), `paste_prompt`, `open_app` (`app`), `manual` |
| `find_scan_finished` | RDST's own scan finished, whether or not it found anything | `run_id`, `state` (`done`, or `failed` when the scan broke), `found` (0 when nothing turned up), `already_added` (already in RDST), `hosted` (on Supabase, Neon, DigitalOcean or AWS), `running` (running right now: a live container or a server answering on a port), `from_projects` (named in a project's files), `projects_with_databases` (project folders read that use at least one; compare with `folders`), `seconds`, `projects`, `docker`, `ports`, `clients` (found by each method), `folders` (project folders read), `failed` (methods that couldn't look) |
| `find_database_picked` | The user picked a found database | `source` (`scan` or `search`), `provider`, `environment`, `has_secret`, `found_by` (kinds of evidence: `env`, `compose`, `docker`, `port`, `saved`) |
| `find_action` | The user acted on the pick | `action`: `agent_setup` (`agent`), `add_self`, `fill_in`, `sign_in` (`provider`); `source` |
| `find_agent_search` | An agent's deeper search started or finished | `stage`, `run_id`, `agent`; when finished `state`, `found`, `seconds` |
| `find_agent_setup_finished` | An agent's setup of the pick ended | `run_id`, `agent`, `state`, `reached` (`starting`, `adding`, `getting_ready`, `awaiting_user`, `monitoring`), `seconds`, `tool_steps` |
| `find_continue_in_app` | The agent's conversation opened in its desktop app, or in a terminal | `run_id`, `app` (`claude`, `codex` or `terminal`), `kind` |
| `agent_prompt_copied` | The paste prompt was copied | `attempt_id` |
| `find_view_closed` | The find view closed (the dialog closed or moved on) | `stage` where the user stood: `choosing`, `scanning`, `picking`, `agent_searching`, `setting_up`, `awaiting_user`, `setup_done`, `setup_failed`; `found`, `used_agent`, `seconds_open` |
| `agent_help` | Fix with Claude (or Codex) on a database that isn't ready to monitor started or finished | `stage`, `where` (`turn_on`), `run_id`, `agent`; when finished `state`, `seconds`, `tool_steps` |
| `agent_question_answered` | The agent stopped to ask a question (rdst_ask_user) and the user answered it in RDST | `run_id` (the run the answer started), `agent`, `chose` (`option` or `other`), `options` (how many choices it offered) |

Funnel: find_view_shown -> find_option_chosen(scan) -> find_scan_finished ->
find_database_picked -> find_action(agent_setup) -> find_agent_setup_finished(reached
awaiting_user) -> monitoring_signin_gate_shown or monitoring_enabled. The paste path runs
find_option_chosen(paste_prompt or open_app) -> agent_prompt_copied -> agent_connect_step
(connected, tool_called...) -> database_add_abandoned or monitoring_enabled.

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

## The #rdst-feedback Slack messages

PostHog's "Slack" destination in the RDST project posts installs, sign-ups and first
runs to #rdst-feedback. Its code is kept in `docs/posthog/slack-destination.hog`; edit it
there and paste it into the destination, so the two stay the same. Each message ends with
one line:

- **Where they are:** city and country, from the event's GeoIP (the app sends events
  with GeoIP on) or the person's.
- **Where they came from:** for an `installation`, the destination looks in the
  readyset.io project (425754) for an `rdst_download_started` from the same IP in the
  two days before the install, and takes that visit's `utm_source`, `utm_campaign` and
  whether it carried a Google Ads click (`gclid`). With no UTM tag it names the referring
  site ("Reddit (referral)", "X (referral)", "YouTube (referral)"). It's a best-effort
  match: a shared office network, VPN or hotspot can mismatch, and nothing matching means
  the part is left off. A match is saved on the device's person, once, as
  `install_source`, `install_campaign` and `install_ad_click`, so later messages and any
  chart in the RDST project can split by it.
- **See everything this device did:** the person's Events tab.

The destination keeps a read-only PostHog key (secret) for the readyset.io lookup, and
the RDST project's public token for saving the source.


## `database_scan_finished`

Sent when RDST's own scan for databases (the start of Search for databases)
finishes. Counts only: no database names, hosts, users or paths leave the
machine.

| Property | Meaning |
|---|---|
| `seconds` | How long the scan took |
| `found` | Databases in the list |
| `methods` | Findings per method (`projects`, `history`, `docker`, `ports`, `clients`) |
| `providers`, `engines`, `environments` | Counts by provider (`supabase`, `docker`...), engine and environment |
| `with_password` | Databases RDST can add by itself, with the password on disk |
| `incomplete` | Databases a project uses without saying where, for the agent to finish |
| `from_history` | Databases found only in agent sessions or shell history |
| `with_secret_manager` | Databases whose project keeps secrets in a manager like Infisical |
| `already_saved` | Databases already in RDST |
| `waiting_for_pick` | Projects listed by name until picked (a Mac folder like Documents) |
