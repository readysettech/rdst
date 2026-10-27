import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parseArgs } from 'node:util'

import { paths } from './driver/paths.ts'
import type { Quota } from './quota.ts'

/**
 * Markdown summary of every verdict under a runs directory, for a CI
 * annotation, or with `--slack TITLE` a Slack message, or with `--gerrit` a
 * change comment listing the P1/P2 findings (nothing when there are none).
 * A `baseline/baseline.json` from verify.ts marks the findings main also
 * has; the change comment leaves those out.
 * `--artifacts PATH` is the runs directory's artifact path in the build, so
 * the annotation links each finding to its page and screenshots. Exits
 * non-zero when any area did not pass.
 *
 *   node report.ts [RUNS_DIR] [--artifacts PATH] [--slack TITLE | --gerrit]
 */
const { values: options, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    slack: { type: 'string' },
    gerrit: { type: 'boolean', default: false },
    artifacts: { type: 'string' },
  },
})
const slackTitle = options.slack
const runsDir = resolve(positionals[0] ?? paths.runsDir)

interface Verdict {
  area: string
  mode: string
  verdict: string
  findings: Record<string, number>
  finding_titles: string[]
  cost_usd: number | null
  duration_s: number
  summary: string | null
  finding_files?: {
    id: string
    severity: string
    title: string
    page: string
    screenshots: string[]
  }[]
  /** The run's directory under the runs directory. */
  dir: string
  usage?: Record<string, number> | null
  quota?: { before: Quota | null; after: Quota | null } | null
  /** Commands the driver refused, such as a goto outside the app. */
  refused?: { command: string; reason: string }[]
}

const verdicts: Verdict[] = existsSync(runsDir)
  ? readdirSync(runsDir)
      .filter((dir) => existsSync(join(runsDir, dir, 'verdict.json')))
      .map((dir) => ({
        ...(JSON.parse(
          readFileSync(join(runsDir, dir, 'verdict.json'), 'utf8')
        ) as Verdict),
        dir,
      }))
  : []

/** verify.ts's check of the serious findings on main, when it ran. */
const baseline: {
  baseline: string | null
  checked: string[]
  reproduced: string[]
  verdict: string
} | null = existsSync(join(runsDir, 'baseline', 'baseline.json'))
  ? JSON.parse(readFileSync(join(runsDir, 'baseline', 'baseline.json'), 'utf8'))
  : null
const onMain = new Set(baseline?.reproduced ?? [])
const mark = (title: string) => (onMain.has(title) ? ' (also on main)' : '')

interface SelfTest {
  mode: string
  passed: boolean
  checks: { name: string; ok: boolean }[]
  probes: Record<string, unknown>
  /** The job it ran in, when a run spans several: selftest-MODE-JOB.json. */
  job?: string
}

const selfTests: SelfTest[] = existsSync(runsDir)
  ? readdirSync(runsDir)
      .filter((entry) => /^selftest-.*\.json$/.test(entry))
      .flatMap((entry) => {
        try {
          const test = JSON.parse(
            readFileSync(join(runsDir, entry), 'utf8')
          ) as SelfTest
          const job = entry.slice(`selftest-${test.mode}-`.length, -5)
          return [{ ...test, job: job || undefined }]
        } catch {
          return []
        }
      })
  : []

function selfTestName(test: SelfTest) {
  return `${test.mode} self-test${test.job ? ` (${test.job})` : ''}`
}

const lines = ['### Relentless UI tester', '']
for (const test of selfTests) {
  const failed = test.checks.filter(({ ok }) => !ok).map(({ name }) => name)
  lines.push(
    `- ${selfTestName(test)}: ${test.passed ? 'passed' : `failed (${failed.join('; ')})`}; probes ${JSON.stringify(test.probes)}`
  )
}
if (selfTests.length) lines.push('')
lines.push(
  '| Mode | Area | Verdict | Findings | Cost | Time |',
  '|---|---|---|---|---|---|'
)
for (const v of verdicts) {
  const findings =
    Object.entries(v.findings)
      .sort()
      .map(([severity, count]) => `${count} ${severity}`)
      .join(', ') || 'none'
  const cost = v.cost_usd === null ? 'n/a' : `$${v.cost_usd.toFixed(2)}`
  lines.push(
    `| ${v.mode} | ${v.area} | ${v.verdict} | ${findings} | ${cost} | ${Math.round(v.duration_s / 60)} min |`
  )
}
if (verdicts.length === 0) lines.push('| - | - | no runs found | - | - | - |')
for (const v of verdicts.filter(
  ({ finding_titles }) => finding_titles.length
)) {
  lines.push('', `**${v.mode} / ${v.area}**`, '')
  if (options.artifacts && v.finding_files) {
    // Buildkite resolves artifact:// links to the build's uploaded files.
    const link = (path: string, text: string) =>
      `<a href="artifact://${options.artifacts}/${v.dir}/${path}">${text}</a>`
    for (const f of v.finding_files) {
      const shots = f.screenshots.map((p, i) => link(p, `screenshot ${i + 1}`))
      lines.push(
        `- ${link(f.page, f.id)} ${f.severity} ${escapeHtml(f.title)}${mark(f.title)}${shots.length ? ` (${shots.join(', ')})` : ''}`
      )
    }
  } else for (const title of v.finding_titles) lines.push(`- ${title}`)
}
const refusals = verdicts.filter(({ refused }) => refused?.length)
if (refusals.length > 0) {
  lines.push('', '**Commands the driver refused**', '')
  for (const v of refusals) {
    const counts = new Map<string, number>()
    for (const { command } of v.refused ?? []) {
      counts.set(command, (counts.get(command) ?? 0) + 1)
    }
    const listed = [...counts].map(([command, n]) => `${command} (${n})`)
    lines.push(`- ${v.mode} / ${v.area}: ${listed.join(', ')}`)
  }
}
if (baseline && baseline.checked.length > 0) {
  lines.push(
    '',
    baseline.verdict === 'checked'
      ? `Checked ${baseline.checked.length} P1/P2 findings on ${baseline.baseline}: ${onMain.size} also happen there.`
      : `Could not check the P1/P2 findings on main (${baseline.verdict}).`
  )
}
const quota = quotaLine()
if (quota) lines.push('', quota)
console.log(
  options.gerrit
    ? gerrit()
    : slackTitle === undefined
      ? lines.join('\n')
      : slack(slackTitle)
)
process.exitCode =
  verdicts.length > 0 &&
  verdicts.every(({ verdict }) => verdict === 'passed') &&
  selfTests.every(({ passed }) => passed)
    ? 0
    : 1

function escapeHtml(text: string) {
  return text
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
}

/** P1/P2 findings main does not also have, one line each, for the change's author. */
function gerrit() {
  // verify.ts names only the findings it decided on main: it tries the most
  // severe, may not reach some, and decides none when it fails. The rest are
  // said to be unchecked rather than new.
  const tried = new Set(baseline?.verdict === 'checked' ? baseline.checked : [])
  const findings = verdicts.flatMap((v) =>
    (v.finding_files ?? [])
      .filter((f) => /^P[12]$/.test(f.severity) && !onMain.has(f.title))
      .map((f) => ({
        line: `- ${v.mode} / ${v.area}: ${f.id} ${f.severity} ${f.title}${tried.has(f.title) ? '' : ' (not checked on main)'}`,
        tried: tried.has(f.title),
      }))
  )
  if (findings.length === 0) return ''
  const serious = findings.map((f) => f.line)
  const untried = findings.filter((f) => !f.tried).length
  const why =
    baseline?.verdict === 'checked'
      ? `main's check decided ${tried.size} of the P1/P2 findings`
      : (baseline?.verdict ?? 'the check did not run')
  const onMainNote =
    untried === 0
      ? `, none of them on ${baseline?.baseline}.`
      : untried === findings.length
        ? `. They could not be checked against main (${why}).`
        : `: ${findings.length - untried} checked and not on ${baseline?.baseline}, ${untried} not checked (${why}).`
  return [
    `Relentless UI tester: ${serious.length} P1/P2 finding${serious.length === 1 ? '' : 's'} in ${verdicts.length} run${verdicts.length === 1 ? '' : 's'}${onMainNote}`,
    '',
    ...serious.slice(0, 25),
    ...(serious.length > 25 ? [`- and ${serious.length - 25} more`] : []),
    '',
    `Steps, evidence, and screenshots are linked from the build's annotation: ${process.env.BUILDKITE_BUILD_URL ?? 'the Buildkite build'}`,
  ].join('\n')
}

/** Slack mrkdwn: one line per run, then the serious findings. */
function slack(title: string) {
  const icon: Record<string, string> = {
    passed: ':white_check_mark:',
    failed: ':x:',
  }
  const serious = verdicts.flatMap((v) =>
    v.finding_titles
      .filter((t) => / P[12] /.test(t))
      .map((t) => `\u2022 ${v.area}: ${t.replace(/^F-\d+ /, '')}`)
  )
  const cost = verdicts.reduce((n, v) => n + (v.cost_usd ?? 0), 0)
  const out = [`*${title}*`]
  for (const test of selfTests.filter((t) => !t.passed))
    out.push(`:warning: ${selfTestName(test)} failed; its runs were skipped`)
  for (const v of verdicts) {
    const findings =
      Object.entries(v.findings)
        .sort()
        .map(([severity, count]) => `${count} ${severity}`)
        .join(', ') || 'no findings'
    out.push(
      `${icon[v.verdict] ?? ':warning:'} ${v.mode} / ${v.area}: ${v.verdict}, ${findings}`
    )
  }
  if (verdicts.length === 0) out.push(':warning: no runs finished')
  if (serious.length > 0) {
    out.push(
      '',
      `*P1/P2 findings (${serious.length})*`,
      ...serious.slice(0, 25)
    )
    if (serious.length > 25) out.push(`and ${serious.length - 25} more`)
  }
  out.push('', `${verdicts.length} runs, $${cost.toFixed(2)}`)
  if (quota) out.push(quota)
  return out.join('\n')
}

/**
 * How far these runs moved the Codex login's weekly quota, from the lowest
 * reading before any run to the highest after one: runs in parallel overlap,
 * so their own differences do not add up. Readings are whole percents and
 * count anything else on the login meanwhile.
 */
function quotaLine() {
  const measured = verdicts.filter((v) => v.quota?.before && v.quota.after)
  if (measured.length === 0) return null
  const windows = new Set(
    measured.flatMap((v) => [
      v.quota?.before?.reset_at,
      v.quota?.after?.reset_at,
    ])
  )
  const tokens = measured.reduce(
    (n, v) => n + Object.values(v.usage ?? {}).reduce((a, b) => a + b, 0),
    0
  )
  const summary = `${measured.length} runs measured, ${(tokens / 1e6).toFixed(1)}M tokens`
  if (windows.size > 1)
    return `Codex weekly quota: the window reset during these runs; ${summary}`
  const from = Math.min(
    ...measured.map((v) => v.quota?.before?.used_percent ?? 100)
  )
  const to = Math.max(...measured.map((v) => v.quota?.after?.used_percent ?? 0))
  const used = to - from
  const perWeek =
    used > 0 ? `, about ${Math.floor(100 / used)} of these a week` : ''
  return `Codex weekly quota: ${from}% to ${to}% (${used} points; ${summary})${perWeek}`
}
