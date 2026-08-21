import { describe, expect, it } from 'vitest'
import {
  analysisAgeBucket,
  analyzedAgoLabel,
  relativeAge,
  stalenessNote,
} from './storedAnalysis'

const NOW = Date.parse('2026-08-21T12:00:00Z')
const ago = (ms: number) => new Date(NOW - ms).toISOString()

const MINUTE = 60_000
const HOUR = 3_600_000
const DAY = 86_400_000

describe('analysisAgeBucket', () => {
  it('buckets an age without ever carrying a timestamp', () => {
    expect(analysisAgeBucket(ago(5 * MINUTE), NOW)).toBe('<1h')
    expect(analysisAgeBucket(ago(3 * HOUR), NOW)).toBe('1h-24h')
    expect(analysisAgeBucket(ago(3 * DAY), NOW)).toBe('1d-7d')
    expect(analysisAgeBucket(ago(30 * DAY), NOW)).toBe('7d+')
  })

  it('treats a clock-skewed future timestamp as brand new', () => {
    expect(analysisAgeBucket(ago(-HOUR), NOW)).toBe('<1h')
  })
})

describe('relativeAge', () => {
  it('reads as plain words', () => {
    expect(relativeAge(ago(20_000), NOW)).toBe('just now')
    expect(relativeAge(ago(MINUTE), NOW)).toBe('1 minute ago')
    expect(relativeAge(ago(40 * MINUTE), NOW)).toBe('40 minutes ago')
    expect(relativeAge(ago(2 * HOUR), NOW)).toBe('2 hours ago')
    expect(relativeAge(ago(3 * DAY), NOW)).toBe('3 days ago')
  })

  it('falls back to a date once relative time stops helping', () => {
    expect(relativeAge(ago(60 * DAY), NOW)).toMatch(/^on /)
  })

  it('says so rather than inventing an age for an unreadable timestamp', () => {
    expect(relativeAge('', NOW)).toBe('an unknown time ago')
  })
})

describe('stalenessNote', () => {
  it('stays quiet while the analysis is recent', () => {
    expect(stalenessNote(ago(2 * HOUR), NOW)).toBeNull()
  })

  it('explains the doubt in a sentence once the analysis ages', () => {
    expect(stalenessNote(ago(3 * DAY), NOW)).toContain('may have changed')
    expect(stalenessNote(ago(30 * DAY), NOW)).toContain('more than a week old')
  })
})

describe('analyzedAgoLabel', () => {
  it('prefixes the card footer line', () => {
    expect(analyzedAgoLabel(ago(2 * HOUR), NOW)).toBe('Analyzed 2 hours ago')
  })
})
