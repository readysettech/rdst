import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AgentActivityItem } from '@/lib/agentActivity'
import { activitySearch, exportCsvUrl } from '@/lib/agentActivity'
import {
  activityNote,
  activitySentence,
  allEntriesQuery,
  customBounds,
  dayKey,
  dayLabel,
  expandQuery,
  formatCost,
  formatTokens,
  groupByDay,
  itemStyle,
  outcomeTag,
  plainReason,
  purposeLabel,
  rangeBounds,
  reportDay,
  sentenceParts,
  statementTiming,
  technicalSummary,
  toQuery,
} from './activityModel'
import { exportHref } from './ExportDialog'

const NOON = new Date(2026, 8, 29, 12, 0).getTime() / 1000
const MIDNIGHT = new Date(2026, 8, 29).getTime() / 1000

function item(overrides: Partial<AgentActivityItem> = {}): AgentActivityItem {
  return {
    key: 'entry:1',
    kind: 'entry',
    id: 1,
    ts: NOON,
    first_ts: NOON,
    count: 1,
    done: 1,
    failed: 0,
    skipped: 0,
    target: 'shop',
    action: 'deep_analysis',
    subject: 'SELECT on events by props',
    outcome: 'done',
    summary: '',
    source: 'live',
    statement_count: 0,
    ai_call_count: 0,
    tokens_in: 0,
    tokens_out: 0,
    cost_usd: 0,
    items_filed: 0,
    ...overrides,
  }
}

function rollup(overrides: Partial<AgentActivityItem>): AgentActivityItem {
  return item({
    kind: 'rollup',
    key: 'attention_check:shop:1',
    action: 'attention_check',
    fold: 'attention_check',
    group: 'day',
    subject: '',
    bucket_start: MIDNIGHT,
    bucket_end: MIDNIGHT + 86400,
    first_ts: MIDNIGHT + 60,
    ...overrides,
  })
}

describe('activity sentences', () => {
  it('says what a deep analysis found', () => {
    expect(
      activitySentence(
        item({
          finding: 'suggested an index on events(event_type, created_at)',
        })
      )
    ).toBe(
      'The agent analyzed SELECT on events by props and suggested an index on events(event_type, created_at).'
    )
    expect(activitySentence(item())).toBe(
      'The agent analyzed SELECT on events by props.'
    )
    const failed = item({
      outcome: 'failed',
      summary: 'LOGIN_REQUIRED: Sign in to run Deep Analyze',
    })
    expect(activitySentence(failed)).toBe(
      "The agent couldn't finish analyzing SELECT on events by props."
    )
    expect(activityNote(failed)).toBe('Sign in to run Deep Analyze.')
    expect(outcomeTag(failed)).toEqual({ label: 'Failed', variant: 'negative' })
    expect(outcomeTag(item())).toBeNull()
  })

  it('quotes the question Ask answered and the tables it used', () => {
    expect(
      activitySentence(
        item({
          action: 'ask',
          subject: 'orders, customers',
          question: 'Top 10 customers by spend?',
        })
      )
    ).toBe(
      'The agent answered "Top 10 customers by spend?" using orders, customers.'
    )
    expect(
      activitySentence(
        item({
          action: 'ask',
          question: 'Which orders?',
          decision: 'Asked you to pick an interpretation',
          subject: '',
        })
      )
    ).toBe('The agent asked you to clarify "Which orders?".')
  })

  it('names the day a daily report covers', () => {
    const nightly = item({
      action: 'daily_snapshot',
      subject: 'Nightly',
      ts: MIDNIGHT + 60,
    })
    expect(activitySentence(nightly)).toBe(
      'The agent ran the nightly checkup for Mon, Sep 28.'
    )
    const recorded = item({
      action: 'daily_snapshot',
      subject: 'On request',
      period_start: MIDNIGHT,
      period_end: MIDNIGHT + 3600 * 12,
    })
    expect(reportDay(recorded)).toEqual({ day: 'Tue, Sep 29', partial: true })
    expect(activitySentence(recorded)).toBe(
      'The agent ran a checkup for Tue, Sep 29 so far, on request.'
    )
  })

  it('sums up a day of attention checks', () => {
    const day = rollup({ count: 288, done: 285, failed: 3, items_filed: 3 })
    expect(activitySentence(day)).toBe(
      'The agent ran 288 attention checks and flagged 3 changes for the Inbox.'
    )
    expect(activityNote(day)).toBe("3 didn't complete.")
    expect(outcomeTag(day)).toBeNull()
    expect(activitySentence(rollup({ count: 1, items_filed: 0 }))).toBe(
      'The agent ran 1 attention check and found nothing new to file.'
    )
  })

  it('batches classifications and skipped analyses', () => {
    const batch = rollup({
      action: 'classification',
      fold: 'classification',
      count: 158,
      done: 12,
      skipped: 146,
    })
    expect(activitySentence(batch)).toBe(
      'The agent classified 12 queries and set 146 aside.'
    )
    expect(
      activitySentence(
        rollup({
          action: 'classification',
          fold: 'classification',
          count: 5,
          done: 0,
          skipped: 5,
        })
      )
    ).toBe('The agent set 5 queries aside without classifying them.')
    const skipped = rollup({
      action: 'deep_analysis',
      fold: 'skipped_analysis',
      count: 4,
      done: 0,
      skipped: 4,
      outcome: 'skipped',
    })
    expect(activitySentence(skipped)).toBe(
      "The agent skipped 4 queries it doesn't run, such as writes and schema changes."
    )
    expect(itemStyle(skipped).icon).toBe('lock-sync')
    expect(activityNote(skipped)).toBe('No statements ran and no AI was used.')
  })

  it('gives each kind of work its own icon and color', () => {
    const styles = (
      [
        'deep_analysis',
        'ask',
        'classification',
        'attention_check',
        'daily_snapshot',
        'benchmark',
      ] as const
    ).map((action) => itemStyle(item({ action })))
    expect(new Set(styles.map((style) => style.icon)).size).toBe(6)
    expect(new Set(styles.map((style) => style.tone)).size).toBe(6)
  })

  it('marks what each sentence is about', () => {
    expect(
      sentenceParts(item({ finding: 'suggested a rewrite' })).filter(
        (part) => part.strong
      )
    ).toEqual([{ text: 'SELECT on events by props', strong: true }])
  })

  it('starts nested sentences at the verb', () => {
    const text = (parts: { text: string }[]) =>
      parts.map((part) => part.text).join('')
    expect(text(sentenceParts(rollup({ group: 'hour', count: 3 }), true))).toBe(
      'Ran 3 attention checks and found nothing new to file.'
    )
    expect(
      text(
        sentenceParts(
          item({ action: 'attention_check', outcome: 'failed' }),
          true
        )
      )
    ).toBe("The attention check didn't complete.")
  })

  it('drops the error code in front of a reason', () => {
    expect(plainReason('LOGIN_REQUIRED: Sign in to run Deep Analyze.')).toBe(
      'Sign in to run Deep Analyze'
    )
    expect(
      plainReason("CREATE changes the schema, so RDST doesn't run it.")
    ).toBe("CREATE changes the schema, so RDST doesn't run it")
  })
})

describe('days, ranges and expansion', () => {
  it('groups newest first by local day with the date beside Today', () => {
    const yesterday = NOON - 86400
    const groups = groupByDay(
      [item({ key: 'a' }), item({ key: 'b', ts: yesterday })],
      NOON
    )
    expect(groups.map((group) => [group.label, group.date])).toEqual([
      ['Today', 'Tue, Sep 29'],
      ['Yesterday', 'Mon, Sep 28'],
    ])
    expect(dayLabel(dayKey(NOON - 3 * 86400), NOON)).toBe('Saturday, Sep 26')
  })

  it('turns presets and custom days into [since, until)', () => {
    expect(rangeBounds('today', NOON)).toEqual({
      since: MIDNIGHT,
      until: MIDNIGHT + 86400,
    })
    const week = rangeBounds('7d', NOON)
    expect(week.until).toBe(MIDNIGHT + 86400)
    expect(dayKey(week.since as number)).toBe('2026-09-23')
    expect(rangeBounds('all', NOON)).toEqual({})
    expect(customBounds('2026-09-20', '2026-09-22')).toEqual({
      since: new Date(2026, 8, 20).getTime() / 1000,
      until: new Date(2026, 8, 23).getTime() / 1000,
    })
    expect(customBounds('2026-09-22', '2026-09-20')).toBeNull()
    expect(toQuery({ target: 'shop', range: 'today', now: NOON })).toEqual({
      target: 'shop',
      action: undefined,
      since: MIDNIGHT,
      until: MIDNIGHT + 86400,
    })
  })

  it('opens a day of checks to its hours and other folds to their entries', () => {
    const day = rollup({ count: 12 })
    expect(expandQuery(day)).toEqual({
      target: 'shop',
      action: 'attention_check',
      since: MIDNIGHT,
      until: MIDNIGHT + 86400,
      group: 'hour',
    })
    expect(expandQuery({ ...day, group: 'hour' }).group).toBe('none')
    const batch = rollup({
      action: 'classification',
      fold: 'classification',
      done: 2,
    })
    expect(expandQuery(batch).outcome).toBe('done')
    expect(allEntriesQuery(batch)).toMatchObject({
      outcome: undefined,
      group: 'none',
    })
    expect(
      expandQuery(rollup({ action: 'deep_analysis', fold: 'skipped_analysis' }))
        .outcome
    ).toBe('skipped')
  })
})

describe('requests', () => {
  afterEach(() => vi.restoreAllMocks())

  it('sends filters, the fold and the local offset', () => {
    vi.spyOn(Date.prototype, 'getTimezoneOffset').mockReturnValue(420)
    expect(
      activitySearch(
        { target: 'shop', action: 'ask', since: 10, until: 20 },
        '5.9'
      )
    ).toBe(
      '?target=shop&action=ask&since=10&until=20&utc_offset=-420&cursor=5.9'
    )
    expect(activitySearch({ group: 'none', outcome: 'skipped' })).toBe(
      '?outcome=skipped&group=none'
    )
    expect(exportCsvUrl({ since: 10, until: 20 })).toBe(
      '/api/agent-activity/export.csv?since=10&until=20&utc_offset=-420'
    )
  })

  it('exports a preset or a finished custom range', () => {
    vi.spyOn(Date.prototype, 'getTimezoneOffset').mockReturnValue(0)
    expect(
      exportHref({ target: 'shop', range: 'today', from: '', to: '' }, NOON)
    ).toBe(
      `/api/agent-activity/export.csv?target=shop&since=${MIDNIGHT}&until=${MIDNIGHT + 86400}&utc_offset=0`
    )
    expect(
      exportHref(
        { range: 'custom', from: '2026-09-29', to: '2026-09-29' },
        NOON
      )
    ).toContain(`since=${MIDNIGHT}&until=${MIDNIGHT + 86400}`)
    expect(
      exportHref({ range: 'custom', from: '2026-09-29', to: '' }, NOON)
    ).toBeNull()
  })
})

describe('technical details', () => {
  it('formats statements, AI calls and costs', () => {
    expect(
      statementTiming({
        sql: 'EXPLAIN ANALYZE SELECT 1',
        kind: 'explain_analyze',
        executions: 1,
        duration_ms: 42,
        timeout_ms: 1500,
      })
    ).toBe('42 ms, timeout 1.5 s')
    expect(formatCost(0.000038, true)).toBe('under $0.0001')
    expect(formatCost(0.0031, true)).toBe('about $0.0031')
    expect(formatCost(0.0031)).toBe('$0.0031')
    expect(formatTokens({ tokens_in: 5200, tokens_out: 640 })).toBe(
      '5,200 in, 640 out'
    )
    expect(purposeLabel({ operation: 'monitor_attention', purpose: 'x' })).toBe(
      'Does anything need attention'
    )
    expect(technicalSummary({ statement_count: 2, ai_call_count: 1 })).toBe(
      '2 statements, 1 AI call'
    )
    expect(technicalSummary({ statement_count: 0, ai_call_count: 0 })).toBe(
      'No statements, no AI calls'
    )
  })
})
