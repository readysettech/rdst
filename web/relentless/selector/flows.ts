import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * The flow catalog: `relentless/flows/<capability>/<flow>.md`, each with a
 * header naming its capability, areas, modes, an estimate in minutes, and
 * the code it covers. A change triggers a flow by editing code it covers,
 * or, when what the change reaches is given, by reaching that code; the
 * triggered flows are packed by how much of the change they cover, within a
 * budget of minutes.
 */

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..', 'flows')

export interface Flow {
  id: string
  capability: string
  areas: string[]
  modes: string[]
  minutes: number
  /** Repository paths: a file, or a directory covering everything under it. */
  covers: string[]
  /**
   * Paths that trigger the flow only when edited: hubs like the app root or
   * the API app, which nearly every change reaches.
   */
  direct: string[]
}

let cached: Flow[] | undefined

export function catalog(): Flow[] {
  if (cached) return cached
  const flows: Flow[] = []
  const walk = (dir: string) => {
    if (!existsSync(dir)) return
    for (const entry of readdirSync(dir)) {
      const path = join(dir, entry)
      if (statSync(path).isDirectory()) walk(path)
      else if (entry.endsWith('.md')) {
        const text = readFileSync(path, 'utf8')
        const field = (name: string) =>
          text.match(new RegExp(`^${name}: (.+)$`, 'm'))?.[1]?.trim() ?? ''
        const list = (name: string) =>
          field(name)
            .split(/[\s,]+/)
            .filter(Boolean)
        flows.push({
          id: relative(root, path).replace(/\.md$/, ''),
          capability: field('Capability'),
          areas: list('Areas'),
          modes: list('Modes'),
          minutes: Number(field('Minutes')) || 10,
          covers: list('Covers'),
          direct: list('Direct'),
        })
      }
    }
  }
  walk(root)
  cached = flows.sort((a, b) => a.id.localeCompare(b.id))
  return cached
}

const under = (paths: string[], path: string) =>
  paths.some((c) => path === c || path.startsWith(`${c.replace(/\/$/, '')}/`))

export function covers(flow: Flow, path: string) {
  return under(flow.covers, path)
}

export interface FlowChoice {
  id: string
  minutes: number
  reasons: string[]
}

/**
 * `edited`: changed files; `reached`: for each changed file, the files its
 * walk reached with the chain that reached them.
 */
export function chooseFlows(
  edited: string[],
  reached: Map<string, Map<string, string[]>>,
  budgetMinutes: number,
  measured: (id: string) => number | undefined = () => undefined
) {
  const scored: { flow: Flow; score: number; reasons: string[] }[] = []
  for (const flow of catalog()) {
    const reasons: string[] = []
    let score = 0
    for (const file of edited) {
      if (covers(flow, file) || under(flow.direct, file)) {
        reasons.push(`${file}: covered by the flow`)
        score += 2
        continue
      }
      for (const [path, chain] of reached.get(file) ?? []) {
        if (!covers(flow, path)) continue
        reasons.push(`${chain.join(' -> ')}: reaches ${path}`)
        score += 1
        break
      }
    }
    if (score > 0) scored.push({ flow, score, reasons })
  }
  scored.sort((a, b) => b.score - a.score || a.flow.minutes - b.flow.minutes)
  const chosen: FlowChoice[] = []
  const deferred: FlowChoice[] = []
  let minutes = 0
  for (const { flow, reasons } of scored) {
    // Measured runs, once there are any, replace the flow's own estimate.
    const cost = measured(flow.id) ?? flow.minutes
    const choice = { id: flow.id, minutes: cost, reasons }
    if (minutes + cost <= budgetMinutes || chosen.length === 0) {
      chosen.push(choice)
      minutes += cost
    } else deferred.push(choice)
  }
  return { chosen, deferred, minutes }
}

/** Problems with the catalog, for `--check`. */
export function checkFlows(trackedFiles: string[], areaNames: string[]) {
  const problems: string[] = []
  for (const flow of catalog()) {
    if (!flow.capability) problems.push(`flow ${flow.id} names no capability`)
    for (const area of flow.areas) {
      if (!areaNames.includes(area))
        problems.push(`flow ${flow.id}: unknown area ${area}`)
    }
    for (const mode of flow.modes) {
      if (mode !== 'desktop' && mode !== 'browser')
        problems.push(`flow ${flow.id}: unknown mode ${mode}`)
    }
    for (const path of [...flow.covers, ...flow.direct]) {
      const prefix = `${path.replace(/\/$/, '')}/`
      if (!trackedFiles.some((f) => f === path || f.startsWith(prefix))) {
        problems.push(`flow ${flow.id}: covers ${path}, which does not exist`)
      }
    }
  }
  return problems
}
