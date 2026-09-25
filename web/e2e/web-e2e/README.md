# Live RDST web E2E

This is the suite of record for live account and hosted AI browser coverage. It signs in through the keyservice with a fresh catch-all address, reads the magic link from the Resend mailbox, and calls real hosted inference. Continuous Monitoring has its own live suite in `continuous-monitoring/` (below). The fast fake-backend `e2e/monitor.spec.ts` remains in the regular browser suite.

CI runs every spec here against the CL preview Worker through `scripts/run-web-e2e-ci.sh` and `e2e/docker-compose.web-e2e.yml`. The Worker URL is derived from the CL and build number. Its attestation and admin secrets come from AWS Secrets Manager `rdst/keyservice/dev01`; the full-access mailbox key comes from `rdst/ci/e2e`. The browser job forwards all three into the backend. The Postgres service has `pg_stat_statements` preloaded.

For a local run, the existing `~/.rdst-monitor-dev/e2e.env` contains the secrets. Source it with exports enabled, then explicitly allow the personal development Worker:

```sh
set -a
. ~/.rdst-monitor-dev/e2e.env
set +a
export RDST_KEYSERVICE_URL=https://rdst-keyservice-mike.readysetio.workers.dev
export RDST_ALLOW_PERSONAL_KEYSERVICE_E2E=1
```

If the local env file needs to be rebuilt, set `KEYSERVICE_AWS_PROFILE` to your AWS profile for the keyservice account (see `rdst/keyservice/CLAUDE.md`) in `us-east-2`. Fetch `rdst/keyservice/dev01` for `ATTESTATION_SECRET` and `ADMIN_SECRET`, and `rdst/ci/e2e` for the full-access `RESEND_API_KEY`; set `RDST_ATTESTATION_SECRET`, `RDST_KEYSERVICE_ADMIN_SECRET`, and `RDST_CI_RESEND_API_KEY` respectively. Do not use the Worker's send-only Resend key for mailbox reads. Keep these values in the local env file, outside the repository. `rdst/docs/plans/continuous_analysis/tools/signin.mjs` shows the same mailbox sign-in against the mike Worker.

For example, fetch the JSON without printing it to the terminal and populate the shell variables:

```sh
keyservice_secret=$(aws secretsmanager get-secret-value --profile "$KEYSERVICE_AWS_PROFILE" --region us-east-2 --secret-id rdst/keyservice/dev01 --query SecretString --output text)
mailbox_secret=$(aws secretsmanager get-secret-value --profile "$KEYSERVICE_AWS_PROFILE" --region us-east-2 --secret-id rdst/ci/e2e --query SecretString --output text)
export RDST_ATTESTATION_SECRET=$(printf '%s' "$keyservice_secret" | jq -r '.ATTESTATION_SECRET')
export RDST_KEYSERVICE_ADMIN_SECRET=$(printf '%s' "$keyservice_secret" | jq -r '.ADMIN_SECRET')
export RDST_CI_RESEND_API_KEY=$(printf '%s' "$mailbox_secret" | jq -r '.RESEND_API_KEY')
unset keyservice_secret mailbox_secret
```

## Continuous Monitoring

`continuous-monitoring/` runs with `playwright.continuous-monitoring.config.ts`, one engine per run (`RDST_E2E_ENGINE=postgresql` or `mysql`, the same scenarios on both). CI runs it as two jobs, "RDST Continuous Monitoring E2E (postgresql)" and "(mysql)", through `scripts/run-continuous-monitoring-ci.sh` and `e2e/docker-compose.continuous-monitoring-{postgres,mysql}.yml`.

The test server (`rdst/tests/web_e2e/full_stack_server.py` with `RDST_CIM_ENGINE` set) seeds the ci-shop database (`rdst/tests/web_e2e/ci_shop/`), drives its load and connects RDST through a proxy the suite can cut. `setup.ts` signs in, turns monitoring on and writes two days of backdated history, replaying the past bursts through the real attention check and building yesterday's nightly report. `1-inbox` and `3-inbox-late` are the Inbox scenarios; `2-database` covers the Database page, the report and agent activity. The scenarios share one database and one monitor, so they run in order with no retries.

For a local run, start a database (the flags in `rdst/tests/integration/docker-compose.yml`), source the env file as above, and build into a separate directory so the run never replaces a `dist/` a running app serves:

```sh
export RDST_E2E_DIST_DIR=/tmp/rdst-e2e-dist
export RDST_E2E_ENGINE=postgresql        # or mysql
export RDST_CIM_DB_PORT=15432            # the database's host port
export RDST_CIM_ADMIN_USER=rdst_test     # root for MySQL; the password is rdst_e2e_password
node ./e2e/run.mjs --config playwright.continuous-monitoring.config.ts   # from apps/rdst
```
