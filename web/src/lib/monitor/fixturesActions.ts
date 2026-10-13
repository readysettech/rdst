// The global Actions page and Applied under a `ca-*` scenario, on the numbers of the nav-actions
// and actions-applied mockups: demo's five index adds (the same ids as its per-database plan),
// two changes on analytics, two dismissed changes, an undone one and four applied ones. Verbs
// move a change through the shared `CaActionStates`; a change without one keeps its own state.
// A database's own actions page agrees with the global one: demo serves its per-database plan,
// another database its changes from here.
import type { components } from '../api.generated'
import type { ActionCard, ActionsPage, IndexPlan } from './actions'
import {
  at,
  CA_KEYS,
  CA_NOW,
  type CaActionStates,
  type CaScenario,
  caFixtures,
} from './fixturesCa'

type PlanCard = components['schemas']['PlanCard']
type GlobalActionsPage = components['schemas']['GlobalActionsPage']
type AppliedPage = components['schemas']['AppliedPage']
type AppliedRow = components['schemas']['AppliedRow']
type Tier = NonNullable<PlanCard['priority']>

const MIN = 60
const DAY = 86400
const TIERS: readonly Tier[] = ['first', 'then', 'later']
const KINDS = ['index', 'setting', 'maintenance', 'rewrite'] as const

/** The scenarios whose databases carry a plan; the first run is still building one. */
const WITH_PLAN: readonly CaScenario[] = ['ca-database', 'ca-report', 'ca-home']

function card(
  fields: Partial<PlanCard> & Pick<PlanCard, 'id' | 'title'>
): PlanCard {
  return {
    key: `ca:${fields.id}`,
    database: 'demo',
    type: 'add_index',
    kind: 'index',
    state: 'open',
    priority: null,
    rank: null,
    risk: 'none',
    effort: 'copy_sql',
    saves_ms_per_min: 0,
    score: 0.05,
    summary: '',
    note: null,
    why: null,
    apply: 'run_sql',
    sql: null,
    rollback_sql: null,
    restart: false,
    confidence: 'measured',
    table: null,
    columns: [],
    index_name: null,
    setting: null,
    queries: [],
    share: 0,
    before_ms: null,
    after_ms: null,
    write_cost: null,
    steps: null,
    evidence: [],
    rows: [],
    opened_at: at(9, 51),
    updated_at: at(9, 52),
    set_aside_until: null,
    closed_at: null,
    reason: null,
    checkup_rank: null,
    ...fields,
  }
}

const DEMO_TIERS: readonly Tier[] = ['first', 'then', 'then', 'later', 'later']
const DEMO_SAVES = [14_000, 5_200, 3_700, 2_400, 1_200]

/** demo's plan: its per-database index adds, each with the queries it speeds up. */
function demoCards(scenario: CaScenario): PlanCard[] {
  const set = caFixtures(scenario)
  const page = set.actions('demo', new Map())
  const adds = set.indexPlan('demo', new Map())?.add ?? []
  return page.actions.map((action, index) => {
    const add = adds.find((candidate) => candidate.id === action.key)
    const queries = (add?.helps ?? []).map((helped) => {
      const mean = set.query('demo', helped.key)?.mean_ms ?? null
      const gain = (helped.gain_pct ?? 0) / 100
      return {
        key: helped.key,
        name: helped.name ?? helped.key,
        share: helped.share,
        mean_ms: mean,
        expected_ms:
          mean === null ? null : Number((mean * (1 - gain)).toFixed(1)),
      }
    })
    const writes = add?.write_cost?.writes_per_s ?? 0
    return card({
      id: action.id ?? index + 1,
      key: action.key,
      title: action.title,
      summary: action.summary,
      priority: DEMO_TIERS[index] ?? 'later',
      saves_ms_per_min: DEMO_SAVES[index] ?? 1_000,
      score: action.score,
      risk: writes >= 1 ? 'writes' : 'none',
      sql: action.sql,
      rollback_sql: add?.undo_sql ?? null,
      table: add?.table ?? null,
      columns: add?.columns ?? [],
      index_name:
        /INDEX CONCURRENTLY (\w+)/.exec(action.sql ?? '')?.[1] ?? null,
      queries,
      share: queries.reduce((sum, query) => sum + query.share, 0),
      // The busiest change was benchmarked on a copy of posts.
      before_ms: index === 0 ? 82 : null,
      after_ms: index === 0 ? 31 : null,
      write_cost: (add?.write_cost as PlanCard['write_cost']) ?? null,
    })
  })
}

const ANALYTICS: PlanCard[] = [
  card({
    id: 101,
    key: 'add:events(account_id,created_at)',
    database: 'analytics',
    title: 'Add an index on events (account_id, created_at)',
    summary: 'Speeds up 2 queries, 31% of database time',
    priority: 'first',
    saves_ms_per_min: 9_100,
    score: 0.31,
    table: 'events',
    columns: ['account_id', 'created_at'],
    index_name: 'idx_events_account_id_created_at',
    sql: 'CREATE INDEX CONCURRENTLY idx_events_account_id_created_at ON public.events (account_id, created_at);',
    rollback_sql:
      'DROP INDEX CONCURRENTLY public.idx_events_account_id_created_at;',
    queries: [
      {
        key: 'ca00000000a1',
        name: 'SELECT on events by account_id',
        share: 0.19,
        mean_ms: 58.2,
        expected_ms: 9.4,
      },
      {
        key: 'ca00000000a2',
        name: 'COUNT on events by account_id',
        share: 0.12,
        mean_ms: 44,
        expected_ms: 8.1,
      },
    ],
    share: 0.31,
  }),
  card({
    id: 102,
    key: 'db:vacuum_sessions',
    database: 'analytics',
    type: 'run_maintenance',
    kind: 'maintenance',
    title: 'Vacuum sessions',
    summary: 'sessions is 38% dead rows',
    note: '38% dead rows',
    priority: 'then',
    saves_ms_per_min: 2_800,
    score: 0.09,
    table: 'sessions',
    sql: 'VACUUM (ANALYZE) public.sessions;',
    queries: [
      {
        key: 'ca00000000a3',
        name: 'SELECT on sessions by token',
        share: 0.09,
        mean_ms: 6.4,
        expected_ms: null,
      },
      {
        key: 'ca00000000a4',
        name: 'UPDATE sessions by token',
        share: 0.06,
        mean_ms: 4.8,
        expected_ms: null,
      },
      {
        key: 'ca00000000a5',
        name: 'DELETE from sessions by expires_at',
        share: 0.04,
        mean_ms: 31,
        expected_ms: null,
      },
      {
        key: 'ca00000000a6',
        name: 'COUNT on sessions by account_id',
        share: 0.03,
        mean_ms: 12.2,
        expected_ms: null,
      },
    ],
    share: 0.22,
  }),
]

/** Closed before the page opened: two dismissed with reasons, one undone. */
const CLOSED: PlanCard[] = [
  card({
    id: 201,
    key: 'db:setting_work_mem_64',
    type: 'change_setting',
    kind: 'setting',
    apply: 'setting',
    effort: 'setting',
    state: 'dismissed',
    title: 'Raise work_mem to 64 MB',
    sql: "ALTER SYSTEM SET work_mem = '64MB';",
    rollback_sql: "ALTER SYSTEM SET work_mem = '4MB';",
    setting: { name: 'work_mem', current: '4MB', target: '64MB' },
    closed_at: at(16, 5, 0, -2),
    reason: 'The host caps memory at 16 GB.',
  }),
  card({
    id: 202,
    key: 'add:users(location)',
    state: 'dismissed',
    title: 'Add an index on users (location)',
    table: 'users',
    columns: ['location'],
    sql: 'CREATE INDEX CONCURRENTLY idx_users_location ON public.users (location);',
    rollback_sql: 'DROP INDEX CONCURRENTLY public.idx_users_location;',
    closed_at: at(18, 40, 0, -1),
    reason: 'One nightly report reads it, not worth a cost on every write.',
  }),
  card({
    id: 203,
    key: 'add:posts(score)',
    state: 'undone',
    title: 'Add an index on posts (score)',
    table: 'posts',
    columns: ['score'],
    sql: 'CREATE INDEX CONCURRENTLY idx_posts_score ON public.posts (score);',
    rollback_sql: 'DROP INDEX CONCURRENTLY public.idx_posts_score;',
    opened_at: at(10, 0, 0, -6),
    closed_at: at(16, 20, 0, -3),
    reason: 'It slowed the nightly import.',
  }),
]

interface AppliedSeed {
  card: PlanCard
  applied_at: number
  by: AppliedRow['by']
  status: AppliedRow['status']
  queries: AppliedRow['queries']
  saved_ms_per_min: number | null
}

const APPLIED: AppliedSeed[] = [
  {
    card: card({
      id: 301,
      key: 'add:tags(count,tagname)',
      state: 'applied',
      title: 'Add an index on tags (count, tagname)',
      rollback_sql: 'DROP INDEX CONCURRENTLY public.idx_tags_count_tagname;',
    }),
    applied_at: at(9, 15),
    by: 'user',
    status: 'measuring',
    queries: [
      {
        key: CA_KEYS.tagsByTagname,
        name: 'SELECT on tags',
        before_ms: 38,
        after_ms: null,
        calls_after: 96,
      },
    ],
    saved_ms_per_min: null,
  },
  {
    card: card({
      id: 302,
      key: 'add:comments(userid,creationdate)',
      state: 'confirmed',
      title: 'Add an index on comments (userid, creationdate)',
      rollback_sql:
        'DROP INDEX CONCURRENTLY public.idx_comments_userid_creationdate;',
    }),
    applied_at: at(8, 10),
    by: 'detected',
    status: 'verified',
    queries: [
      {
        key: CA_KEYS.commentsByUserid,
        name: 'SELECT on comments by userid',
        before_ms: 64,
        after_ms: 4.1,
        calls_after: 1_400,
      },
    ],
    saved_ms_per_min: 2_700,
  },
  {
    card: card({
      id: 303,
      key: 'add:votes(userid,creationdate)',
      state: 'confirmed',
      title: 'Add an index on votes (userid, creationdate)',
      rollback_sql:
        'DROP INDEX CONCURRENTLY public.idx_votes_userid_creationdate;',
    }),
    applied_at: at(14, 40, 0, -2),
    by: 'user',
    status: 'verified',
    queries: [
      {
        key: CA_KEYS.votesByUserid,
        name: 'SELECT on votes by userid',
        before_ms: 82,
        after_ms: 29,
        calls_after: 1_400,
      },
      {
        key: 'ca0000000017',
        name: 'COUNT on votes by userid',
        before_ms: 61,
        after_ms: 22,
        calls_after: 610,
      },
    ],
    saved_ms_per_min: 9_700,
  },
  {
    card: card({
      id: 304,
      key: 'db:analyze_posts',
      type: 'run_maintenance',
      kind: 'maintenance',
      state: 'no_effect',
      title: 'Analyze posts',
    }),
    applied_at: at(11, 30, 0, -8),
    by: 'detected',
    status: 'no_change',
    queries: [
      {
        key: CA_KEYS.postsByCreationdate,
        name: 'COUNT on posts by creationdate',
        before_ms: 76,
        after_ms: 76,
        calls_after: 5_200,
      },
    ],
    saved_ms_per_min: null,
  },
]

/** Every change a scenario knows, in its base state. */
function catalogue(scenario: CaScenario): PlanCard[] {
  if (!WITH_PLAN.includes(scenario)) return []
  return [
    ...demoCards(scenario),
    ...ANALYTICS,
    ...CLOSED,
    ...APPLIED.map((seed) => seed.card),
  ]
}

/** Whether a fixture verb may act on this id. */
export function knownFixtureAction(scenario: CaScenario, id: number): boolean {
  return catalogue(scenario).some((entry) => entry.id === id)
}

function moved(
  entry: PlanCard,
  states: CaActionStates,
  reasons: ReadonlyMap<number, string>
): PlanCard {
  const state = states.get(entry.id)
  if (!state) return entry
  return {
    ...entry,
    state: state.state,
    set_aside_until: state.state === 'snoozed' ? state.snoozed_until : null,
    closed_at: state.state === 'open' ? null : state.updated_at,
    reason: reasons.get(entry.id) ?? null,
    // A change brought back rejoins the plan at the bottom unless it had a tier.
    priority: entry.priority ?? 'later',
  }
}

const tierOrder = (entry: PlanCard) => TIERS.indexOf(entry.priority ?? 'later')

export function globalActionsFixture(
  scenario: CaScenario,
  db: string | null,
  states: CaActionStates,
  reasons: ReadonlyMap<number, string>
): GlobalActionsPage {
  const set = caFixtures(scenario)
  const home = set.home()
  const every = catalogue(scenario).map((entry) =>
    moved(entry, states, reasons)
  )
  const open = every
    .filter((entry) => entry.state === 'open')
    .sort(
      (a, b) =>
        tierOrder(a) - tierOrder(b) || b.saves_ms_per_min - a.saves_ms_per_min
    )
  const scoped = open
    .filter((entry) => db === null || entry.database === db)
    .map((entry, index) => ({ ...entry, rank: index + 1 }))
  const scopedAll = every.filter(
    (entry) => db === null || entry.database === db
  )
  const databases = home.databases.map((row) => ({
    database: row.target,
    monitored: row.state !== 'not_monitored',
    to_do: open.filter((entry) => entry.database === row.target).length,
    do_first: open.filter(
      (entry) => entry.database === row.target && entry.priority === 'first'
    ).length,
    checkup:
      row.state === 'not_monitored' ? null : set.actions(row.target).checkup,
  }))
  const planned = WITH_PLAN.includes(scenario)
  const applied = appliedFixture(scenario, db, states, reasons)
  return {
    db,
    databases,
    unmonitored: home.databases
      .filter((row) => row.state === 'not_monitored')
      .map((row) => row.target),
    updated_at: planned ? CA_NOW - 4 * MIN : null,
    observed_s: planned ? 7 * DAY : null,
    counts: {
      by_database: Object.fromEntries(
        databases.map((row) => [row.database, row.to_do])
      ),
      by_kind: Object.fromEntries(
        KINDS.map((kind) => [
          kind,
          scoped.filter((entry) => entry.kind === kind).length,
        ])
      ),
      first: scoped.filter((entry) => entry.priority === 'first').length,
      // biome-ignore lint/suspicious/noThenProperty: the API names the second tier `then`.
      then: scoped.filter((entry) => entry.priority === 'then').length,
      later: scoped.filter((entry) => entry.priority === 'later').length,
      total: scoped.length,
    },
    cards: scoped,
    set_aside: scopedAll.filter((entry) => entry.state === 'snoozed'),
    dismissed: scopedAll.filter((entry) => entry.state === 'dismissed'),
    applied_count: applied.days.reduce((sum, day) => sum + day.rows.length, 0),
    keep: planned && (db === null || db === 'demo') ? { demo: 2 } : {},
    left_out: planned && (db === null || db === 'demo') ? { demo: 2 } : {},
    report_checks:
      planned && (db === null || db === 'demo')
        ? {
            demo: {
              report_id: 1,
              checks: 8,
              needs_you: 3,
              not_in_actions: [
                {
                  check: 'shared_buffers',
                  label: 'shared_buffers',
                  finding: '128 MB, the default; 2 GB fits this host.',
                },
              ],
            },
          }
        : {},
  }
}

function appliedRow(seed: AppliedSeed, entry: PlanCard): AppliedRow {
  const before = seed.queries.map((query) => query.before_ms ?? 0)
  const after = seed.queries.map((query) => query.after_ms ?? 0)
  const mean = (values: number[]) =>
    values.reduce((sum, value) => sum + value, 0) / (values.length || 1)
  return {
    id: entry.id,
    key: entry.key,
    database: entry.database,
    type: entry.type,
    kind: entry.kind,
    title: entry.title,
    applied_at: seed.applied_at,
    by: seed.by,
    source: seed.by === 'user' ? 'user' : 'index_added',
    status: entry.state === 'undone' ? 'undone' : seed.status,
    queries: seed.queries,
    before_ms: seed.queries.length ? mean(before) : null,
    after_ms:
      seed.status === 'measuring' || !seed.queries.length ? null : mean(after),
    calls_after: seed.queries.reduce(
      (sum, query) => sum + query.calls_after,
      0
    ),
    verified_at: seed.status === 'verified' ? seed.applied_at + 2 * 3600 : null,
    next_check_at: seed.status === 'measuring' ? CA_NOW + 40 * MIN : null,
    rollback_sql: entry.rollback_sql,
    saved_ms_per_min: seed.saved_ms_per_min,
    undone_at: entry.state === 'undone' ? entry.closed_at : null,
    reason: entry.state === 'undone' ? entry.reason : null,
  }
}

/** A change marked done from the plan: applied by you just now, measuring starts. */
function didIt(entry: PlanCard): AppliedSeed {
  return {
    card: entry,
    applied_at: entry.closed_at ?? CA_NOW,
    by: 'user',
    status: 'measuring',
    queries: entry.queries.map((query) => ({
      key: query.key,
      name: query.name,
      before_ms: query.mean_ms,
      after_ms: null,
      calls_after: 0,
    })),
    saved_ms_per_min: null,
  }
}

export function appliedFixture(
  scenario: CaScenario,
  db: string | null,
  states: CaActionStates,
  reasons: ReadonlyMap<number, string>
): AppliedPage {
  const seeds = new Map(APPLIED.map((seed) => [seed.card.id, seed]))
  const every = catalogue(scenario)
    .map((entry) => moved(entry, states, reasons))
    .filter((entry) => db === null || entry.database === db)
  const rows: AppliedRow[] = []
  const undone: AppliedRow[] = []
  for (const entry of every) {
    const done = ['applied', 'confirmed', 'no_effect'].includes(entry.state)
    if (!done && entry.state !== 'undone') continue
    const seed = seeds.get(entry.id) ?? didIt(entry)
    const row = appliedRow(seed, entry)
    if (entry.state === 'undone') {
      const base = CLOSED.find((closed) => closed.id === entry.id)
      undone.push({ ...row, applied_at: base?.opened_at ?? row.applied_at })
    } else rows.push(row)
  }
  rows.sort((a, b) => b.applied_at - a.applied_at || b.id - a.id)
  const days: AppliedPage['days'] = []
  for (const row of rows) {
    const date = new Date(row.applied_at * 1000)
    const start =
      new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime() /
      1000
    const last = days[days.length - 1]
    if (last?.start === start) last.rows.push(row)
    else
      days.push({
        start,
        day: `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`,
        rows: [row],
      })
  }
  const week = rows.filter((row) => row.applied_at >= CA_NOW - 7 * DAY)
  const saved = week.reduce((sum, row) => sum + (row.saved_ms_per_min ?? 0), 0)
  return {
    db,
    since: rows.length ? Math.min(...rows.map((row) => row.applied_at)) : null,
    days,
    undone: undone.sort((a, b) => (b.undone_at ?? 0) - (a.undone_at ?? 0)),
    dismissed: every
      .filter((entry) => entry.state === 'dismissed')
      .map((entry) => ({
        id: entry.id,
        database: entry.database,
        type: entry.type,
        kind: entry.kind,
        title: entry.title,
        dismissed_at: entry.closed_at ?? CA_NOW,
        reason: entry.reason,
      }))
      .sort((a, b) => b.dismissed_at - a.dismissed_at),
    totals: {
      changes_this_week: week.length,
      verified: week.filter((row) => row.status === 'verified').length,
      measuring: week.filter((row) => row.status === 'measuring').length,
      no_change: week.filter((row) => row.status === 'no_change').length,
      saved_ms_per_min: saved,
      // demo and analytics spend about 30 s of database time a minute.
      saved_share: saved ? Math.min(1, saved / 30_000) : null,
    },
  }
}

const CATEGORY: Record<PlanCard['kind'], ActionCard['category']> = {
  index: 'indexes',
  setting: 'settings',
  maintenance: 'maintenance',
  rewrite: 'rewrites',
  caching: 'caching',
}

/** A change of the global plan as its database's own actions list carries it. */
function actionCard(entry: PlanCard, rank: number): ActionCard {
  return {
    id: entry.id,
    key: entry.key,
    type: entry.type,
    category: CATEGORY[entry.kind],
    apply: entry.apply,
    state: entry.state,
    snoozed_until: entry.set_aside_until,
    title: entry.title,
    summary: entry.summary,
    why: entry.why,
    note: entry.note,
    sql: entry.sql,
    steps: entry.steps,
    restart: entry.restart,
    confidence: entry.confidence,
    queries: entry.queries.map((query) => ({
      key: query.key,
      name: query.name,
    })),
    opened_at: entry.opened_at,
    updated_at: entry.updated_at,
    children: [],
    detail: null,
    window: null,
    evidence: [],
    rows: [],
    priority: rank,
    jev: entry.share,
    score: entry.score,
    severity:
      entry.share >= 0.15 ? 'high' : entry.share >= 0.08 ? 'medium' : 'low',
  }
}

/** demo carries the scenario's own per-database plan; the others come from the catalogue. */
const ownPlan = (scenario: CaScenario, target: string) =>
  !WITH_PLAN.includes(scenario) || !target || target === 'demo'

/** One database's actions page, the same changes the global page lists for it. */
export function databaseActionsFixture(
  scenario: CaScenario,
  target: string,
  states: CaActionStates,
  reasons: ReadonlyMap<number, string>
): ActionsPage {
  const set = caFixtures(scenario)
  if (ownPlan(scenario, target)) return set.actions(target, states)
  // ca-home-empty serves the idle page: no checkup, no changes.
  const idle = caFixtures('ca-home-empty').actions(target, new Map())
  const monitored = set
    .home()
    .databases.some(
      (row) => row.target === target && row.state !== 'not_monitored'
    )
  if (!monitored) return idle
  const every = catalogue(scenario)
    .filter((entry) => entry.database === target)
    .map((entry) => moved(entry, states, reasons))
  const open = every
    .filter((entry) => entry.state === 'open')
    .sort(
      (a, b) =>
        tierOrder(a) - tierOrder(b) || b.saves_ms_per_min - a.saves_ms_per_min
    )
    .map((entry, index) => actionCard(entry, index + 1))
  const closed = (kinds: readonly PlanCard['state'][]) =>
    every
      .filter((entry) => kinds.includes(entry.state))
      .map((entry) => actionCard(entry, 0))
  const setAside = closed(['snoozed', 'dismissed'])
  return {
    ...idle,
    computed_at: CA_NOW - 4 * MIN,
    checkup: {
      state: 'done',
      analyzed: 10,
      total: 10,
      due_by: null,
      finished_at: at(9, 51),
      report_id: null,
    },
    actions: open,
    set_aside: setAside,
    recently_done: closed(['applied', 'confirmed', 'no_effect']),
    card_count: open.length,
    change_count: open.length,
    open_count: open.length,
    snoozed: setAside.filter((card) => card.state === 'snoozed').length,
    dismissed: setAside.filter((card) => card.state === 'dismissed').length,
  }
}

/** demo's index plan; another database's index changes count from its actions page. */
export function databaseIndexPlanFixture(
  scenario: CaScenario,
  target: string,
  states: CaActionStates
): IndexPlan | null {
  return ownPlan(scenario, target)
    ? caFixtures(scenario).indexPlan(target, states)
    : null
}
