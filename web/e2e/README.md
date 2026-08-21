# RDST web browser tests

RDST has two Playwright tiers with different guarantees.

## Browser integration

The main suite builds the production frontend and sends every browser request
through production FastAPI routes. It uses deterministic server-side service
adapters for operations that would contact a database, Readyset, or an LLM.
Each adapter subclasses its production service and overrides only those
methods with the exact production signatures; test_web_e2e_service_fakes.py
fails on any drift. Fixture events are validated against RDST's real Python
event unions before the production routes serialize them, and state a faked
method reports as saved (audit snapshots, the semantic layer) is persisted
through the real storage so history and read routes run unfaked.

This tier covers routing, request validation, target/password guards, SSE
serialization, frontend state, and RDST's local persistence. It does not claim
that the adapted services or their external systems ran.

From `web-apps/`:

```bash
pnpm --filter rdst-web test:browser-integration
```

`test:e2e` remains an alias for this command. Useful variants are:

```bash
pnpm --filter rdst-web test:browser-integration e2e/scan.spec.ts
pnpm --filter rdst-web test:browser-integration --grep "retries"
pnpm --filter rdst-web test:e2e:ui
pnpm --filter rdst-web test:e2e:debug
```

The runner starts the server on `127.0.0.1:8787` with a temporary `HOME` and
one worker. Tests configure service responses with `setBackendFixtures`; they
must not use `page.route()` or `route.fulfill()` for RDST API endpoints.

## Postgres and Readyset E2E

The Postgres tier has no service adapters. It drives the browser through the
production frontend and FastAPI app, configures a live PostgreSQL target,
calls the real schema collector, and initializes the semantic layer with the
real `SchemaService`. It also starts a comparison through the web UI, pulls and
runs the managed Readyset image through the host Docker daemon, verifies the
physical container, and removes it by deleting the target.

Buildkite starts both the browser runner and PostgreSQL with:

```bash
docker compose \
  -f web-apps/apps/rdst/e2e/docker-compose.postgres.yml \
  run --rm browser \
  bash -lc 'web-apps/apps/rdst/scripts/run-postgres-e2e-ci.sh'
```

For a database already available on the host, set `RDST_E2E_DB_HOST`,
`RDST_E2E_DB_PORT`, and `RDST_E2E_DB_PASSWORD`, then run:

```bash
pnpm --filter rdst-web test:e2e:postgres
```

The defaults are `127.0.0.1:15432` and `rdst_e2e_password`.

## Failure evidence

The browser-integration tier writes `playwright-report/`,
`test-results/rdst-web-browser-integration.xml`, and
`test-results/artifacts/`. The Postgres tier writes
`playwright-report-postgres/`, `test-results/rdst-web-postgres-e2e.xml`, and
`test-results/postgres-artifacts/`. CI also saves each runner/server log.

CI retries a failed test once to collect another attempt, but
`failOnFlakyTests` keeps the build red when the retry passes. Traces,
screenshots, and video are retained from failed attempts. Buildkite uploads
those artifacts and annotates JUnit failures.

## Adding coverage

- Import `test` and `expect` from `./fixtures` (or `../fixtures` in the
  full-stack directory) so console and page errors fail the test.
- Keep browser API traffic real. Add deterministic behavior at a server-side
  external boundary and validate fixture data against the production model.
- Fixture responses are one-shot by default. Set `repeat: true` only for
  read-only polling responses that are expected to be requested repeatedly.
- Use the Postgres tier when a claim depends on actual database behavior.
- Assert outgoing request bodies for state-changing flows.
- Cover meaningful success, empty, failure, and retry states.
- Prefer roles and accessible names over implementation selectors.
- Clear targets, query registry entries, or semantic-layer state when a test
  mutates them; do not rely on test order.
