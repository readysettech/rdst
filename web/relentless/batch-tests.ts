#!/usr/bin/env node
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { readableFrom } from './agent/read-guard.ts'
import { channelAppImage, verifyMission, verifyOutcome } from './baseline.ts'
import {
  compareFindings,
  parseBuildSpec,
  patchsetCommit,
  similarity,
} from './batch.ts'
import { minutesLeft, waitSeconds } from './driver/budget.ts'
import { EvidenceLog, redactIssuedTokens } from './driver/evidence.ts'
import {
  isDeepLink,
  isPsqlCommand,
  linkedFromMail,
  opensFromApp,
} from './driver/reach.ts'

test('build specs name local packages, Buildkite builds, changes, and main', () => {
  assert.deepEqual(parseBuildSpec('main'), { kind: 'main', flavor: 'staging' })
  assert.deepEqual(parseBuildSpec('buildkite:2705:staging'), {
    kind: 'buildkite',
    number: 2705,
    flavor: 'staging',
  })
  assert.deepEqual(parseBuildSpec('cl:16486/11'), {
    kind: 'cl',
    change: 16486,
    patchset: 11,
    flavor: 'cl',
  })
  assert.equal(parseBuildSpec('cl:16486').patchset, undefined)
  assert.equal(parseBuildSpec('/tmp/app.AppImage').kind, 'local')
  assert.throws(() => parseBuildSpec('gerrit:1'))
})

test('the latest patchset is the highest number, not the last ref listed', () => {
  const refs = [9, 10, 11, 2]
    .map((n) => `${String(n).padStart(40, 'a')}\trefs/changes/86/16486/${n}`)
    .join('\n')
  assert.equal(patchsetCommit(refs).patchset, 11)
  assert.equal(patchsetCommit(refs, 9).commit, String(9).padStart(40, 'a'))
  assert.throws(() => patchsetCommit(refs, 12))
})

test('findings pair by their titles and the rest are labeled by side', () => {
  const build = [
    {
      id: 'F-001',
      severity: 'P2',
      title: 'Reload reruns a completed EXPLAIN ANALYZE',
    },
    {
      id: 'F-002',
      severity: 'P1',
      title: 'Copy fix inlines a sampled literal',
    },
  ]
  const baseline = [
    {
      id: 'F-001',
      severity: 'P2',
      title: 'Native reload reruns completed EXPLAIN ANALYZE',
    },
    { id: 'F-002', severity: 'P3', title: 'Invalid port names filled fields' },
  ]
  assert.ok(similarity(build[0].title, baseline[0].title) > 0.4)
  assert.deepEqual(
    compareFindings(build, baseline).map((row) => [
      row.label,
      row.build?.id,
      row.baseline?.id,
    ]),
    [
      ['pre-existing', 'F-001', 'F-001'],
      ['new-on-build', 'F-002', undefined],
      ['gone-on-build', undefined, 'F-002'],
    ]
  )
})

test('the update channel names its AppImage and checksum', () => {
  const yml = `version: 0.1.2705
files:
  - url: rdst-desktop-staging-0.1.2705-x86_64.AppImage
    sha512: abc==
    size: 1
  - url: rdst-desktop-staging-0.1.2705-amd64.deb
    sha512: def==
path: rdst-desktop-staging-0.1.2705-x86_64.AppImage
`
  assert.deepEqual(channelAppImage(yml), {
    version: '0.1.2705',
    file: 'rdst-desktop-staging-0.1.2705-x86_64.AppImage',
    sha512: 'abc==',
  })
  assert.throws(() => channelAppImage('version: 1\nfiles: []\n'))
})

test('a verify run decides only the findings it reports or files', () => {
  const runDir = mkdtempSync(join(tmpdir(), 'rt-verify-'))
  try {
    const findings = ['A', 'B', 'C', 'D', 'E'].map((title, i) => ({
      id: `F-00${i + 1}`,
      severity: 'P2',
      title,
    }))
    assert.match(verifyMission(findings), /^## verify-2: B$/m)
    writeFileSync(
      join(runDir, 'flows.jsonl'),
      ['failed', 'passed', 'blocked']
        .map((status, i) => JSON.stringify({ id: `verify-${i + 1}`, status }))
        .join('\n')
    )
    // E went unreported but was filed again under its title.
    writeFileSync(
      join(runDir, 'verdict.json'),
      JSON.stringify({
        finding_files: [{ id: 'F-001', severity: 'P2', title: 'E' }],
      })
    )
    // C was out of reach and D never reported: neither is decided.
    assert.deepEqual(verifyOutcome(runDir, findings), {
      decided: ['A', 'B', 'E'],
      reproduced: ['A', 'E'],
    })
  } finally {
    rmSync(runDir, { recursive: true, force: true })
  }
})

test('the change comment leaves out the findings main also has', () => {
  const runs = mkdtempSync(join(tmpdir(), 'rt-report-'))
  try {
    const finding = (id: string, severity: string, title: string) => ({
      id,
      severity,
      title,
      page: `findings/${id}.md`,
      screenshots: [],
    })
    mkdirSync(join(runs, 'ask'))
    writeFileSync(
      join(runs, 'ask', 'verdict.json'),
      JSON.stringify({
        area: 'ask',
        mode: 'desktop',
        verdict: 'failed',
        findings: { P1: 1, P2: 1 },
        finding_titles: ['F-001 P1 Old bug', 'F-002 P2 New bug'],
        finding_files: [
          finding('F-001', 'P1', 'Old bug'),
          finding('F-002', 'P2', 'New bug'),
        ],
        cost_usd: null,
        duration_s: 60,
        summary: null,
      })
    )
    // The report exits non-zero when a run failed; its output is what counts.
    const comment = () =>
      spawnSync(
        process.execPath,
        [join(import.meta.dirname, 'report.ts'), runs, '--gerrit'],
        { encoding: 'utf8' }
      ).stdout
    assert.match(comment(), /Old bug[\s\S]*New bug/)
    assert.match(comment(), /could not be checked against main \(the check/)
    mkdirSync(join(runs, 'baseline'))
    writeFileSync(
      join(runs, 'baseline', 'baseline.json'),
      JSON.stringify({
        baseline: 'main 0.1.2705',
        checked: ['Old bug', 'New bug'],
        reproduced: ['Old bug'],
        verdict: 'checked',
      })
    )
    const filtered = comment()
    assert.doesNotMatch(filtered, /Old bug/)
    assert.match(filtered, /1 P1\/P2 finding .*none of them on main 0\.1\.2705/)
    assert.match(filtered, /New bug$/m)
    // Past the findings verify tried, the comment claims nothing about main.
    writeFileSync(
      join(runs, 'baseline', 'baseline.json'),
      JSON.stringify({
        baseline: 'main 0.1.2705',
        checked: ['Old bug'],
        reproduced: ['Old bug'],
        verdict: 'checked',
      })
    )
    const partial = comment()
    assert.doesNotMatch(partial, /none of them on main/)
    assert.match(partial, /main's check decided 1 of the P1\/P2 findings/)
    assert.match(partial, /New bug \(not checked on main\)/)
  } finally {
    rmSync(runs, { recursive: true, force: true })
  }
})

test('the report names the job each self-test ran in', () => {
  const runs = mkdtempSync(join(tmpdir(), 'rt-selftest-'))
  try {
    const selfTest = (passed: boolean) =>
      JSON.stringify({
        mode: 'desktop',
        passed,
        checks: [{ name: 'ai answers', ok: passed }],
        probes: {},
      })
    // One run's jobs upload their self-tests side by side.
    writeFileSync(
      join(runs, 'selftest-desktop-relentless-ui-desktop-ask.json'),
      selfTest(true)
    )
    writeFileSync(
      join(runs, 'selftest-desktop-relentless-ui-desktop-change.json'),
      selfTest(false)
    )
    writeFileSync(join(runs, 'selftest-desktop.json'), selfTest(true))
    const report = (...args: string[]) =>
      spawnSync(
        process.execPath,
        [join(import.meta.dirname, 'report.ts'), runs, ...args],
        { encoding: 'utf8' }
      ).stdout
    const annotation = report()
    assert.match(
      annotation,
      /desktop self-test \(relentless-ui-desktop-ask\): passed/
    )
    assert.match(
      annotation,
      /desktop self-test \(relentless-ui-desktop-change\): failed \(ai answers\)/
    )
    assert.match(annotation, /- desktop self-test: passed/)
    assert.match(
      report('--slack', 'Run'),
      /desktop self-test \(relentless-ui-desktop-change\) failed/
    )
  } finally {
    rmSync(runs, { recursive: true, force: true })
  }
})

test('tokens the app is issued never reach the evidence log', () => {
  const dir = mkdtempSync(join(tmpdir(), 'rt-evidence-'))
  try {
    const jwt = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyLTEifQ.c2lnbmF0dXJl'
    const log = new EvidenceLog(join(dir, 'events.jsonl'), redactIssuedTokens)
    // As the driver records a sign-in: the body cut short mid-token.
    const event = log.record('api', {
      path: '/api/account/login/complete',
      request_body: `{"access_token":"${jwt.slice(0, 30)}...[truncated]`,
      body: `{"refresh_token": "v1.opaque-refresh", "login_id": "abc", "max_tokens": 5}`,
      headers: 'Authorization: Bearer sk-opaque.123',
    })
    const written = readFileSync(join(dir, 'events.jsonl'), 'utf8')
    assert.deepEqual(JSON.parse(written), event)
    for (const leaked of ['eyJ', 'opaque-refresh', 'sk-opaque']) {
      assert.ok(!written.includes(leaked), `${leaked} reached the log`)
    }
    assert.match(String(event.request_body), /"access_token":"\[REDACTED\]/)
    assert.match(String(event.body), /"login_id": "abc", "max_tokens": 5/)
    assert.equal(event.headers, 'Authorization: Bearer [REDACTED]')
    assert.equal(
      redactIssuedTokens(`{"token":"${jwt}"}`),
      '{"token":"[REDACTED]"}'
    )
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('the agent reads only the run directory, outside the app home', () => {
  const runDir = mkdtempSync(join(tmpdir(), 'rt-read-'))
  try {
    mkdirSync(join(runDir, 'screenshots'))
    mkdirSync(join(runDir, 'home'))
    writeFileSync(join(runDir, 'screenshots', 'a.png'), '')
    writeFileSync(join(runDir, 'home', 'token'), '')
    writeFileSync(join(runDir, 'backend.log'), '')
    symlinkSync('/etc', join(runDir, 'etc'))
    const readable = readableFrom(runDir)
    assert.ok(readable('screenshots/a.png'))
    assert.ok(readable(join(runDir, 'screenshots', 'a.png')))
    for (const path of [
      'home/token',
      'backend.log',
      join(runDir, 'home', 'token'),
      'screenshots/../home/token',
      '../outside',
      '/etc/hostname',
      'etc/hostname',
      `@${join(runDir, 'screenshots', 'a.png')}`,
      '@/proc/self/environ',
      '~/.ssh/id_ed25519',
      `file://${join(runDir, 'screenshots', 'a.png')}`,
      'screenshots/missing.png',
    ]) {
      assert.ok(!readable(path), `${path} was readable`)
    }
    // pi opens a Unicode space as a plain one, so a path is checked as pi
    // will open it: here, in a sibling of the run directory.
    const spaced = mkdtempSync(join(tmpdir(), 'rt-read\u00A0'))
    const plain = spaced.replace('\u00A0', ' ')
    try {
      mkdirSync(plain)
      writeFileSync(join(plain, 'secret'), '')
      writeFileSync(join(spaced, 'a.png'), '')
      assert.ok(readableFrom(spaced)('a.png'))
      assert.ok(!readableFrom(spaced)(join(spaced, 'secret')))
    } finally {
      rmSync(spaced, { recursive: true, force: true })
      rmSync(plain, { recursive: true, force: true })
    }
  } finally {
    rmSync(runDir, { recursive: true, force: true })
  }
})

test('the tester opens the app, its docs and the keyservice, and nothing else', () => {
  const keyservice = 'https://keyservice-staging.readyset.io'
  for (const [url, base] of [
    ['rdst-app://ui/db/rdst_test', 'rdst-app://ui'],
    ['http://127.0.0.1:47815/inbox', 'http://127.0.0.1:47815'],
    ['https://docs.readyset.io/rdst/ask', 'rdst-app://ui'],
    [`${keyservice}/verify?token=x`, 'rdst-app://ui'],
  ]) {
    assert.ok(opensFromApp(url, base, keyservice), url)
  }
  for (const url of [
    'https://example.com/?data=secret',
    'http://docs.readyset.io/',
    'http://169.254.169.254/latest/meta-data/',
    'http://127.0.0.1:9222/json',
    'file:///etc/passwd',
    'rdst-app://other/',
  ]) {
    assert.ok(!opensFromApp(url, 'rdst-app://ui', keyservice), url)
  }
  assert.ok(
    !opensFromApp(
      'https://keyservice-staging.readyset.io/',
      'rdst-app://ui',
      undefined
    )
  )
})

test("a browser tab reopens the sites the run's emails linked to", () => {
  const mail = [
    'https://abc.supabase.co/auth/v1/verify?token=t&type=magiclink',
    'mailto:support@readyset.io',
  ]
  assert.ok(
    linkedFromMail('https://abc.supabase.co/auth/v1/verify?token=u', mail)
  )
  for (const url of [
    'http://abc.supabase.co/auth/v1/verify',
    'https://evil.supabase.co/',
    'https://abc.supabase.co.example.com/',
    'https://readyset.io/',
  ]) {
    assert.ok(!linkedFromMail(url, mail), url)
  }
  assert.ok(!linkedFromMail('https://abc.supabase.co/', []))
})

test('a wait leaves the run time to finish before its limit', () => {
  const now = 1_000_000
  const minutes = (n: number) => now + n * 60_000
  assert.equal(waitSeconds(65, undefined, now), 65)
  assert.equal(waitSeconds(900, undefined, now), 300)
  assert.equal(waitSeconds(300, minutes(30), now), 300)
  // Three minutes stay for filing and rt finish.
  assert.equal(waitSeconds(300, minutes(5), now), 120)
  assert.equal(waitSeconds(300, minutes(2), now), 0)
  assert.equal(minutesLeft(minutes(5.5), now), 5)
  assert.equal(minutesLeft(minutes(-1), now), 0)
  assert.equal(minutesLeft(undefined, now), undefined)
})

test('deep links and SQL reach only the app and the database', () => {
  assert.ok(isDeepLink('rdst://db/rdst_test'))
  for (const url of [
    '--renderer-cmd-prefix=sh',
    'https://example.com',
    ' rdst://x',
  ]) {
    assert.ok(!isDeepLink(url), url)
  }
  assert.ok(isPsqlCommand('\\! id'))
  assert.ok(isPsqlCommand('  \\copy t TO /tmp/x'))
  assert.ok(!isPsqlCommand('SELECT 1; \\! id'))
  assert.ok(!isPsqlCommand("SELECT '\\! id'"))
})
