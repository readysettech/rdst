import { describe, expect, it } from 'vitest'
import type { PrereqResult } from '@/features/prerequisites'
import {
  canEnable,
  EXECUTION_OPTIONS,
  enableAction,
  enableState,
  executionMode,
} from './prerequisites'

const result = (overrides: Partial<PrereqResult> = {}): PrereqResult => ({
  target: 'localdev',
  engine: 'postgresql',
  database: 'shopdb',
  checked_at: 0,
  provider: 'self_hosted',
  provider_name: 'Self-hosted PostgreSQL',
  ready: true,
  blocking: 0,
  attention: 0,
  warnings: 0,
  checks: [
    {
      id: 'pg_stat_statements',
      label: 'pg_stat_statements is created in shopdb',
      status: 'ok',
      detail: '',
      blocking: true,
      fix: null,
      remediation: null,
    },
  ],
  connection_error: null,
  ...overrides,
})

describe('enableState', () => {
  it('allows enabling once every blocking check passes', () => {
    expect(enableState(result())).toEqual({ phase: 'ready', enabled: true })
  })

  it("still allows enabling when only other users' SQL is hidden", () => {
    expect(enableState(result({ attention: 1 })).enabled).toBe(true)
  })

  it('blocks while a required check fails, the target is unreachable or unchecked', () => {
    expect(enableState(result({ ready: false, blocking: 1 })).enabled).toBe(
      false
    )
    expect(
      enableState(result({ connection_error: 'timeout', checks: [] })).phase
    ).toBe('unreachable')
    expect(enableState(undefined)).toEqual({
      phase: 'connecting',
      enabled: false,
    })
    expect(canEnable('unreachable')).toBe(false)
  })
})

describe('executionMode', () => {
  it('defaults to plans only and reads the explicit field once it exists', () => {
    expect(executionMode(undefined)).toBe('plans_only')
    expect(executionMode({ execute: true })).toBe('execute')
    expect(
      executionMode({ execute: true, query_execution: 'plans_only' } as {
        execute: boolean
      })
    ).toBe('plans_only')
  })
})

describe('EXECUTION_OPTIONS', () => {
  it('asks EXPLAIN ANALYZE first, then EXPLAIN only, each explained', () => {
    expect(EXECUTION_OPTIONS.map((option) => option.value)).toEqual([
      'execute',
      'plans_only',
    ])
    expect(EXECUTION_OPTIONS[0].label).toMatch(/EXPLAIN ANALYZE/)
    expect(EXECUTION_OPTIONS[0].description).toMatch(/adds a little load/)
    expect(EXECUTION_OPTIONS[1].label).toBe('EXPLAIN only')
    expect(EXECUTION_OPTIONS[1].description).toMatch(/without running/)
  })
})

describe('enableAction', () => {
  it('turns on when ready, lets blocked checks be skipped, and waits for a connection', () => {
    expect(enableAction('ready')).toEqual({
      label: 'Turn on monitoring',
      enabled: true,
      note: null,
    })
    expect(enableAction('attention').enabled).toBe(true)
    expect(enableAction('blocked')).toMatchObject({
      label: 'Turn on monitoring',
      enabled: false,
    })
    expect(enableAction('blocked').note).toMatch(/setup step/)
    expect(enableAction('unreachable')).toMatchObject({ enabled: false })
    expect(enableAction('unreachable').note).toMatch(/reach the database/)
    expect(enableAction('connecting')).toMatchObject({
      enabled: false,
      note: null,
    })
  })
})
