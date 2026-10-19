#!/usr/bin/env node
import { spawnSync } from 'node:child_process'
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from 'node:fs'
import { join, resolve } from 'node:path'
import { parseArgs } from 'node:util'
import {
  type Finding,
  fetchMainPackage,
  findingsOf,
  verifyMission,
  verifyOutcome,
} from './baseline.ts'
import { paths } from './driver/paths.ts'

/**
 * Check a change's serious findings on main: run one verify mission on main's
 * latest desktop package and write `baseline.json` into OUT, naming the
 * findings the run decided there and those main reproduced, so the report can
 * leave the reproduced ones off the change and call the rest unchecked.
 * Arguments after `--` go to run.ts, such as the agent and its limits.
 *
 *   node relentless/verify.ts --runs RUNS_DIR --out OUT [-- RUN_ARGS...]
 */
const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    runs: { type: 'string' },
    out: { type: 'string' },
    // At most this many findings, the most severe first, so one job stays bounded.
    max: { type: 'string', default: '10' },
  },
})
if (!values.runs || !values.out) throw new Error('pass --runs and --out')
const runs = resolve(values.runs)
const out = resolve(values.out)
mkdirSync(out, { recursive: true })

const serious: Finding[] = (existsSync(runs) ? readdirSync(runs) : [])
  .flatMap((dir) => findingsOf(join(runs, dir)))
  .filter((f) => f.severity === 'P1' || f.severity === 'P2')
  .sort((a, b) => a.severity.localeCompare(b.severity))
const checked = serious.slice(0, Number(values.max))

const write = (result: Record<string, unknown>) =>
  writeFileSync(
    join(out, 'baseline.json'),
    `${JSON.stringify({ checked: checked.map((f) => f.title), ...result }, null, 2)}\n`
  )

if (checked.length === 0) {
  write({ baseline: null, reproduced: [], verdict: 'nothing to check' })
} else {
  const { version, executable } = await fetchMainPackage(
    '/tmp/rdst-relentless-main'
  )
  const mission = join(out, 'verify.md')
  writeFileSync(mission, verifyMission(checked))
  const runDir = join(out, 'run')
  const run = spawnSync(
    process.execPath,
    [
      join(paths.relentlessDir, 'run.ts'),
      '--mission',
      mission,
      '--mode',
      'desktop',
      '--desktop-exec',
      executable,
      '--run-dir',
      runDir,
      ...positionals,
    ],
    { stdio: 'inherit' }
  )
  // A run that ended without deciding leaves every finding unverified, and
  // one that ended decides only those it reported or filed.
  const verdictFile = join(runDir, 'verdict.json')
  const ended = existsSync(verdictFile)
    ? JSON.parse(readFileSync(verdictFile, 'utf8')).verdict
    : `exit ${run.status}`
  const { decided, reproduced } = verifyOutcome(runDir, checked)
  write({
    baseline: `main ${version}`,
    checked: decided,
    reproduced,
    verdict: ended === 'passed' || ended === 'failed' ? 'checked' : ended,
  })
}
