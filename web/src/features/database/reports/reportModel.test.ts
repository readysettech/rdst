import { describe, expect, it } from 'vitest'
import { CA_TARGET, caFixtures } from '@/lib/monitor/fixturesCa'
import type { DatabaseReport } from '@/lib/monitor/types'
import { chartStart } from './DayChart'
import {
  changedMeta,
  changedSub,
  changedValue,
  chartFromLoad,
  checksMeta,
  copyCount,
  dayMenu,
  detailedDayMeta,
  detailedTileText,
  didTime,
  fixesSql,
  fixFirstMeta,
  healthMeta,
  indexChangeText,
  latencyChartCaption,
  numberedFixes,
  reportCsv,
  reportCsvName,
  reportDateLine,
  reportMarkdown,
  reportTitle,
  runLine,
  shareText,
  spikesMeta,
  splitFixes,
  stretchCaption,
  stretchRollbacks,
  stretchTitle,
  tileText,
  timeSpan,
} from './reportModel'

const set = caFixtures('ca-report')
const report = set.report(CA_TARGET, 'latest') as DatabaseReport
const now = set.now
const detailed = report.detailed as NonNullable<DatabaseReport['detailed']>

describe('the report head', () => {
  it('names the day it covers and when it ran', () => {
    expect(reportDateLine(report, now)).toBe(
      'Wednesday, October 7, run today at 12:00 AM'
    )
    const onRequest = set.report(CA_TARGET, 32) as DatabaseReport
    expect(reportDateLine(onRequest, now)).toBe(
      'Wednesday, October 7, run today at 9:51 AM'
    )
  })

  it('calls only the nightly run the daily report, and says who ran it', () => {
    expect(reportTitle({ ...report, trigger: 'nightly' })).toBe('Daily report')
    expect(reportTitle({ ...report, trigger: 'user' })).toBe('Report')
    expect(runLine(now, 'nightly')).toMatch(/^Run at .* by RDST$/)
    expect(runLine(now, 'on_request')).toMatch(/^Run on request at /)
  })

  it('lists every kept day with its fixes as a word, the oldest as the first report', () => {
    const days = dayMenu(set.reportHistory(CA_TARGET, 30).reports, 30)
    expect(days.map((day) => day.label)).toEqual([
      'Oct 7',
      'Oct 6',
      'Oct 5',
      'Oct 4',
      'Oct 3',
      'Oct 2',
      'Oct 1',
    ])
    expect(days.map((day) => day.word)).toEqual([
      '9 fixes',
      '7 fixes',
      '8 fixes',
      '3 fixes',
      'quiet',
      '1 fix',
      'first report',
    ])
    expect(days[0].latest).toBe(true)
    expect(days[1].latest).toBe(false)
  })
})

describe('the day tiles', () => {
  it('prints each unit the way the report reads it', () => {
    const [qps, latency, connections, cache] = report.day!.tiles
    expect(tileText(qps)).toEqual({ value: '11.7' })
    expect(tileText(latency)).toEqual({ value: '15.9', unit: 'ms' })
    expect(tileText(connections)).toEqual({ value: '8', unit: 'of 100' })
    expect(tileText(cache)).toEqual({ value: '11.5', unit: '%' })
    // Under a millisecond, two decimals, as the p95 under it reads.
    expect(tileText({ ...latency, value: 0.38 })).toEqual({
      value: '0.38',
      unit: 'ms',
    })
  })
})

describe('the latency caption', () => {
  it('reads the p95 the day carries, and nothing without one', () => {
    expect(latencyChartCaption(report.day)).toBe('p95 69.7 ms')
    expect(latencyChartCaption(null)).toBeNull()
    const tiles = report.day!.tiles.map((tile) =>
      tile.metric === 'mean_ms'
        ? { ...tile, detail: '+0% vs the day before' }
        : tile
    )
    expect(latencyChartCaption({ ...report.day!, tiles })).toBeNull()
  })
})

describe('fix first', () => {
  it('splits the numbered rows from the smaller ones and counts the numbered', () => {
    const { main, smaller } = splitFixes(report.fix_first!)
    expect(main.map((item) => item.rank)).toEqual([1, 2, 3, 4, 5, null])
    expect(smaller.map((item) => item.rank)).toEqual([6, 7, 8, 9])
    expect(numberedFixes(main)).toHaveLength(5)
  })

  it('says which index covers several queries', () => {
    expect(fixFirstMeta(report.fix_first!)).toBe(
      'in this order, one index on posts covers three queries'
    )
    expect(fixFirstMeta([])).toBe('in this order')
  })

  it('joins every fix with SQL for the copy', () => {
    const sql = fixesSql(report.fix_first!)
    expect(sql.split('\n')).toHaveLength(8)
    expect(sql.startsWith("ALTER SYSTEM SET shared_buffers = '2GB';")).toBe(
      true
    )
  })
})

describe('health', () => {
  it('counts the checks that need you against every check', () => {
    expect(healthMeta(report.health!, report.health_fine!)).toBe(
      '3 of 8 checks need you'
    )
  })

  it('counts the rows the payload says need you, not every warning', () => {
    const rows = report.health!.map((row, index) => ({
      ...row,
      needs_you: index === 0,
    }))
    expect(healthMeta(rows, report.health_fine!)).toBe('1 of 8 checks need you')
    const checks = detailed.health_checks.map((check) => ({
      ...check,
      needs_you: check.state === 'bad',
    }))
    expect(checksMeta(checks)).toBe('1 of 8 checks need you')
  })
})

describe('exports', () => {
  it('names the CSV after the database and the day, and counts one analysis', () => {
    expect(reportCsvName(report)).toBe(
      `rdst-report-${CA_TARGET}-2026-10-07.csv`
    )
    const one = { ...report, run: { ...report.run!, analyses: 1, rewrites: 1 } }
    expect(reportMarkdown(one, now)).toContain('1 analysis, 1 rewrite tested')
  })

  it('writes the report as CSV and Markdown with every section', () => {
    const csv = reportCsv(report)
    expect(csv.split(/\r?\n/)[0]).toBe('section,item,value,detail')
    expect(csv).toContain('fix,1 Raise shared_buffers to 2 GB')
    expect(csv).toContain('health,Cache hit fell to 11.5%,bad')
    const markdown = reportMarkdown(report, now)
    expect(markdown).toContain('# Daily report, Wednesday, October 7')
    expect(markdown).toContain('demo needs two things today:')
    expect(markdown).toContain('## Fix first, in this order')
    expect(markdown).toContain(
      '2. Index posts (creationdate, owneruserid, score)'
    )
    expect(markdown).toContain('## Health, 3 of 8 checks need you')
    expect(markdown).toContain('Report #31')
  })
})

describe('the day chart', () => {
  it('starts where readings began on a day that started partway', () => {
    const chart = { start: 0, end: 86_400, res: 300 }
    expect(chartStart(chart, 80_000)).toBe(79_800)
    expect(chartStart(chart, null)).toBe(0)
    expect(chartStart(chart, 90_000)).toBe(0)
  })
})

describe('the first day chart', () => {
  it('borrows the live load model in the report chart shape', () => {
    const chart = chartFromLoad(
      {
        xs: [0, 60, 120],
        res: 60,
        bands: [
          {
            key: 'a',
            label: 'A',
            color: 'x',
            share: 0.7,
            totalMs: 700,
            calls: 70,
            meanMs: 10,
            isOther: false,
          },
          {
            key: 'other',
            label: 'Everything else',
            color: 'y',
            share: 0.3,
            totalMs: 300,
            calls: 30,
            meanMs: 10,
            isOther: true,
          },
        ],
        load: [
          [1, 2, 3],
          [1, 1, 1],
        ],
        calls: [
          [1, 2, 3],
          [1, 1, 1],
        ],
        latency: [
          [10, 10, 10],
          [10, 10, 10],
        ],
        overallLatency: [10, 10, 10],
        totalMs: 1000,
        shares: new Map(),
        scope: new Map(),
        colorFor: () => 'x',
      },
      [0, 120]
    )
    expect(chart.bands.map((band) => band.kind)).toEqual(['query', 'other'])
    expect(chart.bands[1].color_index).toBeNull()
    expect(chart.qps[0]).toEqual([1, 2, 3])
    expect(chart.start).toBe(0)
    expect(chart.end).toBe(120)
  })
})

describe('the Detailed tiles', () => {
  it('prints each number with the day before under it', () => {
    expect(detailed.tiles.map(detailedTileText)).toEqual([
      { value: '11.7', detail: 'was 9.8' },
      { value: '15.9', unit: 'ms', detail: 'was 12.4 ms' },
      { value: '69.7', unit: 'ms', detail: 'was 51.2 ms' },
      { value: '8', unit: 'of 100', detail: 'was 6' },
      { value: '11.5', unit: '%', detail: 'was 93.8%' },
      { value: '143', unit: 'and 0', detail: 'was 21 and 0' },
    ])
  })

  it('leaves the day before out when there is none, and dashes a collecting tile', () => {
    const [qps] = detailed.tiles
    expect(detailedTileText({ ...qps, prior: null })).toEqual({ value: '11.7' })
    expect(
      detailedTileText({
        ...qps,
        prior: null,
        prior_note: 'No data for the day before',
      })
    ).toEqual({ value: '11.7', detail: 'No data for the day before' })
    expect(
      detailedTileText({ ...qps, value: null, state: 'collecting' })
    ).toEqual({ value: '\u2014', detail: 'Collecting' })
  })

  it('says once in the meta that the day before has no data', () => {
    const none = {
      ...detailed,
      tiles: detailed.tiles.map((tile) => ({
        ...tile,
        prior_note: 'No data for the day before',
      })),
    }
    expect(detailedDayMeta('today so far', none)).toBe(
      'today so far, no data for the day before'
    )
    expect(detailedDayMeta('Oct 7, no data for the day before', none)).toBe(
      'Oct 7, no data for the day before'
    )
    expect(detailedDayMeta('Oct 7, vs the day before', detailed)).toBe(
      'Oct 7, vs the day before'
    )
  })
})

describe('the Detailed stretches', () => {
  it('names the busiest stretches, their queries and their rollbacks', () => {
    const [first, second] = detailed.spikes
    expect(timeSpan(first.start, first.end)).toBe('9:40 to 10:05 AM')
    expect(timeSpan(first.start, second.end)).toBe('9:40 AM to 2:50 PM')
    expect(stretchTitle(first)).toBe(
      "15.9 queries/s, 1.4 times the day's median"
    )
    expect(stretchTitle(second)).toBe('13.2 queries/s, p95 88.4 ms')
    expect(stretchRollbacks(first, 143)).toBe('121 of 143 rollbacks')
    expect(stretchRollbacks(second, 143)).toBe('0 rollbacks')
    expect(stretchCaption(detailed.spikes)).toBe(
      'No spikes, the two busiest stretches marked.'
    )
    expect(spikesMeta(detailed.spikes)).toBe(
      'None crossed the spike line. The two busiest stretches, with the queries behind them.'
    )
  })

  it('counts real spikes as spikes', () => {
    const spike = { ...detailed.spikes[0], kind: 'spike' as const }
    expect(stretchCaption([spike])).toBe('1 spike, marked.')
    expect(spikesMeta([spike])).toBe(
      '1 crossed the spike line, with the queries behind it.'
    )
    expect(stretchTitle({ ...spike, metric: 'latency', peak: 88.4 })).toBe(
      '88.4 ms, 1.4 times usual'
    )
  })
})

describe('what changed', () => {
  it('names the day before only when there is one to compare against', () => {
    expect(changedMeta(report, 12)).toBe(
      '12 queries against October 6, average time per call'
    )
    expect(changedMeta(report, 1, false)).toBe('1 query, average time per call')
  })

  it('maps each kind to its line and its before and after', () => {
    const rows = detailed.what_changed
    expect(rows.map(changedSub)).toEqual([
      '17% of time, reads went to disk as cache hit fell',
      '17% of time, fix 3 covers it',
      'After the index added at 8:12 AM',
      'First seen 9:46 AM, 9.6% of time',
      'Sequential scan to index scan on idx_comments_userid_creationdate',
    ])
    expect(rows.map(changedValue)).toEqual([
      { lead: '48.7 ms to ', strong: '65.0 ms' },
      { lead: '22.1 ms to ', strong: '29.0 ms' },
      { lead: '27.6 ms to ', strong: '1.9 ms' },
      { lead: 'new, ', strong: '63.3 ms' },
      { lead: '', strong: '8:12 AM' },
    ])
    expect(shareText(0.096)).toBe('9.6%')
    expect(shareText(0.37)).toBe('37%')
  })
})

describe('the rest of Detailed', () => {
  it('counts the checks, copies and index changes from the payload', () => {
    expect(checksMeta(detailed.health_checks)).toBe('3 of 8 checks need you')
    expect(copyCount(report)).toBe(8)
    expect(copyCount({ ...report, copy_count: undefined })).toBe(8)
    expect(detailed.index_changes_seen.map(indexChangeText)).toEqual([
      {
        title: 'Added idx_comments_userid_creationdate',
        sub: 'Matches a fix RDST suggested',
        value: 'Confirmed',
      },
      {
        title: 'Added idx_users_displayname',
        sub: 'Not from RDST. No query read it by midnight.',
        value: '0 scans',
      },
    ])
  })

  it('times what RDST did, a stretch to the end of the day as all day', () => {
    const end = report.period_end as number
    expect(detailed.what_rdst_did.map((row) => didTime(row, end))).toEqual([
      '12:00 AM',
      '8:12 AM',
      '9:12 AM',
      '9:47 to 9:49 AM',
      '9:52 AM',
      '9:55 AM',
      'all day',
    ])
  })

  it('adds What changed and the settings audit to the Markdown', () => {
    const markdown = reportMarkdown(report, now)
    expect(markdown).toContain(
      '- Got slower: SELECT on posts by owneruserid, 48.7 ms to 65.0 ms'
    )
    expect(markdown).toContain(
      '- shared_buffers: 128 MB, recommended 2 GB, after a restart'
    )
    expect(reportMarkdown({ ...report, detailed: null }, now)).not.toContain(
      '## Settings audit'
    )
  })
})
