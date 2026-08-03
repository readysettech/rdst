# RDST Architecture

## Overview

RDST is organized around two top-level concepts:

- `features/`: vertical slices that own business behavior
- `shared/`: infrastructure and platform substrate

`lib/` has been removed. New code should not recreate a generic dumping ground under another name.

## Design Rules

### 1. Features own domain behavior

A feature owns its:

- `models.py`
- `events.py`
- `service.py`
- `cli/`
- `api/`
- feature-specific helpers, workflows, prompts, and engines

If code mainly exists to serve one feature, it belongs in that feature even if it is reused by multiple files inside the slice.

### 2. Shared owns infrastructure

`shared/` is for reusable substrate such as:

- API bootstrap and generic route infrastructure
- CLI bootstrap and parser data
- target/config persistence
- DB connection and password/secret resolution
- LLM provider abstraction
- query registry substrate
- workflow runtime
- UI primitives
- generic execution layers like `data_manager` and `data_manager_service`

`shared/` should not own feature prompts, feature workflows, feature-specific engines, or domain services.

### 3. Dependency direction

Preferred dependency flow:

```text
rdst.py / mcp_server.py
  -> features/*
  -> shared/*

features/*
  -> shared/*

shared/*
  -> shared/*
```

Avoid:

- `shared/* -> features/*`
- cross-feature imports unless the dependency is truly another feature's public surface
- rebuilding a monolith under `shared/`

## Top-Level Layout

### Entrypoints

- [rdst.py](./rdst.py): CLI entrypoint
- [mcp_server.py](./mcp_server.py): MCP server entrypoint

### Feature slices

Current feature-owned domains:

- `agent`
- `allowlist`
- `analyze`
- `ask`
- `audit`
- `bootstrap`
- `cache`
- `configure`
- `demo`
- `fleet`
- `guard`
- `init`
- `interactive`
- `providers`
- `qpdemo`
- `qprouter`
- `query_registry`
- `scan`
- `schema`
- `slack`
- `top`
- `trial`
- `update`
- `tunnel`

Each slice should be readable from its `models.py` and `service.py` first. That is the feature contract.

### Feature map

- `agent`: agent runtime and MCP-facing agent behavior
- `allowlist`: provider IP-allowlist context and explicit one-click write-back (Supabase, Neon, DigitalOcean)
- `analyze`: query analysis, explain flows, rewrite evaluation, analysis rendering
- `ask`: natural-language-to-SQL, Ask engine, validation, Ask prompts, Ask debug tooling
- `audit`: audit workflows, capture, storage, scoring, audit prompts
- `bootstrap`: post-add target bootstrap orchestration and its run API
- `cache`: Readyset setup, cacheability, deployment, cache command flows
- `configure`: target configuration, connection profiles, setup wizard, config API
- `demo`: demo setup and sample data loading
- `fleet`: fleet modeling, multi-target status/audit, fleet snapshots, pricing/scoring support
- `guard`: guardrail configuration, masking, intent checks, guard CLI
- `init`: first-run setup and environment/bootstrap validation
- `interactive`: interactive query status and interactive command flow
- `providers`: cloud-provider sign-in and target discovery (AWS RDS, Supabase, Neon, DigitalOcean), OAuth broker client
- `qpdemo`: QueryPilot web demo orchestration, dual-path load, demo API
- `qprouter`: SQP router deploy and clients, pattern/reason engine
- `query_registry`: saved query management, registry APIs, query execution helpers
- `scan`: codebase scanning, ORM extraction, query discovery, scan analysis
- `schema`: schema inspection, semantic layer management, annotation flows
- `slack`: Slack bot, Slack formatting, Slack manifest/handler logic
- `top`: live/top query monitoring, realtime display, top command sets
- `trial`: trial registration and trial status flows
- `update`: installer-owned version resolution and immutable generation activation
- `tunnel`: SSH tunnel status, test, and close surfaces over the shared tunnel manager

### Shared infrastructure

Key shared areas:

- [shared/api](./shared/api)
- [shared/cli](./shared/cli)
- [shared/config](./shared/config)
- [shared/data_manager](./shared/data_manager)
- [shared/data_manager_service](./shared/data_manager_service)
- [shared/db_connection.py](./shared/db_connection.py)
- [shared/password_resolver.py](./shared/password_resolver.py)
- [shared/llm_manager](./shared/llm_manager)
- [shared/query_registry](./shared/query_registry)
- [shared/ui](./shared/ui)
- [shared/workflow_manager.py](./shared/workflow_manager.py)
- [shared/workflow_manager_runtime.py](./shared/workflow_manager_runtime.py)

## Runtime Flow

### CLI flow

1. [rdst.py](./rdst.py) parses top-level arguments.
2. [shared/cli/rdst_cli.py](./shared/cli/rdst_cli.py) provides CLI orchestration and dispatch.
3. Command execution is delegated into feature commands such as:
   - `features.configure.cli.command`
   - `features.analyze.cli.command`
   - `features.top.cli.command`
   - `features.schema.cli.command`
4. Feature services use shared infrastructure for storage, DB access, workflow execution, and UI output.

### API flow

1. [shared/api/app.py](./shared/api/app.py) creates the FastAPI app.
2. It mounts feature routers directly from `features/*/api`.
3. Shared API modules provide only generic app/bootstrap concerns such as:
   - static serving
   - common guards
   - environment/dev/report/status routes

### Service flow

Typical service flow:

```text
feature CLI or API
  -> feature service
  -> shared infra
  -> external systems (DB, LLM, filesystem, keychain, Docker, web)
```

## Important Boundaries

### `shared/config/targets.py` is the config owner

[shared/config/targets.py](./shared/config/targets.py) owns:

- target persistence
- connection-string parsing
- engine normalization
- default ports
- LLM config persistence

Do not reintroduce target/config ownership into CLI modules.

### `shared/data_manager*` is substrate, not feature SQL ownership

[shared/data_manager](./shared/data_manager) and [shared/data_manager_service](./shared/data_manager_service) are intentionally shared.

They provide:

- connection/runtime types
- execution machinery
- system-level command substrate

They do **not** own feature SQL catalogs anymore. Feature-owned command sets live with their features:

- `features/top/command_sets.py`
- `features/cache/command_sets.py`
- `features/schema/command_sets.py`
- `features/init/command_sets.py`

### Feature prompts stay with features

Prompts, heuristics, engines, and workflows that are specific to a feature belong in that feature. `shared/` may host generic LLM plumbing, but not feature prompt libraries.

### Rich components go through `shared.ui`

Do not import Rich directly in application code. Use [shared/ui](./shared/ui) instead.

```python
# NO
from rich.console import Group
from rich.text import Text

# YES
from shared.ui import Group, Text, Tree, Spinner, Live
```

If a Rich component is missing, export it from `shared.ui` rather than importing `rich.*` directly in a feature or shared module.

## What Not To Do

Do not put code in `shared/` just because:

- it is imported in more than one file
- it used to live in `lib/`
- it feels “utility-like”

Questions to ask before adding a module to `shared/`:

1. Is this infrastructure rather than business logic?
2. Would it make sense even if the owning feature disappeared?
3. Is it generic enough to serve multiple features without encoding one feature's language or workflow?

If the answer is no, put it in a feature slice.

## Practical Rule Of Thumb

- If you are implementing user-facing behavior, start in `features/`.
- If you are implementing reusable substrate, start in `shared/`.
- If a `shared/` module starts mentioning one feature's concepts repeatedly, move it back into that feature.

## Where To Look

- Add or change a CLI command: `features/<name>/cli/command.py`
- Add or change an API endpoint: `features/<name>/api/`
- Add domain models or events: `features/<name>/models.py`, `features/<name>/events.py`
- Add feature behavior: `features/<name>/service.py`
- Add shared config or DB plumbing: `shared/config/`, `shared/db_connection.py`
- Add reusable UI primitives: `shared/ui/`
- Add parser/bootstrap CLI wiring: `shared/cli/`
- Add API bootstrap or generic routes: `shared/api/`
