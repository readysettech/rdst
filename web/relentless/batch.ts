#!/usr/bin/env node
import { execFileSync, spawn } from 'node:child_process'
import {
  createWriteStream,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from 'node:fs'
import { homedir } from 'node:os'
import { basename, join, relative, resolve } from 'node:path'
import { pipeline } from 'node:stream/promises'
import { parseArgs } from 'node:util'
import {
  type Finding,
  fetchMainPackage,
  findingsOf,
  unpack,
  verifyMission,
  verifyOutcome,
} from './baseline.ts'
import { paths } from './driver/paths.ts'

/**
 * Run relentless missions against a desktop build on this machine, several at
 * once. Each run gets its own Compose project with no host ports, so runs cannot
 * reach each other's databases. With `--baseline`, every mission also runs on
 * the baseline build; findings only the build has go to a verify mission on the
 * baseline, and summary.md labels each finding pre-existing, new on the build,
 * gone from it, or unverified when the baseline could not decide it.
 *
 * Builds: a local AppImage or extracted executable, `buildkite:NUMBER[:FLAVOR]`
 * from readyset-rdst, `cl:CHANGE[/PATCHSET]`, or `main` (its latest package,
 * from the public update channel). Buildkite builds need an API token in
 * BUILDKITE_API_TOKEN or the secrets directory's buildkite-token.
 */

const PIPELINE = 'readyset/pipelines/readyset-rdst'
const KEYSERVICE = 'https://keyservice-staging.readyset.io'
const SECRET_FILES = {
  RDST_RELENTLESS_CLIPROXY_API_KEY_FILE: 'cliproxy-key',
  RDST_RELENTLESS_ANTHROPIC_API_KEY_FILE: 'anthropic-key',
  RDST_RELENTLESS_RESEND_API_KEY_FILE: 'resend-key',
  RDST_RELENTLESS_ATTESTATION_SECRET_FILE: 'staging-attestation',
}

export interface BuildSpec {
  kind: 'local' | 'buildkite' | 'cl' | 'main'
  path?: string
  number?: number
  flavor?: string
  change?: number
  patchset?: number
}

export function parseBuildSpec(spec: string): BuildSpec {
  if (spec === 'main') return { kind: 'main', flavor: 'staging' }
  let m = spec.match(/^buildkite:(\d+)(?::([a-z]+))?$/)
  if (m) return { kind: 'buildkite', number: Number(m[1]), flavor: m[2] }
  m = spec.match(/^cl:(\d+)(?:\/(\d+))?$/)
  if (m) {
    return {
      kind: 'cl',
      change: Number(m[1]),
      patchset: m[2] ? Number(m[2]) : undefined,
      flavor: 'cl',
    }
  }
  if (/^[a-z]+:/.test(spec)) throw new Error(`unknown build ${spec}`)
  return { kind: 'local', path: resolve(spec) }
}

/** `git ls-remote` lines for a change's refs; the highest patchset number wins. */
export function patchsetCommit(lsRemote: string, patchset?: number) {
  const refs = lsRemote
    .trim()
    .split('\n')
    .map((line) =>
      line.match(/^([0-9a-f]{40})\s+refs\/changes\/\d+\/\d+\/(\d+)$/)
    )
    .filter((m): m is RegExpMatchArray => m !== null)
    .map((m) => ({ commit: m[1], patchset: Number(m[2]) }))
    .sort((a, b) => b.patchset - a.patchset)
  const found = patchset ? refs.find((r) => r.patchset === patchset) : refs[0]
  if (!found)
    throw new Error(`no patchset ${patchset ?? ''} in the change's refs`)
  return found
}

const words = (title: string) =>
  new Set(
    title
      .toLowerCase()
      .replace(/[^a-z0-9 ]+/g, ' ')
      .split(/\s+/)
      .filter((w) => w.length > 2)
  )

/** Word overlap of two finding titles, from 0 to 1. */
export function similarity(a: string, b: string) {
  const x = words(a)
  const y = words(b)
  const shared = [...x].filter((w) => y.has(w)).length
  return shared / Math.max(1, new Set([...x, ...y]).size)
}

export type Label =
  | 'pre-existing'
  | 'new-on-build'
  | 'gone-on-build'
  | 'unverified'

/**
 * Pair each build finding with its closest baseline finding. Titles are the
 * agent's own words, so pairs are suggestions that a verify pass confirms.
 */
export function compareFindings(
  build: Finding[],
  baseline: Finding[],
  threshold = 0.4
) {
  const used = new Set<number>()
  const rows: { label: Label; build?: Finding; baseline?: Finding }[] = []
  for (const finding of build) {
    let best = -1
    let score = threshold
    baseline.forEach((other, i) => {
      const s = similarity(finding.title, other.title)
      if (!used.has(i) && s >= score) {
        best = i
        score = s
      }
    })
    if (best >= 0) {
      used.add(best)
      rows.push({
        label: 'pre-existing',
        build: finding,
        baseline: baseline[best],
      })
    } else rows.push({ label: 'new-on-build', build: finding })
  }
  baseline.forEach((other, i) => {
    if (!used.has(i)) rows.push({ label: 'gone-on-build', baseline: other })
  })
  return rows
}

function options() {
  const { values } = parseArgs({
    options: {
      build: { type: 'string' },
      baseline: { type: 'string' },
      areas: { type: 'string' },
      flows: { type: 'string', multiple: true },
      name: { type: 'string' },
      parallel: { type: 'string', default: '4' },
      // Sign-in emails go through staging Supabase, which limits how fast they
      // may be sent; spacing starts keeps parallel runs under it.
      'stagger-sec': { type: 'string', default: '60' },
      'timeout-min': { type: 'string', default: '60' },
      model: { type: 'string' },
      'no-first-run-popups': { type: 'boolean', default: false },
      'no-verify': { type: 'boolean', default: false },
      'secrets-dir': {
        type: 'string',
        default: join(homedir(), '.config', 'rdst-relentless'),
      },
    },
  })
  if (!values.build) throw new Error('pass --build')
  const missions = [
    ...(values.areas ?? '')
      .split(',')
      .filter(Boolean)
      .map((area) => ({ key: area, args: ['--area', area] })),
    ...(values.flows ?? []).map((flows) => ({
      key: `flows-${flows.replace(/[/,]/g, '_')}`,
      args: ['--flows', flows],
    })),
  ]
  if (missions.length === 0) throw new Error('pass --areas and/or --flows')
  const name = values.name ?? new Date().toISOString().replace(/[:.]/g, '-')
  const out = resolve(paths.appDir, 'test-results', 'batch', name)
  mkdirSync(out, { recursive: true })
  return { values, missions, out }
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

function secret(dir: string, file: string) {
  const path = join(dir, file)
  return existsSync(path) ? readFileSync(path, 'utf8').trim() : undefined
}

async function buildkite(token: string, path: string) {
  const response = await fetch(
    `https://api.buildkite.com/v2/organizations/${path}`,
    {
      headers: { authorization: `Bearer ${token}` },
    }
  )
  if (!response.ok)
    throw new Error(`Buildkite ${path}: HTTP ${response.status}`)
  return response.json()
}

/** Download a build's Linux AppImage of a flavor, then unpack it. */
async function fetchBuild(spec: BuildSpec, cache: string, token: string) {
  let number = spec.number
  if (spec.kind === 'cl') {
    const nn = String(spec.change).slice(-2).padStart(2, '0')
    const refs = execFileSync(
      'git',
      ['ls-remote', 'origin', `refs/changes/${nn}/${spec.change}/*`],
      { cwd: paths.repoRoot, encoding: 'utf8' }
    )
    const { commit, patchset } = patchsetCommit(refs, spec.patchset)
    const builds = await buildkite(token, `${PIPELINE}/builds?commit=${commit}`)
    if (builds.length === 0) {
      throw new Error(
        `no readyset-rdst build for change ${spec.change}/${patchset}`
      )
    }
    number = builds[0].number
    spec.patchset = patchset
  }
  const flavor = spec.flavor ?? 'cl'
  const label =
    spec.kind === 'cl'
      ? `cl-${spec.change}-ps${spec.patchset}`
      : `bk-${number}-${flavor}`
  const dir = join(cache, label)
  const done = join(dir, 'executable')
  if (existsSync(done))
    return { label, executable: readFileSync(done, 'utf8').trim() }
  mkdirSync(dir, { recursive: true })
  const artifacts: { path: string; download_url: string }[] = []
  for (let page = 1; ; page++) {
    const batch = await buildkite(
      token,
      `${PIPELINE}/builds/${number}/artifacts?per_page=100&page=${page}`
    )
    artifacts.push(...batch)
    if (batch.length < 100) break
  }
  const wanted = `rdst-desktop-linux-${flavor}/`
  const artifact = artifacts.find(
    (a) => a.path.includes(wanted) && a.path.endsWith('.AppImage')
  )
  if (!artifact) throw new Error(`build ${number} has no ${flavor} AppImage`)
  // The download URL redirects to storage that must not see the token.
  const redirect = await fetch(artifact.download_url, {
    headers: { authorization: `Bearer ${token}` },
    redirect: 'manual',
  })
  const location = redirect.headers.get('location')
  if (!location) throw new Error(`artifact download: HTTP ${redirect.status}`)
  const response = await fetch(location)
  if (!response.ok || !response.body) {
    throw new Error(`artifact download: HTTP ${response.status}`)
  }
  const image = join(dir, basename(artifact.path))
  await pipeline(response.body, createWriteStream(image))
  return { label, executable: unpack(image, dir, done) }
}

async function resolveBuild(spec: string, cache: string, secretsDir: string) {
  const parsed = parseBuildSpec(spec)
  if (parsed.kind === 'local') {
    const path = parsed.path as string
    if (!path.startsWith(paths.repoRoot)) {
      throw new Error(
        `${path} must be inside the repository the container mounts`
      )
    }
    if (!path.endsWith('.AppImage'))
      return { label: basename(path), executable: path }
    const dir = join(cache, basename(path, '.AppImage'))
    const done = join(dir, 'executable')
    if (existsSync(done)) {
      return {
        label: basename(dir),
        executable: readFileSync(done, 'utf8').trim(),
      }
    }
    mkdirSync(dir, { recursive: true })
    const image = join(dir, basename(path))
    execFileSync('cp', [path, image])
    return { label: basename(dir), executable: unpack(image, dir, done) }
  }
  if (parsed.kind === 'main') {
    const { version, executable } = await fetchMainPackage(cache)
    return { label: `main-${version}`, executable }
  }
  const token =
    process.env.BUILDKITE_API_TOKEN ?? secret(secretsDir, 'buildkite-token')
  if (!token) {
    throw new Error(
      'Buildkite builds need BUILDKITE_API_TOKEN or a buildkite-token file; pass a local AppImage instead'
    )
  }
  return fetchBuild(parsed, cache, token)
}

/** pi and its CLIProxyAPI provider at the versions CI pins, installed once. */
function installAgent(cache: string) {
  const ci = readFileSync(
    join(paths.appDir, 'scripts', 'run-relentless-ci.sh'),
    'utf8'
  )
  const pin = (name: string) =>
    ci.match(new RegExp(`${name}="\\$\\{${name}:-([^}]+)\\}"`))?.[1]
  const pi = pin('PI_VERSION')
  const provider = pin('PI_CLIPROXYAPI_VERSION')
  if (!pi || !provider)
    throw new Error('could not read the pi versions CI pins')
  const dir = join(cache, `agent-pi-${pi}-${provider}`)
  if (!existsSync(join(dir, 'node_modules', '.bin', 'pi'))) {
    execFileSync(
      'npm',
      [
        'install',
        '--prefix',
        dir,
        '--silent',
        `@earendil-works/pi-coding-agent@${pi}`,
        `@router-for-me/pi-cliproxyapi-provider@${provider}`,
      ],
      { stdio: 'inherit' }
    )
  }
  return dir
}

interface Run {
  build: string
  mission: string
  dir: string
  exit?: number | null
}

const compose = join(paths.appDir, 'e2e', 'docker-compose.relentless.yml')

function docker(
  project: string,
  override: string,
  args: string[],
  log?: string
) {
  return new Promise<number | null>((done) => {
    const child = spawn(
      'docker',
      ['compose', '-p', project, '-f', compose, '-f', override, ...args],
      { cwd: join(paths.appDir, 'e2e'), stdio: ['ignore', 'pipe', 'pipe'] }
    )
    if (log) {
      const file = createWriteStream(log, { flags: 'a' })
      child.stdout.pipe(file)
      child.stderr.pipe(file)
    } else {
      child.stdout.resume()
      child.stderr.resume()
    }
    child.on('exit', (code) => done(code))
  })
}

async function runOne(
  run: Run,
  project: string,
  executable: string,
  agent: string,
  extra: string[],
  ctx: ReturnType<typeof options> & { override: string }
) {
  const { values, out } = ctx
  const inRepo = (path: string) => `/workdir/${relative(paths.repoRoot, path)}`
  mkdirSync(run.dir, { recursive: true })
  const log = join(run.dir, 'driver.log')
  await docker(project, ctx.override, [
    'up',
    '-d',
    '--wait',
    'postgres',
    'mysql',
    'bastion',
  ])
  const env: string[] = []
  const set = (key: string, value: string) => env.push('-e', `${key}=${value}`)
  for (const service of ['postgres', 'mysql', 'bastion']) {
    set(
      `RDST_RELENTLESS_${service.toUpperCase()}_CONTAINER`,
      `${project}-${service}-1`
    )
  }
  for (const [key, file] of Object.entries(SECRET_FILES)) {
    if (existsSync(join(values['secrets-dir'], file)))
      set(key, `/secrets/${file}`)
  }
  set(
    'RDST_RELENTLESS_CLIPROXY_URL',
    process.env.RDST_RELENTLESS_CLIPROXY_URL ?? 'http://10.3.153.177:8317'
  )
  set('RDST_RELENTLESS_KEYSERVICE_URL', KEYSERVICE)
  set(
    'RDST_RELENTLESS_PI_PROVIDER',
    '/opt/agent/node_modules/@router-for-me/pi-cliproxyapi-provider'
  )
  set(
    'PATH',
    '/opt/agent/node_modules/.bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
  )
  run.exit = await docker(
    project,
    ctx.override,
    [
      'run',
      '--rm',
      '--no-deps',
      '-v',
      `${values['secrets-dir']}:/secrets:ro`,
      '-v',
      `${agent}:/opt/agent:ro`,
      ...env,
      '-w',
      '/workdir/web-apps/apps/rdst',
      'relentless',
      'node',
      'relentless/run.ts',
      '--agent-provider',
      'pi',
      '--mode',
      'desktop',
      '--desktop-exec',
      inRepo(executable),
      '--run-dir',
      inRepo(run.dir),
      '--timeout-min',
      values['timeout-min'],
      ...(values.model ? ['--model', values.model] : []),
      ...(values['no-first-run-popups'] ? ['--no-first-run-popups'] : []),
      ...extra,
    ],
    log
  )
  await docker(project, ctx.override, ['down', '-v', '--remove-orphans'])
  console.log(
    `${run.build} ${run.mission}: exit ${run.exit} (${relative(out, run.dir)})`
  )
}

function verdictOf(dir: string) {
  const file = join(dir, 'verdict.json')
  return existsSync(file)
    ? JSON.parse(readFileSync(file, 'utf8')).verdict
    : 'no verdict'
}

async function pool<T>(
  items: T[],
  size: number,
  staggerMs: number,
  work: (item: T, i: number) => Promise<void>
) {
  let next = 0
  let lastStart = 0
  const worker = async () => {
    while (next < items.length) {
      const i = next++
      const wait = lastStart + staggerMs - Date.now()
      lastStart = Math.max(Date.now(), lastStart + staggerMs)
      if (wait > 0) await sleep(wait)
      await work(items[i], i)
    }
  }
  await Promise.all(Array.from({ length: size }, worker))
}

async function batch() {
  const ctx = options()
  const { values, missions, out } = ctx
  const cache = resolve(paths.appDir, 'test-results', 'batch', '.cache')
  mkdirSync(cache, { recursive: true })
  const override = join(out, 'compose.override.yml')
  writeFileSync(
    override,
    // Named images are built once and shared by every run's project.
    `services:
  postgres:
    ports: !reset []
  mysql:
    ports: !reset []
  bastion:
    image: rdst-relentless-bastion:local
    ports: !reset []
  relentless:
    image: rdst-relentless:local
    init: true
`
  )
  const agent = installAgent(cache)
  const builds = [
    {
      role: 'build',
      ...(await resolveBuild(
        values.build as string,
        cache,
        values['secrets-dir']
      )),
    },
    ...(values.baseline
      ? [
          {
            role: 'baseline',
            ...(await resolveBuild(
              values.baseline,
              cache,
              values['secrets-dir']
            )),
          },
        ]
      : []),
  ]
  const tag = basename(out)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '')
    .slice(-8)
  const runs = builds.flatMap((build) =>
    missions.map((mission) => ({
      build,
      mission,
      run: {
        build: build.role,
        mission: mission.key,
        dir: join(out, build.role, mission.key),
      } as Run,
    }))
  )
  const full = { ...ctx, override }
  const built = await docker(`rtb${tag}`, override, [
    'build',
    'relentless',
    'bastion',
  ])
  if (built !== 0) throw new Error('building the tester images failed')
  const parallel = Number(values.parallel)
  const stagger = Number(values['stagger-sec']) * 1000
  await pool(runs, parallel, stagger, (r, i) =>
    runOne(
      r.run,
      `rtb${tag}${i}`,
      r.build.executable,
      agent,
      r.mission.args,
      full
    )
  )

  const lines = [`# Relentless batch ${basename(out)}`, '']
  for (const b of builds) lines.push(`- ${b.role}: ${b.label}`)
  const summary: Record<string, unknown>[] = []
  for (const mission of missions) {
    const buildDir = join(out, 'build', mission.key)
    const baseDir = join(out, 'baseline', mission.key)
    const found = findingsOf(buildDir)
    lines.push(
      '',
      `## ${mission.key}`,
      '',
      `- build: ${verdictOf(buildDir)}, ${found.length} findings`
    )
    if (!values.baseline) {
      for (const f of found) lines.push(`  - ${f.severity} ${f.title}`)
      summary.push({ mission: mission.key, findings: found })
      continue
    }
    lines.push(`- baseline: ${verdictOf(baseDir)}`)
    let rows = compareFindings(found, findingsOf(baseDir))
    const unpaired = rows
      .filter((r) => r.label === 'new-on-build')
      .map((r) => r.build as Finding)
    if (unpaired.length > 0 && !values['no-verify']) {
      const file = join(out, `verify-${mission.key}.md`)
      writeFileSync(file, verifyMission(unpaired))
      const verify = {
        build: 'baseline',
        mission: `verify-${mission.key}`,
        dir: join(out, 'baseline', `verify-${mission.key}`),
      }
      const baseline = builds.find((b) => b.role === 'baseline')
      await runOne(
        verify,
        `rtb${tag}v`,
        baseline?.executable as string,
        agent,
        ['--mission', `/workdir/${relative(paths.repoRoot, file)}`],
        full
      )
      const { decided, reproduced } = verifyOutcome(verify.dir, unpaired)
      // Only a finding the baseline decided is new on the build.
      rows = rows.map((row): (typeof rows)[number] => {
        if (row.label !== 'new-on-build' || !row.build) return row
        if (reproduced.includes(row.build.title))
          return { ...row, label: 'pre-existing' }
        return decided.includes(row.build.title)
          ? row
          : { ...row, label: 'unverified' }
      })
    }
    for (const row of rows) {
      const f = row.build ?? row.baseline
      lines.push(`  - ${row.label}: ${f?.severity} ${f?.title}`)
    }
    summary.push({ mission: mission.key, rows })
  }
  writeFileSync(join(out, 'summary.md'), `${lines.join('\n')}\n`)
  writeFileSync(
    join(out, 'summary.json'),
    `${JSON.stringify({ builds, summary }, null, 2)}\n`
  )
  console.log(`summary: ${join(out, 'summary.md')}`)
}

if (process.argv[1] === import.meta.filename) {
  batch().catch((error) => {
    console.error(error instanceof Error ? error.message : error)
    process.exitCode = 1
  })
}
