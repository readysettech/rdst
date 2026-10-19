import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * Capability manifests: a `relentless.json` in a folder says which capability
 * the folder's code belongs to. A capability spanning several folders, such
 * as a UI slice and its backend feature, has one in each; exactly one of them
 * names the areas that test it, and any may name flows that exercise it.
 * Ownership is a fact rather than a score: the capability that owns a changed
 * file must be tested.
 *
 * Manifests are read from the selector's own checkout, like `areas.json`, so
 * replaying an older tree judges it by today's ownership map.
 */

const here = dirname(fileURLToPath(import.meta.url))
const selectorRepo = resolve(here, '../../../../..')
const ROOTS = ['web-apps/apps/rdst/src', 'web-apps/apps/rdst-desktop', 'rdst']
const SKIP = new Set([
  // Build output: a packaged sidecar carries copies of the backend's manifests.
  'build',
  'dist',
  'out',
  'sidecar',
  'node_modules',
  '.venv',
  'venv',
  '__pycache__',
  'tests',
  'web_dist',
  'keyservice',
])

export interface Capability {
  name: string
  /** One per folder the capability spans, such as its UI and its backend. */
  manifests: string[]
  areas: string[]
  owns: string[]
  flows: string[]
}

interface Pattern {
  capability: Capability
  source: string
  regex: RegExp
  /** Exact files beat globs; longer literal prefixes beat shorter ones. */
  specificity: number
}

function globToRegex(glob: string) {
  let out = ''
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i]
    if (c === '*' && glob[i + 1] === '*') {
      out += glob[i + 2] === '/' ? '(?:.*/)?' : '.*'
      i += glob[i + 2] === '/' ? 2 : 1
    } else if (c === '*') out += '[^/]*'
    else out += c.replace(/[.+?^$()|[\]\\{}]/g, '\\$&')
  }
  return new RegExp(`^${out}$`)
}

function findManifests(root: string, dir: string, out: string[]) {
  const absolute = join(root, dir)
  if (!existsSync(absolute)) return
  for (const entry of readdirSync(absolute)) {
    if (SKIP.has(entry) || entry.startsWith('.')) continue
    const path = join(dir, entry)
    if (entry === 'relentless.json') out.push(path)
    else if (statSync(join(root, path)).isDirectory())
      findManifests(root, path, out)
  }
}

let cached: { capabilities: Capability[]; patterns: Pattern[] } | undefined
// Manifests that break the rules above, reported by `--check`.
const problems: string[] = []

export function capabilities() {
  if (cached) return cached
  const files: string[] = []
  for (const root of ROOTS) findManifests(selectorRepo, root, files)
  const list: Capability[] = []
  const patterns: Pattern[] = []
  for (const manifest of files.sort()) {
    const data = JSON.parse(
      readFileSync(join(selectorRepo, manifest), 'utf8')
    ) as {
      capability: string
      areas?: string[]
      flows?: string[]
      owns?: unknown
    }
    if (data.owns !== undefined)
      problems.push(`${manifest}: a manifest owns its folder; remove owns`)
    const owns = join(dirname(manifest), '**')
    let capability = list.find((c) => c.name === data.capability)
    if (!capability) {
      capability = {
        name: data.capability,
        manifests: [],
        areas: [],
        owns: [],
        flows: [],
      }
      list.push(capability)
    }
    capability.manifests.push(manifest)
    capability.owns.push(owns)
    capability.flows.push(...(data.flows ?? []))
    if (data.areas) {
      if (capability.areas.length > 0)
        problems.push(
          `${manifest}: ${data.capability} already names its areas elsewhere`
        )
      else capability.areas = data.areas
    }
    patterns.push({
      capability,
      source: owns,
      regex: globToRegex(owns),
      // A folder inside another is the more specific owner.
      specificity: owns.length,
    })
  }
  for (const capability of list) {
    if (capability.areas.length === 0)
      problems.push(
        `${capability.manifests.join(', ')}: no manifest names the areas of ${capability.name}`
      )
  }
  cached = { capabilities: list, patterns }
  return cached
}

/** The capability that owns a repository-relative path, if any. */
export function ownerOf(path: string): Capability | undefined {
  let best: Pattern | undefined
  for (const pattern of capabilities().patterns) {
    if (!pattern.regex.test(path)) continue
    if (!best || pattern.specificity > best.specificity) best = pattern
  }
  return best?.capability
}

/** Problems with the manifests, for `--check`. */
export function checkOwnership(
  trackedFiles: string[],
  areaNames: string[],
  flowIds: string[]
) {
  const { capabilities: list, patterns } = capabilities()
  const found = [...problems]
  for (const capability of list) {
    for (const area of capability.areas) {
      if (!areaNames.includes(area))
        found.push(`${capability.manifests[0]}: unknown area ${area}`)
    }
    for (const flow of capability.flows) {
      if (!flowIds.includes(flow))
        found.push(`${capability.manifests[0]}: unknown flow ${flow}`)
    }
  }
  for (const pattern of patterns) {
    if (!trackedFiles.some((f) => pattern.regex.test(f))) {
      found.push(
        `${pattern.capability.manifests.join(', ')}: ${pattern.source} matches no file`
      )
    }
  }
  return found
}

/** Flow ids under `relentless/flows`. */
export function flowIds() {
  const root = join(here, '..', 'flows')
  const ids: string[] = []
  const walk = (dir: string) => {
    if (!existsSync(dir)) return
    for (const entry of readdirSync(dir)) {
      const path = join(dir, entry)
      if (statSync(path).isDirectory()) walk(path)
      else if (entry.endsWith('.md'))
        ids.push(relative(root, path).replace(/\.md$/, ''))
    }
  }
  walk(root)
  return ids
}
