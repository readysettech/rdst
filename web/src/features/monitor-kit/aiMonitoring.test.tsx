import { cleanup, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { keyRoutingLines } from '@/features/settings/AiKeyRouting'
import { settingsFixture } from '@/lib/monitor/fixtures'
import type { MonitoringLimit, MonitorSettings } from '@/lib/monitor/types'
import { renderWithClient } from '@/test-utils'

const state = vi.hoisted(() => ({
  limit: undefined as MonitoringLimit | undefined,
  mutate: vi.fn(),
  settings: undefined as MonitorSettings | null | undefined,
}))

vi.mock('@/lib/monitor/hooks', async (original) => ({
  ...(await original<typeof import('@/lib/monitor/hooks')>()),
  useMonitorSettings: () => ({
    data:
      state.settings === null
        ? undefined
        : (state.settings ?? {
            ...settingsFixture('localdev'),
            enabled: false,
            enabled_at: null,
            configured: false,
          }),
  }),
  useMonitoringLimit: () => ({ data: state.limit, isLoading: false }),
  useUpdateMonitorSettings: () => ({
    mutate: state.mutate,
    isPending: false,
    isError: false,
  }),
  useClaimAiMonitoring: () => ({
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
    isError: false,
  }),
}))

vi.mock('@/features/prerequisites', async (original) => ({
  ...(await original<typeof import('@/features/prerequisites')>()),
  usePrerequisites: () => ({ phase: 'ready', recheck: vi.fn() }),
  PrerequisitesChecker: () => null,
}))

const {
  aiStateCopy,
  allowanceCopy,
  enableAiCopy,
  limitBasis,
  limitLine,
  monitoringStatus,
  needsUserAction,
} = await import('./aiMonitoring')
const { AllowanceNotice } = await import('./AiMonitoringStatus')

const limit = (extra: Partial<MonitoringLimit> = {}): MonitoringLimit => ({
  in_use: 2,
  limit: 3,
  own_key: false,
  known: true,
  full: false,
  target_fits: true,
  signed_in: true,
  targets: [],
  ...extra,
})

afterEach(() => {
  cleanup()
  state.settings = undefined
  state.limit = undefined
  state.mutate.mockReset()
  delete window.rdstDesktop
})

describe('limit words', () => {
  it('counts against the Key Service limit, never a built-in number', () => {
    expect(limitLine(limit())).toBe('AI monitoring on 2 of 3 databases')
    expect(limitLine(limit({ in_use: 4, limit: 10, own_key: true }))).toBe(
      'AI monitoring on 4 of 10 databases'
    )
    expect(limitLine(limit({ in_use: 1, limit: null, known: false }))).toBe(
      'AI monitoring on 1 database'
    )
    expect(limitLine(undefined)).toBeNull()
    expect(limitBasis(limit({ own_key: true }))).toMatch(/own Anthropic key/)
    expect(limitBasis(limit({ limit: null }))).toBeNull()
  })

  it('keeps local monitoring and AI monitoring apart', () => {
    expect(aiStateCopy('on').label).toBe('AI monitoring')
    expect(aiStateCopy('local').label).toBe('Local monitoring only')
    expect(aiStateCopy('local').tone).toBe('warning')
    expect(aiStateCopy('pending').label).toBe('AI monitoring starting')
  })

  it('turns on local monitoring when the account is full', () => {
    expect(enableAiCopy(limit()).aiOff).toBe(false)
    // The account already holds this database's slot.
    expect(enableAiCopy(limit({ in_use: 3, full: true })).aiOff).toBe(false)
    const full = enableAiCopy(
      limit({ in_use: 3, full: true, target_fits: false })
    )
    expect(full.aiOff).toBe(true)
    expect(full.buttonLabel).toBe('Turn on local monitoring')
    expect(full.title).toBe(
      'AI monitoring is off for this database: 3 of 3 are in use'
    )
    expect(full.message).toMatch(/turn off monitoring on another database/)
    // Unknown limit (offline): no claim about AI either way.
    expect(enableAiCopy(limit({ limit: null, target_fits: null })).aiOff).toBe(
      false
    )
  })

  it('says which key covers what', () => {
    expect(keyRoutingLines('claude', true)).toEqual([
      'Your Anthropic key covers Analyze, Ask, deep analysis and nightly reports.',
      'Quick checks and monitoring alerts always run through Readyset and use your Readyset allowance.',
    ])
    expect(keyRoutingLines('readyset', true)[0]).toMatch(
      /Readyset account covers Analyze/
    )
    expect(keyRoutingLines(null, false)[0]).toMatch(/Sign in to Readyset/)
  })
})

describe('AI limit reached', () => {
  it('pauses AI without naming an amount, and says who to contact', () => {
    expect(aiStateCopy('paused').label).toBe('AI paused: out of AI usage')
    expect(allowanceCopy(limit())).toBeNull()
    const copy = allowanceCopy(
      limit({ allowance: ['hosted'], contact: 'hello@readyset.io' })
    )
    expect(copy?.message).toMatch(/^Deep analysis and reports are paused/)
    expect(copy?.message).not.toMatch(/\$/)
    expect(
      allowanceCopy(limit({ allowance: ['jev', 'hosted'] }))?.message
    ).toMatch(/^AI checks, deep analysis and reports are paused/)
  })

  it('links to the contact address', () => {
    renderWithClient(
      <AllowanceNotice
        limit={limit({ allowance: ['jev'], contact: 'hello@readyset.io' })}
      />
    )
    const link = screen.getByRole('link', { name: 'hello@readyset.io' })
    expect(link.getAttribute('href')).toMatch(/^mailto:hello@readyset.io/)
    expect(screen.getByTestId('ai-allowance').textContent).toContain(
      "You've run out of AI usage for now"
    )
  })
})

describe('one monitoring status for every surface', () => {
  const now = 10_000
  it('names the four states and says why AI is off', () => {
    expect(monitoringStatus({ enabled: true, ai: 'on' }).label).toBe(
      'AI monitoring on'
    )
    expect(monitoringStatus({ enabled: false, ai: 'off' })).toMatchObject({
      kind: 'off',
      label: 'Monitoring off',
      action: { kind: 'turn_on' },
    })
    expect(
      monitoringStatus({ enabled: true, ai: 'on', connectionLost: true })
    ).toMatchObject({ kind: 'paused', label: 'Monitoring paused' })
    expect(monitoringStatus({ enabled: true, ai: 'local' })).toMatchObject({
      kind: 'local_only',
      label: 'Local monitoring only',
      reasonLabel: 'all AI slots in use',
      action: { kind: 'free_slot' },
    })
    expect(monitoringStatus({ enabled: true, ai: 'paused' })).toMatchObject({
      reason: 'quota',
      action: { kind: 'contact' },
    })
  })

  it('says signed out at once, and turns "starting" into a reason after a minute', () => {
    expect(
      monitoringStatus({
        enabled: true,
        ai: 'pending',
        reason: 'signed_out',
        since: now,
        now,
      })
    ).toMatchObject({ kind: 'local_only', action: { kind: 'sign_in' } })
    const starting = { enabled: true, ai: 'pending' as const, since: now }
    expect(monitoringStatus({ ...starting, now: now + 30 })).toMatchObject({
      kind: 'ai_starting',
      busy: true,
    })
    expect(monitoringStatus({ ...starting, now: now + 61 })).toMatchObject({
      kind: 'local_only',
      reason: 'not_started',
      action: { kind: 'retry' },
    })
    expect(
      monitoringStatus({ ...starting, reason: 'unreachable', now: now + 61 })
        .reasonLabel
    ).toBe("can't reach Readyset")
  })

  it('reads a retrying AI connection as passing, not as a mode the user must fix', () => {
    const unreachable = monitoringStatus({
      enabled: true,
      ai: 'pending',
      reason: 'unreachable',
      since: now,
      now: now + 61,
    })
    expect(unreachable).toMatchObject({
      label: 'AI reconnecting',
      tone: 'neutral',
      busy: true,
    })
    expect(needsUserAction(unreachable)).toBe(false)
    const signedOut = monitoringStatus({
      enabled: true,
      ai: 'pending',
      reason: 'signed_out',
    })
    expect(signedOut.label).toBe('AI off: signed out')
    expect(needsUserAction(signedOut)).toBe(true)
  })
})
