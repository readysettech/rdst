# RDST

**Find slow queries, analyze them, and fix them — from your terminal, your
browser, or your desktop.**

RDST is the Readyset Data and SQL Toolkit: point it at PostgreSQL or MySQL and
it finds your slow queries, explains *why* they are slow, and shows what an
index, a rewrite, or a [Readyset](https://readyset.io) cache would do about it.
This repository holds the whole suite — the Python CLI, the web UI it serves,
the desktop shell that wraps both, and the design system they are built on.

The quickest way in is the desktop app — grab it at
[readyset.io/downloads](https://readyset.io/downloads). For the terminal
side, [`cli/README.md`](cli/README.md) covers the commands, the install
script, and the MCP integration.

## Layout

| | |
| --- | --- |
| `cli/` | the `rdst` Python CLI, and the HTTP API the UI talks to |
| `web/` | the browser UI, served by `rdst web` and embedded in the desktop app |
| `desktop/` | the Electron shell that ships the CLI as a local sidecar |
| `packages/` | the shared design system: `ui-new`, `ui-icons`, `tailwind-base`, `typescript-config` |

## Get started

### The desktop app — the easiest way in

Download the prebuilt app for macOS, Windows or Linux at
[readyset.io/downloads](https://readyset.io/downloads), open it, and connect
your database. Everything ships inside it — the CLI runs as a bundled
sidecar, so there is nothing else to install.

### The CLI

Prefer the terminal? On macOS or Linux, with no sudo, pip, or preinstalled
Python:

```bash
curl -fsSL https://downloads.readyset.io/packages/rdst-cli/install.sh | sh
```

Then point it at a database and look around:

```bash
rdst init       # guided setup for your first Postgres or MySQL target
rdst top        # live view of the slowest queries hitting it
rdst analyze    # why a query is slow, and what an index or cache would do
```

Also installable from PyPI, or from this repository — the CLI lives in a
subdirectory, so pip needs to be told which one:

```bash
pip install rdst
pip install "git+https://github.com/readysettech/rdst.git#subdirectory=cli"
```

Full documentation lives at
[readyset.io/docs](https://readyset.io/docs/readyset-ai/rdst/cli).

## Build from source

Node 22+ and pnpm 11 for the JavaScript side; Python 3.10+ and
[uv](https://docs.astral.sh/uv/) for the CLI.

```bash
pnpm install
pnpm build                          # web bundle + desktop main process
pnpm --filter rdst-web dev          # the UI and a reloading `rdst web` backend
pnpm --filter rdst-desktop dev      # the same, inside the Electron shell
pnpm --filter rdst-web dev:vite     # the UI alone, against a running `rdst web`
```

Both `dev` launchers start the CLI out of `cli/` with `uv`, so they need `uv`
on PATH and an interpreter whose bundled SQLite is new enough to be WAL-safe;
they name the interpreters they tried when none is. The desktop app's sidecar
build — `pnpm --filter rdst-desktop build:sidecar`, which freezes `cli/` into a
PyInstaller bundle the packaged app launches — needs Python 3.12 specifically,
and the web bundle built first (`pnpm --filter rdst-desktop build:web`).

The CLI is a standard Python project and builds on its own; see
[`cli/README.md`](cli/README.md) for its toolchain.

## Community builds

Readyset's own releases — the PyPI wheel, the install script and the desktop
apps — are built from Readyset's internal repository, which this repository is
generated from. Everything here builds and runs the same way, with one
deliberate difference: the type and the icon artwork are **open equivalents**
rather than the commercially licensed sets the official builds use. Outfit
stands in for the official UI face and the Hugeicons free set for the icons,
matched closely enough that layout and spacing are unchanged. See
[`LICENSE-THIRD-PARTY`](LICENSE-THIRD-PARTY).

Product analytics are compiled out unless a key is supplied at build time, so a
build from this source reports nothing. Desktop builds from this source have
auto-update switched off and never contact Readyset's update servers.

## Contributing

Pull requests are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for how
they land.

## License

MIT — see [`LICENSE`](LICENSE). Third-party fonts and icons keep their own
licenses, listed in [`LICENSE-THIRD-PARTY`](LICENSE-THIRD-PARTY).
