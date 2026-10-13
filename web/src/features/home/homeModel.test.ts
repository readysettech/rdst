import { describe, expect, it } from 'vitest'
import { caFixtures } from '../../lib/monitor/fixturesCa'
import {
  appliedLine,
  doFirstTarget,
  latencyWord,
  monitorLinkTo,
  relativeDay,
  reportDayLabel,
  stripRows,
} from './homeModel'

describe('monitorLinkTo', () => {
  it('sends each destination word to its route', () => {
    expect(
      monitorLinkTo({
        kind: 'inbox',
        target: 'demo',
        label: 'Inbox',
        item_id: 1,
      })
    ).toEqual({ to: '/inbox', search: { item: 1 } })
    expect(
      monitorLinkTo({
        kind: 'query',
        target: 'demo',
        label: 'Query',
        query_key: 'k1',
      })
    ).toEqual({ to: '/database', search: { target: 'demo', query: 'k1' } })
    expect(
      monitorLinkTo({
        kind: 'report',
        target: 'demo',
        label: 'Report',
        report_id: 31,
      })
    ).toEqual({
      to: '/database',
      search: { target: 'demo', view: 'report', report: '31' },
    })
    expect(
      monitorLinkTo({ kind: 'chart', target: 'demo', label: 'Chart' })
    ).toEqual({
      to: '/database',
      search: { target: 'demo' },
    })
    expect(
      monitorLinkTo({ kind: 'actions', target: 'analytics', label: 'Actions' })
    ).toEqual({ to: '/actions', search: { db: 'analytics' } })
    expect(monitorLinkTo({ kind: 'sign_in', target: null, label: '' })).toEqual(
      {
        to: '/account-login',
      }
    )
  })
})

describe('day words', () => {
  it('compares latency against yesterday in one word', () => {
    expect(latencyWord(0)).toBe('same as yesterday')
    expect(latencyWord(-0.2)).toBe('faster than yesterday')
    expect(latencyWord(0.3)).toBe('slower than yesterday')
    expect(latencyWord(null)).toBeNull()
  })

  it('names a day relative to now', () => {
    const now = new Date(2026, 9, 8, 9, 56).getTime() / 1000
    expect(relativeDay(now - 3600, now)).toBe('today')
    expect(relativeDay(new Date(2026, 9, 7, 15).getTime() / 1000, now)).toBe(
      'yesterday'
    )
    expect(relativeDay(new Date(2026, 9, 6, 13).getTime() / 1000, now)).toBe(
      '2 days ago'
    )
    expect(reportDayLabel('2026-10-08')).toBe('Oct 8')
  })
})

describe('across databases', () => {
  const home = () => caFixtures('ca-home').home()

  it('reads the strip in attention order, or derives it from the database list', () => {
    const model = home()
    expect(
      stripRows(model).map((row) => [row.target, row.act_now, row.to_do])
    ).toEqual([
      ['demo', 1, 5],
      ['analytics', 0, 2],
      ['staging', 0, 0],
    ])
    // The list carries no activity; the counts match.
    expect(stripRows({ ...model, databases_strip: undefined })).toEqual(
      stripRows(model).map(({ activity: _activity, ...row }) => row)
    )
  })

  it('starts the Do first changes on the database with the most of them', () => {
    const model = home()
    expect(doFirstTarget(model)).toBe('demo')
    const strip = stripRows(model).map((row) =>
      row.target === 'analytics' ? { ...row, do_first: 4 } : row
    )
    expect(doFirstTarget({ ...model, databases_strip: strip })).toBe(
      'analytics'
    )
    const none = strip.map((row) => ({ ...row, do_first: 0 }))
    expect(doFirstTarget({ ...model, databases_strip: none })).toBe('demo')
  })

  it("says what the week's applied changes saved", () => {
    const totals = {
      changes_this_week: 3,
      measuring: 1,
      no_change: 0,
      verified: 2,
      saved_ms_per_min: 412,
      saved_share: 0.41,
    }
    expect(appliedLine(totals)).toBe(
      '3 changes applied this week saved 41% of database time'
    )
    expect(
      appliedLine({ ...totals, changes_this_week: 1, saved_share: null })
    ).toBe('1 change applied this week')
    expect(appliedLine({ ...totals, changes_this_week: 0 })).toBeNull()
    expect(appliedLine(null)).toBeNull()
  })
})
