import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import {
  chmodSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
  writeFileSync,
} from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * What a finding is checked against: main's latest Linux desktop package, and
 * the mission that asks whether a build's findings also happen there.
 */

/** main's staging packages, published by every main build that packages them. */
export const MAIN_CHANNEL =
  'https://downloads.readyset.io/packages/rdst-desktop-staging/linux/update'
const INSPECT_FUSE_SCRIPT = fileURLToPath(
  new URL('../../rdst-desktop/scripts/inspect-fuse.mjs', import.meta.url)
)

/** The driver needs --inspect, which a staging package ships fused off; this copy gets it back. */
function enableInspect(executable: string) {
  execFileSync(process.execPath, [INSPECT_FUSE_SCRIPT, executable], {
    stdio: 'ignore',
  })
  return executable
}

export interface Finding {
  id: string
  severity: string
  title: string
  /** The finding's page, when its run directory is at hand. */
  page?: string
}

/** The AppImage entry of an update channel's latest-linux.yml. */
export function channelAppImage(yml: string) {
  const version = yml.match(/^version: (\S+)$/m)?.[1]
  const entry = yml.match(/- url: (\S+\.AppImage)\n\s+sha512: (\S+)/)
  if (!version || !entry)
    throw new Error('the update channel lists no AppImage')
  return { version, file: entry[1], sha512: entry[2] }
}

/** Download main's latest package into `dir` and unpack it; cached by version. */
export async function fetchMainPackage(dir: string) {
  const response = await fetch(`${MAIN_CHANNEL}/latest-linux.yml`)
  if (!response.ok) throw new Error(`update channel: HTTP ${response.status}`)
  const { version, file, sha512 } = channelAppImage(await response.text())
  const home = join(dir, `main-${version}`)
  const done = join(home, 'executable')
  if (existsSync(done)) {
    return {
      version,
      executable: enableInspect(readFileSync(done, 'utf8').trim()),
    }
  }
  mkdirSync(home, { recursive: true })
  const image = await fetch(`${MAIN_CHANNEL}/${file}`)
  if (!image.ok) throw new Error(`${file}: HTTP ${image.status}`)
  const bytes = Buffer.from(await image.arrayBuffer())
  if (createHash('sha512').update(bytes).digest('base64') !== sha512) {
    throw new Error(`${file} does not match the channel's checksum`)
  }
  const path = join(home, file)
  writeFileSync(path, bytes)
  return { version, executable: unpack(path, home, done) }
}

/** Extract an AppImage beside itself; returns its main executable. */
export function unpack(image: string, dir: string, done: string) {
  chmodSync(image, 0o755)
  execFileSync(image, ['--appimage-extract'], { cwd: dir, stdio: 'ignore' })
  const root = join(dir, 'squashfs-root')
  const name = readdirSync(root).find(
    (entry) =>
      /^rdst-desktop[\w-]*$/.test(entry) &&
      (statSync(join(root, entry)).mode & 0o111) !== 0
  )
  if (!name) throw new Error(`no rdst-desktop executable in ${root}`)
  const executable = enableInspect(join(root, name))
  writeFileSync(done, executable)
  return executable
}

/** A run's findings, with their pages. */
export function findingsOf(dir: string): Finding[] {
  const file = join(dir, 'verdict.json')
  if (!existsSync(file)) return []
  const verdict = JSON.parse(readFileSync(file, 'utf8'))
  return (verdict.finding_files ?? []).map((f: Finding) => ({
    ...f,
    page: f.page && join(dir, f.page),
  }))
}

/** The id a verify mission reports a finding's outcome under. */
const verifyId = (index: number) => `verify-${index + 1}`

/**
 * A mission that reports, for each finding, whether it happens on this build,
 * and files it again under its own title when it does.
 */
export function verifyMission(findings: Finding[]) {
  const items = findings.map((f, index) => {
    const page =
      f.page && existsSync(f.page) ? readFileSync(f.page, 'utf8') : ''
    return `## ${verifyId(index)}: ${f.title}\n\n${page.slice(0, 3000).trim()}`
  })
  return `# Mission: verify findings on this build

Another build of this app showed the problems below. Check, on this build,
whether each one also happens here. Follow each report's steps as closely as
this build allows, and reproduce each at least twice before deciding.

Record each problem's outcome under the id in its heading, such as
\`rt flow verify-1 --status failed --summary '...'\`:

- \`failed\`: it happens here. Also file a finding titled exactly as its
  heading is, after the id.
- \`passed\`: you followed its steps and it does not happen here.
- \`blocked\`: you could not reach or set up what its steps need, so you
  cannot tell.

${items.join('\n\n')}
`
}

/**
 * Which findings a verify run decided, and which of those it reproduced: an
 * outcome under a finding's id, or a finding filed under its title.
 */
export function verifyOutcome(runDir: string, findings: Finding[]) {
  const file = join(runDir, 'flows.jsonl')
  const statuses = new Map<string, string>()
  if (existsSync(file)) {
    for (const line of readFileSync(file, 'utf8').split('\n')) {
      if (!line) continue
      const { id, status } = JSON.parse(line) as { id: string; status: string }
      statuses.set(id, status)
    }
  }
  const filed = new Set(findingsOf(runDir).map((f) => f.title))
  const reproduced = findings.filter(
    (f, index) =>
      statuses.get(verifyId(index)) === 'failed' || filed.has(f.title)
  )
  const decided = findings.filter(
    (f, index) =>
      reproduced.includes(f) || statuses.get(verifyId(index)) === 'passed'
  )
  return {
    decided: decided.map((f) => f.title),
    reproduced: reproduced.map((f) => f.title),
  }
}
