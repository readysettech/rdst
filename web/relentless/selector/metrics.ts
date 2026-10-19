import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Measures the selector from tester artifacts: what it recommended against
 * what the operator chose, and whether serious findings came from areas it
 * left out. A P1 or P2 found in an area the selector did not recommend is the
 * clearest sign it missed something. Findings from flows only the graphs
 * suggested decide whether the graphs earn their keep.
 *
 *   node metrics.ts ARTIFACT_DIR...
 *
 * Each directory is one tester step's `test-results/relentless`, holding
 * `selection-outcome.json` and the area run directories.
 */

interface Outcome {
  recommended: string[]
  status: string | null
  chosen: string[]
  recommended_flows?: string[]
  suggested_flows?: string[]
  chosen_flows?: string[]
  change_mission?: boolean
}

interface Verdict {
  area?: string
  /** A mission's flows and how each ended. */
  flows?: Record<string, string>
  findings?: Record<string, number>
  /** A mission's findings per flow, `unattributed` for any filed outside one. */
  findings_by_flow?: Record<string, Record<string, number>>
}

const seriousIn = (bySeverity: Record<string, number> = {}) =>
  (bySeverity.P1 ?? 0) + (bySeverity.P2 ?? 0)

function findOutcomes(paths: string[]) {
  const found: string[] = []
  const visit = (dir: string, depth: number) => {
    if (existsSync(join(dir, 'selection-outcome.json'))) found.push(dir)
    if (depth === 0) return
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (entry.isDirectory()) visit(join(dir, entry.name), depth - 1)
    }
  }
  for (const path of paths) visit(path, 4)
  return found
}

const dirs = findOutcomes(process.argv.slice(2))
if (dirs.length === 0) {
  console.error('no selection-outcome.json under the given directories')
  process.exit(1)
}
let added = 0
let removed = 0
let agreed = 0
let choseNothing = 0
let choseNothingAgainst = 0
let flowsAdded = 0
let flowsRemoved = 0
let serious = 0
let seriousOutside = 0
let seriousSuggested = 0
let seriousUnattributed = 0
let seriousChange = 0
let changeMissions = 0
const outside: string[] = []
const statuses: Record<string, number> = {}
for (const dir of dirs) {
  const outcome = JSON.parse(
    readFileSync(join(dir, 'selection-outcome.json'), 'utf8')
  ) as Outcome
  statuses[outcome.status ?? 'unknown'] =
    (statuses[outcome.status ?? 'unknown'] ?? 0) + 1
  const extra = outcome.chosen.filter((a) => !outcome.recommended.includes(a))
  const dropped = outcome.recommended.filter((a) => !outcome.chosen.includes(a))
  added += extra.length
  removed += dropped.length
  const recommendedFlows = outcome.recommended_flows ?? []
  const chosenFlows = outcome.chosen_flows ?? []
  const extraFlows = chosenFlows.filter((f) => !recommendedFlows.includes(f))
  const droppedFlows = recommendedFlows.filter((f) => !chosenFlows.includes(f))
  flowsAdded += extraFlows.length
  flowsRemoved += droppedFlows.length
  if (
    extra.length + dropped.length + extraFlows.length + droppedFlows.length ===
    0
  )
    agreed += 1
  if (outcome.chosen.length === 0 && chosenFlows.length === 0) {
    choseNothing += 1
    if (outcome.recommended.length > 0 || recommendedFlows.length > 0)
      choseNothingAgainst += 1
  }
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const verdictPath = join(dir, entry.name, 'verdict.json')
    if (!entry.isDirectory() || !existsSync(verdictPath)) continue
    const verdict = JSON.parse(readFileSync(verdictPath, 'utf8')) as Verdict
    serious += seriousIn(verdict.findings)
    // A change mission tests the change, not a selection.
    if (verdict.area === 'change') {
      changeMissions += 1
      seriousChange += seriousIn(verdict.findings)
      continue
    }
    // Each flow's findings are credited to that flow, and an area's to the area.
    const credits: [string, number, 'flow' | 'area'][] = verdict.flows
      ? Object.entries(verdict.findings_by_flow ?? {}).map(([f, s]) => [
          f,
          seriousIn(s),
          'flow',
        ])
      : [[verdict.area ?? '', seriousIn(verdict.findings), 'area']]
    for (const [name, count, kind] of credits) {
      if (count === 0) continue
      if (kind === 'flow' && name === 'unattributed') {
        seriousUnattributed += count
      } else if (
        kind === 'flow'
          ? recommendedFlows.includes(name)
          : outcome.recommended.includes(name)
      ) {
      } else if (
        kind === 'flow' &&
        (outcome.suggested_flows ?? []).includes(name)
      ) {
        seriousSuggested += count
      } else {
        seriousOutside += count
        outside.push(`${name} (${count}) in ${dir}`)
      }
    }
  }
}
const lines = [
  '# Relentless selector outcomes',
  '',
  `- Builds: ${dirs.length}; status ${Object.entries(statuses)
    .map(([s, n]) => `${s} ${n}`)
    .join(', ')}`,
  `- The operator ran exactly the recommended areas and flows in ${agreed} of ${dirs.length}`,
  `- Areas the operator added: ${added}; removed: ${removed}`,
  `- Flows the operator added: ${flowsAdded}; removed: ${flowsRemoved}`,
  `- The operator chose nothing in ${choseNothing} builds, ${choseNothingAgainst} of them against a recommendation`,
  `- Change missions: ${changeMissions}, with ${seriousChange} P1/P2 findings`,
  `- P1/P2 findings: ${serious}, of which ${seriousChange} came from change missions, ${seriousSuggested} came from flows only the graphs suggested, ${seriousOutside} from areas or flows the selector did not recommend or suggest, and ${seriousUnattributed} from missions without a flow to credit`,
]
for (const miss of outside) lines.push(`  - ${miss}`)
process.stdout.write(`${lines.join('\n')}\n`)
