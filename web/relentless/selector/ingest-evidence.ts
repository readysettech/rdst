import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * Folds relentless run directories into `evidence.json`: for each area, the
 * API routes its runs requested and how often. Only the method and route
 * template are kept, never bodies, query strings, or ids. A run is counted
 * once however often it is ingested.
 *
 *   node ingest-evidence.ts RUN_DIR_OR_PARENT...
 *
 * Evidence only adds: an endpoint no run requested is not evidence that an
 * area is independent of it, since a blocked or short run may have stopped
 * before reaching it. Areas the contract no longer has are dropped.
 */

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = join(here, '../../../../..')
const indexPath = join(here, 'evidence.json')

export interface EndpointEvidence {
  runs: number
  renderer: number
  rt_api: number
}

export interface AreaEvidence {
  runs: string[]
  last_run: string | null
  prompt_sha256: string[]
  endpoints: Record<string, EndpointEvidence>
}

export interface FlowEvidence {
  runs: string[]
  /**
   * Measured minutes per run that finished the flow, passed or failed, from
   * its first event to its outcome. A blocked flow's time says nothing of
   * its cost.
   */
  minutes: number[]
  statuses: Record<string, number>
}

export interface Evidence {
  areas: Record<string, AreaEvidence>
  flows?: Record<string, FlowEvidence>
  unmatched: Record<string, number>
}

export function loadEvidence(path = indexPath): Evidence {
  if (!existsSync(path)) return { areas: {}, unmatched: {} }
  return JSON.parse(readFileSync(path, 'utf8')) as Evidence
}

interface Route {
  method: string
  path: string
}

function routes(): Route[] {
  const output = execFileSync(
    process.env.RT_SELECTOR_PYTHON ?? 'python3',
    [join(here, 'backend_graph.py'), '--routes', join(repoRoot, 'rdst')],
    { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 }
  )
  return JSON.parse(output) as Route[]
}

/** The mounted route a concrete request hit; literal segments win over parameters. */
export function templateFor(method: string, path: string, table: Route[]) {
  const parts = path.split('?')[0].replace(/\/$/, '').split('/')
  let best: { route: Route; literal: number } | null = null
  for (const route of table) {
    if (route.method !== method && route.method !== 'API_ROUTE') continue
    const template = route.path.replace(/\/$/, '').split('/')
    if (template.length !== parts.length) continue
    let literal = 0
    const matches = template.every((segment, i) => {
      if (/^\{.*\}$/.test(segment)) return true
      if (segment === decodeURIComponent(parts[i])) {
        literal += 1
        return true
      }
      return false
    })
    if (matches && (!best || literal > best.literal)) best = { route, literal }
  }
  return best ? `${method} ${best.route.path}` : null
}

function runDirs(paths: string[]) {
  const found: string[] = []
  const visit = (dir: string, depth: number) => {
    if (
      existsSync(join(dir, 'events.jsonl')) &&
      existsSync(join(dir, 'verdict.json'))
    ) {
      found.push(dir)
      return
    }
    if (depth === 0) return
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) visit(join(dir, entry.name), depth - 1)
    }
  }
  for (const path of paths) visit(path, 3)
  return found
}

export function ingest(paths: string[], evidence = loadEvidence()) {
  const table = routes()
  let added = 0
  for (const dir of runDirs(paths)) {
    const verdict = JSON.parse(
      readFileSync(join(dir, 'verdict.json'), 'utf8')
    ) as {
      area?: string
      area_prompt_sha256?: string
    }
    if (verdict.area === 'mission') {
      if (ingestMission(dir, evidence)) added += 1
      continue
    }
    if (
      !verdict.area ||
      !existsSync(join(here, '..', 'areas', `${verdict.area}.md`))
    ) {
      continue
    }
    const events = readFileSync(join(dir, 'events.jsonl'), 'utf8')
      .split('\n')
      .filter(Boolean)
      .map((line) => JSON.parse(line) as Record<string, unknown>)
    const first = String(events[0]?.ts ?? '')
    const id = createHash('sha256')
      .update(`${verdict.area}|${basename(dir)}|${first}`)
      .digest('hex')
      .slice(0, 12)
    evidence.areas[verdict.area] ??= {
      runs: [],
      last_run: null,
      prompt_sha256: [],
      endpoints: {},
    }
    const area = evidence.areas[verdict.area]
    if (area.runs.includes(id)) continue
    area.runs.push(id)
    added += 1
    if (!area.last_run || first > area.last_run) area.last_run = first
    if (
      verdict.area_prompt_sha256 &&
      !area.prompt_sha256.includes(verdict.area_prompt_sha256)
    ) {
      area.prompt_sha256.push(verdict.area_prompt_sha256)
    }
    const seen = new Map<string, Set<string>>()
    for (const event of events) {
      if (event.kind !== 'api' || typeof event.path !== 'string') continue
      const method = String(event.method ?? 'GET').toUpperCase()
      const key = templateFor(method, event.path, table)
      if (!key) {
        const loose = `${method} ${event.path.split('?')[0].replace(/\/[0-9a-f-]{8,}(?=\/|$)/gi, '/{}')}`
        evidence.unmatched[loose] = (evidence.unmatched[loose] ?? 0) + 1
        continue
      }
      // Runs before attribution recorded only the page's own requests.
      const source = event.source === 'rt-api' ? 'rt_api' : 'renderer'
      const sources = seen.get(key) ?? new Set()
      sources.add(source)
      seen.set(key, sources)
    }
    for (const [key, sources] of seen) {
      area.endpoints[key] ??= {
        runs: 0,
        renderer: 0,
        rt_api: 0,
      }
      const entry = area.endpoints[key]
      entry.runs += 1
      for (const source of sources) entry[source as 'renderer' | 'rt_api'] += 1
    }
  }
  return { evidence, added }
}

/**
 * A mission's flows: how long each took and how it ended. A flow starts at
 * its first stamped event, or else where the previous flow ended.
 */
function ingestMission(dir: string, evidence: Evidence) {
  const flowsFile = join(dir, 'flows.jsonl')
  if (!existsSync(flowsFile)) return false
  const outcomes = readFileSync(flowsFile, 'utf8')
    .split('\n')
    .filter(Boolean)
    .map(
      (line) => JSON.parse(line) as { id: string; status: string; at: string }
    )
  const events = readFileSync(join(dir, 'events.jsonl'), 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((line) => JSON.parse(line) as { ts: string; flow?: string })
  const runId = createHash('sha256')
    .update(`mission|${basename(dir)}|${events[0]?.ts ?? ''}`)
    .digest('hex')
    .slice(0, 12)
  evidence.flows ??= {}
  let previous = events[0]?.ts
  let added = false
  for (const outcome of outcomes) {
    evidence.flows[outcome.id] ??= {
      runs: [],
      minutes: [],
      statuses: {},
    }
    const record = evidence.flows[outcome.id]
    const first = events.find((e) => e.flow === outcome.id)?.ts ?? previous
    previous = outcome.at
    if (record.runs.includes(runId)) continue
    record.runs.push(runId)
    record.statuses[outcome.status] = (record.statuses[outcome.status] ?? 0) + 1
    if (first && outcome.status !== 'blocked') {
      const minutes =
        (new Date(outcome.at).getTime() - new Date(first).getTime()) / 60_000
      record.minutes.push(Math.round(minutes * 10) / 10)
    }
    added = true
  }
  return added
}

function sorted<T>(record: Record<string, T>): Record<string, T> {
  return Object.fromEntries(
    Object.entries(record).sort(([a], [b]) => a.localeCompare(b))
  )
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const paths = process.argv.slice(2)
  if (paths.length === 0)
    throw new Error('usage: ingest-evidence.ts RUN_DIR_OR_PARENT...')
  const { evidence, added } = ingest(paths)
  const contract = JSON.parse(
    readFileSync(join(here, 'areas.json'), 'utf8')
  ) as { areas: Record<string, unknown> }
  for (const name of Object.keys(evidence.areas))
    if (!(name in contract.areas)) delete evidence.areas[name]
  for (const area of Object.values(evidence.areas)) {
    area.runs.sort()
    area.endpoints = sorted(area.endpoints)
  }
  evidence.areas = sorted(evidence.areas)
  evidence.unmatched = sorted(evidence.unmatched)
  writeFileSync(indexPath, `${JSON.stringify(evidence, null, 2)}\n`)
  console.log(`ingested ${added} new runs into ${indexPath}`)
}
