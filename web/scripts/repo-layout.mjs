/**
 * Locates the trees the rdst development scripts drive, in either checkout.
 *
 * The suite is published through a josh filter, so the same scripts run from
 * two layouts: this monorepo, where the apps sit under `web-apps/apps/` beside
 * a sibling `rdst/` Python tree, and the public mirror, where those three
 * trees are `web/`, `desktop/` and `cli/` at the repository root. A relative
 * path can only spell one of the two, so every cross-tree path resolves here.
 *
 * `apps/rdst/scripts` and `apps/rdst-desktop/scripts` hold byte-identical
 * copies of this file: the two app roots sit at different depths in the two
 * layouts, so no relative specifier reaches one shared file from both. The
 * desktop app's `dev-launch.test.mjs` fails if the copies drift.
 */
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

// Both layouts hold this file at a fixed depth below their root, so `up` is
// how far to climb from the `scripts/` directory it lives in. `rdst.py` is the
// Python tree's entrypoint, and the app directory this copy sits in has to be
// one the candidate layout names — which is what keeps a mirror materialized
// inside the monorepo from matching the private layout it is nested in.
const LAYOUTS = [
  {
    name: 'private monorepo',
    up: ['..', '..', '..', '..'],
    workspace: 'web-apps',
    python: 'rdst',
    web: 'web-apps/apps/rdst',
    desktop: 'web-apps/apps/rdst-desktop',
  },
  {
    name: 'public mirror',
    up: ['..', '..'],
    workspace: '.',
    python: 'cli',
    web: 'web',
    desktop: 'desktop',
  },
]

const SCRIPTS_DIR = import.meta.dirname
const APP_DIR = resolve(SCRIPTS_DIR, '..')

let resolved = null

/**
 * @returns {{ name: string, root: string, workspaceRoot: string,
 *   pythonDir: string, webDir: string, desktopDir: string }}
 */
export function repoLayout() {
  if (resolved) {
    return resolved
  }

  const tried = []
  for (const layout of LAYOUTS) {
    const root = resolve(SCRIPTS_DIR, ...layout.up)
    const pythonDir = resolve(root, layout.python)
    const webDir = resolve(root, layout.web)
    const desktopDir = resolve(root, layout.desktop)
    tried.push(`${resolve(pythonDir, 'rdst.py')} (${layout.name})`)

    if (APP_DIR !== webDir && APP_DIR !== desktopDir) {
      continue
    }
    if (!existsSync(resolve(pythonDir, 'rdst.py'))) {
      continue
    }

    resolved = {
      name: layout.name,
      root,
      workspaceRoot: resolve(root, layout.workspace),
      pythonDir,
      webDir,
      desktopDir,
    }
    return resolved
  }

  throw new Error(
    `Unable to locate the rdst Python tree from ${SCRIPTS_DIR}. Tried:\n` +
      tried.map((candidate) => `  ${candidate}`).join('\n')
  )
}

/** A `file:` URL for a module in the Python tree's `scripts/` directory. */
export function pythonScriptUrl(name) {
  return pathToFileURL(resolve(repoLayout().pythonDir, 'scripts', name)).href
}
