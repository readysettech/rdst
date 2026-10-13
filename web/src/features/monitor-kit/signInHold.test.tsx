import { renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { MonitorSettings } from '@/lib/monitor/types'
import { queryClientWrapper } from '@/test-utils'

const settings = vi.hoisted(
  () =>
    ({
      shop: { enabled: true, ai_reason: 'signed_out' },
      paused: { enabled: true, ai_reason: 'quota' },
      off: { enabled: false, ai_reason: 'signed_out' },
    }) as Record<string, Partial<MonitorSettings>>
)

vi.mock('@/lib/monitor/api', () => ({
  getSettings: (target: string) => Promise.resolve(settings[target]),
}))

const { heldForSignIn, useSignInHeld } = await import('./signInHold')

describe('signInHold', () => {
  it('holds only a monitored database whose AI waits for a sign-in', () => {
    expect(heldForSignIn(settings.shop as MonitorSettings)).toBe(true)
    expect(heldForSignIn(settings.paused as MonitorSettings)).toBe(false)
    expect(heldForSignIn(settings.off as MonitorSettings)).toBe(false)
    expect(heldForSignIn(undefined)).toBe(false)
  })

  it('names the databases held for a sign-in from their settings', async () => {
    const { result } = renderHook(
      () => useSignInHeld(['shop', 'paused', 'off']),
      { wrapper: queryClientWrapper() }
    )
    await waitFor(() => expect([...result.current]).toEqual(['shop']))
  })
})
