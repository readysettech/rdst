import { describe, expect, it } from 'vitest'
import type { PrereqCheck, PrereqResult } from './api'
import {
  checkerPhase,
  firstOpenFix,
  headline,
  orderedChecks,
  pollInterval,
  progress,
  statusWord,
} from './model'

const check = (
  id: string,
  status: PrereqCheck['status'],
  overrides: Partial<PrereqCheck> = {}
): PrereqCheck => ({
  id,
  label: id,
  status,
  detail: '',
  blocking: true,
  fix:
    status === 'ok'
      ? null
      : {
          summary: id,
          sql: [],
          steps: [],
          commands: [],
          after: [],
          restart: false,
          note: null,
          docs_url: null,
        },
  remediation: null,
  ...overrides,
})

const result = (
  checks: PrereqCheck[],
  overrides: Partial<PrereqResult> = {}
): PrereqResult => ({
  target: 't',
  engine: 'postgresql',
  database: 'db',
  checked_at: 0,
  provider: 'self_hosted',
  provider_name: 'Self-hosted PostgreSQL',
  ready: true,
  blocking: 0,
  attention: 0,
  warnings: 0,
  checks,
  connection_error: null,
  ...overrides,
})

describe('checkerPhase', () => {
  it('maps the answer to one phase', () => {
    expect(checkerPhase(undefined, false)).toBe('connecting')
    expect(checkerPhase(undefined, true)).toBe('unreachable')
    expect(checkerPhase(result([], { connection_error: 'x' }), false)).toBe(
      'unreachable'
    )
    expect(
      checkerPhase(result([check('a', 'missing')], { ready: false }), false)
    ).toBe('blocked')
    expect(
      checkerPhase(result([check('a', 'ok')], { attention: 1 }), false)
    ).toBe('attention')
    expect(checkerPhase(result([check('a', 'ok')]), false)).toBe('ready')
  })
})

describe('pollInterval', () => {
  it('polls fast while fixing, slower when unreachable, never once ready', () => {
    expect(pollInterval('blocked')).toBe(4000)
    expect(pollInterval('attention')).toBe(4000)
    expect(pollInterval('connecting')).toBe(4000)
    expect(pollInterval('unreachable')).toBe(8000)
    expect(pollInterval('ready')).toBe(false)
    expect(
      pollInterval('unreachable', 'SSH tunnel host not found: bastion.example.com')
    ).toBe(false)
  })
})

describe('ordering and counts', () => {
  const checks = [
    check('preload', 'waiting'),
    check('extension', 'missing'),
    check('grant', 'missing', { blocking: false }),
    check('size', 'warn', { blocking: false, recommended: true }),
    check('probe', 'ok'),
  ]

  it('keeps recommendations apart and counts only what monitoring needs', () => {
    const { required, optional } = orderedChecks(checks)
    expect(required.map((c) => c.id)).toEqual([
      'preload',
      'extension',
      'grant',
      'probe',
    ])
    expect(optional.map((c) => c.id)).toEqual(['size'])
    expect(progress(checks)).toEqual({ done: 1, total: 4, fixes: 2 })
  })

  it('treats older non-blocking warnings as recommendations', () => {
    const legacy = [check('size', 'warn', { blocking: false })]
    expect(orderedChecks(legacy).optional).toHaveLength(1)
  })

  it('opens the first failing required fix', () => {
    expect(firstOpenFix(checks)).toBe('extension')
    expect(
      firstOpenFix([check('size', 'missing', { recommended: true })])
    ).toBe('size')
    expect(firstOpenFix([check('ok', 'ok')])).toBeNull()
  })

  it('titles the blocked phase by fixes left', () => {
    expect(headline('blocked', result(checks), 't').title).toBe(
      '2 fixes needed'
    )
    expect(
      headline('blocked', result([check('extension', 'missing')]), 't').title
    ).toBe('One fix needed')
    expect(
      headline('blocked', result([check('p', 'waiting')]), 't').title
    ).toBe('Checking the rest')
    expect(headline('ready', result([]), 'orders').body).toBe(
      'RDST can see every query orders runs.'
    )
  })

  it('words each status', () => {
    expect(statusWord(check('a', 'ok'))).toBe('Passed')
    expect(statusWord(check('a', 'missing'))).toBe('Needs a fix')
    expect(statusWord(check('a', 'missing', { recommended: true }))).toBe(
      'Recommended'
    )
    expect(statusWord(check('a', 'waiting'))).toBe('Waiting')
    expect(statusWord(check('a', 'unknown'))).toBe('Not checked')
  })
})
