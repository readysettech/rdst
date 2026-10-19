import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { parseArgs } from 'node:util'

import {
  changesFrom,
  checkContract,
  explain,
  repoRoot,
  select,
  type Vcs,
} from './selector.ts'

/**
 * Picks relentless areas for a change and writes `selection.json` and
 * `selection.md` to `--out`.
 *
 *   node select.ts --base SHA --head SHA --out DIR   (git, as in CI)
 *   node select.ts --jj-rev @- --out DIR             (a local jj change)
 *   node select.ts --check                           (validate the contract)
 */
const { values } = parseArgs({
  options: {
    base: { type: 'string' },
    head: { type: 'string', default: 'HEAD' },
    'jj-rev': { type: 'string' },
    out: { type: 'string' },
    budget: { type: 'string' },
    check: { type: 'boolean', default: false },
  },
})

if (values.check) {
  const tracked = values['jj-rev']
    ? execFileSync('jj', ['file', 'list', '-r', values['jj-rev']], {
        cwd: repoRoot,
        encoding: 'utf8',
        maxBuffer: 64 * 1024 * 1024,
      })
    : execFileSync('git', ['ls-files'], {
        cwd: repoRoot,
        encoding: 'utf8',
        maxBuffer: 64 * 1024 * 1024,
      })
  const problems = checkContract(tracked.split('\n').filter(Boolean))
  for (const problem of problems) console.error(`contract: ${problem}`)
  process.exit(problems.length > 0 ? 1 : 0)
}

const vcs: Vcs = values['jj-rev']
  ? { kind: 'jj', rev: values['jj-rev'] }
  : { kind: 'git', base: values.base ?? 'HEAD~1', head: values.head ?? 'HEAD' }
const selection = {
  ...select(changesFrom(vcs), {
    budget: values.budget ? Number(values.budget) : undefined,
  }),
  ...(vcs.kind === 'git'
    ? { base: vcs.base, head: vcs.head }
    : { head: vcs.rev }),
}
if (values.out) {
  mkdirSync(values.out, { recursive: true })
  writeFileSync(
    join(values.out, 'selection.json'),
    `${JSON.stringify(selection, null, 2)}\n`
  )
  writeFileSync(join(values.out, 'selection.md'), explain(selection))
}
console.log(JSON.stringify(selection, null, 2))
