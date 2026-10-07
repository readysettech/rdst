import { describe, expect, it } from 'vitest'
import { nextCheck } from './actionsModel'

describe('nextCheck', () => {
  it('counts down to the next check', () => {
    expect(nextCheck(1000 + 12 * 60, 1000)).toBe('Next check in 12 min')
    expect(nextCheck(1030, 1000)).toBe('Next check in under a minute')
    expect(nextCheck(900, 1000)).toBe('Next check in under a minute')
  })
})
