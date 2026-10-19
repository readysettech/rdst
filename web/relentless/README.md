# Relentless UI tester

An adversarial Claude agent that tests the RDST web and desktop apps the way
`rdst/skills/relentless-tester` tests the CLI. The agent explores; the driver
and supervisor own isolation, evidence, cleanup, and the verdict.

## Pieces

- `driver/`: a daemon that owns one running application and serves commands
  over loopback HTTP. Two backends share every command:
  - `browser`: the production bundle and FastAPI app
    (`tests.web_e2e.full_stack_server`) in headless Chromium.
  - `desktop`: a packaged Electron build with its bundled sidecar, driven
    through Playwright's Electron support. Nothing is bypassed. It runs
    headless (Chromium's headless platform, no display variables), so no
    window appears on the machine running it; pass `--headed` to watch.
    Native OS dialogs cannot open in headless runs.
- `cli.ts`: `rt`, the command-line client the agent uses.
- `run.ts`: the supervisor. It runs one area with a Claude agent that may only
  call `rt` and read its own run directory, then decides the verdict.
- `batch.ts`: runs many areas and flows against a desktop build in parallel,
  optionally against a baseline build too, and labels each finding.
- `selftest.ts`: a scripted scenario (no agent) that proves the driver works
  in a given mode.
- `to-spec.ts`: drafts a Playwright spec from a run's commands up to a
  finding, the first step in turning it into a deterministic regression.
- `prompts/tester.md`, `areas/*.md`: the agent's instructions and test areas.

Each run gets its own directory with an isolated `HOME` (RDST config,
Electron profile, keyring), its own ports, and a marker in every process it
starts, so parallel runs cannot collide and cleanup can prove nothing
survived. A run directory holds `events.jsonl` (console and page errors,
failed requests, API responses, SSE summaries), `backend.log`, screenshots,
Playwright traces, `commands.jsonl` (every driver command with its duration),
`findings.jsonl`, `result.json` from the agent, and
`verdict.json` from the supervisor.

## Prerequisites

Linux with `dbus-daemon`, `dbus-send`, and `gnome-keyring-daemon`. From
`web-apps/`:

```bash
pnpm install
pnpm --filter rdst-web build        # browser mode serves apps/rdst/dist
docker compose -f apps/rdst/e2e/docker-compose.relentless.yml \
  up -d --build --wait postgres mysql bastion
```

Desktop mode needs a packaged build. An unpacked Linux build is enough:

```bash
cd apps/rdst-desktop
pnpm build:web && pnpm build:sidecar && pnpm build
pnpm exec electron-builder --linux --x64 --dir --publish never
# executable: apps/rdst-desktop/dist/linux-unpacked/rdst-desktop
```

## Running

From `web-apps/apps/rdst/`:

```bash
# Driver proof, no agent
node relentless/selftest.ts --mode browser
node relentless/selftest.ts --mode desktop \
  --desktop-exec ../rdst-desktop/dist/linux-unpacked/rdst-desktop

# One agent area; an hour at most, with no spend or turn cap unless
# --budget or --max-turns sets one
node relentless/run.ts --area onboarding-connect --mode browser

# A mission written for one run, such as checking a list of findings
node relentless/run.ts --mission /tmp/verify.md --mode browser

# Drive an app by hand
export RDST_RELENTLESS_RUN_DIR=/tmp/rt-manual
node relentless/cli.ts start --mode browser
node relentless/cli.ts snapshot
node relentless/cli.ts stop
```

Nothing is faked. Browser mode runs `rdst web --ui dist`, the command a
person runs; desktop mode runs the packaged app. Each run has a real Secret
Service (gnome-keyring on a private D-Bus session) in place of the OS
keychain, so saved secrets behave as they do on a desktop without touching
yours. The private bus activates no services, so nothing on it can reach
your own keychain daemon. gnome-keyring can abort when a client disconnects
mid-call; the driver then restarts it on the same keyrings, records
`keychain_restarted` in `events.jsonl`, and logs the daemon to
`keyring.log`. The application starts with no AI credentials, as a new install does,
and every `ANTHROPIC_*` variable is removed from its environment.

AI runs through the real Anthropic API with a key you provide:

```bash
export RDST_RELENTLESS_ANTHROPIC_API_KEY_FILE=~/.config/rdst-relentless/anthropic-key
```

The driver types it into the app's own key dialog when the agent calls
`rt fill-secret TARGET anthropic-api-key`. The agent never sees it: the
driver redacts it from every reply, from `events.jsonl`, and from what the
agent can read, and it records no Playwright trace in a run that can type it
(a trace keeps typed text and request bodies). Tokens the app is issued
while it runs, such as a signed-in account's access token, are redacted the
same way. The agent itself authenticates with your Claude login.

## Missions

### Monitoring

Four areas test continuous monitoring: `monitoring` (the Database page:
turning it on, readings, analysis, and reports), `actions` (the plan of fixes
across databases), `inbox` (alerts from real outages and every way of putting
one away), and `activity` (what RDST ran and is running). Their flows under `flows/monitoring/` follow one promise
each across pages: `local-lifecycle` keeps collecting with the window closed
and across restarts, `outage-alert` turns a paused database into one Inbox item
that clears on recovery, and `hosted-ai` analyzes on its own with
Readyset-hosted AI and no Anthropic key:

```bash
node relentless/run.ts \
  --flows monitoring/local-lifecycle,monitoring/outage-alert \
  --mode desktop --desktop-exec /absolute/path/to/rdst.AppImage
```

Monitoring reads on a schedule, so these runs wait for real readings after
real traffic; nothing seeds monitoring data or forces a reading. `hosted-ai`
needs the staging mail configuration below.

### Selecting flows

An area is a broad audit. A mission is narrower: `run.ts --flows ID,ID`
runs the chosen flows from `flows/<capability>/<flow>.md` in one bounded
run instead of an area, time-boxed from the flows' `Minutes:` estimates.
Each flow names its capability, areas, modes, the code it covers
(`Covers:`, planned when edited and suggested when a change only reaches it),
and hubs it covers only when edited (`Direct:`), then the mission, what must be
proved, and cleanup. The agent marks where it is with `rt phase ID
setup|action|observe|recover|cleanup`, which stamps every event with the
flow and phase, and records each outcome with `rt flow ID --status`. The
verdict fails when a flow failed, is blocked when one was blocked, and is
incomplete when one went unreported.

The selector chooses flows too: those a change edits, ranked by how much of
the change they cover, packed within `budget.mission_minutes`.
Changes classification sets aside, such as tests or package metadata,
trigger none. A flow's measured minutes replace its estimate once three
runs finished it.
The detector runs them as one mission, beside the change mission and the
areas. `evaluation/flow-labels.json` labels which
flows each past change needs, so a replay reports flow recall and flows
triggered for changes that need none.

### Change missions

A change mission probes what a change could break, with no area or flow
chosen: `run.ts --change BASE..HEAD` gives the agent every commit's
description from `BASE` to `HEAD`, the changed files, as much of the diff as
fits, and the UX map, and puts the whole diff in its run directory as
`change.diff`. A single revision is the change from its parent; git is used
where the checkout has it, jj otherwise. `--change-dir DIR` runs a change
`selector/change.ts --range BASE..HEAD --out DIR` already wrote.

On three past changes, change missions found twice as many problems the
change itself caused as the owning areas did, in a third of the time; the
areas found far more problems unrelated to the change. So a change mission
is the per-change default, and areas are the broad audit.

### Batches and baselines

`batch.ts` runs areas and flows against a desktop build on this machine,
several at once, each in its own Compose project with no host ports:

```bash
node relentless/batch.ts --build cl:16486 --baseline main \
  --areas monitoring,inbox --flows ask/read-only --name 16486
```

A build is a local AppImage or extracted executable inside the repository,
`buildkite:NUMBER[:FLAVOR]` from `readyset-rdst`, `cl:CHANGE[/PATCHSET]` (the
highest patchset by default), or `main`, its latest staging package from the
public update channel. Buildkite builds need an API token in
`BUILDKITE_API_TOKEN` or `~/.config/rdst-relentless/buildkite-token`. Keys come from that directory
as files, and the app uses the staging keyservice. Starts are spaced
(`--stagger-sec`, a minute by default) so parallel sign-ins stay under
staging Supabase's email limit.

With `--baseline`, every mission also runs on the baseline build. Findings
whose titles pair with a baseline finding are pre-existing; the rest go to a
verify mission on the baseline (`run.ts --mission`), and those it reproduces
are pre-existing too. `summary.md` in `test-results/batch/NAME/` labels each
finding pre-existing, new on the build, gone from it, or unverified when the
baseline's verify mission could not decide it.

The app holds its first-run pop-ups back under automation. Runs opt in, so
a new install opens as it does for a person; `--no-first-run-popups` on
`run.ts` and `batch.ts` leaves them closed.

## Environment

`docker-compose.relentless.yml` provides what a run can connect RDST to and
disturb:

- PostgreSQL on `127.0.0.1:15432` and MySQL on `127.0.0.1:13307`, both with
  the IMDb-style sample schema. Override with `RDST_E2E_DB_HOST`,
  `RDST_E2E_DB_PORT`, `RDST_E2E_DB_PASSWORD`, `RDST_RELENTLESS_MYSQL_HOST`,
  and `RDST_RELENTLESS_MYSQL_PORT`.
- An SSH jump host on `127.0.0.1:12222` (user `relentless`) in front of both,
  as `postgres:5432` and `mysql:3306`. Its throwaway key is generated at
  start into `test-results/relentless-ssh/`; the driver copies it into each
  run's `~/.ssh` with mode 600.

The agent reaches them through `rt`:

| Command | What it does |
|---|---|
| `rt db list` | Connection details for each database and the jump host |
| `rt db exec postgres\|mysql -- SQL` | Runs SQL on the database directly, as another application would |
| `rt db pause\|resume postgres\|mysql\|bastion` | Freezes the container, as an outage would; the driver resumes anything still paused when the run stops |
| `rt tab new\|switch\|close` | More tabs in browser mode; the desktop app has one window |
| `rt menu [PATH]` | Lists or clicks the desktop app's native menu, such as `"View > Actual Size"` |
| `rt mail address\|read\|open` | A real inbox for sign-up and report emails (see below) |
| `rt upload TARGET FILE` | Sets a file input to a file in the run directory, such as the fixtures below |
| `rt restart --offline\|--online` | Relaunches with the internet unreachable (a dead proxy; loopback and the databases still work), or reachable again |
| `rt deeplink URL` | Desktop: starts a second instance with an `rdst://` URL, as the OS does |
| `rt crash-renderer` | Crashes the renderer; the reply says whether the window recovered by itself |

Each run writes fixtures into its directory, listed by `rt db list`:
`targets.csv` (two good rows, one without a host, one duplicate) and
`queries.sql` (four statements, one broken).

`--home-from DIR` on `run.ts` and `rt start` copies an earlier run's `home/`
into the new run, keychain included, so an area can start past onboarding:

```bash
node relentless/run.ts --area home --mode desktop \
  --desktop-exec ... --home-from /tmp/rt-earlier/home
```

Actions on a snapshot ref (`e12`) are logged in `commands.jsonl` with the
role and name the ref pointed to, so `to-spec.ts` can replay them:

```bash
node relentless/to-spec.ts RUN_DIR --finding F-003 > e2e/draft.spec.ts
```

### Mail and Readyset accounts

Account sign-in and report emails need a real inbox and a keyservice that
sends to it. Both are opt-in:

```bash
export RDST_RELENTLESS_RESEND_API_KEY_FILE=~/.config/rdst-relentless/resend-key
export RDST_RELENTLESS_ATTESTATION_SECRET_FILE=~/.config/rdst-relentless/staging-attestation
export RDST_RELENTLESS_KEYSERVICE_URL=https://keyservice-staging.readyset.io
```

The Resend key is the full-access CI inbox key (`rdst/ci/e2e` in Secrets
Manager). The attestation secret is staging's (`rdst/keyservice/staging`,
`ATTESTATION_SECRET`): non-production keyservices do not accept the
client's built-in one. Both are redacted everywhere. The app gets them as
`RDST_KEYSERVICE_URL` and `RDST_ATTESTATION_SECRET`. The production
keyservice is refused, so runs never create production accounts; staging
sends no signup alerts. Without them, `rt status` reports mail as
unavailable and the prompt tells the agent not to sign up.

CI reads both secrets and signs in to the keyservice the build's binaries
name. A stack that changes `rdst/keyservice/` gets its own CL env
(`keyservice-cl<change>.readyset.io`), which runs the non-prod secrets
bundle, so its runs attest with `rdst/keyservice/dev01` and wait for the CL
env to deploy. Every other change, and the check of findings on main, uses
staging. A CL env throttles no sign-ins, so runs side by side do not lock
each other out.

To point a local run at a CL env, use its URL and dev01's
`ATTESTATION_SECRET`.

## CI

At the tip of a Gerrit stack, the "Relentless UI Tester" step runs without a
gate once `package-rdst-desktop-linux-cl` has built the Linux packages, whenever
the stack reaches an area. It plans the run
(`scripts/run-relentless-ci.sh plan`): it checks the choice the detector
made, records it against the selector's recommendation, and uploads one
job per mode and per part (the change mission, each flow, each area), so a
run takes as long as its slowest part. A job then checks the parts' P1/P2
findings on main's latest desktop package, from its public update channel,
with one verify mission (`verify.ts`), and a last job annotates the build
with all of them, marking the findings main also has. Each job unpacks that AppImage, so it tests the package
the build ships, and runs its part inside `e2e/docker-compose.relentless.yml`,
with the databases, jump host, and mail above. The app's key comes from the
`ANTHROPIC_API_KEY` secret and the agent's login from
`buildkite/rdst/claude-oauth-token`. The step removes each run's `home/`
(it holds the keychain), fails if any of its secrets appears anywhere in the
runs, and only then uploads them, so a job stopped first uploads none; it
then annotates the build with `report.ts`. It is soft-fail:
findings are leads until they become deterministic specs. The comment on the
change lists only the P1/P2 findings main does not also have, and says so
when the check on main could not decide.

The runner takes area names, `all`, or `none`; a blank or unknown name fails
the step before it spends anything. `RELENTLESS_UI_AREAS`,
`RELENTLESS_UI_FLOWS`, `RELENTLESS_UI_CHANGE`, `RELENTLESS_UI_MODES`,
`RELENTLESS_UI_BUDGET`, and `RELENTLESS_UI_TIMEOUT_MIN` override the
detector's choice, for a run by hand.

Every main build that builds the desktop package starts the release run
once the package is built: every area and every flow in desktop mode, one
job each
(`rdst/.buildkite/relentless_ui_step.sh`, which the change's step shares),
then a report job annotates the build and
posts each run's verdict and every P1 and P2 finding to `#builds-rdst`. The
jobs are soft-fail and outside the Release block's dependencies, so they
inform a release without gating it. Both runs' jobs and report come from
`relentless_ui_step.sh`, and the report from
`rdst/.buildkite/report_relentless_runs.sh`.

### Choosing areas for a change

A detector step (`rdst/.buildkite/detect_relentless_ui_areas.sh`) runs
`selector/select.ts` on the change's diff, annotates the build with the
areas it can affect and why, and starts the tester with the change mission,
the recommended flows, and the recommended areas, in desktop mode. It starts
nothing when no area is affected.

A Gerrit stack is tested once, at its tip. The detector asks Gerrit for the
CL's related changes; when an open CL at its current patchset sits above
this one, it annotates where the tip is and uploads nothing. At the tip,
the change is the whole stack, from the parent of its lowest open CL, so
selection and the change mission cover every CL in it. The detector writes the
change with `selector/change.ts` and the tester step downloads it, since the
tester's image has no git.

The selector itself is checked on every change that touches RDST, below the
tip too: `rdst/.buildkite/check_relentless_selector.sh` runs its tests, the
contract check, and `evaluation/report.ts --verify`, and fails the build. A
refactor that breaks selection fails there instead of quietly choosing
nothing.

Areas follow ownership. A `relentless.json` in a folder says which
capability the folder's code belongs to: `{"capability": "connections"}`. A
capability spanning several folders, such as a UI slice and its backend
features, has one in each, and exactly one of them also names the areas that
test it (for example `src/features/connections/relentless.json`); any may
name flows that exercise it. A manifest names no paths, so it moves with its
folder. A capability is code (connections, the model layer) and an area is a
tester mission (onboarding-connect, settings-targets), many to many. The
areas of
the capability owning a changed file are obligations: they are recommended
before any ranking, up to `budget.max_areas`, with the rest shown as
overflow. A file a capability owns is that capability's whatever its kind,
so a slice's assets and packaging follow it. A capability can be data or a
shared layer rather than a page: `rdst/features/query_registry` (query
history Queries and Home read), `rdst/shared/deploy` (the Readyset sandbox
Benchmarks and the demo run on) and `rdst/shared/llm_manager` (the model
layer) each declare the areas that test them. Code no capability owns takes
its users from the change's import and API graphs: the capabilities whose
owned code it reaches first, ranked by how many of its changed declarations
reach each and by how specifically each area's recorded runs request the
API routes it reaches. Code half of all capabilities use is broad, and code
no page or route reaches selects nothing. An area's entry pages own the code
they render, except its `shared_entries` in `areas.json` (the root layout, a
dialog any page opens): those are its own when they change, and the walk
passes through them to the pages that use them. A file the backend loads by
a path relative to its own code is part of the code that loads it.
Manifests are read from the selector's own checkout, so a replay judges old
trees by today's, with
`evaluation/history.json` resolving paths only older trees had; `--check`
fails on unknown areas or flows, patterns that match nothing, a file two
manifests name, and a history pattern that matches a current file.

Before that, changes are classified by `selector/rules.json`, which names
kinds of file rather than app code: build configuration, tests and docs,
the tester itself, and what the UI tester does not cover (the CLI, dev
tooling, the keyservice, /scan and /guards). License, ignore, and
placeholder files select nothing. A dependency manifest is read: a runtime
dependency, export, build tool, or packaging change is broad; metadata and
test tooling are not; a lockfile follows its manifests. Changes to what ships
(runtime dependencies, resolved versions) and to the app's entry point run
the `infra` areas (the app starting, its shell) before anything they reach,
not the smoke set: their failures show as the app not starting.

The graphs also suggest flows.
`selector/frontend_graph.ts` builds a declaration-level graph of the
frontend, desktop, and shared UI packages with TypeScript's parser, and
`selector/backend_graph.py` does the same for rdst's Python with `ast`,
splitting classes into methods and reading the mounted route table. A
backend change reaches the routes whose handlers depend on it, and each
route seeds the frontend code that requests it. A flow covering code the
change reaches but does not edit is listed as suggested, outside the
mission. Findings from missions of suggested flows are counted separately
by `metrics.ts`; if they find nothing the edited flows would not, the graphs
can go.

Runs are evidence too. `selector/ingest-evidence.ts RUN_DIRS...` folds run
directories into `selector/evidence.json`, which records per area the route
templates its runs requested, never bodies or ids, and per flow its measured
minutes. Among equally ranked areas the least recently run comes first, and
the stalest area not recommended is suggested as a rotating check.

The result is `none`, `targeted`, `broad` (shared infrastructure, build
configuration, or more areas than the budget), or `unknown` (an app file no
rule covers). It keeps every affected area and recommends at most
`budget.areas`, owners first, with the smoke set for broad and unknown
changes. `node relentless/selector/select.ts --jj-rev @-` shows the choice
for a local change; `--check` validates the contract, which also fails when
a route has no area or a rule matches no file.
`node --test relentless/selector/selector-tests.ts` runs its tests.

### Validating the selector

- `selector/evaluation/labels.json` holds 298 past RDST changes, each
  labeled with today's areas it needs by two independent agent passes on
  different models, blind to the selector: `required` are the areas both
  named, `disputed` those only one did, graded neither way until someone
  reviews them (`source: reviewed`). A frozen 30% `held-out` split, chosen by
  commit hash, guided no tuning and gives the headline; tune on `tune`.
  `node selector/evaluation/replay.ts --out DIR [--split held-out]` checks
  each change out in a scratch jj workspace and runs the selector on its own
  tree; `node selector/evaluation/report.ts DIR [--baseline OTHER_DIR]`
  grades it over the areas that exist today: required areas found at all,
  required areas recommended, misses, false alarms and their minutes, by
  split, era, label confidence and selection status, and flow recall
  against `flow-labels.json`.
- `evaluation/finding-labels.json` is ground truth from real runs: the areas
  whose runs found a P1 or P2 that main did not reproduce. Replays include
  its changes, and the report grades them on their own ("Found bugs").
  `node selector/metrics.ts --labels-out FILE ARTIFACT_DIRS...` adds a
  build's to FILE; CI's report step writes each build's as an artifact.
- `evaluation/replay.json` records a complete replay under today's selector
  and labels (`report.ts DIR --record`): the fingerprint of everything that
  decides a selection, and the held-out and overall numbers. CI fails while
  either changed since (`report.ts --verify`), so a change to selection
  carries the replay that measured it, and its diff shows the effect.
- The tester step records what ran in `selection-outcome.json`, and
  `node selector/metrics.ts ARTIFACT_DIRS...` reports whether any P1 or P2
  finding came from an area or mission the selector left out.
- Every tester run in CI folds its runs into the evidence index and ships
  the updated `evidence.json` as an artifact, to be committed so the index
  keeps up with the prompts and the app.
- `--check` also fails when an API route has no caller in the frontend, no
  run evidence, and no entry in `areas.json`'s `uncalledRoutes`, and when a
  rule's `review_by` date has passed.

## Verdicts

`run.ts` exits 0 only for `passed`. The verdict comes from the run's
artifacts, not the agent's claim:

| Verdict | Meaning |
|---|---|
| `passed` | The agent finished and no P1/P2 finding was filed. |
| `failed` | The agent reported failure, or any P1/P2 finding exists. |
| `blocked` | The agent could not test the area and said why. |
| `incomplete` | The agent stopped without calling `rt finish`. |
| `timeout` | The run exceeded `--timeout-min`. |
| `budget_exhausted` | The agent hit `--budget` or `--max-turns` before finishing. |
| `agent_error` | The agent process failed. |
| `infra_error` | The app or driver failed to start or died mid-run. |

Findings are leads, not regressions. Once a finding reproduces reliably, turn
it into a deterministic spec under `e2e/` so it stays fixed.
