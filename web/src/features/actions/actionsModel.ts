// The words and groupings of the global Actions page: tiers, captions, the lead sentence,
// Applied's day groups, and the migration files built from a change's SQL and rollback SQL.
import type {
  ActionKind,
  AppliedPage,
  AppliedRow,
  KeptIndex,
  PlanCard,
  PlanTier,
} from '@/lib/monitor/actions'

export type Scope = 'plan' | 'applied'
export type Group = 'category' | 'priority' | 'database'

export const KIND_LABEL: Record<ActionKind, string> = {
  index: 'Index',
  setting: 'Setting',
  maintenance: 'Maintenance',
  rewrite: 'Rewrite',
  caching: 'Caching',
}

export const GROUP_OPTIONS: readonly { value: Group; label: string }[] = [
  { value: 'category', label: 'Category' },
  { value: 'priority', label: 'Priority' },
  { value: 'database', label: 'Database' },
]

/** The categories in order; caching shows only when it has changes. */
const CATEGORIES: readonly { kind: ActionKind; title: string }[] = [
  { kind: 'index', title: 'Indexes' },
  { kind: 'setting', title: 'Server settings' },
  { kind: 'maintenance', title: 'Maintenance' },
  { kind: 'rewrite', title: 'Query rewrites' },
  { kind: 'caching', title: 'Caching' },
]

/** The tiers in order, each with its title and the note beside its count. */
const TIERS: readonly { tier: PlanTier; title: string; note?: string }[] = [
  {
    tier: 'first',
    title: 'Do first',
    note: 'the most database time per change',
  },
  { tier: 'then', title: 'Then' },
  {
    tier: 'later',
    title: 'Later',
    note: 'small wins that still cost every write',
  },
]

const tierIndex = (tier: PlanTier | null) =>
  TIERS.findIndex((entry) => entry.tier === (tier ?? 'later'))

export function asKind(value: string | null | undefined): ActionKind | null {
  return value && value in KIND_LABEL ? (value as ActionKind) : null
}

export function asGroup(value: string | null | undefined): Group | null {
  return GROUP_OPTIONS.some((option) => option.value === value)
    ? (value as Group)
    : null
}

/** A share of database time: two digits above 10%, one decimal under. */
export function pct(share: number): string {
  const value = Math.round(share * 1000) / 10
  return value >= 10 ? `${Math.round(value)}%` : `${value.toFixed(1)}%`
}

/** Milliseconds as read on a row: 82 ms, 4.1 ms, 0.42 ms. */
export function ms(value: number): string {
  if (value >= 10) return `${Math.round(value)} ms`
  if (value >= 1) return `${value.toFixed(1)} ms`
  return `${value.toFixed(2)} ms`
}

/** Database time saved a minute: 14 s, 9.1 s, 420 ms. */
export function saves(msPerMin: number): string {
  if (msPerMin >= 10_000) return `${Math.round(msPerMin / 1000)} s`
  if (msPerMin >= 1_000) return `${(msPerMin / 1000).toFixed(1)} s`
  return `${Math.round(msPerMin)} ms`
}

/** "demo's", "analytics'". */
export function possessive(name: string): string {
  return name.endsWith('s') ? `${name}'` : `${name}'s`
}

const plural = (count: number, one: string, many: string) =>
  `${count} ${count === 1 ? one : many}`

/** The cards in view, one kind or all, each numbered by its place in the whole plan. */
export function inView(cards: PlanCard[], kind: ActionKind | null): PlanCard[] {
  return [...cards]
    .sort(
      (a, b) =>
        tierIndex(a.priority) - tierIndex(b.priority) ||
        (a.rank ?? 0) - (b.rank ?? 0)
    )
    .map((card, index) => ({ ...card, rank: index + 1 }))
    .filter((card) => kind === null || card.kind === kind)
}

export interface TierGroup {
  tier: PlanTier
  title: string
  meta: string
  cards: PlanCard[]
}

/** Do first, Then and Later, each with its quiet count; empty tiers drop out. */
export function tierGroups(cards: PlanCard[]): TierGroup[] {
  const first = cards.some((card) => card.priority === 'first')
  return TIERS.map(({ tier, title, note }) => {
    const members = cards.filter((card) => (card.priority ?? 'later') === tier)
    return {
      tier,
      // "Then" follows Do first; without it the tier leads.
      title: tier === 'then' && !first ? 'To do' : title,
      meta: note ? `${members.length}, ${note}` : String(members.length),
      cards: members,
    }
  }).filter((group) => group.cards.length > 0)
}

export interface DatabaseGroup {
  database: string
  meta: string
  cards: PlanCard[]
}

/** The same rows under their database, databases in the order of their best change. */
export function databaseGroups(cards: PlanCard[]): DatabaseGroup[] {
  const groups = new Map<string, PlanCard[]>()
  for (const card of cards)
    groups.set(card.database, [...(groups.get(card.database) ?? []), card])
  return [...groups].map(([database, members]) => {
    const first = members.filter((card) => card.priority === 'first').length
    return {
      database,
      meta: first
        ? `${members.length} to do, ${first} first`
        : `${members.length} to do`,
      cards: members,
    }
  })
}

export interface CategoryGroup {
  kind: ActionKind
  title: string
  meta: string
  cards: PlanCard[]
}

/**
 * Indexes, Server settings, Maintenance and Query rewrites, in rank order inside each, with
 * the one line under each title. An empty category stays as its line. `keep` counts the indexes
 * kept; `nextCheckMin` is the minutes to the next check, when known.
 */
export function categoryGroups(
  cards: PlanCard[],
  keep: number,
  nextCheckMin: number | null
): CategoryGroup[] {
  return CATEGORIES.map(({ kind, title }) => {
    const members = cards.filter((card) => card.kind === kind)
    return {
      kind,
      title,
      meta: categoryMeta(
        kind,
        members,
        kind === 'index' ? keep : 0,
        nextCheckMin
      ),
      cards: members,
    }
  }).filter((group) => group.cards.length > 0 || group.kind !== 'caching')
}

function categoryMeta(
  kind: ActionKind,
  cards: PlanCard[],
  keep: number,
  nextCheckMin: number | null
): string {
  const kept = keep ? `${keep} to keep` : null
  const count = cards.length
  const ofType = (type: PlanCard['type']) =>
    cards.filter((card) => card.type === type).length
  const parts = !count
    ? [
        'Nothing to change',
        kept,
        nextCheckMin != null ? `next check in ${nextCheckMin} min` : null,
      ]
    : kind === 'index'
      ? [
          ofType('add_index') ? `${ofType('add_index')} to add` : null,
          ofType('drop_index') ? `${ofType('drop_index')} to drop` : null,
          kept,
        ]
      : kind === 'setting'
        ? [
            `${count} to set`,
            cards.some((card) => card.restart)
              ? 'needs a restart'
              : 'no restart',
          ]
        : kind === 'maintenance'
          ? [
              `${count} to run`,
              cards.every((card) => card.risk === 'none')
                ? 'no downtime'
                : null,
            ]
          : kind === 'rewrite'
            ? [`${count} to rewrite`, 'needs a code change']
            : [`${count} to cache`]
  return parts.filter(Boolean).join(', ')
}

/** The rows in the order the plan lists them, which up and down walk. */
export function planOrder(cards: PlanCard[], group: Group): PlanCard[] {
  return (
    group === 'database'
      ? databaseGroups(cards)
      : group === 'priority'
        ? tierGroups(cards)
        : categoryGroups(cards, 0, null)
  ).flatMap((entry) => entry.cards)
}

export type VerbTone = 'positive' | 'negative' | 'neutral'

/** A change as its row names it: the verb, then what it acts on and a quieter detail. */
export interface PlanName {
  verb: string
  tone: VerbTone
  name: string
  detail?: string
}

/** Verbs a maintenance, rewrite or caching title opens with. */
const TITLE_VERBS = new Set([
  'Vacuum',
  'Analyze',
  'Reindex',
  'Cluster',
  'Rewrite',
  'Cache',
  'Run',
  'Drop',
  'End',
  'Close',
])
const KIND_VERB: Record<ActionKind, string> = {
  index: 'Add',
  setting: 'Set',
  maintenance: 'Run',
  rewrite: 'Rewrite',
  caching: 'Cache',
}

export function planName(card: PlanCard): PlanName {
  const [first, ...rest] = card.title.split(' ')
  const titled = TITLE_VERBS.has(first) && rest.length > 0
  const verb =
    card.type === 'add_index'
      ? 'Add'
      : card.type === 'drop_index'
        ? 'Drop'
        : card.kind === 'setting'
          ? 'Set'
          : titled
            ? first
            : KIND_VERB[card.kind]
  const tone: VerbTone =
    verb === 'Add' ? 'positive' : verb === 'Drop' ? 'negative' : 'neutral'
  const setting =
    typeof card.setting?.name === 'string' ? card.setting.name : null
  if (card.type === 'add_index' && card.table)
    return {
      verb,
      tone,
      name: card.table,
      detail: card.columns.length ? `(${card.columns.join(', ')})` : undefined,
    }
  if (card.type === 'drop_index' && card.index_name)
    return {
      verb,
      tone,
      name: card.index_name,
      detail: card.table ? `on ${card.table}` : undefined,
    }
  if (card.kind === 'setting' && setting) return { verb, tone, name: setting }
  const name = titled
    ? rest.join(' ')
    : card.title.replace(
        /^(add (an )?index (on )?|index |drop (the )?((unused|duplicate) )?index |(raise|lower|set|change) )/i,
        ''
      )
  return { verb, tone, name }
}

/** The opened change's header: its tier, its kind and its database. */
export function paneMeta(card: PlanCard): string[] {
  const tier = TIERS[tierIndex(card.priority)].title
  return [tier, KIND_LABEL[card.kind], card.database]
}

/** What an index statement does to writes while it runs, when it builds or drops concurrently. */
export function writesNote(sql: string): string | null {
  if (/\bcreate\s+(unique\s+)?index\s+concurrently\b/i.test(sql))
    return 'Builds without blocking writes.'
  if (/\bdrop\s+index\s+concurrently\b/i.test(sql))
    return 'Drops it without blocking writes.'
  return null
}

/** Under a change's title: its kind, unless its group names it, its database, then its shape. */
export function caption(card: PlanCard, withKind = true): string {
  const parts = withKind
    ? [KIND_LABEL[card.kind], card.database]
    : [card.database]
  if (card.kind === 'index' && card.columns.length)
    parts.push(
      card.columns.length === 1
        ? 'single column'
        : `composite, ${card.columns.length} columns`
    )
  else if (card.restart) parts.push('restart needed')
  else if (card.note) parts.push(card.note)
  return parts.join(', ')
}

/** What a change does, and the share of its database's time behind that. */
export function whatItDoes(card: PlanCard): { text: string; sub: string } {
  const count = card.queries.length
  const verb = card.kind === 'maintenance' ? 'Steadies' : 'Speeds up'
  const text = count
    ? `${verb} ${plural(count, 'query', 'queries')}`
    : card.kind === 'setting'
      ? 'Every query'
      : card.summary
  const sub = card.share
    ? `${pct(card.share)} of ${possessive(card.database)} time`
    : ''
  return { text, sub }
}

/** A kept index as its row reads it. */
export function keptRow(
  index: KeptIndex,
  withKind: boolean
): { sub: string; what: { text: string; sub: string } } {
  const shape = index.primary
    ? 'primary key'
    : index.unique
      ? 'unique, enforces a key'
      : index.columns.length > 1
        ? `composite, ${index.columns.length} columns`
        : 'single column'
  return {
    sub: [withKind ? 'Index' : null, index.database, shape]
      .filter(Boolean)
      .join(', '),
    what: {
      text: index.window_scans
        ? `Read ${index.window_scans.toLocaleString('en-US')} times`
        : 'Not read',
      sub: `in the last ${span(index.observed_s).replace(/^a /, '')}`,
    },
  }
}

/** What the first change is about, as the lead sentence names it. */
function subject(card: PlanCard): string {
  const setting =
    typeof card.setting?.name === 'string' ? card.setting.name : null
  if (card.kind === 'index' && card.table) return `the index on ${card.table}`
  if (card.kind === 'setting' && setting) return setting
  if (card.kind === 'maintenance' && card.table)
    return `the maintenance on ${card.table}`
  return card.title.charAt(0).toLowerCase() + card.title.slice(1)
}

export interface LeadPart {
  text: string
  strong?: boolean
  /** Part of the change's name, which opens its row. */
  link?: boolean
}

/**
 * The sentence over the plan, from its first change: "Start with the index on posts: one
 * change speeds up 3 queries that take 46% of demo's database time."
 */
export function leadSentence(cards: PlanCard[]): LeadPart[] {
  const [first] = cards
  if (!first) return []
  const name = subject(first)
  const bold = first.table ?? (first.kind === 'setting' ? name : null)
  const opening: LeadPart[] =
    bold && name.endsWith(bold)
      ? [
          { text: 'Start with ' },
          { text: name.slice(0, name.length - bold.length), link: true },
          { text: bold, strong: true, link: true },
        ].filter((part) => part.text)
      : [{ text: 'Start with ' }, { text: name, link: true }]
  if (!first.queries.length || !first.share) return [...opening, { text: '.' }]
  const verb = first.kind === 'maintenance' ? 'steadies' : 'speeds up'
  return [
    ...opening,
    {
      text: `: one change ${verb} ${plural(first.queries.length, 'query', 'queries')} that ${first.queries.length === 1 ? 'takes' : 'take'} `,
    },
    {
      text: `${pct(first.share)} of ${possessive(first.database)} database time`,
      strong: true,
    },
    { text: '.' },
  ]
}

function span(seconds: number): string {
  if (seconds >= 2 * 86400) return `${Math.round(seconds / 86400)} days`
  if (seconds >= 2 * 3600) return `${Math.round(seconds / 3600)} hours`
  if (seconds >= 120) return `${Math.round(seconds / 60)} minutes`
  return 'a minute'
}

export function ago(at: number, now: number): string {
  const seconds = Math.max(0, now - at)
  if (seconds < 90) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 2 * 86400) return `${Math.floor(seconds / 3600)} h ago`
  return `${Math.floor(seconds / 86400)} days ago`
}

/** "Based on 7 days of traffic, updated 4 min ago." */
export function basis(
  observedS: number | null,
  updatedAt: number | null,
  now: number
): string {
  const parts = [
    observedS ? `Based on ${span(observedS)} of traffic` : null,
    updatedAt ? `updated ${ago(updatedAt, now)}` : null,
  ].filter(Boolean) as string[]
  if (!parts.length) return ''
  const text = parts.join(', ')
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`
}

/** "7 to do", "7 to do across 2 databases", or "5 of 7 to do" once narrowed. */
export function pageMeta(
  inViewCount: number,
  total: number,
  databases: number,
  narrowed: boolean
): string {
  if (narrowed) return `${inViewCount} of ${total} to do`
  if (total === 0) return 'nothing to do'
  if (databases <= 1) return `${total} to do`
  return `${total} to do across ${plural(databases, 'database', 'databases')}`
}

/** How each engine runs the script's index changes, said once at its top. */
export const ENGINE_NOTE = {
  postgresql:
    "CONCURRENTLY keeps writes flowing while each index builds, and PostgreSQL won't run it inside a transaction. Run the statements one at a time. If you need to back out, run the undo lines at the bottom.",
  mysql:
    "MySQL commits each index change on its own, so a transaction can't roll them back. InnoDB builds them online. If you need to back out, run the undo lines at the bottom.",
} as const

const MYSQL_SQL =
  /`|\badd\s+(unique\s+)?index\b|\boptimize\s+table\b|\balgorithm\s*=/i

const commented = (text: string) =>
  text
    .split('\n')
    .map((line) => `-- ${line}`)
    .join('\n')

/** Steps arrive one per line; a single line is a sentence, several are a numbered list. */
export function stepList(steps: string | null | undefined): string[] {
  return (steps ?? '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
}

/**
 * One script for the index and maintenance changes in view, each under its title: the
 * engine's note on top, then the changes, then the commented-out lines that undo them.
 */
export function allSql(cards: PlanCard[]): string {
  const picked = cards.filter(
    (card) => card.sql && (card.kind === 'index' || card.kind === 'maintenance')
  )
  if (!picked.length) return ''
  const label = (card: PlanCard) => `-- ${card.rank ?? ''}. ${card.title}`
  const engines = new Set(
    picked.map((card) =>
      MYSQL_SQL.test(card.sql ?? '') ? 'mysql' : 'postgresql'
    )
  )
  const notes = (['postgresql', 'mysql'] as const)
    .filter((engine) => engines.has(engine))
    .map((engine) => commented(ENGINE_NOTE[engine]))
  const changes = picked.map((card) => `${label(card)}\n${card.sql}`)
  const undo = picked
    .filter((card) => card.rollback_sql)
    .map((card) => `${label(card)}\n${commented(card.rollback_sql ?? '')}`)
  return [
    ...notes,
    ...changes,
    ...(undo.length
      ? [`-- To undo these changes, run these:\n${undo.join('\n')}`]
      : []),
  ].join('\n\n')
}

export interface WhatIf {
  title: string
  today: number
  after: number
  query: string
  note: string
}

/**
 * Today against with the change: measured across its queries when the change carries before
 * and after, else the plan's own estimate for the slowest query it touches.
 */
export function whatIf(card: PlanCard): WhatIf | null {
  const slowest = [...card.queries].sort(
    (a, b) => (b.mean_ms ?? 0) - (a.mean_ms ?? 0)
  )[0]
  const title =
    card.queries.length > 1
      ? `What if, the slowest of the ${card.queries.length}`
      : 'What if'
  if (card.before_ms != null && card.after_ms != null)
    return {
      title: 'What if, measured',
      today: card.before_ms,
      after: card.after_ms,
      query:
        card.queries.length > 1
          ? `Its ${card.queries.length} queries, on average`
          : (slowest?.name ?? card.title),
      note: 'Measured by RDST with and without the change.',
    }
  if (slowest?.mean_ms != null && slowest.expected_ms != null)
    return {
      title,
      today: slowest.mean_ms,
      after: slowest.expected_ms,
      query: slowest.name,
      note: 'Estimated from the plan, before the change is made.',
    }
  return null
}

/** "2.6x faster". */
export function speedup(today: number, after: number): string {
  if (after <= 0 || today <= after) return 'about the same'
  return `${(today / after).toFixed(1)}x faster`
}

// Migrations

export type MigrationFormat = 'alembic' | 'prisma' | 'rails' | 'flyway'

export const MIGRATION_FORMATS: readonly {
  value: MigrationFormat
  label: string
}[] = [
  { value: 'alembic', label: 'Alembic' },
  { value: 'prisma', label: 'Prisma' },
  { value: 'rails', label: 'Rails' },
  { value: 'flyway', label: 'Flyway' },
]

/** Statements a migration tool must run outside its transaction. */
const NO_TRANSACTION = /\b(concurrently|alter\s+system|vacuum)\b/i

function slug(title: string): string {
  return (
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 60) || 'rdst_change'
  )
}

function camel(title: string): string {
  return (
    title
      .replace(/[^A-Za-z0-9]+/g, ' ')
      .trim()
      .split(' ')
      .filter(Boolean)
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
      .join('') || 'RdstChange'
  )
}

function stamp(now: Date): string {
  const two = (value: number) => String(value).padStart(2, '0')
  return `${now.getFullYear()}${two(now.getMonth() + 1)}${two(now.getDate())}${two(now.getHours())}${two(now.getMinutes())}${two(now.getSeconds())}`
}

const indent = (text: string, by: string) =>
  text
    .split('\n')
    .map((line) => (line ? `${by}${line}` : line))
    .join('\n')

/** A migration file for one change, in a tool's own shape, from its SQL and rollback SQL. */
export function migration(
  format: MigrationFormat,
  change: { title: string; sql: string; rollback_sql: string | null },
  now: Date = new Date()
): string {
  const { title, sql } = change
  const rollback = change.rollback_sql
  const outside = NO_TRANSACTION.test(`${sql}\n${rollback ?? ''}`)
  const name = slug(title)
  const version = stamp(now)
  if (format === 'alembic') {
    const body = (statement: string | null) =>
      statement === null
        ? '    raise NotImplementedError("RDST has no rollback for this change")'
        : outside
          ? `    with op.get_context().autocommit_block():\n        op.execute(\n            """\n${indent(statement, '            ')}\n            """\n        )`
          : `    op.execute(\n        """\n${indent(statement, '        ')}\n        """\n    )`
    return `"""${title}

Revision ID: ${version}
"""
from alembic import op

revision = "${version}"
down_revision = None  # set to your latest revision
branch_labels = None
depends_on = None


def upgrade() -> None:
${body(sql)}


def downgrade() -> None:
${body(rollback)}
`
  }
  if (format === 'rails') {
    const body = (statement: string | null) =>
      statement === null
        ? '    raise ActiveRecord::IrreversibleMigration'
        : `    execute <<~SQL\n${indent(statement, '      ')}\n    SQL`
    return `# db/migrate/${version}_${name}.rb
class ${camel(title)} < ActiveRecord::Migration[7.1]
${outside ? '  disable_ddl_transaction!\n\n' : ''}  def up
${body(sql)}
  end

  def down
${body(rollback)}
  end
end
`
  }
  if (format === 'prisma') {
    const down = rollback
      ? `\n-- To roll back, run:\n${indent(rollback, '-- ')}\n`
      : ''
    return `-- prisma/migrations/${version}_${name}/migration.sql
-- ${title}
${sql}
${down}`
  }
  const undo = rollback ? `\n-- U${version}__${name}.sql\n${rollback}\n` : ''
  return `-- V${version}__${name}.sql
-- ${title}
${outside ? '-- Runs outside a transaction: set executeInTransaction=false in its .sql.conf\n' : ''}${sql}
${undo}`
}

// Applied

export const STATUS_LABEL: Record<AppliedRow['status'], string> = {
  verified: 'Verified',
  measuring: 'Measuring',
  no_change: 'No change yet',
  undone: 'Undone',
}

/** "This week: 3 changes applied, 41% of database time saved." then the counts. */
export function appliedLead(totals: AppliedPage['totals']): {
  lead: string
  rest: string
} {
  const count = totals.changes_this_week
  const saved =
    totals.saved_share != null && totals.saved_share > 0
      ? `, ${pct(totals.saved_share)} of database time saved`
      : ''
  return {
    lead: `This week: ${plural(count, 'change', 'changes')} applied${saved}.`,
    rest: `${totals.verified} verified, ${totals.measuring} measuring.`,
  }
}

const WEEKDAY = new Intl.DateTimeFormat('en-US', { weekday: 'long' })
const MONTH_DAY = new Intl.DateTimeFormat('en-US', {
  month: 'long',
  day: 'numeric',
})
const FULL_DAY = new Intl.DateTimeFormat('en-US', {
  weekday: 'long',
  month: 'long',
  day: 'numeric',
})

/** A day's heading and its date: Today, Yesterday, a weekday, Last week or Earlier. */
export function dayLabel(
  start: number,
  now: number
): {
  label: string
  date: string
} {
  const today = new Date(now * 1000)
  const midnight =
    new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime() /
    1000
  const days = Math.round((midnight - start) / 86400)
  const date = new Date(start * 1000)
  if (days <= 0) return { label: 'Today', date: FULL_DAY.format(date) }
  if (days === 1) return { label: 'Yesterday', date: FULL_DAY.format(date) }
  if (days < 7)
    return { label: WEEKDAY.format(date), date: MONTH_DAY.format(date) }
  return {
    label: days < 14 ? 'Last week' : 'Earlier',
    date: FULL_DAY.format(date),
  }
}

const TIME = new Intl.DateTimeFormat('en-US', {
  hour: 'numeric',
  minute: '2-digit',
  hour12: true,
})

/** "9:15" on the row, the AM or PM left to the day around it. */
export function clock(at: number): string {
  return TIME.format(new Date(at * 1000)).replace(/\s?[AP]M$/, '')
}

export function dateOf(at: number): string {
  return MONTH_DAY.format(new Date(at * 1000))
}

export function appliedBy(row: AppliedRow): string {
  return row.by === 'user'
    ? 'by you, applied from the plan'
    : 'detected in the database'
}

/** One query's before and after, as the row reads it. */
export function queryChange(
  query: AppliedRow['queries'][number],
  status: AppliedRow['status']
): string {
  const { before_ms: before, after_ms: after } = query
  if (status === 'measuring' || after == null)
    return before != null ? `${ms(before)} before, measuring` : 'measuring'
  if (status === 'no_change') return `${ms(after)}, unchanged`
  return before != null ? `${ms(before)} to ${ms(after)}` : ms(after)
}

/** The line under the queries: how far the measuring has got, or when it was verified. */
export function checkLine(row: AppliedRow, now: number): string {
  const calls = row.calls_after.toLocaleString('en-US')
  if (row.status === 'measuring') {
    const next = row.next_check_at
      ? `, next check in ${Math.max(1, Math.ceil((row.next_check_at - now) / 60))} min`
      : ''
    return `${calls} ${row.calls_after === 1 ? 'call' : 'calls'} so far${next}`
  }
  if (row.status === 'verified') return `verified over ${calls} calls`
  if (row.status === 'no_change')
    return `checked over ${calls} calls, checks again after the next report`
  return ''
}
