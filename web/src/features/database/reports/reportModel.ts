// Pure helpers for the Report tab: the head's date line, the day menu, the fix groups
// and their copy text, the health meta, the chart shape the first day borrows from the
// live load model, and the words the Detailed sections put on their rows.
import {
  BAND_COLORS,
  type LoadModel,
  OTHER_COLOR,
} from '@/features/monitor-kit/charts'
import { formatClock, plural } from '@/features/monitor-kit/format'
import { toCsv } from '@/lib/csv'
import type {
  ConfirmedFixRow,
  DatabaseReport,
  DetailedStretch,
  DetailedTile,
  HealthCheckRow,
  IndexChangeSeen,
  RdstDidRow,
  ReportDay,
  ReportDayTile,
  ReportDetailed,
  ReportFixItem,
  ReportHealthRow,
  ReportSummary,
  SnapshotChart,
  WhatChangedRow,
} from '@/lib/monitor/types'
import { cleanHistory } from './reportNav'

const DAY = 86400
const DASH = '\u2014'
const NUMBER_WORDS = [
  'zero',
  'one',
  'two',
  'three',
  'four',
  'five',
  'six',
  'seven',
  'eight',
  'nine',
]

function startOfDay(ts: number): number {
  const date = new Date(ts * 1000)
  date.setHours(0, 0, 0, 0)
  return date.getTime() / 1000
}

/** "Wednesday, October 7". */
export function longDay(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  })
}

/** "October 7". */
export function monthDay(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString('en-US', {
    month: 'long',
    day: 'numeric',
  })
}

/** "Oct 7". */
export function shortDay(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  })
}

/** The local day a report covers: its period, or the day before it ran. */
export function reportDay(report: DatabaseReport | ReportSummary): number {
  return report.period_start ?? startOfDay(report.ts - DAY)
}

/** "today", "yesterday", or the date, for when a report ran relative to `now`. */
function runDayWord(ranAt: number, now: number): string {
  const days = Math.round((startOfDay(now) - startOfDay(ranAt)) / DAY)
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  return monthDay(ranAt)
}

/** "Wednesday, October 7, run today at 12:00 AM". */
export function reportDateLine(report: DatabaseReport, now: number): string {
  const ranAt = report.run?.ran_at ?? report.finished_at ?? report.ts
  return `${longDay(reportDay(report))}, run ${runDayWord(ranAt, now)} at ${formatClock(ranAt)}`
}

export interface DayMenuEntry {
  id: number
  label: string
  /** "9 fixes", "quiet", "first report". */
  word: string
  latest: boolean
}

/** The day menu: every kept report, newest first, with its fix count as a word. */
export function dayMenu(
  history: ReportSummary[] | undefined,
  limit: number
): DayMenuEntry[] {
  const reports = cleanHistory(history)
  const complete = reports.length < limit
  return reports.map((report, index) => {
    const fixes = report.counts.fix_first
    const first = complete && index === reports.length - 1
    return {
      id: report.id,
      label: shortDay(reportDay(report)),
      word: report.period_partial
        ? 'so far'
        : first
          ? 'first report'
          : fixes === 0
            ? 'quiet'
            : `${fixes} ${fixes === 1 ? 'fix' : 'fixes'}`,
      latest: index === 0,
    }
  })
}

/** The number and unit a day tile shows. */
export function tileText(tile: ReportDayTile): {
  value: string
  unit?: string
} {
  const value = tile.value
  if (value == null) return { value: DASH }
  switch (tile.unit) {
    case 'ms':
      return { value: value.toFixed(value < 1 ? 2 : 1), unit: 'ms' }
    case 'ratio':
      return { value: (value * 100).toFixed(1), unit: '%' }
    case 'count':
      return {
        value: String(Math.round(value)),
        unit: tile.limit ? `of ${tile.limit}` : undefined,
      }
    default:
      return { value: value.toFixed(1) }
  }
}

export function tileTone(
  tile: Pick<ReportDayTile | DetailedTile, 'state'>
): 'neutral' | 'warning' | 'negative' {
  return tile.state === 'bad'
    ? 'negative'
    : tile.state === 'warn'
      ? 'warning'
      : 'neutral'
}

/** The numbered rows and the ones folded behind "N smaller fixes". */
export function splitFixes(items: ReportFixItem[]): {
  main: ReportFixItem[]
  smaller: ReportFixItem[]
} {
  return {
    main: items.filter((item) => item.state !== 'smaller'),
    smaller: items.filter((item) => item.state === 'smaller'),
  }
}

/** Rows that still carry a number: fixed rows read as a check instead. */
export function numberedFixes(items: ReportFixItem[]): ReportFixItem[] {
  return items.filter((item) => item.rank != null && item.state !== 'fixed')
}

/** "Copy all N as SQL": the payload's count, the same on both views. */
export function copyCount(report: DatabaseReport): number {
  return (
    report.copy_count ??
    (report.fix_first ?? []).filter(
      (item) => item.sql && item.state !== 'fixed'
    ).length
  )
}

/** Every fix's SQL in order, for "Copy all N as SQL". */
export function fixesSql(items: ReportFixItem[]): string {
  return items
    .map((item) => item.sql?.trim())
    .filter((sql): sql is string => Boolean(sql))
    .join('\n')
}

/** "in this order, one index on posts covers three queries". */
export function fixFirstMeta(items: ReportFixItem[]): string {
  const grouped = items.find(
    (item) => item.group && item.query_keys.length > 1 && item.kind === 'index'
  )
  if (!grouped) return 'in this order'
  const table = grouped.group?.split(' ')[0] ?? ''
  const count = grouped.query_keys.length
  const word = NUMBER_WORDS[count] ?? String(count)
  return `in this order, one index on ${table} covers ${word} queries`
}

/** "3 of 8 checks need you". */
export function healthMeta(rows: ReportHealthRow[], fine: string[]): string {
  const needing = rows.filter((row) => row.needs_you).length
  return `${needing} of ${rows.length + fine.length} checks need you`
}

/** "5 checks fine". */
export function fineLabel(fine: string[]): string {
  return `${fine.length} ${fine.length === 1 ? 'check' : 'checks'} fine`
}

function percent(share: number | null): string {
  return share == null ? '' : `${Math.round(share * 100)}%`
}

/** The report as CSV: the day's numbers, then the fixes, then the health rows. */
export function reportCsv(report: DatabaseReport): string {
  const rows: unknown[][] = []
  for (const tile of report.day?.tiles ?? []) {
    const text = tileText(tile)
    rows.push([
      'day',
      tile.label,
      `${text.value}${text.unit ? ` ${text.unit}` : ''}`,
      tile.reason ?? tile.detail ?? '',
    ])
  }
  for (const item of report.fix_first ?? [])
    rows.push([
      'fix',
      item.rank == null ? item.state : `${item.rank} ${item.title}`,
      percent(item.share),
      item.sql ?? item.detail ?? '',
    ])
  for (const row of report.health ?? [])
    rows.push(['health', row.finding, row.state, row.detail ?? ''])
  return toCsv(['section', 'item', 'value', 'detail'], rows)
}

/** "rdst-report-demo-2026-10-07.csv": the database and the day the report covers. */
export function reportCsvName(report: DatabaseReport): string {
  const day = new Date(reportDay(report) * 1000)
  const pad = (value: number) => String(value).padStart(2, '0')
  return `rdst-report-${report.target}-${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}.csv`
}

/** "Daily report" for the nightly run; "Report" for one run on request. */
export function reportTitle(report: DatabaseReport): string {
  return report.trigger === 'user' ? 'Report' : 'Daily report'
}

/** "Run at 11:22 PM by RDST" for the nightly run; "Run on request at 11:22 PM" otherwise. */
export function runLine(
  ranAt: number,
  trigger?: 'nightly' | 'on_request'
): string {
  return trigger === 'on_request'
    ? `Run on request at ${formatClock(ranAt)}`
    : `Run at ${formatClock(ranAt)} by RDST`
}

/** The Detailed day's meta, saying once that the day before has no data. */
export function detailedDayMeta(
  label: string | null | undefined,
  detailed: ReportDetailed
): string | undefined {
  const note = detailed.tiles.find((tile) => tile.prior_note)?.prior_note
  if (!note || label?.toLowerCase().includes(note.toLowerCase()))
    return label ?? undefined
  return label ? `${label}, ${note.toLowerCase()}` : note
}

/** The report as Markdown, section by section. */
export function reportMarkdown(report: DatabaseReport, now: number): string {
  const lines: string[] = [
    `# ${reportTitle(report)}, ${reportDateLine(report, now)}`,
  ]
  if (report.verdict)
    lines.push('', `${report.verdict.lead} ${report.verdict.rest}`)
  if (report.day) {
    lines.push('', `## The day, ${report.day.label}`, '')
    for (const tile of report.day.tiles) {
      const text = tileText(tile)
      lines.push(
        `- ${tile.label}: ${text.value}${text.unit ? ` ${text.unit}` : ''}${
          tile.reason
            ? ` (${tile.reason})`
            : tile.detail
              ? `, ${tile.detail}`
              : ''
        }`
      )
    }
  }
  if (report.fix_first?.length) {
    lines.push('', `## Fix first, ${fixFirstMeta(report.fix_first)}`, '')
    for (const item of report.fix_first) {
      const head =
        item.rank == null
          ? `- [x] ${item.title}`
          : `${item.rank}. ${item.title}`
      lines.push(item.detail ? `${head} (${item.detail})` : head)
      if (item.sql)
        lines.push('', '    ```sql', `    ${item.sql}`, '    ```', '')
    }
  }
  if (report.health?.length || report.health_fine?.length) {
    lines.push(
      '',
      `## Health, ${healthMeta(report.health ?? [], report.health_fine ?? [])}`,
      ''
    )
    for (const row of report.health ?? [])
      lines.push(
        `- ${row.finding}${row.detail ? `. ${row.detail}` : ''}${
          row.fix_rank != null ? ` (fix ${row.fix_rank})` : ''
        }`
      )
    if (report.health_fine?.length)
      lines.push(
        `- ${fineLabel(report.health_fine)}: ${report.health_fine.join(', ')}`
      )
  }
  const detailed = report.detailed
  if (detailed?.what_changed.length) {
    lines.push('', '## What changed', '')
    for (const row of detailed.what_changed) {
      const value = changedValue(row)
      lines.push(
        `- ${CHANGED_TAG[row.kind].label}: ${row.display_name}, ${value.lead}${value.strong}`
      )
    }
  }
  if (detailed?.settings_audit.length) {
    lines.push('', '## Settings audit', '')
    for (const row of detailed.settings_audit)
      lines.push(
        `- ${row.parameter}: ${row.current}, recommended ${row.recommended}, ${row.takes_effect}`
      )
  }
  if (report.run)
    lines.push(
      '',
      `${runLine(report.run.ran_at, report.run.trigger)}, ${plural(report.run.analyses, 'analysis', 'analyses')}, ${plural(report.run.rewrites, 'rewrite')} tested, Report #${report.run.number}`
    )
  return `${lines.join('\n')}\n`
}

/** The live load model in the report chart's shape, for the day before the first report. */
export function chartFromLoad(
  model: LoadModel,
  domain: [number, number]
): SnapshotChart {
  return {
    res: model.res,
    start: domain[0],
    end: domain[1],
    xs: model.xs,
    bands: model.bands.map((band, index) => ({
      key: band.key,
      kind: band.isOther ? 'other' : 'query',
      display_name: band.label,
      color_index: band.isOther ? null : index,
      share: band.share,
      calls: band.calls,
      qps: band.calls / Math.max(1, domain[1] - domain[0]),
      mean_ms: band.meanMs,
    })),
    qps: model.calls,
    ms: model.load,
    mean_ms: model.latency,
    overall_mean_ms: model.overallLatency,
    markers: [],
  }
}

/** "p95 69.7 ms": the Latency chart's caption, from the day's latency tile. */
export function latencyChartCaption(
  day: ReportDay | null | undefined
): string | null {
  const detail = day?.tiles.find((tile) => tile.metric === 'mean_ms')?.detail
  return detail?.startsWith('p95 ') ? detail : null
}

/** The identity ramp color for a band; the rest band takes the grey. */
export function bandColor(index: number | null | undefined): string {
  return index == null ? OTHER_COLOR : (BAND_COLORS[index] ?? OTHER_COLOR)
}

// Detailed view

const MERIDIEM = / ?(AM|PM)$/

function oneDecimal(value: number): string {
  return value.toFixed(1)
}

/** "65.0 ms", "12.4 s". */
export function msText(value: number): string {
  return value >= 10_000
    ? `${oneDecimal(value / 1000)} s`
    : `${oneDecimal(value)} ms`
}

/** "17%", "9.6%": one decimal under ten percent. */
export function shareText(share: number): string {
  const percent = share * 100
  return percent > 0 && percent < 10
    ? `${oneDecimal(percent)}%`
    : `${Math.round(percent)}%`
}

/** "9:40 to 10:05 AM", "11:30 AM to 12:10 PM". */
export function timeSpan(start: number, end: number): string {
  const from = formatClock(start)
  const to = formatClock(end)
  const same = from.match(MERIDIEM)?.[1] === to.match(MERIDIEM)?.[1]
  return `${same ? from.replace(MERIDIEM, '') : from} to ${to}`
}

/** A Detailed tile's number, unit, and the day before's under it ("was 9.8"). */
export function detailedTileText(tile: DetailedTile): {
  value: string
  unit?: string
  detail?: string
} {
  if (tile.value == null)
    return {
      value: DASH,
      detail: tile.state === 'collecting' ? 'Collecting' : undefined,
    }
  const was = (text: string | null) =>
    text == null ? (tile.prior_note ?? undefined) : `was ${text}`
  const prior = tile.prior
  switch (tile.metric) {
    case 'rollbacks_deadlocks': {
      const second = Math.round(tile.second ?? 0)
      return {
        value: String(Math.round(tile.value)),
        unit: `and ${second}`,
        detail: was(
          prior == null
            ? null
            : `${Math.round(prior)} and ${Math.round(tile.second_prior ?? 0)}`
        ),
      }
    }
    case 'cache_hit':
      return {
        value: oneDecimal(tile.value * 100),
        unit: '%',
        detail: was(prior == null ? null : `${oneDecimal(prior * 100)}%`),
      }
    case 'connections':
      return {
        value: String(Math.round(tile.value)),
        unit: tile.limit ? `of ${tile.limit}` : undefined,
        detail: was(prior == null ? null : String(Math.round(prior))),
      }
    case 'mean_ms':
    case 'p95_ms':
      return {
        value: oneDecimal(tile.value),
        unit: 'ms',
        detail: was(prior == null ? null : `${oneDecimal(prior)} ms`),
      }
    default:
      return {
        value: oneDecimal(tile.value),
        detail: was(prior == null ? null : oneDecimal(prior)),
      }
  }
}

const COUNT_WORDS = ['no', 'one', 'two', 'three', 'four', 'five']

/** The chart card's caption once the stretches are marked on it. */
export function stretchCaption(stretches: DetailedStretch[]): string {
  const spikes = stretches.filter((stretch) => stretch.kind === 'spike').length
  if (spikes > 0)
    return `${spikes} ${spikes === 1 ? 'spike' : 'spikes'}, marked.`
  if (stretches.length === 0) return 'No spikes.'
  const word = COUNT_WORDS[stretches.length] ?? String(stretches.length)
  return `No spikes, the ${word} busiest ${stretches.length === 1 ? 'stretch' : 'stretches'} marked.`
}

/** The Spikes subhead's meta line. */
export function spikesMeta(stretches: DetailedStretch[]): string {
  const spikes = stretches.filter((stretch) => stretch.kind === 'spike').length
  if (spikes > 0)
    return `${spikes} crossed the spike line, with the queries behind ${spikes === 1 ? 'it' : 'them'}.`
  const word = COUNT_WORDS[stretches.length] ?? String(stretches.length)
  return `None crossed the spike line. The ${word} busiest ${stretches.length === 1 ? 'stretch' : 'stretches'}, with the queries behind them.`
}

/** "15.9 queries/s, 1.4 times the day's median". */
export function stretchTitle(stretch: DetailedStretch): string {
  if (stretch.summary) return stretch.summary
  const latency = stretch.metric === 'latency'
  const peak =
    stretch.peak == null
      ? null
      : latency
        ? msText(stretch.peak)
        : `${oneDecimal(stretch.peak)} queries/s`
  const ratio =
    stretch.ratio == null
      ? null
      : `${oneDecimal(stretch.ratio)} times ${stretch.kind === 'busiest' ? "the day's median" : 'usual'}`
  return [peak, ratio].filter(Boolean).join(', ') || 'A busy stretch'
}

/** "SELECT on votes by postid 31%, SELECT on posts by id 18%". */
export function stretchQueries(stretch: DetailedStretch): string {
  return stretch.queries
    .map((query) =>
      query.share != null
        ? `${query.display_name} ${shareText(query.share)}`
        : query.ratio != null
          ? `${query.display_name} ${oneDecimal(query.ratio)} times`
          : query.display_name
    )
    .join(', ')
}

/** "121 of 143 rollbacks", "0 rollbacks". */
export function stretchRollbacks(
  stretch: DetailedStretch,
  dayTotal: number | null
): string | null {
  const count = stretch.rollbacks
  if (count == null) return null
  const rounded = Math.round(count)
  if (rounded > 0 && dayTotal)
    return `${rounded} of ${Math.round(dayTotal)} rollbacks`
  return `${rounded} ${rounded === 1 ? 'rollback' : 'rollbacks'}`
}

export const CHANGED_TAG: Record<
  WhatChangedRow['kind'],
  { label: string; variant: 'warning' | 'positive' | 'neutral' }
> = {
  slower: { label: 'Got slower', variant: 'warning' },
  faster: { label: 'Got faster', variant: 'positive' },
  new_heavy: { label: 'New heavy', variant: 'neutral' },
  plan_changed: { label: 'Plan changed', variant: 'neutral' },
}

/** "12 queries against October 6, average time per call". */
export function changedMeta(
  report: DatabaseReport,
  queryCount: number,
  compared = true
): string {
  const queries = `${queryCount} ${queryCount === 1 ? 'query' : 'queries'}`
  if (!compared) return `${queries}, average time per call`
  return `${queries} against ${monthDay(reportDay(report) - DAY)}, average time per call`
}

/** The line under a What changed row. */
export function changedSub(row: WhatChangedRow): string | null {
  const share = row.share == null ? null : `${shareText(row.share)} of time`
  if (row.kind === 'plan_changed' || row.kind === 'faster')
    return row.detail ?? share
  if (row.kind === 'new_heavy')
    return [row.detail, share].filter(Boolean).join(', ') || null
  return [share, row.detail].filter(Boolean).join(', ') || null
}

/** The number at a What changed row's right: lead text and the bold part. */
export function changedValue(row: WhatChangedRow): {
  lead: string
  strong: string
} {
  if (row.kind === 'plan_changed')
    return { lead: '', strong: row.ts == null ? '' : formatClock(row.ts) }
  const after = row.after_ms == null ? DASH : msText(row.after_ms)
  if (row.kind === 'new_heavy' || row.before_ms == null)
    return { lead: 'new, ', strong: after }
  return { lead: `${msText(row.before_ms)} to `, strong: after }
}

/** A time on the report's day as a clock, another day with its date. */
function dayClock(ts: number, dayStart: number): string {
  return ts >= dayStart && ts < dayStart + DAY
    ? formatClock(ts)
    : `${monthDay(ts)} at ${formatClock(ts)}`
}

/** "Applied 8:12 AM, confirmed 9:12 AM, 27.6 ms to 1.9 ms". */
export function confirmedSub(row: ConfirmedFixRow, dayStart: number): string {
  return [
    row.applied_at == null
      ? null
      : `Applied ${dayClock(row.applied_at, dayStart)}`,
    row.confirmed_at == null
      ? null
      : `confirmed ${dayClock(row.confirmed_at, dayStart)}`,
    row.before_ms == null || row.after_ms == null
      ? null
      : `${msText(row.before_ms)} to ${msText(row.after_ms)}`,
  ]
    .filter(Boolean)
    .join(', ')
}

/** "3 of 8 checks need you", over every check. */
export function checksMeta(checks: HealthCheckRow[]): string {
  const needing = checks.filter((check) => check.needs_you).length
  return `${needing} of ${checks.length} checks need you`
}

const INDEX_STATUS: Record<NonNullable<IndexChangeSeen['status']>, string> = {
  measuring: 'Measuring',
  verified: 'Confirmed',
  no_change: 'No change yet',
  undone: 'Undone',
}

/** The words on an index change row: title, the line under it and the right value. */
export function indexChangeText(change: IndexChangeSeen): {
  title: string
  sub: string
  value: string | null
} {
  const added = change.kind === 'added'
  const unread = added && change.scans === 0
  return {
    title: `${added ? 'Added' : 'Dropped'} ${change.index_name}`,
    sub: change.from_rdst
      ? 'Matches a fix RDST suggested'
      : unread
        ? 'Not from RDST. No query read it by midnight.'
        : 'Not from RDST',
    value:
      change.from_rdst && change.status
        ? INDEX_STATUS[change.status]
        : change.scans != null
          ? `${change.scans.toLocaleString('en-US')} ${change.scans === 1 ? 'scan' : 'scans'}`
          : null,
  }
}

/** "8:12 AM", "9:47 to 9:49 AM", or "all day" for what ran to the end of the day. */
export function didTime(row: RdstDidRow, dayEnd: number | null): string {
  if (row.until == null) return formatClock(row.ts)
  if (dayEnd != null && row.until >= dayEnd) return 'all day'
  return timeSpan(row.ts, row.until)
}
