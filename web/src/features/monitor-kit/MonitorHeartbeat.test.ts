import { describe, expect, it } from 'vitest'
import { pulseLine } from './MonitorHeartbeat'

const NOW = 1_000_000

describe('pulseLine', () => {
  it('counts down to the next reading while readings are on time', () => {
    expect(pulseLine('live', NOW - 37, NOW + 23, NOW)).toBe(
      'Next reading in 23 s'
    )
    expect(pulseLine('ai_paused', NOW - 60, NOW, NOW)).toBe(
      'Next reading any second'
    )
    expect(pulseLine('live', NOW - 10, NOW + 150, NOW)).toBe(
      'Next reading in 3 min'
    )
    expect(pulseLine('live', null, NOW + 20, NOW)).toBe(
      'Waiting for the first reading'
    )
  })

  it('says so in one line when readings are late or failing', () => {
    expect(pulseLine('late', NOW - 180, NOW - 60, NOW)).toMatch(
      /^Last reading .+ ago, retrying$/
    )
    expect(pulseLine('connection_lost', NOW - 180, null, NOW)).toMatch(
      /^Can't reach the database, last reading .+ ago$/
    )
    expect(pulseLine('late', null, null, NOW)).toBe('No reading yet, retrying')
  })

  it('names the idle states plainly', () => {
    expect(pulseLine('off', NOW - 5, null, NOW)).toBe('Monitoring is off')
    expect(pulseLine('paused', NOW - 5, null, NOW)).toBe('Monitoring is off')
    expect(pulseLine('unknown', null, null, NOW)).toBe('Connecting')
  })
})
