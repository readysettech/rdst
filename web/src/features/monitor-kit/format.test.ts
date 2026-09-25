import { describe, expect, it } from 'vitest'
import { formatHitRatio } from './format'

describe('formatHitRatio', () => {
  it('shows one decimal and never rounds up to 100', () => {
    expect(formatHitRatio(1)).toBe('100')
    expect(formatHitRatio(0.999)).toBe('99.9')
    expect(formatHitRatio(0.99994)).toBe('99.9')
    expect(formatHitRatio(0.9952)).toBe('99.5')
    expect(formatHitRatio(null)).toBe('-')
    expect(formatHitRatio(undefined)).toBe('-')
  })
})
