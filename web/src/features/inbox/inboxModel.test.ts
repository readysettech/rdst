import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { muteRuleInput, muteScopes } from '@/features/monitor-kit/MuteMenu'
import {
  alertTypeLabel,
  bandFor,
  countsAsUnread,
  describeRule,
  itemBand,
  ruleMatches,
} from '@/features/monitor-kit/severity'
import {
  createMuteRule,
  deleteMuteRule,
  getInbox,
  getMuteRules,
  resetStubState,
  setFixtureTiming,
} from '@/lib/monitor/api'
import { setFixtureOverride } from '@/lib/monitor/contract'
import { FIXTURE_TARGET, inboxItems } from '@/lib/monitor/fixtures'
import type { InboxItem, MuteRule } from '@/lib/monitor/types'
import { adviceSteps } from './adviceSteps'
import { itemLink, linkRoute } from './inboxLinks'
import {
  closedLabel,
  formatStampTime,
  groupItems,
  groupSummary,
  inboxCounts,
  itemPlace,
  recentlyClosedItems,
  sentStamp,
  shapeOpen,
  summaryTokens,
} from './inboxModel'

const NOW = 1_790_611_200

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('localdev')
})

afterEach(() => setFixtureOverride(undefined))

async function openInbox() {
  const [{ items }, { rules }] = await Promise.all([
    getInbox({ state: 'open', include_muted: true }),
    getMuteRules(),
  ])
  return { items, rules, shaped: shapeOpen(items, rules) }
}

describe('severity bands', () => {
  it('maps severity and type to act now, watch and FYI', () => {
    expect(bandFor('latency_regression', 0.9)).toBe('act_now')
    expect(bandFor('stuck_query', 0.3)).toBe('act_now')
    expect(bandFor('latency_regression', 0.7)).toBe('watch')
    expect(bandFor('latency_regression', 0.4)).toBe('fyi')
    expect(bandFor('query_improved', 0.7)).toBe('fyi')
  })

  it('lists the fixture as Act now and Watch, with FYI last in Watch', async () => {
    expect(inboxItems(NOW)).toHaveLength(47)
    const { shaped } = await openInbox()
    const { act_now, watch } = shaped.sections
    expect([act_now.count, watch.count]).toEqual([2, 7])
    // Recoveries from the last day list apart from the open alerts.
    expect(shaped.recovered).toHaveLength(2)
    // FYI rows sort after every real Watch row.
    const bands = watch.groups.map((group) => itemBand(group.lead))
    expect(bands.indexOf('fyi')).toBeGreaterThan(bands.lastIndexOf('watch'))
    // Three stopped queries filed minutes apart collapse into one row.
    const stopped = watch.groups.find((group) => group.items.length > 1)
    expect(stopped?.items).toHaveLength(3)
    expect(stopped && groupSummary(stopped)).toMatch(
      /^3 alerts like this on localdev, filed within \d+ min of each other$/
    )
  })

  it('gives the tabs, sections and badge one set of counts', async () => {
    const { items, rules, shaped } = await openInbox()
    const counts = inboxCounts(items, rules)
    expect(counts).toEqual(shaped.counts)
    expect(counts.open).toBe(counts.actNow + counts.watch)
    expect(counts.actNow).toBe(shaped.sections.act_now.count)
    expect(counts.watch).toBe(shaped.sections.watch.count)
    expect(counts.muted).toBe(shaped.muted.length)
    // Every open alert lands in exactly one place.
    const listed =
      counts.open + counts.muted + counts.snoozed + shaped.recovered.length
    expect(listed).toBe(items.length)
  })

  it('places recoveries and closed alerts apart from the live sections', () => {
    const [item] = inboxItems(NOW)
    expect(itemPlace({ ...item, severity_band: 'act_now' })).toBe('act_now')
    expect(itemPlace({ ...item, severity_band: 'fyi' })).toBe('watch')
    expect(itemPlace({ ...item, severity_band: 'recovered' })).toBe('resolved')
    expect(itemPlace({ ...item, state: 'dismissed' })).toBe('resolved')
  })

  it('counts unread only for new, unmuted act now and watch items', () => {
    const [episode] = inboxItems(NOW)
    expect(countsAsUnread(episode)).toBe(true)
    expect(countsAsUnread({ ...episode, state: 'seen' })).toBe(false)
    expect(countsAsUnread({ ...episode, muted_by: 3 })).toBe(false)
    expect(
      countsAsUnread({ ...episode, severity: 0.2, severity_band: 'fyi' })
    ).toBe(false)
  })
})

describe('grouping', () => {
  it('keeps singletons as item rows and orders groups by urgency', () => {
    const items = inboxItems(NOW).slice(0, 12)
    const groups = groupItems(items)
    expect(groups[0].lead.type).toBe('many_queries_changed')
    expect(groups.find((group) => group.id.startsWith('item:'))).toBeTruthy()
  })

  it('lists recoveries and alerts closed in the last 24 hours, newest first, once', () => {
    const done = inboxItems(NOW).filter((item) => item.state === 'resolved')
    const [listed] = done
    const old = { ...done[1], resolved_at: NOW - 25 * 3600 }
    const recovered = {
      ...inboxItems(NOW)[0],
      id: 9999,
      state: 'resolved' as const,
      severity_band: 'recovered' as const,
      resolved_at: NOW - 60,
    }
    const live = {
      ...inboxItems(NOW)[0],
      id: 9998,
      severity_band: 'watch' as const,
    }
    const recent = recentlyClosedItems(
      [listed, recovered, live],
      [...done.slice(2), old, listed],
      NOW
    )
    const ids = recent.map((item) => item.id)
    expect(ids.filter((id) => id === listed.id)).toHaveLength(1)
    expect(ids[0]).toBe(recovered.id)
    expect(ids).not.toContain(old.id)
    expect(ids).not.toContain(live.id)
    const times = recent.map((item) => item.resolved_at ?? item.updated_at)
    expect(times).toEqual([...times].sort((a, b) => b - a))
  })
})

describe('mute rules', () => {
  const regression = () =>
    inboxItems(NOW).find(
      (item) => item.type === 'latency_regression' && item.muted_by === null
    ) as InboxItem

  it('offers three scopes for a query alert, two for a database alert', () => {
    const item = regression()
    const subject = {
      target: item.target,
      queryKey: item.query_keys[0],
      type: item.type,
    }
    expect(muteScopes(subject).map((option) => option.scope)).toEqual([
      'query_type',
      'query',
      'type_target',
      'type_all',
    ])
    expect(
      muteScopes({
        target: 'localdev',
        type: 'rollback_or_deadlock_spike',
      }).map((option) => option.scope)
    ).toEqual(['type_target', 'type_all'])
  })

  it.each([
    'query_type',
    'query',
    'type_target',
    'type_all',
  ] as const)('round-trips a %s mute through the Muted tab and back', async (scope) => {
    const item = regression()
    const subject = {
      target: item.target,
      queryKey: item.query_keys[0],
      type: item.type,
    }
    const rule = await createMuteRule(muteRuleInput(subject, scope, 'week'))
    expect(rule.until).not.toBeNull()
    let { shaped } = await openInbox()
    expect(shaped.muted.map((muted) => muted.id)).toContain(item.id)
    expect(
      Object.values(shaped.sections).flatMap((section) =>
        section.groups.flatMap((group) => group.items.map((i) => i.id))
      )
    ).not.toContain(item.id)

    await deleteMuteRule(rule.id)
    ;({ shaped } = await openInbox())
    expect(shaped.muted.map((muted) => muted.id)).not.toContain(item.id)
  })

  it('matches on target, type and query, and ignores expired rules', () => {
    const item = regression()
    const base: MuteRule = {
      id: 1,
      target: FIXTURE_TARGET,
      query_key: null,
      type: item.type,
      until: null,
      created_at: NOW,
      note: null,
      matched_count: 0,
    }
    expect(ruleMatches(base, item, NOW)).toBe(true)
    expect(ruleMatches({ ...base, target: 'aurora-pg' }, item, NOW)).toBe(false)
    expect(ruleMatches({ ...base, until: NOW - 1 }, item, NOW)).toBe(false)
    expect(
      ruleMatches({ ...base, type: null, query_key: 'other' }, item, NOW)
    ).toBe(false)
    expect(describeRule({ ...base, target: null })).toBe(
      'Query got slower on all targets'
    )
  })
})

describe('summary tokens', () => {
  it('emphasizes numbers with units and turns -> into an arrow', () => {
    const tokens = summaryTokens('Latency 0.08 ms -> 0.58 ms, 25 % of load.')
    expect(tokens.filter((token) => token.kind === 'number')).toEqual([
      { kind: 'number', text: '0.08 ms' },
      { kind: 'number', text: '0.58 ms' },
      { kind: 'number', text: '25 %' },
    ])
    expect(tokens.some((token) => token.kind === 'arrow')).toBe(true)
  })

  it('leaves digits inside identifiers alone', () => {
    const numbers = summaryTokens(
      'pg_sleep(180) has held a connection for 180 s'
    )
      .filter((token) => token.kind === 'number')
      .map((token) => (token.kind === 'number' ? token.text : ''))
    expect(numbers).toEqual(['180 s'])
  })

  it('keeps a time and its date together', () => {
    const numbers = summaryTokens('It ran until Sep 27, 8:49 AM and stopped.')
      .filter((token) => token.kind === 'number')
      .map((token) => (token.kind === 'number' ? token.text : ''))
    expect(numbers).toEqual(['Sep 27, 8:49 AM'])
  })
})

describe('alertTypeLabel', () => {
  it.each([
    ['rollback_or_deadlock_spike', 'Rollbacks or deadlocks rose'],
    ['query_stopped_running', 'Query stopped running'],
    ['cache_hit_ratio_drop', 'Cache hit ratio dropped'],
    ['traffic_spike', 'Traffic spike'],
    ['connections_near_max', 'Connections near maximum'],
  ])('labels the backend type %s', (type, label) => {
    expect(alertTypeLabel(type)).toBe(label)
  })
})

describe('deep links', () => {
  const base = inboxItems(NOW)[0]
  it('opens a query row on the tab the backend chose', () => {
    expect(
      linkRoute({
        kind: 'query',
        target: 'localdev',
        query_key: 'abc',
        tab: 'analysis',
        label: 'Open query',
      })
    ).toEqual({
      to: '/database',
      search: { target: 'localdev', query: 'abc', tab: 'analysis' },
    })
  })

  it('opens reports, settings, connections and sign-in', () => {
    expect(
      linkRoute({
        kind: 'report',
        target: 'db',
        report_id: 7,
        label: 'Open report',
      })
    ).toEqual({
      to: '/database',
      search: { target: 'db', view: 'report', report: '7' },
    })
    expect(
      linkRoute({ kind: 'setup', target: 'db', label: 'Check setup' })
    ).toEqual({ to: '/database', search: { target: 'db', panel: 'setup' } })
    expect(
      linkRoute({
        kind: 'settings',
        target: 'db',
        label: 'Monitoring settings',
      })
    ).toEqual({ to: '/database', search: { target: 'db', panel: 'settings' } })
    expect(
      linkRoute({ kind: 'configure', target: 'db', label: 'Check connection' })
        .search
    ).toEqual({ panel: 'connections', edit: 'db' })
    expect(
      linkRoute({ kind: 'sign_in', target: 'db', label: 'Sign in' }).to
    ).toBe('/account-login')
  })

  it('falls back to the query row for items without a link', () => {
    const { link: _link, ...legacy } = base
    const single = { ...legacy, query_keys: ['k1'] } as InboxItem
    expect(itemLink(single)).toMatchObject({ kind: 'query', query_key: 'k1' })
  })
})

describe('what you can do', () => {
  it('splits SQL out of a step so it can be copied', () => {
    expect(
      adviceSteps([
        'Cancel it: SELECT pg_cancel_backend(<pid>);',
        'Check the service that runs it.',
      ])
    ).toEqual([
      { text: 'Cancel it', sql: 'SELECT pg_cancel_backend(<pid>);' },
      { text: 'Check the service that runs it.', sql: null },
    ])
  })
})

describe('closed labels', () => {
  const [item] = inboxItems(NOW)
  const closed = NOW - 3600
  const time = formatStampTime(closed, NOW)
  it('says how it ended, as observed', () => {
    const base = { ...item, state: 'resolved' as const, resolved_at: closed }
    expect(
      closedLabel(
        { ...base, resolved_by: 'code', severity_band: 'recovered' },
        NOW
      )
    ).toBe(`Every query back to normal at ${time}`)
    expect(
      closedLabel(
        { ...base, type: 'query_stopped_running', resolved_by: 'code' },
        NOW
      )
    ).toBe(`Running again at ${time}`)
    expect(closedLabel({ ...base, resolved_by: 'user' }, NOW)).toBe(
      `You resolved it at ${time}`
    )
    expect(
      closedLabel({ ...base, state: 'dismissed', resolved_by: null }, NOW)
    ).toBe(`Dismissed at ${time}`)
    expect(
      closedLabel(
        { ...base, resolved_at: closed - 86_400, resolved_by: 'user' },
        NOW
      )
    ).toMatch(/^You resolved it (yesterday at|on \w{3} \d+ at) /)
  })
})

describe('sent time', () => {
  // Local times, so the checks hold in any time zone.
  const at = (day: number, hour: number, minute: number) =>
    new Date(2026, 8, day, hour, minute).getTime() / 1000
  const now = at(28, 22, 30)
  const item = (patch: Partial<InboxItem>): InboxItem => ({
    ...inboxItems(NOW, 'localdev')[0],
    delivery: 'notify',
    muted_by: null,
    created_at: at(28, 21, 42),
    updated_at: at(28, 21, 42),
    ...patch,
  })

  it('names today, yesterday and older days', () => {
    expect(formatStampTime(at(28, 21, 42), now)).toBe('9:42 PM')
    expect(formatStampTime(at(27, 23, 5), now)).toBe('yesterday 11:05 PM')
    expect(formatStampTime(at(26, 16, 10), now)).toBe('Sep 26, 4:10 PM')
    expect(
      formatStampTime(new Date(2025, 8, 26, 16, 10).getTime() / 1000, now)
    ).toBe('Sep 26, 2025, 4:10 PM')
  })

  it('says Sent for pushed alerts, at the push when the backend reports it', () => {
    expect(sentStamp(item({}), now).label).toBe('Sent 9:42 PM')
    const reopened = sentStamp(item({ notified_at: at(28, 22, 5) }), now)
    expect(reopened.label).toBe('Sent 10:05 PM')
    expect(reopened.title).toContain('Filed ')
  })

  it('says where an alert went when nothing was pushed', () => {
    expect(sentStamp(item({ delivery: 'digest' }), now).label).toBe(
      'Filed 9:42 PM, in the daily digest'
    )
    expect(sentStamp(item({ delivery: 'inbox' }), now).label).toBe(
      'Filed 9:42 PM, no alert sent'
    )
    expect(sentStamp(item({ notified_at: null }), now).label).toBe(
      'Filed 9:42 PM, no alert sent'
    )
    expect(sentStamp(item({ muted_by: 3 }), now).label).toBe(
      'Filed 9:42 PM, muted'
    )
  })

  it('keeps the full timestamps for the tooltip', () => {
    const stamp = sentStamp(item({ updated_at: at(28, 22, 20) }), now)
    expect(stamp.title.split('\n')).toHaveLength(2)
    expect(stamp.title).toMatch(/^Sent .*2026.*9:42:00 PM/)
    expect(stamp.title).toMatch(/Last updated .*10:20:00 PM/)
    expect(stamp.iso).toBe(new Date(at(28, 21, 42) * 1000).toISOString())
  })
})

describe('several-query links', () => {
  it('open the Database page on exactly the affected queries', async () => {
    const { linkRoute } = await import('./inboxLinks')
    expect(
      linkRoute({
        kind: 'database',
        target: 'db',
        label: 'Open affected queries',
        query_keys: ['a1', 'b2'],
      })
    ).toEqual({ to: '/database', search: { target: 'db', queries: 'a1,b2' } })
  })
})
