# Security Policy

## Reporting a Vulnerability

Please report security vulnerabilities privately to **support@readyset.io** with
`SECURITY` in the subject line. Do not open a public issue for a suspected
vulnerability.

Include whatever you have: affected version (`rdst version`), a description of
the issue, and the steps or input needed to reproduce it. A proof of concept
helps but is not required.

We aim to acknowledge a report within three business days and to keep you
updated as we work on a fix. We will credit you in the release notes unless you
would rather stay anonymous.

## Supported Versions

Fixes land on the latest released version, published to
[PyPI](https://pypi.org/project/rdst/). Please confirm an issue reproduces on
the current release before reporting it.

## Scope

RDST is a command-line tool that connects to databases you point it at. The
areas we consider security-relevant:

- Credential handling — passwords, API keys, cloud credentials, SSH keys
- SQL that RDST generates or executes on your behalf, including anything
  produced by the LLM-backed commands
- The local API server started by `rdst serve`
- What leaves your machine: data sent to LLM providers and to telemetry

## What RDST Sends Off Your Machine

Two destinations, both worth knowing about:

- **LLM providers.** Commands such as `ask`, `analyze`, `scan` and `audit` send
  schema and query text to the configured provider so it can do its job. Query
  text keeps its literal values, because they tell the model how selective a
  filter is. Monitoring analyzes the queries it captures from your database the
  same way, filling placeholders with values seen there. Schema annotation sends
  a few sample rows and column values so the model can tell what a column holds.
  With your own API key, the provider's terms for that key apply. RDST's hosted
  inference only routes to providers that do not train on request data.
- **Telemetry.** Usage telemetry is on by default. It is not anonymous: if you
  have supplied an email address, events carry it and it is used to link your
  installs together. Turn telemetry off either way:

  ```
  export RDST_TELEMETRY=false          # per shell
  telemetry_enabled = false            # in ~/.rdst/config.toml, permanently
  ```

  In `config.toml`, `telemetry_enabled = false` works at the top level or in any table, as
  does `enabled = false` in a `[telemetry]` table.

Credentials are never intentionally included in either. If you find a case where
one is, that is a vulnerability and we would like to hear about it.

## Credential Storage

RDST stores database passwords in your operating system's keychain via
[keyring](https://pypi.org/project/keyring/). Configuration files hold a
*pointer* to the credential, never the credential itself. `rdst` has no
`--password` flag, so passwords do not enter your shell history or the process
table. A pointer may not name RDST's own credentials or common provider and cloud
keys (`ANTHROPIC_API_KEY`, `AWS_SECRET_ACCESS_KEY` and the like).
