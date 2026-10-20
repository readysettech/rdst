import { beforeEach, describe, expect, it } from 'vitest'
import { nudgeDue, silenceNudge, snoozeNudge } from './feedbackNudge'

const DAY = 24 * 60 * 60 * 1000
const now = Date.UTC(2026, 9, 1, 12)

describe('feedbackNudge', () => {
  beforeEach(() => localStorage.clear())

  it('is due for anyone who has not seen it', () => {
    expect(nudgeDue(now)).toBe(true)
  })

  it('stays away for two weeks after showing and a month after Not now', () => {
    snoozeNudge('shown', now)
    expect(nudgeDue(now + 13 * DAY)).toBe(false)
    expect(nudgeDue(now + 15 * DAY)).toBe(true)
    snoozeNudge('dismissed', now)
    expect(nudgeDue(now + 29 * DAY)).toBe(false)
    expect(nudgeDue(now + 31 * DAY)).toBe(true)
  })

  it("never returns after Don't ask again", () => {
    silenceNudge()
    expect(nudgeDue(now + 400 * DAY)).toBe(false)
  })
})
