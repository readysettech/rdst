import { afterEach, describe, expect, it, vi } from 'vitest'

import { HAND_RAISER_URL, isHandRaiserSeen, markHandRaiserSeen } from './handRaiser'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('hand-raiser storage', () => {
  it('is unseen until marked, then seen, and isolates signals', () => {
    const store = new Map<string, string>()
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => void store.set(k, v),
    })

    expect(isHandRaiserSeen('cache_created')).toBe(false)
    markHandRaiserSeen('cache_created')
    expect(isHandRaiserSeen('cache_created')).toBe(true)
    // A different signal is tracked independently.
    expect(isHandRaiserSeen('fleet_audit')).toBe(false)
  })

  it('degrades to not-seen when localStorage throws (private mode, tests)', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    })

    expect(isHandRaiserSeen('retention_30d')).toBe(false)
    expect(() => markHandRaiserSeen('retention_30d')).not.toThrow()
  })

  it('routes to the readyset contact destination', () => {
    expect(HAND_RAISER_URL).toContain('readyset.io')
  })
})
