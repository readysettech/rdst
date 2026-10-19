import { execFileSync } from 'node:child_process'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { basename, dirname, extname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { canonical, checkAliases } from './aliases.ts'
import { classifyPackageJson, classifyPyproject } from './classify.ts'
import { checkFlows, chooseFlows, type FlowChoice } from './flows.ts'
import {
  buildGraph,
  declOf,
  declsIn,
  fileOf,
  type Graph,
  matchesRoute,
  type Node,
  node,
  propagate,
  WHOLE,
} from './frontend_graph.ts'
import { type Evidence, loadEvidence } from './ingest-evidence.ts'
import { type Moves, moduleName, onlyMoved } from './moves.ts'
import {
  type Capability,
  checkOwnership,
  flowIds,
  ownerOf,
} from './ownership.ts'

/**
 * Chooses the relentless areas a change can affect. Rules classify each
 * changed file first: prompts and assets, shared stored data, build
 * configuration, tests, the tester itself, and code out of scope. A file runs
 * the areas of the capability owning it; code none owns runs the areas of
 * the owners nearest to it in the change's import and API graphs, which also
 * suggest flows a change reaches without editing. The result separates every affected area from the ones
 * recommended within the budget.
 */

const here = dirname(fileURLToPath(import.meta.url))
// RT_SELECTOR_ROOT points the selector at another checkout of the repo, such
// as an older revision being replayed.
export const repoRoot = resolve(
  process.env.RT_SELECTOR_ROOT ?? resolve(here, '../../../../..')
)
const ts = createRequire(import.meta.url)(
  'typescript'
) as typeof import('typescript')

export interface Change {
  path: string
  /** Path before a rename; equal to `path` otherwise. */
  oldPath: string
  status: 'added' | 'modified' | 'deleted' | 'renamed'
  /** Changed line ranges in the new and old revisions. */
  newRanges: [number, number][]
  oldRanges: [number, number][]
  oldSource?: string
  /** The same code at a new path, or with only its import paths updated. */
  moved?: boolean
}

interface AreaSpec {
  minutes: number
  entries: string[]
}

function readIfExists(path: string) {
  return existsSync(path) ? readFileSync(path, 'utf8') : undefined
}

interface Contract {
  budget: { areas: number; max_areas?: number; mission_minutes?: number }
  smoke: string[]
  frontend: {
    tsconfig: string
    sources: string[]
    roots: string[]
    routes: string
    excludedRoutes: Record<string, string>
  }
  areas: Record<string, AreaSpec>
  /** Routes no page calls and no run requested, each with why that is expected. */
  uncalledRoutes?: Record<string, string>
}

interface Rule {
  match: string[]
  action:
    | 'area-file'
    | 'none'
    | 'areas'
    | 'also'
    | 'broad'
    | 'frontend-graph'
    | 'backend-graph'
    | 'manifest'
    | 'lockfile'
    | 'as-module'
    | 'unknown'
  areas?: string[]
  reason?: string
  /** `as-module`: the code that loads this asset, analyzed in its place. */
  module?: string
  /** Who keeps an override honest, and when it is next due for review. */
  owner?: string
  review_by?: string
  /** How much an `also` rule's areas count toward ranking; 1 makes them owners. */
  weight?: number
}

export type Status = 'none' | 'targeted' | 'broad' | 'unknown'

export interface Selection {
  status: Status
  base?: string
  head?: string
  affected_areas: string[]
  recommended_areas: string[]
  deferred_areas: string[]
  /** area -> why, each a chain from a changed file to the area */
  reasons: Record<string, string[]>
  broad_reasons: string[]
  unknown: { file: string; reason: string }[]
  ignored: { file: string; reason: string }[]
  estimated_minutes: number
  /** Flows the change edits, packed into one mission within the budget. */
  flows: FlowChoice[]
  deferred_flows: FlowChoice[]
  /** Flows only the import and route graphs reach: suggestions, not planned. */
  suggested_flows: FlowChoice[]
  mission_minutes: number
  /** Areas testing the capabilities that own the change; they run first. */
  owners: string[]
  /** Owner areas beyond the cap, shown rather than silently dropped. */
  overflow: string[]
  /** Changed files reaching each area, additive rules weighted down: the ranking's first key. */
  strength: Record<string, number>
  /** The least recently run area not recommended, for a rotating spot check. */
  audit_suggestion: { area: string; last_run: string | null } | null
}

export const contract = JSON.parse(
  readFileSync(join(here, 'areas.json'), 'utf8')
) as Contract
const rules = (
  JSON.parse(readFileSync(join(here, 'rules.json'), 'utf8')) as {
    rules: Rule[]
  }
).rules

/** `**` crosses directories, `*` does not, `{a,b}` is either, each a glob itself. */
export function globRegex(glob: string) {
  let out = ''
  for (let i = 0; i < glob.length; i++) {
    const c = glob[i]
    if (c === '*' && glob[i + 1] === '*') {
      out += glob[i + 2] === '/' ? '(?:.*/)?' : '.*'
      i += glob[i + 2] === '/' ? 2 : 1
    } else if (c === '*') out += '[^/]*'
    else if (c === '{') {
      const end = glob.indexOf('}', i)
      out += `(?:${glob
        .slice(i + 1, end)
        .split(',')
        .map((s) => globRegex(s).source.slice(1, -1))
        .join('|')})`
      i = end
    } else out += c.replace(/[.+?^$()|[\]\\]/g, '\\$&')
  }
  return new RegExp(`^${out}$`)
}
const compiled = rules.map((rule) => ({
  rule,
  patterns: rule.match.map(globRegex),
}))

interface Consumer {
  match: string[]
  action: 'none' | 'areas' | 'broad'
  areas?: string[]
  reason: string
}
const compileConsumers = (list: Consumer[]) =>
  list.map((consumer) => ({
    consumer,
    patterns: consumer.match.map(globRegex),
  }))
// Paths only older trees had, for replays of them; see evaluation/history.json.
const historyFile = join(here, 'evaluation', 'history.json')
const readHistory = (file: string) =>
  compileConsumers(
    (JSON.parse(readFileSync(file, 'utf8')) as { history: Consumer[] }).history
  )
const pastConsumers = process.env.RT_SELECTOR_HISTORY
  ? readHistory(process.env.RT_SELECTOR_HISTORY)
  : []

/** The consumers a replayed older tree declared for a path it had. */
function consumerOf(path: string) {
  return pastConsumers.find(({ patterns }) =>
    patterns.some((p) => p.test(path))
  )?.consumer
}

function rulesFor(path: string) {
  const additive: Rule[] = []
  for (const { rule, patterns } of compiled) {
    if (!patterns.some((p) => p.test(path))) continue
    if (rule.action === 'also') additive.push(rule)
    else return { rule, additive }
  }
  return {
    rule: { match: [], action: 'none', reason: 'outside RDST' } as Rule,
    additive,
  }
}

/** Parses `git diff -U0` output, as `git diff` or `jj diff --git` print it. */
export function parseDiff(text: string): Change[] {
  const changes: Change[] = []
  let current: Change | null = null
  for (const line of text.split('\n')) {
    const header = line.match(/^diff --git a\/(.+) b\/(.+)$/)
    if (header) {
      current = {
        path: header[2],
        oldPath: header[1],
        status: header[1] === header[2] ? 'modified' : 'renamed',
        newRanges: [],
        oldRanges: [],
      }
      changes.push(current)
      continue
    }
    if (!current) continue
    if (line.startsWith('new file mode')) current.status = 'added'
    else if (line.startsWith('deleted file mode')) current.status = 'deleted'
    else if (line.startsWith('rename from ')) {
      current.oldPath = line.slice('rename from '.length)
      current.status = 'renamed'
    } else if (line.startsWith('rename to ')) {
      current.path = line.slice('rename to '.length)
    } else {
      const hunk = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/)
      if (!hunk) continue
      const [oldStart, oldCount, newStart, newCount] = [
        Number(hunk[1]),
        hunk[2] === undefined ? 1 : Number(hunk[2]),
        Number(hunk[3]),
        hunk[4] === undefined ? 1 : Number(hunk[4]),
      ]
      // A pure insertion or deletion touches the declaration around its point.
      current.oldRanges.push([oldStart, oldStart + Math.max(oldCount, 1) - 1])
      current.newRanges.push([newStart, newStart + Math.max(newCount, 1) - 1])
    }
  }
  return changes
}

export type Vcs =
  | { kind: 'git'; base: string; head: string }
  | { kind: 'jj'; rev: string }

export function changesFrom(vcs: Vcs): Change[] {
  const run = (cmd: string, args: string[]) =>
    execFileSync(cmd, args, {
      cwd: repoRoot,
      encoding: 'utf8',
      maxBuffer: 256 * 1024 * 1024,
    })
  const diff =
    vcs.kind === 'git'
      ? run('git', [
          'diff',
          '-U0',
          '--no-color',
          '--no-ext-diff',
          '-M',
          vcs.base,
          vcs.head,
        ])
      : run('jj', ['diff', '--git', '--context', '0', '-r', vcs.rev])
  const changes = parseDiff(diff)
  const show = (rev: string, path: string) =>
    vcs.kind === 'git'
      ? run('git', ['show', `${rev}:${path}`])
      : run('jj', ['file', 'show', '-r', rev, `file:${JSON.stringify(path)}`])
  const [before, after] =
    vcs.kind === 'git' ? [vcs.base, vcs.head] : [`${vcs.rev}-`, vcs.rev]
  const sources = new Map<Change, string>()
  for (const change of changes) {
    try {
      if (change.status !== 'added')
        change.oldSource = show(before, change.oldPath)
      if (change.status !== 'deleted')
        sources.set(change, show(after, change.path))
    } catch {}
  }
  // Modules the change moved or deleted: only imports of those can change path.
  const moved = new Set(
    changes
      .filter((c) => c.status === 'renamed' || c.status === 'deleted')
      .map((c) => moduleName(c.oldPath))
  )
  // A deleted file and an added one with the same code may be a move the diff
  // did not pair, as jj prints a rename with edits: same name first, then
  // same code under another name.
  const deleted = changes.filter((c) => c.status === 'deleted' && c.oldSource)
  const pairs: [Change, Change][] = []
  const pair = (
    matches: (from: Change, to: Change) => boolean,
    moves?: Moves
  ) => {
    for (const added of changes.filter((c) => c.status === 'added')) {
      if (added.moved) continue
      const source = sources.get(added)
      const from = deleted.find(
        (d) =>
          !d.moved &&
          source !== undefined &&
          matches(d, added) &&
          onlyMoved(d.path, added.path, d.oldSource ?? '', source, moves)
      )
      if (!from) continue
      from.moved = added.moved = true
      pairs.push([from, added])
    }
  }
  const sameName = (from: Change, to: Change) =>
    basename(from.path) === basename(to.path)
  pair(sameName, { moved })
  // Comparing every pair of a large diff costs more than it can save.
  const unpaired = (status: Change['status']) =>
    changes.filter((c) => c.status === status && !c.moved).length
  if (unpaired('added') * unpaired('deleted') <= 2_500)
    pair((from, to) => extname(from.path) === extname(to.path), { moved })
  // Modules this change renamed, by name, so imports of them still match.
  const renamed = new Map(
    [
      ...changes
        .filter((c) => c.status === 'renamed')
        .map((c): [string, string] => [c.oldPath, c.path]),
      ...pairs.map(([from, to]): [string, string] => [from.path, to.path]),
    ]
      .map(([from, to]): [string, string] => [moduleName(from), moduleName(to)])
      .filter(([from, to]) => from !== to)
  )
  const moves: Moves = { renamed, moved }
  // Moves whose imports name a module renamed in the same change.
  pair(sameName, moves)
  for (const change of changes) {
    const source = sources.get(change)
    if (change.moved || change.oldSource === undefined || source === undefined)
      continue
    change.moved = onlyMoved(
      change.oldPath,
      change.path,
      change.oldSource,
      source,
      moves
    )
  }
  return changes
}

let frontendGraph: Graph | undefined
export function frontend() {
  if (!frontendGraph) {
    const configPath = join(repoRoot, contract.frontend.tsconfig)
    const parsed = ts.parseJsonConfigFileContent(
      ts.readConfigFile(configPath, ts.sys.readFile).config,
      ts.sys,
      dirname(configPath)
    )
    frontendGraph = buildGraph(
      repoRoot,
      contract.frontend.sources.map((s) => join(repoRoot, s)),
      parsed.options
    )
  }
  return frontendGraph
}

interface BackendResult {
  endpoints: {
    method: string
    path: string
    handler: string
    chain: string[]
  }[]
  /** changed file -> module file reached -> first chain reaching it */
  reached: Record<string, Record<string, string[]>>
  unreachable: string[]
}

/** Files the backend loads by a path relative to its own code, and the loading line. */
let backendAssets: { path: string; module: string; line: number }[] | undefined
function assetLoaders(path: string) {
  backendAssets ??= (
    JSON.parse(
      execFileSync(
        process.env.RT_SELECTOR_PYTHON ?? 'python3',
        [join(here, 'backend_graph.py'), '--assets', join(repoRoot, 'rdst')],
        { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 }
      )
    ) as { path: string; module: string; line: number }[]
  ).map((a) => ({
    ...a,
    path: a.path.replace(`${repoRoot}/`, ''),
    module: a.module.replace(`${repoRoot}/`, ''),
  }))
  return backendAssets.filter(
    (a) => path === a.path || path.startsWith(`${a.path}/`)
  )
}

function backend(changes: Change[]): BackendResult {
  const request = {
    root: join(repoRoot, 'rdst'),
    changes: Object.fromEntries(
      changes.map((c) => [
        join(repoRoot, c.path),
        { new: c.newRanges, old: c.oldRanges, old_source: c.oldSource ?? null },
      ])
    ),
  }
  const output = execFileSync(
    process.env.RT_SELECTOR_PYTHON ?? 'python3',
    [join(here, 'backend_graph.py')],
    {
      input: JSON.stringify(request),
      encoding: 'utf8',
      maxBuffer: 64 * 1024 * 1024,
    }
  )
  const result = JSON.parse(output) as BackendResult
  const relative = (p: string) => p.replace(`${repoRoot}/`, '')
  for (const e of result.endpoints) e.chain = e.chain.map(relative)
  result.reached = Object.fromEntries(
    Object.entries(result.reached).map(([file, files]) => [
      relative(file),
      Object.fromEntries(
        Object.entries(files).map(([path, chain]) => [
          relative(path),
          chain.map(relative),
        ])
      ),
    ])
  )
  result.unreachable = result.unreachable.map(relative)
  return result
}

export function select(
  changes: Change[],
  options: { budget?: number; evidence?: Evidence; now?: Date } = {}
): Selection {
  const budget = options.budget ?? contract.budget.areas
  const evidence = options.evidence ?? loadEvidence()
  const reasons = new Map<string, string[]>()
  const distance = new Map<string, number>()
  const broad: string[] = []
  const unknown: Selection['unknown'] = []
  const ignored: Selection['ignored'] = []
  // Changed files reaching each area, weighted: how much of the change is its.
  const origins = new Map<string, Map<string, number>>()
  const addReason = (area: string, why: string, steps: number, weight = 1) => {
    reasons.set(area, [...(reasons.get(area) ?? []), why])
    distance.set(area, Math.min(distance.get(area) ?? steps, steps))
    const origin = why.split(/ -> |: |::/)[0]
    const byOrigin = origins.get(area) ?? new Map<string, number>()
    byOrigin.set(origin, Math.max(byOrigin.get(origin) ?? 0, weight))
    origins.set(area, byOrigin)
  }
  // Areas that must run because they test the capability owning a change.
  const owners = new Set<string>()
  const addOwner = (path: string) => {
    const capability = ownerOf(canonical(path))
    if (!capability) return
    for (const area of capability.areas) {
      owners.add(area)
      addReason(area, `${path}: owned by ${capability.name}`, 0)
    }
  }
  const frontendChanges: Change[] = []
  const backendChanges: Change[] = []
  // A lockfile follows its manifests: it can only carry what they changed.
  const manifests: boolean[] = []
  const lockfiles: Change[] = []
  // Changes classification set aside, such as tests or metadata; flows skip them.
  const setAside = new Set<string>()
  // An area's instructions, when more than one changed at once.
  const areaFiles: { area: string; why: string }[] = []
  for (const change of changes) {
    // A file the backend loads by path is part of the code that loads it.
    const loaders =
      change.path.startsWith('rdst/') && !change.path.endsWith('.py')
        ? assetLoaders(change.path)
        : []
    if (loaders.length > 0) {
      for (const loader of loaders)
        backendChanges.push({
          path: loader.module,
          oldPath: loader.module,
          status: 'modified',
          newRanges: [[loader.line, loader.line]],
          oldRanges: [],
        })
      ignored.push({
        file: change.path,
        reason: `loaded by ${[...new Set(loaders.map((l) => l.module))].join(', ')}`,
      })
      continue
    }
    const { rule, additive } = rulesFor(change.path)
    if (
      change.moved &&
      !['area-file', 'manifest', 'lockfile'].includes(rule.action)
    ) {
      setAside.add(change.path)
      ignored.push({
        file: change.path,
        reason: 'moved: the same code, or only its import paths changed',
      })
      continue
    }
    for (const extra of additive) {
      for (const area of extra.areas ?? []) {
        addReason(area, `${change.path}: ${extra.reason}`, 2, extra.weight)
      }
    }
    switch (rule.action) {
      case 'area-file': {
        const area = basename(change.path, '.md')
        if (area in contract.areas)
          areaFiles.push({ area, why: `${change.path}: ${rule.reason}` })
        break
      }
      case 'areas':
        for (const area of rule.areas ?? []) {
          addReason(area, `${change.path}: ${rule.reason}`, 1)
        }
        break
      case 'broad':
        broad.push(`${change.path}: ${rule.reason}`)
        break
      case 'manifest': {
        const current = readIfExists(join(repoRoot, change.path))
        const verdict = change.path.endsWith('package.json')
          ? classifyPackageJson(change.oldSource, current)
          : classifyPyproject(
              change.oldSource,
              current,
              change.oldRanges,
              change.newRanges
            )
        manifests.push(verdict.runtime)
        const what = verdict.changed.slice(0, 6).join(', ')
        if (verdict.runtime)
          broad.push(`${change.path}: changes what ships (${what})`)
        else {
          setAside.add(change.path)
          ignored.push({
            file: change.path,
            reason: `metadata or test tooling only (${what})`,
          })
        }
        break
      }
      case 'lockfile':
        lockfiles.push(change)
        break
      case 'as-module':
        if (rule.module) {
          const loader = {
            path: rule.module,
            oldPath: rule.module,
            status: 'modified',
            newRanges: [[1, 1_000_000]],
            oldRanges: [],
          } satisfies Change
          ;(rule.module.endsWith('.py')
            ? backendChanges
            : frontendChanges
          ).push(loader)
          ignored.push({
            file: change.path,
            reason: `analyzed as ${rule.module}, which loads it`,
          })
        }
        break
      case 'frontend-graph':
        frontendChanges.push(change)
        break
      case 'backend-graph':
        backendChanges.push(change)
        break
      case 'unknown':
        // A file a capability owns is that capability's, whatever its kind.
        if (ownerOf(canonical(change.path))) addOwner(change.path)
        else unknown.push({ file: change.path, reason: rule.reason ?? '' })
        break
      default:
        setAside.add(change.path)
        ignored.push({ file: change.path, reason: rule.reason ?? '' })
    }
  }

  // Instructions edited across areas change the tester, not any area's
  // subject: the shortest of them proves they still work.
  const minutes = (a: string) => contract.areas[a]?.minutes ?? 0
  const [proof, ...others] = areaFiles.sort(
    (a, b) => minutes(a.area) - minutes(b.area) || a.area.localeCompare(b.area)
  )
  if (proof) addReason(proof.area, proof.why, 0)
  for (const { why } of others) {
    const [file] = why.split(': ')
    ignored.push({
      file,
      reason: `one of ${areaFiles.length} areas' instructions changed; ${proof.area} runs for them`,
    })
  }

  for (const lockfile of lockfiles) {
    if (manifests.length > 0 && !manifests.includes(true)) {
      setAside.add(lockfile.path)
      ignored.push({
        file: lockfile.path,
        reason: 'follows manifests that changed only metadata or test tooling',
      })
    } else {
      broad.push(`${lockfile.path}: resolved dependency versions changed`)
    }
  }

  // Areas follow ownership: the capability owning a changed file, or for code
  // none owns, the owners nearest to it in the import and API graphs of this
  // tree (or, replaying an older tree, the consumers it declared).
  const unowned: Change[] = []
  for (const change of [...frontendChanges, ...backendChanges]) {
    if (ownerOf(canonical(change.path))) {
      addOwner(change.path)
      continue
    }
    const consumer = consumerOf(canonical(change.path))
    if (consumer?.action === 'none') {
      setAside.add(change.path)
      ignored.push({ file: change.path, reason: consumer.reason })
    } else if (consumer?.action === 'areas') {
      for (const area of consumer.areas ?? [])
        addReason(area, `${change.path}: ${consumer.reason}`, 1)
    } else if (consumer) broad.push(`${change.path}: ${consumer.reason}`)
    else unowned.push(change)
  }
  const walked = (list: Change[]) =>
    list.filter((c) => c.status !== 'deleted' && !setAside.has(c.path))
  const routesReached = new Map<string, Set<string>>()
  const reachedFiles = reachedBy(
    walked(frontendChanges),
    walked(backendChanges),
    routesReached
  )
  // How specific to an area each endpoint is, from the endpoints its real
  // runs requested: one every area requests says nothing about any of them.
  const requesting = new Map<string, number>()
  for (const spec of Object.values(evidence.areas))
    for (const endpoint of Object.keys(spec.endpoints))
      requesting.set(endpoint, (requesting.get(endpoint) ?? 0) + 1)
  const areaCount = Object.keys(evidence.areas).length
  const routeScore = (area: string, routes: Set<string>) =>
    [...routes].reduce(
      (sum, route) =>
        evidence.areas[area]?.endpoints[route]
          ? sum + Math.log(areaCount / (requesting.get(route) ?? areaCount))
          : sum,
      0
    )
  const roots = new Set(contract.frontend.roots)
  for (const change of unowned) {
    // Nothing imports an entry point; every page starts from it.
    if (roots.has(canonical(change.path))) {
      broad.push(`${change.path}: the app's entry point`)
      continue
    }
    const nearest = nearestOwners(
      change.path,
      reachedFiles.get(canonical(change.path))
    )
    // A page nothing imports belongs to the areas it is an entry of and to
    // the capabilities whose code it renders.
    const rendered = nearest.size === 0 ? pageOwners(change.path) : []
    for (const { area, why } of rendered) {
      owners.add(area)
      addReason(area, `${change.path}: ${why}`, 0)
    }
    if (nearest.size === 0 && rendered.length === 0) {
      ignored.push({
        file: change.path,
        reason: 'no page or API route reaches it',
      })
    }
    if (nearest.size === 0) continue
    // Code whose users span half the areas is what every page stands on.
    const spanned = new Set([...nearest.keys()].flatMap((c) => c.areas))
    if (spanned.size * 2 >= Object.keys(contract.areas).length) {
      broad.push(
        `${change.path}: used by ${nearest.size} capabilities, across ${spanned.size} areas`
      )
      continue
    }
    // Code only one capability uses is that capability's.
    if (nearest.size === 1) {
      const [[capability, use]] = [...nearest]
      for (const area of capability.areas) {
        owners.add(area)
        addReason(
          area,
          `${change.path}: used only by ${capability.name} (${use.via})`,
          0
        )
      }
      continue
    }
    // The users that more of the change's declarations reach rank higher,
    // and among them the areas whose runs request the routes it reaches.
    const top = Math.max(...[...nearest.values()].map((u) => u.declarations))
    const routes = routesReached.get(canonical(change.path)) ?? new Set()
    const scores = new Map(
      [...nearest.keys()].flatMap((c) =>
        c.areas.map((a): [string, number] => [a, routeScore(a, routes)])
      )
    )
    const topScore = Math.max(0, ...scores.values())
    for (const [capability, use] of nearest) {
      for (const area of capability.areas) {
        const share = use.declarations / top
        addReason(
          area,
          `${change.path}: used by ${capability.name} (${use.via})`,
          1,
          topScore > 0
            ? (share + (scores.get(area) ?? 0) / topScore) / 2
            : share
        )
      }
    }
  }

  // Among equals, the area run longest ago ranks first.
  const now = options.now ?? new Date()
  const daysSince = (area: string) => {
    const last = evidence.areas[area]?.last_run
    return last
      ? (now.getTime() - new Date(last).getTime()) / 86_400_000
      : Number.POSITIVE_INFINITY
  }
  // A changed file counts less the more areas it reaches: one every page
  // imports says little about any of them, one only a single area uses is
  // that area's change.
  const reach = new Map<string, number>()
  for (const byOrigin of origins.values()) {
    for (const origin of byOrigin.keys())
      reach.set(origin, (reach.get(origin) ?? 0) + 1)
  }
  const strength = (area: string) =>
    [...(origins.get(area) ?? new Map<string, number>())].reduce(
      (sum, [origin, w]) => sum + w / Math.sqrt(reach.get(origin) ?? 1),
      0
    )
  // How closely a change reaches an area comes before how long ago it ran.
  const affected = [...reasons.keys()].sort(
    (a, b) =>
      strength(b) - strength(a) ||
      (distance.get(a) ?? 0) - (distance.get(b) ?? 0) ||
      daysSince(b) - daysSince(a)
  )
  let status: Status = 'none'
  if (unknown.length > 0) status = 'unknown'
  else if (broad.length > 0 || affected.length > budget) status = 'broad'
  else if (affected.length > 0) status = 'targeted'

  // Owners are obligations, not candidates: they run whatever the ranking
  // says, up to a cap; the rest of the budget goes to consumers by strength.
  const maxAreas = contract.budget.max_areas ?? 6
  const ownerOrder = affected.filter((a) => owners.has(a))
  const overflow = ownerOrder.slice(maxAreas)
  let recommended: string[] = ownerOrder.slice(0, maxAreas)
  if (status === 'targeted') recommended = affected
  else if (status !== 'none') {
    // A broad change (shared infrastructure, build configuration, what ships)
    // tops up with the smoke set; otherwise it stands in only when no area was
    // reached.
    const fill =
      affected.length === 0
        ? contract.smoke
        : broad.length > 0
          ? [...affected, ...contract.smoke]
          : affected
    for (const area of fill) {
      if (recommended.length >= budget) break
      if (!recommended.includes(area)) recommended.push(area)
    }
  }
  // A flow names the code it covers, so an edit to it is worth testing even
  // when no owner or consumer thought so.
  const mission = missionFor(
    changes.filter((c) => !setAside.has(c.path)),
    reachedFiles,
    evidence
  )
  if (status === 'none' && mission.flows.length > 0) status = 'targeted'
  const all = broad.length > 0 ? Object.keys(contract.areas) : affected
  return {
    status,
    affected_areas: [...new Set([...affected, ...all])],
    recommended_areas: recommended,
    deferred_areas: [...new Set([...affected, ...all])].filter(
      (a) => !recommended.includes(a)
    ),
    reasons: Object.fromEntries(reasons),
    broad_reasons: broad,
    unknown,
    ignored,
    estimated_minutes: recommended.reduce(
      (sum, a) => sum + (contract.areas[a]?.minutes ?? 0),
      0
    ),
    ...mission,
    strength: Object.fromEntries(affected.map((a) => [a, strength(a)])),
    owners: ownerOrder,
    overflow,
    audit_suggestion: auditSuggestion(recommended, daysSince, evidence),
  }
}

// An area's entry page stands for its areas wherever no capability owns it.
const pages = new Map<string, Capability>()
function pageOf(file: string): Capability | undefined {
  if (!pages.size) {
    for (const [area, spec] of Object.entries(contract.areas)) {
      for (const entry of spec.entries) {
        const page = pages.get(entry) ?? {
          name: `page ${entry}`,
          manifests: [],
          areas: [],
          owns: [entry],
          flows: [],
        }
        page.areas.push(area)
        pages.set(entry, page)
      }
    }
  }
  return pages.get(file)
}

/**
 * The areas a page belongs to: those listing it as an entry, and those of
 * the capabilities whose owned code it renders, found by following what it
 * imports through unowned code, a step or two deep.
 */
function pageOwners(path: string) {
  const page = canonical(path)
  const found: { area: string; why: string }[] = []
  for (const [area, spec] of Object.entries(contract.areas))
    if (spec.entries.includes(page))
      found.push({ area, why: `an entry page of ${area}` })
  const graph = frontend()
  const seen = new Set([page])
  let frontier = [page]
  for (let depth = 0; depth < 3 && frontier.length > 0; depth++) {
    const next: string[] = []
    for (const file of frontier) {
      for (const used of graph.files.get(file)?.refs.values() ?? []) {
        for (const n of used) {
          const target = fileOf(n)
          if (seen.has(target)) continue
          seen.add(target)
          const capability = ownerOf(canonical(target))
          if (capability) {
            for (const area of capability.areas)
              found.push({
                area,
                why: `renders ${capability.name} (${target})`,
              })
          } else next.push(target)
        }
      }
    }
    // The nearest capabilities it renders are its own; deeper ones are theirs.
    if (found.length > 0) break
    frontier = next
  }
  return found
}

/** Each change's nearest owners by the graphs alone. */
export function graphOwners(changes: Change[]) {
  const of = (action: string) =>
    changes.filter((c) => rulesFor(c.path).rule.action === action)
  const reached = reachedBy(of('frontend-graph'), of('backend-graph'))
  return new Map(
    changes.map((c) => [
      c.path,
      nearestOwners(c.path, reached.get(canonical(c.path))),
    ])
  )
}

/**
 * The capabilities whose code an unowned file reaches first: each owned file
 * its walk reached with no other owned file on the way, so a helper used by
 * one feature is that feature's, whatever pages the feature is on. For each,
 * the first such file and how many of the change's declarations reach it.
 */
export function nearestOwners(
  origin: string,
  reached: Map<string, string[]> | undefined
) {
  const seeds = new Map<Capability, { via: string; seeds: Set<string> }>()
  const self = canonical(origin)
  for (const [file, chain] of reached ?? []) {
    const capability = ownerOf(file) ?? pageOf(file)
    if (!capability || file === self) continue
    const between = chain
      .slice(1, -1)
      .map((step) => canonical(step.split('::')[0]))
      .filter((f) => f !== self && f !== file)
    if (between.some((f) => ownerOf(f) ?? pageOf(f))) continue
    const use = seeds.get(capability) ?? { via: file, seeds: new Set() }
    use.seeds.add(chain[0])
    seeds.set(capability, use)
  }
  return new Map(
    [...seeds].map(([c, u]) => [c, { via: u.via, declarations: u.seeds.size }])
  )
}

/**
 * For each changed code file, the files it reaches, with the first chain that
 * reached each: up the import graph to the area entry points, and from Python
 * through the API routes it reaches to the frontend code requesting them.
 * These only suggest flows; areas follow ownership.
 */
function reachedBy(
  frontendChanges: Change[],
  backendChanges: Change[],
  /** Filled with the API routes each changed backend file reaches. */
  routes?: Map<string, Set<string>>
) {
  const reached = new Map<string, Map<string, string[]>>()
  const note = (origin: string, path: string, chain: string[]) => {
    const files = reached.get(canonical(origin)) ?? new Map<string, string[]>()
    if (!files.has(canonical(path))) files.set(canonical(path), chain)
    reached.set(canonical(origin), files)
  }
  if (frontendChanges.length === 0 && backendChanges.length === 0)
    return reached
  const graph = frontend()
  // Seeds per changed file: two changes reaching the same caller both keep it.
  const groups = new Map<string, Map<Node, string[]>>()
  const seed = (origin: string, n: Node, chain: string[]) => {
    const group = groups.get(origin) ?? new Map<Node, string[]>()
    if (!group.has(n)) group.set(n, chain)
    groups.set(origin, group)
  }
  for (const change of frontendChanges) {
    const decls = declsIn(graph, change.path, change.newRanges)
    if (change.oldSource !== undefined && change.oldRanges.length > 0) {
      for (const d of declsIn(
        graph,
        change.oldPath,
        change.oldRanges,
        change.oldSource
      )) {
        decls.add(d)
      }
    }
    const info = graph.files.get(change.path)
    const all = decls.has(WHOLE)
      ? [WHOLE, ...(info?.spans.keys() ?? [])]
      : [...decls]
    for (const decl of all) {
      const label = decl === WHOLE ? change.path : `${change.path}::${decl}`
      seed(change.path, node(change.path, decl), [label])
    }
  }
  if (backendChanges.length > 0) {
    const result = backend(backendChanges)
    const unreachable = new Set(result.unreachable)
    for (const change of backendChanges) {
      if (unreachable.has(change.path)) continue
      for (const [path, chain] of Object.entries(
        result.reached[change.path] ?? {}
      ))
        note(change.path, path, chain)
    }
    // Each route the change reaches seeds the frontend code that calls it.
    const callers = new Map<string, Node[]>()
    for (const [file, info] of graph.files) {
      for (const [decl, calls] of info.apiCalls) {
        for (const call of calls) {
          callers.set(call, [...(callers.get(call) ?? []), node(file, decl)])
        }
      }
    }
    for (const endpoint of result.endpoints) {
      const route = `${endpoint.method} ${endpoint.path}`
      const origin = endpoint.chain[0].split('::')[0]
      const reachedRoutes = routes?.get(canonical(origin)) ?? new Set<string>()
      reachedRoutes.add(route)
      routes?.set(canonical(origin), reachedRoutes)
      for (const [call, nodes] of callers) {
        if (!matchesRoute(call, endpoint.method, endpoint.path)) continue
        for (const n of nodes)
          seed(origin, n, [
            ...endpoint.chain,
            route,
            `${fileOf(n)}::${declOf(n)}`,
          ])
      }
    }
  }
  const entries = new Set(
    Object.values(contract.areas).flatMap((spec) => spec.entries)
  )
  const roots = new Set(contract.frontend.roots)
  const excluded = new Set(Object.keys(contract.frontend.excludedRoutes))
  // Pages end the walk; excluded routes too, being part of no area.
  const stop = (file: string) => {
    const path = canonical(file)
    return entries.has(path) || roots.has(path) || excluded.has(path)
  }
  // Each changed file is walked on its own: a walk keeps one path per node,
  // so a shared walk would credit a node to whichever change reached it first.
  for (const group of groups.values()) {
    for (const [n, chain] of propagate(graph, group, stop))
      note(chain[0].split('::')[0], fileOf(n), chain)
  }
  return reached
}

const MEASURED_RUNS = 3

function missionFor(
  changes: Change[],
  reachedFiles: Map<string, Map<string, string[]>>,
  evidence: Evidence
) {
  // A flow's own estimate stands until enough runs measure it.
  const median = (values: number[]) =>
    values.length < MEASURED_RUNS
      ? undefined
      : [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
  const edited = changes.map((c) => canonical(c.path))
  const measured = (id: string) => median(evidence.flows?.[id]?.minutes ?? [])
  const mission = chooseFlows(
    edited,
    new Map(),
    contract.budget.mission_minutes ?? 45,
    measured
  )
  // Flows only the graphs reach are suggested, never planned.
  const planned = new Set(
    [...mission.chosen, ...mission.deferred].map((f) => f.id)
  )
  const suggested = chooseFlows(
    edited,
    reachedFiles,
    Number.POSITIVE_INFINITY,
    measured
  ).chosen.filter((f) => !planned.has(f.id))
  return {
    flows: mission.chosen,
    deferred_flows: mission.deferred,
    suggested_flows: suggested,
    mission_minutes: mission.minutes,
  }
}

/** Selected areas alone would never re-check the rest; suggest the stalest one. */
function auditSuggestion(
  recommended: string[],
  daysSince: (area: string) => number,
  evidence: Evidence
) {
  const candidates = Object.keys(contract.areas).filter(
    (a) => !recommended.includes(a)
  )
  if (candidates.length === 0) return null
  const area = candidates.sort((a, b) => daysSince(b) - daysSince(a))[0]
  return { area, last_run: evidence.areas[area]?.last_run ?? null }
}

/**
 * Problems with the contract itself: missing files, routes and API routes no
 * area owns, rules that match nothing, and overrides past their review date.
 */
export function checkContract(
  trackedFiles: string[],
  today = new Date()
): string[] {
  const problems: string[] = [
    ...checkOwnership(trackedFiles, Object.keys(contract.areas), flowIds()),
    ...checkFlows(trackedFiles, Object.keys(contract.areas)),
    ...checkAliases(trackedFiles),
    ...checkConsumers(trackedFiles),
  ]
  const date = today.toISOString().slice(0, 10)
  for (const { rule } of compiled) {
    if (rule.action !== 'also' && rule.action !== 'areas') continue
    if (!rule.owner || !rule.review_by) {
      problems.push(`rule "${rule.reason}" needs an owner and a review_by date`)
    } else if (rule.review_by < date) {
      problems.push(
        `rule "${rule.reason}" was due for review on ${rule.review_by} (${rule.owner})`
      )
    }
  }
  const table = routeTable()
  const used = usedRoutes()
  const uncalled = contract.uncalledRoutes ?? {}
  for (const route of table) {
    const key = `${route.method} ${route.path}`
    if (!used.has(key) && !(key in uncalled)) {
      problems.push(
        `API route ${key} has no caller, no run evidence, and no uncalledRoutes entry`
      )
    }
    if (used.has(key) && key in uncalled) {
      problems.push(
        `uncalledRoutes lists ${key}, which is now called; remove it`
      )
    }
  }
  const known = new Set(table.map((r) => `${r.method} ${r.path}`))
  for (const key of Object.keys(uncalled)) {
    if (!known.has(key))
      problems.push(`uncalledRoutes lists ${key}, which no longer exists`)
  }
  const areaFiles = readdirSync(join(here, '..', 'areas'))
    .filter((f) => f.endsWith('.md'))
    .map((f) => basename(f, '.md'))
  for (const area of areaFiles) {
    if (!(area in contract.areas))
      problems.push(`area ${area} is missing from areas.json`)
  }
  for (const [area, spec] of Object.entries(contract.areas)) {
    if (!areaFiles.includes(area))
      problems.push(`areas.json names ${area}, which has no area file`)
    for (const entry of spec.entries) {
      if (!existsSync(join(repoRoot, entry)))
        problems.push(`${area}: entry ${entry} does not exist`)
    }
  }
  for (const area of contract.smoke) {
    if (!(area in contract.areas))
      problems.push(`smoke area ${area} is not an area`)
  }
  const owned = new Set(Object.values(contract.areas).flatMap((a) => a.entries))
  const routesDir = join(repoRoot, contract.frontend.routes)
  for (const file of readdirSync(routesDir)) {
    if (!/\.tsx?$/.test(file) || /\.test\./.test(file)) continue
    const path = `${contract.frontend.routes}/${file}`
    if (!owned.has(path) && !(path in contract.frontend.excludedRoutes)) {
      problems.push(`route ${path} has no area and is not excluded`)
    }
  }
  for (const excluded of Object.keys(contract.frontend.excludedRoutes)) {
    if (!existsSync(join(repoRoot, excluded)))
      problems.push(`excluded route ${excluded} does not exist`)
  }
  for (const { rule, patterns } of compiled) {
    patterns.forEach((pattern, i) => {
      if (!trackedFiles.some((f) => pattern.test(f))) {
        problems.push(`rule pattern ${rule.match[i]} matches no file`)
      }
    })
    for (const area of rule.areas ?? []) {
      if (!(area in contract.areas))
        problems.push(`rule names unknown area ${area}`)
    }
  }
  return problems
}

/** The replay-only history matches no current file and names known areas. */
function checkConsumers(trackedFiles: string[]) {
  const problems: string[] = []
  for (const { consumer, patterns } of readHistory(historyFile)) {
    patterns.forEach((pattern, i) => {
      if (trackedFiles.some((f) => pattern.test(f)))
        problems.push(
          `history pattern ${consumer.match[i]} matches a current file`
        )
    })
    for (const area of consumer.areas ?? []) {
      if (!(area in contract.areas))
        problems.push(`history names unknown area ${area}`)
    }
  }
  return problems
}

/** Markdown for the build annotation. */
export function explain(selection: Selection) {
  const lines = [
    `**Relentless UI areas: ${selection.status}**`,
    '',
    selection.recommended_areas.length > 0
      ? `Recommended: ${selection.recommended_areas.map((a) => `\`${a}\``).join(', ')} (about ${selection.estimated_minutes} min)`
      : 'Recommended: none',
  ]
  if (selection.flows.length > 0) {
    lines.push(
      `Mission: ${selection.flows.map((f) => `\`${f.id}\``).join(', ')} (about ${selection.mission_minutes} min)`
    )
  }
  if (selection.suggested_flows.length > 0) {
    lines.push(
      `Suggested flows, which the change reaches without editing: ${selection.suggested_flows.map((f) => `\`${f.id}\``).join(', ')}`
    )
  }
  if (selection.owners.length > 0) {
    lines.push(
      `Owners (run first): ${selection.owners.map((a) => `\`${a}\``).join(', ')}`
    )
  }
  if (selection.overflow.length > 0) {
    lines.push(
      `Owners over the cap, not recommended: ${selection.overflow.map((a) => `\`${a}\``).join(', ')}`
    )
  }
  if (selection.deferred_areas.length > 0) {
    lines.push(
      `Also affected, not recommended: ${selection.deferred_areas.map((a) => `\`${a}\``).join(', ')}`
    )
  }
  for (const why of selection.broad_reasons.slice(0, 10))
    lines.push(`- Broad: ${why}`)
  for (const u of selection.unknown.slice(0, 10))
    lines.push(`- Unknown: \`${u.file}\` ${u.reason}`)
  for (const [area, list] of Object.entries(selection.reasons)) {
    lines.push('', `\`${area}\`:`)
    for (const why of list.slice(0, 3)) lines.push(`- ${why}`)
    if (list.length > 3) lines.push(`- and ${list.length - 3} more`)
  }
  if (selection.audit_suggestion) {
    const { area, last_run } = selection.audit_suggestion
    lines.push(
      '',
      `Rotation check, if there is room: \`${area}\` (last run ${last_run ? last_run.slice(0, 10) : 'never'})`
    )
  }
  if (selection.ignored.length > 0) {
    lines.push(
      '',
      `Not affecting any area: ${selection.ignored.length} (see selection.json)`
    )
  }
  return `${lines.join('\n')}\n`
}

/** The API routes rdst mounts. */
export function routeTable() {
  return JSON.parse(
    execFileSync(
      process.env.RT_SELECTOR_PYTHON ?? 'python3',
      [join(here, 'backend_graph.py'), '--routes', join(repoRoot, 'rdst')],
      { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }
    )
  ) as { method: string; path: string }[]
}

/** Routes some frontend code calls or some run requested. */
export function usedRoutes(evidence = loadEvidence()) {
  const graph = frontend()
  const calls = [...graph.files.values()].flatMap((info) =>
    [...info.apiCalls.values()].flatMap((set) => [...set])
  )
  const used = new Set<string>()
  for (const route of routeTable()) {
    const key = `${route.method} ${route.path}`
    const requested = Object.values(evidence.areas).some(
      (a) => a.endpoints[key]
    )
    if (
      requested ||
      calls.some((call) => matchesRoute(call, route.method, route.path))
    ) {
      used.add(key)
    }
  }
  return used
}
