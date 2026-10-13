import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { muteRuleInput, muteScopes } from '@/features/monitor-kit/MuteMenu'
import {
  alertTypeLabel,
  describeRule,
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
import { at, CA_NOW } from '@/lib/monitor/fixturesCa'
import type { InboxItem, MuteRule } from '@/lib/monitor/types'
import { fixRoute, itemLink, itemRoute, linkRoute } from './inboxLinks'
import {
  asideItems,
  bandOf,
  closedGroups,
  closedSentence,
  detailMeta,
  emptySentence,
  formatStampTime,
  inboxClock,
  inboxCounts,
  isMuted,
  keptClosed,
  kindOf,
  noChangeText,
  openGroups,
  queueTime,
  rowMeta,
  runningText,
  sameWords,
  summaryTokens,
  timePhrase,
  wholeSentences,
  withoutDatabase,
} from './inboxModel'

const NOW = 1_790_611_200

beforeEach(() => {
  setFixtureTiming(0)
  resetStubState()
  setFixtureOverride('localdev')
})

afterEach(() => setFixtureOverride(undefined))

async function redesignInbox() {
  setFixtureOverride('ca-inbox')
  const [open, done] = await Promise.all([
    getInbox({ state: 'open' }),
    getInbox({ state: 'done' }),
  ])
  return { open: open.items, closed: done.items }
}

describe('bands and kinds', () => {
  it('reads the redesign fields and derives them for older items', () => {
    const [older] = inboxItems(NOW)
    expect(bandOf({ ...older, band: 'watch' })).toBe('watch')
    expect(kindOf({ ...older, kind: 'fixes_ready' })).toBe('fixes_ready')
    const stuck = {
      ...older,
      type: 'stuck_query',
      severity: 0.9,
      kind: undefined,
      band: undefined,
    }
    expect(kindOf(stuck)).toBe('stuck_query')
    expect(bandOf(stuck)).toBe('act_now')
    expect(bandOf({ ...stuck, state: 'resolved' })).toBe('closed')
    expect(
      kindOf({ ...older, type: 'latency_regression', kind: undefined })
    ).toBe('other')
    for (const type of ['changes_ready', 'no_change_yet'])
      expect(kindOf({ ...older, type, kind: undefined })).toBe(type)
  })

  it('lets the state win over the band the item was fetched with', async () => {
    const { open, closed } = await redesignInbox()
    expect(bandOf({ ...open[0], state: 'resolved' })).toBe('closed')
    expect(bandOf({ ...closed[0], state: 'new' })).toBe('watch')
  })

  it('counts open items once, leaving muted, snoozed and closed ones out', async () => {
    const { open, closed } = await redesignInbox()
    const counts = inboxCounts([...open, ...closed], [])
    expect(counts).toMatchObject({ open: 2, actNow: 1, watch: 1 })
    expect(inboxCounts([{ ...open[0], state: 'snoozed' }], []).snoozed).toBe(1)
  })
})

describe('queue groups', () => {
  it('lists Act now then Watch on Open, closed items staying behind Closed', async () => {
    const { open, closed } = await redesignInbox()
    const groups = openGroups([...open, ...closed])
    expect(groups.map((group) => [group.label, group.items.length])).toEqual([
      ['Act now', 1],
      ['Watch', 1],
    ])
    expect(groups[0].items[0].title).toBe('Stuck query on demo')
    expect(groups[1].items[0].kind).toBe('changes_ready')
  })

  it('leaves out a group with nothing in it', () => {
    expect(openGroups([])).toEqual([])
  })

  it('groups Closed by Today, Yesterday and Earlier this week, kept 7 days', async () => {
    const { closed } = await redesignInbox()
    const groups = closedGroups(closed, CA_NOW)
    expect(groups.map((group) => [group.label, group.items.length])).toEqual([
      ['Today', 3],
      ['Yesterday', 3],
      ['Earlier this week', 2],
    ])
    expect(groups[0].items.map((item) => item.id)).toEqual([5, 4, 13])
    expect(keptClosed(closed, at(9, 56, 0, 8))).toEqual([])
  })

  it('uses the fixture clock while a redesign scenario is on', () => {
    setFixtureOverride('ca-inbox')
    expect(inboxClock()).toBe(CA_NOW)
    setFixtureOverride('localdev')
    expect(Math.abs(inboxClock() - Date.now() / 1000)).toBeLessThan(5)
  })
})

describe('row and header words', () => {
  it('shows the clock today, the clock with AM or PM yesterday, the weekday before', () => {
    expect(queueTime(at(9, 55), CA_NOW)).toBe('9:55')
    expect(queueTime(at(0, 0), CA_NOW)).toBe('12:00')
    expect(queueTime(at(16, 31, 0, -1), CA_NOW)).toBe('4:31 PM')
    expect(queueTime(at(14, 25, 0, -2), CA_NOW)).toBe('Tue')
    expect(queueTime(at(14, 25, 0, -9), CA_NOW)).toBe('Sep 29')
  })

  it('says how long a stuck statement has run, only while it runs', async () => {
    const { open, closed } = await redesignInbox()
    const stuck = open.find((item) => item.kind === 'stuck_query') as InboxItem
    expect(runningText(stuck, CA_NOW)).toBe('running 4 min')
    expect(runningText({ ...stuck, running_s: null }, CA_NOW)).toBe(
      'running 2 min'
    )
    expect(runningText(closed[0], CA_NOW)).toBeNull()
    expect(rowMeta(stuck)).toBe('COUNT on posts with pg_sleep')
    expect(rowMeta(closed[0])).toBe(closed[0].summary)
  })

  it('leaves the database out of the caption, which shows it under the time', async () => {
    const { closed } = await redesignInbox()
    expect(withoutDatabase('staging, 41.2 ms to 3.4 ms', 'staging')).toBe(
      '41.2 ms to 3.4 ms'
    )
    expect(withoutDatabase('3 of 8 checks need you on demo.', 'demo')).toBe(
      '3 of 8 checks need you.'
    )
    expect(withoutDatabase('demographics grew', 'demo')).toBe(
      'demographics grew'
    )
    const staging = closed.find((item) => item.id === 7) as InboxItem
    expect(rowMeta(staging)).toBe('41.2 ms to 3.4 ms')
  })

  it('says what a change that did not move still costs', async () => {
    const { closed } = await redesignInbox()
    const none = closed.find((item) => item.kind === 'no_change_yet')
    expect(noChangeText(none as InboxItem)).toBe(
      'index applied October 6, still 14 ms after 2 days'
    )
    expect(rowMeta(none as InboxItem)).toBe(
      'index applied October 6, still 14 ms after 2 days'
    )
    const bare = { ...(none as InboxItem), body_json: '{}' }
    expect(noChangeText(bare)).toBeNull()
    expect(rowMeta(bare)).toBe(bare.summary)
  })

  it('names the database and the moment in the detail header', async () => {
    const { open, closed } = await redesignInbox()
    expect(detailMeta(open[0])).toBe('demo, filed 9:55 AM')
    expect(detailMeta(open[1])).toBe('demo, filed 9:51 AM')
    expect(detailMeta(closed[0])).toBe('demo, closed 9:48 AM')
  })

  it('sums up the open queue for the empty detail panel', () => {
    expect(emptySentence(1, 1)).toBe('1 item needs you, 1 to watch.')
    expect(emptySentence(2, 0)).toBe('2 items need you, 0 to watch.')
    expect(emptySentence(0, 3)).toBe('Nothing needs you, 3 to watch.')
  })

  it('counts the closed list for its empty panel', () => {
    expect(closedSentence(1)).toBe('1 item closed in the last 7 days.')
    expect(closedSentence(0)).toBe('Nothing closed in the last 7 days.')
  })

  it('treats a summary that repeats the title as the same words', () => {
    expect(sameWords('Stuck query ended.', 'Stuck query ended')).toBe(true)
    expect(sameWords('Ran for 9 min.', 'Stuck query ended')).toBe(false)
    expect(sameWords(null, 'Stuck query ended')).toBe(false)
  })
})

describe('time phrases', () => {
  it('names today, yesterday and older days', () => {
    const now = new Date(2026, 8, 29, 11, 40).getTime() / 1000
    const today = new Date(2026, 8, 29, 9, 42).getTime() / 1000
    const yesterday = new Date(2026, 8, 28, 23, 5).getTime() / 1000
    const older = new Date(2026, 8, 26, 16, 10).getTime() / 1000
    expect(formatStampTime(today, now)).toBe('9:42 AM')
    expect(formatStampTime(yesterday, now)).toBe('yesterday 11:05 PM')
    expect(formatStampTime(older, now)).toBe('Sep 26, 4:10 PM')
    expect(timePhrase('at', yesterday, now)).toBe('yesterday at 11:05 PM')
    expect(timePhrase('by', older, now)).toBe('on Sep 26 by 4:10 PM')
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
    const muted = async () => {
      const [{ items }, { rules }] = await Promise.all([
        getInbox({ state: 'open', include_muted: true }),
        getMuteRules(),
      ])
      const listed = items.find((candidate) => candidate.id === item.id)
      return listed ? isMuted(listed, rules) : false
    }
    expect(await muted()).toBe(true)
    const { items } = await getInbox({ state: 'open' })
    expect(items.map((candidate) => candidate.id)).not.toContain(item.id)

    await deleteMuteRule(rule.id)
    expect(await muted()).toBe(false)
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
      'Query got slower on all databases'
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

  it('opens Actions on the database, a measured change on Applied', async () => {
    const { open, closed } = await redesignInbox()
    const ready = open.find((item) => item.kind === 'changes_ready')
    expect(itemRoute(ready as InboxItem)).toEqual({
      to: '/actions',
      search: { db: 'demo' },
    })
    const none = closed.find((item) => item.kind === 'no_change_yet')
    expect(itemRoute(none as InboxItem)).toEqual({
      to: '/actions',
      search: { db: 'demo', scope: 'applied' },
    })
    expect(
      fixRoute({
        ...(ready as InboxItem),
        action: {
          id: 4,
          title: 'Raise max_connections',
          type: 'change_setting',
        },
      })
    ).toEqual({ to: '/actions', search: { db: 'demo', action: '4' } })
  })

  it('falls back to the query row for items without a link', () => {
    const { link: _link, ...legacy } = base
    const single = { ...legacy, query_keys: ['k1'] } as InboxItem
    expect(itemLink(single)).toMatchObject({ kind: 'query', query_key: 'k1' })
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

describe('items set aside', () => {
  it('splits muted and snoozed open items and words the count', async () => {
    const { open } = await redesignInbox()
    const [stuck, fixes] = open
    const rule: MuteRule = {
      id: 9,
      target: 'demo',
      query_key: null,
      type: 'stuck_query',
      until: null,
      created_at: CA_NOW,
      note: null,
      matched_count: 0,
    }
    const aside = asideItems([stuck, { ...fixes, state: 'snoozed' }], [rule])
    expect(aside.muted.map((item) => item.id)).toEqual([stuck.id])
    expect(aside.snoozed.map((item) => item.id)).toEqual([fixes.id])
    expect(asideItems(open, []).muted).toEqual([])
  })
})

it('keeps a row meta that only restates the title off the row', () => {
  const item = inboxItems(NOW).find((row) => row.type === 'daily_summary')
  const legacy = {
    ...(item as NonNullable<typeof item>),
    title: 'Raise work_mem so sorts stay in memory',
    summary: 'Raise work_mem so sorts stay in memory.',
  }
  expect(rowMeta(legacy)).toBe('')
})

it('shows only whole sentences from a snippet that stops mid-sentence', () => {
  expect(
    wholeSentences(
      '9 queries have a fix, led by SELECT on orders. Latency spi...'
    )
  ).toBe('9 queries have a fix, led by SELECT on orders.')
  expect(wholeSentences('The nightly report is ready.')).toBe(
    'The nightly report is ready.'
  )
  expect(wholeSentences('Latency spiked at 6 PM and the queries behi...')).toBe(
    null
  )
  expect(wholeSentences(null)).toBe(null)
})
