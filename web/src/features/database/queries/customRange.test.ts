// Run with TZ=UTC: the inputs are local wall-clock times.
import { describe, expect, it } from 'vitest'
import {
  fromInputValue,
  MIN_CUSTOM_SECONDS,
  resolveCustomRange,
  toInputValue,
} from './customRange'

const at = (text: string) => Date.parse(`${text}:00Z`) / 1000
const FIRST = at('2026-09-28T16:48')
const NOW = at('2026-09-30T07:05')
const bounds = { first: FIRST, now: NOW }

describe('datetime-local values', () => {
  it('round-trips minutes in local time', () => {
    expect(toInputValue(at('2026-09-30T06:03'))).toBe('2026-09-30T06:03')
    expect(fromInputValue('2026-09-30T06:03')).toBe(at('2026-09-30T06:03'))
    expect(fromInputValue('2026-09-30T06:03:30')).toBe(
      at('2026-09-30T06:03') + 30
    )
  })

  it('refuses what is not a date and time', () => {
    expect(fromInputValue('')).toBeNull()
    expect(fromInputValue('2026-09-30')).toBeNull()
    expect(fromInputValue('2026-02-31T10:00')).toBeNull()
  })
})

describe('resolveCustomRange', () => {
  it('takes a window inside the history as typed', () => {
    expect(
      resolveCustomRange('2026-09-30T05:00', '2026-09-30T06:00', bounds)
    ).toEqual({
      ok: true,
      window: [at('2026-09-30T05:00'), at('2026-09-30T06:00')],
      note: null,
    })
  })

  it('needs both ends, in order, and at least five minutes', () => {
    expect(resolveCustomRange('', '2026-09-30T06:00', bounds)).toMatchObject({
      ok: false,
    })
    expect(
      resolveCustomRange('2026-09-30T06:00', '2026-09-30T05:00', bounds)
    ).toEqual({ ok: false, error: 'The end has to be after the start.' })
    expect(
      resolveCustomRange('2026-09-30T06:00', '2026-09-30T06:04', bounds)
    ).toEqual({ ok: false, error: 'Pick at least 5 minutes.' })
    expect(
      resolveCustomRange('2026-09-30T06:00', '2026-09-30T06:05', bounds)
    ).toMatchObject({ ok: true })
  })

  it('clamps the start to the first reading and says so', () => {
    const result = resolveCustomRange(
      '2026-07-01T00:00',
      '2026-09-29T00:00',
      bounds
    )
    expect(result).toMatchObject({
      ok: true,
      window: [FIRST, at('2026-09-29T00:00')],
    })
    expect(result.ok && result.note).toMatch(/^Starts at the first reading/)
  })

  it('clamps the end to now and says so', () => {
    expect(
      resolveCustomRange('2026-09-30T06:00', '2026-10-02T00:00', bounds)
    ).toEqual({
      ok: true,
      window: [at('2026-09-30T06:00'), NOW],
      note: 'Ends at the latest reading.',
    })
  })

  it('widens back to five minutes when the clamp cuts it shorter', () => {
    const result = resolveCustomRange(
      '2026-09-30T07:03',
      '2026-09-30T08:00',
      bounds
    )
    expect(result).toMatchObject({
      ok: true,
      window: [NOW - MIN_CUSTOM_SECONDS, NOW],
    })
    const early = resolveCustomRange(
      '2026-09-28T10:00',
      '2026-09-28T16:50',
      bounds
    )
    expect(early).toMatchObject({
      ok: true,
      window: [FIRST, FIRST + MIN_CUSTOM_SECONDS],
    })
  })

  it('ignores the part of a minute before a first reading mid-minute', () => {
    const first = FIRST + 20
    expect(
      resolveCustomRange('2026-09-28T16:48', '2026-09-28T18:00', {
        first,
        now: NOW,
      })
    ).toEqual({
      ok: true,
      window: [first, at('2026-09-28T18:00')],
      note: null,
    })
  })

  it('refuses a window wholly outside the history', () => {
    expect(
      resolveCustomRange('2026-09-01T00:00', '2026-09-02T00:00', bounds)
    ).toMatchObject({
      ok: false,
      error: expect.stringMatching(/^That's before/),
    })
    expect(
      resolveCustomRange('2026-10-01T00:00', '2026-10-02T00:00', bounds)
    ).toEqual({ ok: false, error: "That's after the latest reading." })
    expect(
      resolveCustomRange('2026-09-30T07:00', '2026-09-30T08:00', {
        first: NOW - 120,
        now: NOW,
      })
    ).toEqual({
      ok: false,
      error: 'There are under 5 minutes of readings so far.',
    })
  })
})
