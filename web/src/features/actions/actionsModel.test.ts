import { describe, expect, it } from 'vitest'
import type { AppliedRow, PlanCard } from '@/lib/monitor/actions'
import {
  allSql,
  appliedLead,
  basis,
  caption,
  categoryGroups,
  checkLine,
  databaseGroups,
  dayLabel,
  ENGINE_NOTE,
  inView,
  keptRow,
  leadSentence,
  migration,
  pageMeta,
  paneMeta,
  pct,
  planName,
  planOrder,
  queryChange,
  saves,
  speedup,
  stepList,
  tierGroups,
  whatIf,
  whatItDoes,
  writesNote,
} from './actionsModel'

function card(fields: Partial<PlanCard> & Pick<PlanCard, 'id'>): PlanCard {
  return {
    key: `k${fields.id}`,
    database: 'demo',
    type: 'add_index',
    kind: 'index',
    state: 'open',
    priority: 'then',
    rank: fields.id,
    risk: 'none',
    effort: 'copy_sql',
    saves_ms_per_min: 1000,
    score: 0.1,
    title: `Change ${fields.id}`,
    summary: '',
    note: null,
    why: null,
    apply: 'run_sql',
    sql: `CREATE INDEX CONCURRENTLY idx_${fields.id} ON t (c);`,
    rollback_sql: `DROP INDEX CONCURRENTLY public.idx_${fields.id};`,
    restart: false,
    confidence: null,
    table: 't',
    columns: ['c'],
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
    opened_at: 0,
    updated_at: 0,
    set_aside_until: null,
    closed_at: null,
    reason: null,
    checkup_rank: null,
    ...fields,
  }
}

const posts = card({
  id: 1,
  priority: 'first',
  title: 'Add an index on posts (creationdate, owneruserid, score)',
  table: 'posts',
  columns: ['creationdate', 'owneruserid', 'score'],
  saves_ms_per_min: 14_000,
  share: 0.46,
  queries: [
    {
      key: 'a',
      name: 'SELECT on posts and users',
      share: 0.19,
      mean_ms: 82,
      expected_ms: 31,
    },
    {
      key: 'b',
      name: 'SELECT on posts by owneruserid',
      share: 0.17,
      mean_ms: 71,
      expected_ms: 20,
    },
    {
      key: 'c',
      name: 'COUNT on posts by creationdate',
      share: 0.1,
      mean_ms: 76,
      expected_ms: 25,
    },
  ],
})
const events = card({
  id: 2,
  priority: 'first',
  database: 'analytics',
  rank: 2,
})
const vacuum = card({
  id: 3,
  kind: 'maintenance',
  type: 'run_maintenance',
  database: 'analytics',
  title: 'Vacuum sessions',
  table: 'sessions',
  columns: [],
  note: '38% dead rows',
  rank: 3,
})
const badges = card({ id: 4, priority: 'later', rank: 4 })

describe('the plan', () => {
  it('ranks the view 1 to N in tier order, and a kind keeps its place in the plan', () => {
    const all = inView([badges, vacuum, events, posts], null)
    expect(all.map((entry) => [entry.id, entry.rank])).toEqual([
      [1, 1],
      [2, 2],
      [3, 3],
      [4, 4],
    ])
    expect(
      inView(all, 'maintenance').map((entry) => [entry.id, entry.rank])
    ).toEqual([[3, 3]])
  })

  it('groups Do first, Then and Later with their quiet meta, and drops empty tiers', () => {
    const groups = tierGroups(inView([posts, events, vacuum, badges], null))
    expect(groups.map((group) => [group.title, group.meta])).toEqual([
      ['Do first', '2, the most database time per change'],
      ['Then', '1'],
      ['Later', '1, small wins that still cost every write'],
    ])
    expect(tierGroups([vacuum]).map((group) => group.title)).toEqual(['To do'])
  })

  it('groups by category in a fixed order, each with its count and one line', () => {
    const drop = card({
      id: 5,
      type: 'drop_index',
      index_name: 'idx_orders_status',
      rank: 5,
    })
    const rewrite = card({
      id: 6,
      kind: 'rewrite',
      type: 'rewrite_query',
      title: 'Rewrite COUNT on customers by region',
      priority: 'later',
      rank: 6,
    })
    const groups = categoryGroups(
      inView([badges, rewrite, drop, vacuum, events, posts], null),
      10,
      11
    )
    expect(
      groups.map((group) => [
        group.title,
        group.meta,
        group.cards.map((entry) => entry.rank),
      ])
    ).toEqual([
      ['Indexes', '3 to add, 1 to drop, 10 to keep', [1, 2, 4, 5]],
      ['Server settings', 'Nothing to change, next check in 11 min', []],
      ['Maintenance', '1 to run, no downtime', [3]],
      ['Query rewrites', '1 to rewrite, needs a code change', [6]],
    ])
    const setting = card({ id: 7, kind: 'setting', restart: true })
    const locks = { ...vacuum, risk: 'locks' as const }
    const caching = card({ id: 8, kind: 'caching', type: 'try_caching' })
    expect(
      categoryGroups([setting, locks, caching], 0, null).map(
        (group) => group.meta
      )
    ).toEqual([
      'Nothing to change',
      '1 to set, needs a restart',
      '1 to run',
      'Nothing to change',
      '1 to cache',
    ])
  })

  it('names each change by its verb and what it acts on', () => {
    expect(planName(posts)).toEqual({
      verb: 'Add',
      tone: 'positive',
      name: 'posts',
      detail: '(creationdate, owneruserid, score)',
    })
    expect(
      planName(
        card({
          id: 9,
          type: 'drop_index',
          title: 'Drop the unused index idx_orders_status',
          index_name: 'idx_orders_status',
          table: 'orders',
        })
      )
    ).toEqual({
      verb: 'Drop',
      tone: 'negative',
      name: 'idx_orders_status',
      detail: 'on orders',
    })
    expect(
      planName(
        card({
          id: 9,
          kind: 'setting',
          type: 'change_setting',
          title: 'Raise work_mem to 64 MB',
          setting: { name: 'work_mem' },
        })
      )
    ).toEqual({ verb: 'Set', tone: 'neutral', name: 'work_mem' })
    expect(planName(vacuum)).toEqual({
      verb: 'Vacuum',
      tone: 'neutral',
      name: 'sessions',
    })
    expect(
      planName(
        card({
          id: 9,
          kind: 'rewrite',
          type: 'rewrite_query',
          title: 'Rewrite COUNT on customers by region',
        })
      )
    ).toMatchObject({ verb: 'Rewrite', name: 'COUNT on customers by region' })
    // Without the fields, the title loses its verb.
    expect(
      planName(
        card({
          id: 9,
          title: 'Add an index on posts(score)',
          table: null,
          columns: [],
        })
      )
    ).toMatchObject({ verb: 'Add', name: 'posts(score)' })
    expect(
      planName(
        card({
          id: 9,
          kind: 'maintenance',
          type: 'run_maintenance',
          title: 'Long-open transactions',
        })
      )
    ).toMatchObject({ verb: 'Run', name: 'Long-open transactions' })
  })

  it('reads a kept index by its shape and its reads', () => {
    const kept = {
      table: 'posts',
      index_name: 'posts_pkey',
      columns: ['id'],
      primary: true,
      unique: true,
      scans: 10,
      window_scans: 412_000,
      size_bytes: 0,
      database: 'demo',
      observed_s: 7 * 86400,
    }
    expect(keptRow(kept, false)).toEqual({
      sub: 'demo, primary key',
      what: { text: 'Read 412,000 times', sub: 'in the last 7 days' },
    })
    expect(
      keptRow(
        { ...kept, primary: false, window_scans: 0, observed_s: 60 },
        true
      )
    ).toEqual({
      sub: 'Index, demo, unique, enforces a key',
      what: { text: 'Not read', sub: 'in the last minute' },
    })
  })

  it('groups the same rows by database, in the order of their best change', () => {
    const groups = databaseGroups(inView([posts, events, vacuum, badges], null))
    expect(
      groups.map((group) => [
        group.database,
        group.meta,
        group.cards.map((entry) => entry.id),
      ])
    ).toEqual([
      ['demo', '2 to do, 1 first', [1, 4]],
      ['analytics', '2 to do, 1 first', [2, 3]],
    ])
  })

  it('leads with the first change', () => {
    const text = (cards: PlanCard[]) =>
      leadSentence(cards)
        .map((part) => part.text)
        .join('')
    expect(text([posts])).toBe(
      "Start with the index on posts: one change speeds up 3 queries that take 46% of demo's database time."
    )
    expect(
      leadSentence([posts])
        .filter((part) => part.strong)
        .map((part) => part.text)
    ).toEqual(['posts', "46% of demo's database time"])
    // The change's name is the run of parts that opens its row.
    expect(
      leadSentence([posts])
        .filter((part) => part.link)
        .map((part) => part.text)
        .join('')
    ).toBe('the index on posts')
    expect(
      text([{ ...vacuum, queries: [posts.queries[0]], share: 0.19 }])
    ).toBe(
      "Start with the maintenance on sessions: one change steadies 1 query that takes 19% of analytics' database time."
    )
    expect(text([])).toBe('')
  })

  it('captions a change by kind, database and shape', () => {
    expect(caption(posts)).toBe('Index, demo, composite, 3 columns')
    expect(caption(posts, false)).toBe('demo, composite, 3 columns')
    expect(caption(card({ id: 9 }))).toBe('Index, demo, single column')
    expect(caption(vacuum)).toBe('Maintenance, analytics, 38% dead rows')
    expect(
      caption(card({ id: 9, kind: 'setting', columns: [], restart: true }))
    ).toBe('Setting, demo, restart needed')
    expect(whatItDoes(posts)).toEqual({
      text: 'Speeds up 3 queries',
      sub: "46% of demo's time",
    })
  })

  it('reads time saved a minute and the page meta', () => {
    expect(pct(0.0999)).toBe('10%')
    expect(pct(0.049)).toBe('4.9%')
    expect(saves(14_000)).toBe('14 s')
    expect(saves(9_100)).toBe('9.1 s')
    expect(saves(420)).toBe('420 ms')
    expect(pageMeta(7, 7, 2, false)).toBe('7 to do across 2 databases')
    expect(pageMeta(4, 4, 1, false)).toBe('4 to do')
    expect(pageMeta(5, 7, 2, true)).toBe('5 of 7 to do')
    expect(pageMeta(0, 0, 0, false)).toBe('nothing to do')
    expect(basis(7 * 86400, 1000 - 240, 1000)).toBe(
      'Based on 7 days of traffic, updated 4 min ago.'
    )
  })

  it('shows what if from the measurement, else from the plan', () => {
    expect(whatIf(posts)).toMatchObject({
      title: 'What if, the slowest of the 3',
      today: 82,
      after: 31,
    })
    expect(whatIf({ ...posts, before_ms: 90, after_ms: 30 })).toMatchObject({
      title: 'What if, measured',
      today: 90,
      after: 30,
      query: 'Its 3 queries, on average',
    })
    expect(whatIf(badges)).toBeNull()
    expect(speedup(82, 31)).toBe('2.6x faster')
  })

  it('copies the index and maintenance changes in view as one script', () => {
    const rewrite = card({
      id: 5,
      kind: 'rewrite',
      type: 'rewrite_query',
      sql: 'SELECT 1',
      rollback_sql: null,
    })
    expect(allSql(inView([posts, badges, rewrite], null))).toBe(
      [
        `-- ${ENGINE_NOTE.postgresql}`,
        `-- 1. ${posts.title}\n${posts.sql}`,
        `-- 3. Change 4\n${badges.sql}`,
        `-- To undo these changes, run these:\n-- 1. ${posts.title}\n-- ${posts.rollback_sql}\n-- 3. Change 4\n-- ${badges.rollback_sql}`,
      ].join('\n\n')
    )
    expect(allSql([rewrite])).toBe('')
  })

  it("says MySQL can't roll index changes back in a transaction", () => {
    const mysql = card({
      id: 6,
      sql: 'ALTER TABLE `posts` ADD INDEX idx_6 (score), ALGORITHM=INPLACE;',
      rollback_sql: 'ALTER TABLE `posts` DROP INDEX idx_6;',
    })
    const script = allSql([mysql])
    expect(script.startsWith(`-- ${ENGINE_NOTE.mysql}`)).toBe(true)
    expect(script).not.toContain('CONCURRENTLY keeps writes')
    expect(script).toContain('-- ALTER TABLE `posts` DROP INDEX idx_6;')
  })

  it('splits steps one per line', () => {
    expect(stepList('Run it\n\n  Then check ')).toEqual([
      'Run it',
      'Then check',
    ])
    expect(stepList(null)).toEqual([])
  })
})

describe('migrations', () => {
  const change = {
    title: 'Add an index on posts (score)',
    sql: 'CREATE INDEX CONCURRENTLY idx_posts_score ON public.posts (score);',
    rollback_sql: 'DROP INDEX CONCURRENTLY public.idx_posts_score;',
  }
  const at = new Date(2026, 9, 8, 9, 56, 30)

  it('writes an Alembic revision that runs outside the transaction', () => {
    const text = migration('alembic', change, at)
    expect(text).toContain('revision = "20261008095630"')
    expect(text).toContain(
      'def upgrade() -> None:\n    with op.get_context().autocommit_block():'
    )
    expect(text).toContain(`            ${change.sql}`)
    expect(text).toContain(
      `def downgrade() -> None:\n    with op.get_context().autocommit_block():\n        op.execute(\n            """\n            ${change.rollback_sql}`
    )
  })

  it('writes a Rails migration with up and down', () => {
    const text = migration('rails', change, at)
    expect(text.split('\n').slice(0, 4)).toEqual([
      '# db/migrate/20261008095630_add_an_index_on_posts_score.rb',
      'class AddAnIndexOnPostsScore < ActiveRecord::Migration[7.1]',
      '  disable_ddl_transaction!',
      '',
    ])
    expect(text).toContain(
      `  def down\n    execute <<~SQL\n      ${change.rollback_sql}\n    SQL`
    )
  })

  it('writes a Prisma migration.sql with the rollback as a comment', () => {
    expect(migration('prisma', change, at)).toBe(
      `-- prisma/migrations/20261008095630_add_an_index_on_posts_score/migration.sql\n-- ${change.title}\n${change.sql}\n\n-- To roll back, run:\n-- ${change.rollback_sql}\n`
    )
  })

  it('writes a Flyway versioned and undo script', () => {
    const text = migration('flyway', change, at)
    expect(text).toContain(
      '-- V20261008095630__add_an_index_on_posts_score.sql'
    )
    expect(text).toContain('executeInTransaction=false')
    expect(text).toContain(
      `-- U20261008095630__add_an_index_on_posts_score.sql\n${change.rollback_sql}`
    )
  })

  it('says so where a change has no rollback, and keeps transactions for plain DDL', () => {
    const plain = { title: 'Rewrite', sql: 'SELECT 1;', rollback_sql: null }
    expect(migration('alembic', plain, at)).toContain(
      'raise NotImplementedError'
    )
    expect(migration('alembic', plain, at)).not.toContain('autocommit_block')
    expect(migration('rails', plain, at)).toContain(
      'raise ActiveRecord::IrreversibleMigration'
    )
    expect(migration('rails', plain, at)).not.toContain(
      'disable_ddl_transaction!'
    )
    expect(migration('flyway', plain, at)).not.toContain('-- U')
  })
})

describe('applied', () => {
  const row = (fields: Partial<AppliedRow>): AppliedRow => ({
    id: 1,
    key: 'k',
    database: 'demo',
    type: 'add_index',
    kind: 'index',
    title: 'Add an index on tags (count, tagname)',
    applied_at: 0,
    by: 'user',
    source: 'user',
    status: 'measuring',
    queries: [],
    before_ms: null,
    after_ms: null,
    calls_after: 96,
    verified_at: null,
    next_check_at: null,
    rollback_sql: null,
    saved_ms_per_min: null,
    undone_at: null,
    reason: null,
    ...fields,
  })

  it('leads with the week', () => {
    expect(
      appliedLead({
        changes_this_week: 3,
        verified: 2,
        measuring: 1,
        no_change: 0,
        saved_ms_per_min: 12_400,
        saved_share: 0.41,
      })
    ).toEqual({
      lead: 'This week: 3 changes applied, 41% of database time saved.',
      rest: '2 verified, 1 measuring.',
    })
  })

  it('names the day, then its date', () => {
    const now = new Date(2026, 9, 8, 9, 56).getTime() / 1000
    const day = (offset: number) =>
      new Date(2026, 9, 8 + offset).getTime() / 1000
    expect(dayLabel(day(0), now)).toEqual({
      label: 'Today',
      date: 'Thursday, October 8',
    })
    expect(dayLabel(day(-1), now).label).toBe('Yesterday')
    expect(dayLabel(day(-2), now)).toEqual({
      label: 'Tuesday',
      date: 'October 6',
    })
    expect(dayLabel(day(-8), now)).toEqual({
      label: 'Last week',
      date: 'Wednesday, September 30',
    })
    expect(dayLabel(day(-20), now).label).toBe('Earlier')
  })

  it('reads each query before and after, and how far the check has got', () => {
    const query = {
      key: 'q',
      name: 'SELECT on tags',
      before_ms: 38,
      after_ms: null,
      calls_after: 96,
    }
    expect(queryChange(query, 'measuring')).toBe('38 ms before, measuring')
    expect(
      queryChange({ ...query, before_ms: 64, after_ms: 4.1 }, 'verified')
    ).toBe('64 ms to 4.1 ms')
    expect(queryChange({ ...query, after_ms: 76 }, 'no_change')).toBe(
      '76 ms, unchanged'
    )
    expect(checkLine(row({ next_check_at: 1000 + 40 * 60 }), 1000)).toBe(
      '96 calls so far, next check in 40 min'
    )
    expect(
      checkLine(row({ status: 'verified', calls_after: 1400 }), 1000)
    ).toBe('verified over 1,400 calls')
  })
})

describe('the opened change', () => {
  it('walks the rows in the order the plan lists them', () => {
    const cards = inView(
      [
        card({ id: 1, priority: 'first', database: 'analytics' }),
        card({ id: 2, priority: 'then', database: 'demo' }),
        card({ id: 3, priority: 'then', database: 'analytics' }),
      ],
      null
    )
    expect(planOrder(cards, 'priority').map((row) => row.id)).toEqual([1, 2, 3])
    expect(
      planOrder(
        [cards[0], { ...cards[1], kind: 'maintenance' as const }, cards[2]],
        'category'
      ).map((row) => row.id)
    ).toEqual([1, 3, 2])
    expect(planOrder(cards, 'database').map((row) => row.id)).toEqual([1, 3, 2])
  })

  it('heads the pane with the tier, the kind and the database', () => {
    expect(paneMeta(card({ id: 1, priority: 'first' }))).toEqual([
      'Do first',
      'Index',
      'demo',
    ])
    expect(
      paneMeta(card({ id: 2, priority: null, kind: 'setting', database: 'x' }))
    ).toEqual(['Later', 'Setting', 'x'])
  })

  it('says when an index builds or drops without blocking writes', () => {
    expect(writesNote('CREATE INDEX CONCURRENTLY idx ON t (c);')).toBe(
      'Builds without blocking writes.'
    )
    expect(writesNote('create unique index concurrently idx on t (c)')).toBe(
      'Builds without blocking writes.'
    )
    expect(writesNote('DROP INDEX CONCURRENTLY public.idx;')).toBe(
      'Drops it without blocking writes.'
    )
    expect(writesNote('CREATE INDEX idx ON t (c);')).toBeNull()
    expect(writesNote("ALTER SYSTEM SET work_mem = '16MB';")).toBeNull()
  })
})
