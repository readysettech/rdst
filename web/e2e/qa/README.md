# UX regression suite

The September 2026 UX audit drove every user-facing flow of this app end to end
and raised 247 findings. Six waves of fixes followed. This directory is what the
audit left behind: the flows it recorded, rewritten to assert the behaviour the
product has now, so a regression fails a test instead of waiting for the next
audit.

It is a separate suite from `e2e/*.spec.ts` (the browser-integration run) because
it has a different job. The browser-integration suite proves features work. This
one proves the interface keeps its manners: one name per thing, a focus ring on
every stop, an error that says what failed and offers one retry verb, nothing
clipped at 1024 or 390, no page claiming to be empty while it loads.

The audit report is `web-apps/docs/plans/rdst-ux-audit-2026-09.md`.

## Running it

```
cd web-apps/apps/rdst
pnpm run test:qa
```

`test:qa` goes through `e2e/run.mjs`, which gives the run its own temporary
`HOME` and `RDST_E2E_HOME`, so it never touches `~/.rdst`. The config
(`playwright.qa.config.ts`) builds the app and serves it with the same
`tests/web_e2e/server:app` harness as the browser-integration run, one worker,
serial. 129 tests across sixteen files; expect roughly four minutes.

The server binds `127.0.0.1:8787` by default. If something already holds that
port, point the run somewhere else:

```
RDST_E2E_BASE_URL=http://127.0.0.1:8872 pnpm run test:qa
```

Run one area with a path filter:

```
pnpm run test:qa -- e2e/qa/analyze.spec.ts
```

### Screenshots

The flows were recorded with screenshots, and the screenshots were evidence, not
assertions. They are off by default. `QA_SHOTS=1` turns them back on; they land
in `test-results/qa-shots/`, together with the structural probe dumps.

```
QA_SHOTS=1 pnpm run test:qa
```

No assertion reads a screenshot. Turning them off never changes a verdict.

## Layout

One file per product area. `_helpers.ts` carries everything the six audit packs
had in common: the SSE frame builder and background-run mocks, the comparison
event builders and sandbox states, the analyze fixtures, the scrolling
screenshot, the clip / overflow / occlusion probes, the per-route structural
probe, the focus walk, and the contrast sampler.

| File | Covers |
|---|---|
| `onboarding.spec.ts` | first run, connect, the AI gate, the demo, the setup guide |
| `shell.spec.ts` | sidebar and nav, the mobile drawer, the target switcher, legacy redirects, Home |
| `configure.spec.ts` | targets, the add and delete flows, the AI panel, account sign-in |
| `queries.spec.ts` | the Queries library: card anatomy, action hierarchy, running and analyzed states |
| `analyze.spec.ts` | the analyze drawer: focus order, parameters, consent, jobs, cancellation |
| `results.spec.ts` | `/results` and analysis recall |
| `ask.spec.ts` | Ask and the follow-up conversation |
| `schema.spec.ts` | the schema explorer and its tablist |
| `benchmarks.spec.ts` | Compare against Readyset and Load test |
| `health-check.spec.ts` | `/audit`: runs, reports, the email dialog, fleet, run history |
| `scan.spec.ts` | Code scan |
| `guards.spec.ts` | Guards, and the retired Agents workspace |
| `states.spec.ts` | the cross-cutting empty / error / loading sweep, and the console smoke gate |
| `a11y.spec.ts` | focus, accessible names, heading outline, contrast, dialogs |
| `viewports.spec.ts` | the 1024 floor and the 390 phone width |
| `motion.spec.ts` | `prefers-reduced-motion` |

`states.spec.ts` holds the suite's baseline gate: plain navigation to every route
logs no console error and no page error. Nothing in that test accepts an error,
deliberately. Everything else in the suite is downstream of it.

## The id convention

Every test names the finding it defends, using the id from the audit's master
list (`MASTER-findings.json` in the audit's working set, reproduced in the report
appendix). The id goes in a doc comment directly above the test:

```ts
/** B-01 - a value whose text looks like a placeholder still reaches /api/analyze. */
test('an email-shaped parameter value runs an analysis', async ({ page }) => {
```

The prefixes are the audit's own: `A` onboarding and settings, `B` the query and
analyze loop, `C` Ask and schema, `D` benchmarks, `E` health check and scan, `F`
guards and the cross-cutting sweeps, `MG` findings that several agents raised
independently and that were merged.

The recorded flows used each agent's working numbers, which drifted from the
final list before it was reconciled. Those are renumbered here: pack C's schema
`C-20/C-22/C-23/C-24` are `C-64/C-60/C-62/C-61`, its legacy routes `C-30/C-31`
are `C-80/C-81`, its Ask `C-05..C-12` land in `C-20..C-40`, and its motion `C-13`
is `MG-13`; pack B's Jobs-chip-over-the-drawer `B-13` is `B-08`; pack D's
docker gate `D-13` is `D-11`; pack E's preflight placeholder is `E-14`. Check an
id against the master list before reusing it.

## Adding a flow

1. Find the product area's file. A new area gets a new file, not a new directory.
2. Write the test title as the behaviour, in the present tense: what a working
   product does, not what a broken one did.
3. Put the finding id in a doc comment above the test, with one sentence saying
   what the product must do. If the test defends something the audit never
   raised, say so instead of inventing an id.
4. Take setup from `_helpers.ts`. Add to it only what a second file would use;
   a one-off mock belongs in the spec that needs it.
5. Set up your own target and registry state at the top of the test. The suite
   shares one RDST home across all sixteen files, and order is not a contract.
6. Screenshots go through `shot` / `shotScroll`, and no assertion may read one.
7. Keep it fast. The whole suite has a fifteen-minute budget and one worker.
   Stub the SSE fixtures rather than waiting for real elapsed time.

The `browserErrors` fixture fails a test on any console or page error. When a
flow provokes one on purpose - a deliberate 500, a 404 lookup - accept exactly
that message with `acceptBrowserError(browserErrors, '<message>')` and say why.
Never blanket-accept.

## Held findings

A test that describes behaviour the product deliberately does not have yet is
declared `test.fixme`, so the suite stays green while the intent stays visible.
Each is named with its id, so `pnpm run test:qa` lists them in its skipped set.
Delete the `.fixme` when the hold lifts.

| Id | Spec | What the test asserts, and why it is held |
|---|---|---|
| F-05 | guards | Guards is reachable from inside the product. Held: the route stays off the nav until Guards leaves experimental. |

## What is not here

The audit's recordings included screenshot-only passes, contact-sheet builders
and probe dumps that asserted nothing. Those were evidence for the report and did
not become tests. Findings that need a real Postgres, a live keyservice, or the
Electron shell are out of this harness's reach and stay in the report's "owed by
the real-backend pass" list.
