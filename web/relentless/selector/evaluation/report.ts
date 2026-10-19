import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * Grades a replay against the labels.
 *
 *   node report.ts REPLAY_DIR [--baseline OTHER_REPLAY_DIR] [--labels FILE]
 *
 * Writes REPLAY_DIR/report.md and prints it. "Recall, affected" is the share
 * of required areas the selector found at all; "recall, recommended" the
 * share it would run. A change is missed when none of its required areas is
 * recommended, whatever else is. Changes from before the web app existed, and
 * ones where most area entry points did not exist yet, are reported
 * separately. A label's areas that the contract no longer has are not graded.
 */

const here = dirname(fileURLToPath(import.meta.url))
const WEB_APP_START = '2026-02-19'

interface Label {
  commit: string
  date: string
  title: string
  required: string[]
  confidence: string
  source: string
}
interface Replay {
  commit: string
  date: string
  flows?: { id: string; minutes: number }[]
  suggested_flows?: { id: string }[]
  mission_minutes?: number
  status?: string
  recommended_areas?: string[]
  affected_areas?: string[]
  estimated_minutes?: number
  entries_present?: Record<string, boolean>
  error?: string
}

const contract = JSON.parse(
  readFileSync(join(here, '..', 'areas.json'), 'utf8')
) as { areas: Record<string, { minutes: number }> }
const minutes = (areas: string[]) =>
  areas.reduce((sum, a) => sum + (contract.areas[a]?.minutes ?? 0), 0)
/** Which pilot flows each change needs, labeled the same way as the areas. */
const flowLabelPath = join(here, 'flow-labels.json')
const flowLabels = existsSync(flowLabelPath)
  ? new Map(
      (
        JSON.parse(readFileSync(flowLabelPath, 'utf8')) as {
          changes: { commit: string; flows: string[] }[]
        }
      ).changes.map((l) => [l.commit, l.flows])
    )
  : null

const labels = new Map(
  (
    JSON.parse(readFileSync(join(here, 'labels.json'), 'utf8')) as {
      changes: Label[]
    }
  ).changes.map((l) => [l.commit, l])
)

export interface Graded {
  label: Label
  replay: Replay
  era: 'pre-app' | 'early-app' | 'current-app'
  kind: 'quiet' | 'false-alarm' | 'missed' | 'partial' | 'hit'
  notRecommended: string[]
  notAffected: string[]
  extra: string[]
}

/** Replays that failed, which grading leaves out. */
export function errors(dir: string) {
  return readFileSync(join(dir, 'replay.jsonl'), 'utf8')
    .split('\n')
    .filter(Boolean)
    .map((line) => JSON.parse(line) as Replay)
    .filter((r) => r.error)
}

export function grade(dir: string, labelSet = labels): Graded[] {
  const graded: Graded[] = []
  for (const line of readFileSync(join(dir, 'replay.jsonl'), 'utf8').split(
    '\n'
  )) {
    if (!line) continue
    const replay = JSON.parse(line) as Replay
    const label = labelSet.get(replay.commit)
    if (!label || replay.error) continue
    const required = new Set(
      label.required.filter((area) => area in contract.areas)
    )
    const recommended = new Set(replay.recommended_areas ?? [])
    const affected = new Set(replay.affected_areas ?? [])
    const entries = Object.values(replay.entries_present ?? {})
    const present = entries.filter(Boolean).length
    const kind: Graded['kind'] =
      required.size === 0
        ? recommended.size === 0
          ? 'quiet'
          : 'false-alarm'
        : ![...required].some((a) => recommended.has(a))
          ? 'missed'
          : [...required].every((a) => recommended.has(a))
            ? 'hit'
            : 'partial'
    graded.push({
      label,
      replay,
      era:
        label.date < WEB_APP_START
          ? 'pre-app'
          : present * 2 <= entries.length
            ? 'early-app'
            : 'current-app',
      kind,
      notRecommended: [...required].filter((a) => !recommended.has(a)),
      notAffected: [...required].filter((a) => !affected.has(a)),
      extra: [...recommended].filter((a) => !required.has(a)),
    })
  }
  return graded
}

export function summarize(rows: Graded[]) {
  const required = rows.reduce((n, r) => n + r.label.required.length, 0)
  const missed = rows.reduce((n, r) => n + r.notRecommended.length, 0)
  const unseen = rows.reduce((n, r) => n + r.notAffected.length, 0)
  const kinds: Record<string, number> = {}
  for (const r of rows) kinds[r.kind] = (kinds[r.kind] ?? 0) + 1
  return {
    changes: rows.length,
    kinds,
    recallAffected: required ? (required - unseen) / required : null,
    recallRecommended: required ? (required - missed) / required : null,
    minutes: rows.reduce(
      (n, r) => n + minutes(r.replay.recommended_areas ?? []),
      0
    ),
    missionMinutes: Math.round(
      rows.reduce((n, r) => n + (r.replay.mission_minutes ?? 0), 0)
    ),
    requiredMinutes: rows.reduce((n, r) => n + minutes(r.label.required), 0),
    allMinutes: rows.length * minutes(Object.keys(contract.areas)),
    falseAlarmMinutes: rows
      .filter((r) => r.kind === 'false-alarm')
      .reduce((n, r) => n + minutes(r.replay.recommended_areas ?? []), 0),
  }
}

const pct = (x: number | null) => (x === null ? '-' : `${Math.round(x * 100)}%`)

export function render(
  rows: Graded[],
  baseline?: Graded[],
  failed: Replay[] = [],
  flowSet = flowLabels
) {
  const groups: [string, (r: Graded) => boolean][] = [
    ['All', () => true],
    ['Before the web app', (r) => r.era === 'pre-app'],
    ['Early app', (r) => r.era === 'early-app'],
    ['Current app', (r) => r.era === 'current-app'],
    ['High-confidence labels', (r) => r.label.confidence === 'high'],
    ['Reviewed labels', (r) => r.label.source === 'reviewed'],
  ]
  const lines = [
    '# Selector replay',
    '',
    '| Changes | n | Quiet | Hit | Partial | Missed | False alarm | Recall, affected | Recall, recommended | Area minutes | Mission minutes | False-alarm minutes |',
    '|---|---|---|---|---|---|---|---|---|---|---|---|',
  ]
  for (const [name, keep] of groups) {
    const s = summarize(rows.filter(keep))
    const b = baseline ? summarize(baseline.filter(keep)) : null
    const delta = (now: number | null, then: number | null | undefined) =>
      then === null || then === undefined || now === null
        ? ''
        : ` (${now >= then ? '+' : ''}${Math.round((now - then) * 100)})`
    lines.push(
      `| ${name} | ${s.changes} | ${s.kinds.quiet ?? 0} | ${s.kinds.hit ?? 0} | ${s.kinds.partial ?? 0} | ${s.kinds.missed ?? 0} | ${s.kinds['false-alarm'] ?? 0} | ${pct(s.recallAffected)}${delta(s.recallAffected, b?.recallAffected)} | ${pct(s.recallRecommended)}${delta(s.recallRecommended, b?.recallRecommended)} | ${s.minutes}${b ? ` (${b.minutes})` : ''} | ${s.missionMinutes}${b ? ` (${b.missionMinutes})` : ''} | ${s.falseAlarmMinutes} |`
    )
  }
  const all = summarize(rows)
  if (failed.length > 0) {
    lines.push(
      '',
      `Errors: ${failed.length} replays failed and are not graded: ${failed
        .slice(0, 10)
        .map((r) => r.commit.slice(0, 10))
        .join(', ')}`
    )
  }
  lines.push(
    '',
    `Minutes: ${all.minutes} in recommended areas and ${all.missionMinutes} in missions; ${all.requiredMinutes} for exactly the labeled areas, ${all.allMinutes} for every area on every change.`,
    '',
    '| Area | Required | Recommended |',
    '|---|---|---|'
  )
  const app = rows.filter((r) => r.era !== 'pre-app')
  const need = new Map<string, number>()
  const got = new Map<string, number>()
  for (const r of app) {
    for (const a of r.label.required) {
      need.set(a, (need.get(a) ?? 0) + 1)
      if (!r.notRecommended.includes(a)) got.set(a, (got.get(a) ?? 0) + 1)
    }
  }
  for (const [area, n] of [...need].sort(
    (a, b) => (got.get(b[0]) ?? 0) / b[1] - (got.get(a[0]) ?? 0) / a[1]
  )) {
    lines.push(`| ${area} | ${n} | ${pct((got.get(area) ?? 0) / n)} |`)
  }
  const list = (kind: Graded['kind'], title: string) => {
    const hits = rows.filter((r) => r.kind === kind && r.era !== 'pre-app')
    if (hits.length === 0) return
    lines.push('', `${title} (${hits.length}):`, '')
    for (const r of hits.slice(0, 40)) {
      lines.push(
        `- \`${r.label.commit}\` ${r.label.title}: needs ${r.label.required.join(', ') || 'nothing'}; recommended ${(r.replay.recommended_areas ?? []).join(', ') || 'nothing'} (${r.replay.status})`
      )
    }
  }
  if (flowSet) lines.push('', ...flowSection(rows, flowSet))
  list('missed', 'Missed: none of the needed areas recommended')
  list('false-alarm', 'False alarms after the web app')
  return `${lines.join('\n')}\n`
}

/** Flow recall, false triggers, and mission minutes, over labeled changes. */
function flowSection(rows: Graded[], flowSet: Map<string, string[]>) {
  let needed = 0
  let found = 0
  let foundWithSuggestions = 0
  let suggestedUnneeded = 0
  let falseTriggers = 0
  let extra = 0
  let extraChanges = 0
  let minutes = 0
  const perFlow = new Map<string, [number, number]>()
  for (const r of rows) {
    const want = flowSet.get(r.label.commit)
    if (!want) continue
    const got = new Set((r.replay.flows ?? []).map((f) => f.id))
    const suggested = new Set((r.replay.suggested_flows ?? []).map((f) => f.id))
    suggestedUnneeded += [...suggested].filter((f) => !want.includes(f)).length
    minutes += (r.replay.flows ?? []).reduce((n, f) => n + f.minutes, 0)
    if (want.length === 0 && got.size > 0) falseTriggers += 1
    const unneeded = [...got].filter((f) => !want.includes(f)).length
    extra += unneeded
    if (unneeded > 0) extraChanges += 1
    for (const flow of want) {
      needed += 1
      const hit = got.has(flow)
      if (hit) found += 1
      if (hit || suggested.has(flow)) foundWithSuggestions += 1
      const [n, h] = perFlow.get(flow) ?? [0, 0]
      perFlow.set(flow, [n + 1, h + (hit ? 1 : 0)])
    }
  }
  const out = [
    `Flows: ${found} of ${needed} needed flows chosen (${pct(needed ? found / needed : null)}); ${falseTriggers} changes needing no flow triggered one; ${extra} unneeded flow selections on ${extraChanges} changes; ${Math.round(minutes)} mission minutes.`,
    `With the graphs' suggestions: ${foundWithSuggestions} of ${needed} (${pct(needed ? foundWithSuggestions / needed : null)}), at ${suggestedUnneeded} unneeded suggestions.`,
    '',
    '| Flow | Needed | Chosen |',
    '|---|---|---|',
  ]
  for (const [flow, [n, h]] of [...perFlow].sort())
    out.push(`| ${flow} | ${n} | ${pct(h / n)} |`)
  return out
}

/**
 * Another labeler's judgments for some of the changes, graded in place of
 * `labels.json` and `flow-labels.json`, with that labeler's confidence and
 * provenance; only dates and titles come from `labels.json`.
 */
function otherLabels(path: string) {
  const other = (
    JSON.parse(readFileSync(path, 'utf8')) as {
      changes: {
        commit: string
        required: string[]
        flows?: string[]
        confidence?: string
      }[]
    }
  ).changes
  const areaSet = new Map<string, Label>()
  const flowSet = new Map<string, string[]>()
  for (const o of other) {
    const base = labels.get(o.commit)
    if (!base) throw new Error(`${o.commit} is not in labels.json`)
    areaSet.set(o.commit, {
      ...base,
      required: o.required,
      confidence: o.confidence ?? 'unknown',
      source: basename(path, '.json'),
    })
    if (o.flows) flowSet.set(o.commit, o.flows)
  }
  return { areaSet, flowSet }
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const dir = process.argv[2]
  if (!dir)
    throw new Error(
      'usage: report.ts REPLAY_DIR [--baseline DIR] [--labels FILE]'
    )
  const flag = (name: string) => {
    const at = process.argv.indexOf(name)
    return at > 0 ? process.argv[at + 1] : undefined
  }
  const labelFile = flag('--labels')
  const other = labelFile ? otherLabels(labelFile) : undefined
  const baselineDir = flag('--baseline')
  const baseline = baselineDir ? grade(baselineDir, other?.areaSet) : undefined
  const report = render(
    grade(dir, other?.areaSet),
    baseline,
    errors(dir),
    other ? other.flowSet : flowLabels
  )
  const name = labelFile
    ? `report-${basename(labelFile, '.json')}.md`
    : 'report.md'
  writeFileSync(join(dir, name), report)
  process.stdout.write(report)
}
