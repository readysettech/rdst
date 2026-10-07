// The index plan's changes as one table: each row is an index to add or drop, on which table,
// and what it does. A row opens on the queries it speeds up (or why it goes), what it costs
// writes, and its SQL. The indexes to keep sit below, closed.
import { cn } from '@rs/tailwind-base'
import { CopyButton } from '@rs/ui-new/copy-button'
import { Icon } from '@rs/ui-new/icon'
import { m } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useState } from 'react'
import type {
  ActionCard,
  IndexPlan,
  IndexPlanAdd,
  IndexPlanDrop,
  IndexPlanKeep,
} from '@/lib/monitor/actions'
import { CARD, CodeBlock } from '../reports/snapshotKit'
import { FreshGlow, Verbs } from './ActionsTab'
import { formatBytes, pct, span } from './actionsModel'
import { Reveal } from './Reveal'

/** The plan as one script: the changes, then the commented-out lines that undo them. */
export function planScript(plan: IndexPlan): string {
  if (!plan.rollback_sql) return plan.sql
  const undo = plan.rollback_sql
    .split('\n')
    .map((line) => `-- ${line}`)
    .join('\n')
  return `${plan.sql}\n\n-- To undo the plan, run these:\n${undo}`
}

/** The table's header: how many changes, and the plan's SQL to copy or read. */
function PlanHeader({ plan, count }: { plan: IndexPlan; count: number }) {
  const [open, setOpen] = useState(false)
  const script = planScript(plan)
  return (
    <div className="border-b border-border-layout-1" data-testid="apply-plan">
      <div className="flex flex-wrap items-center justify-between gap-3 px-6 py-3">
        <Text as="h3" level="label-large" className="text-content-layout-1">
          {count} {count === 1 ? 'change' : 'changes'}
        </Text>
        {plan.sql ? (
          <div className="flex flex-wrap items-center gap-1">
            {open ? null : <CopyButton text={script} label="Copy SQL" />}
            <Pressable
              onClick={() => setOpen((value) => !value)}
              aria-expanded={open}
              className="rounded-control px-2 py-1 text-label-medium text-content-layout-2 hover:bg-surface-layout-2 hover:text-content-layout-1"
            >
              {open ? 'Hide the SQL' : 'Show the SQL'}
            </Pressable>
          </div>
        ) : null}
      </div>
      <Reveal open={open && Boolean(plan.sql)}>
        <div className="space-y-3 px-6 pb-5">
          <Text as="p" level="body-small" className="text-content-layout-3">
            {plan.engine === 'mysql'
              ? "MySQL commits each index change on its own, so a transaction can't roll them back. InnoDB builds them online. If you need to back out, run the undo lines at the bottom."
              : "CONCURRENTLY keeps writes flowing while each index builds, and PostgreSQL won't run it inside a transaction. Run the statements one at a time. If you need to back out, run the undo lines at the bottom."}
          </Text>
          <CodeBlock sql={script} label="Copy SQL" />
        </div>
      </Reveal>
    </div>
  )
}

// Change, table, columns, what it does. On a phone the table, columns and effect stack in
// the middle column and the chevron stays on the first line.
const GRID =
  'grid grid-cols-[4rem_minmax(0,1fr)_16px] gap-x-4 gap-y-1 tablet:grid-cols-[4rem_minmax(0,0.7fr)_minmax(0,1.3fr)_minmax(0,1.1fr)_16px]'

export function IndexChanges({
  target,
  plan,
  fresh,
  cards,
  onShowQuery,
}: {
  target: string
  plan: IndexPlan
  fresh?: Set<string>
  cards: ActionCard[]
  onShowQuery: (key: string) => void
}) {
  const ids = new Map(cards.map((card) => [card.key, card.id]))
  // Biggest share of database time first, the number each row shows.
  const share = (add: IndexPlanAdd) =>
    add.helps.reduce((sum, helped) => sum + helped.share, 0)
  const adds = [...plan.add].sort(
    (a, b) => a.table.localeCompare(b.table) || share(b) - share(a)
  )
  const drops = [...plan.drop].sort((a, b) => a.table.localeCompare(b.table))
  const considered = [...plan.skipped, ...(plan.later ?? [])]
  // Rows of one table sit together, adds before drops.
  const rows = [
    ...adds.map((add) => ({ table: add.table, add, drop: null })),
    ...drops.map((drop) => ({ table: drop.table, add: null, drop })),
  ].sort((a, b) => a.table.localeCompare(b.table))
  return (
    <div className="space-y-4">
      {plan.add.length || plan.drop.length ? (
        <div className={cn(CARD, 'overflow-hidden')} data-testid="index-plan">
          <PlanHeader plan={plan} count={rows.length} />
          <div
            className={cn(
              GRID,
              'hidden border-b border-border-layout-1 px-6 py-2.5 text-label-small text-content-layout-3 tablet:grid'
            )}
          >
            <span>Change</span>
            <span>Table</span>
            <span>Columns, in index order</span>
            <span>What it does</span>
            <span />
          </div>
          <ul className="divide-y divide-border-layout-1">
            {rows.map((row, index) => (
              <m.li
                key={row.add?.id ?? row.drop?.id}
                className="relative"
                initial={{ opacity: 0, y: 6 }}
                animate={{
                  opacity: 1,
                  y: 0,
                  transition: {
                    duration: 0.24,
                    ease: 'easeOut',
                    delay: Math.min(index, 8) * 0.035,
                  },
                }}
              >
                <FreshGlow
                  fresh={Boolean(fresh?.has(row.add?.id ?? row.drop?.id ?? ''))}
                />
                {row.add ? (
                  <AddRow
                    target={target}
                    add={row.add}
                    id={ids.get(row.add.id) ?? null}
                    onShowQuery={onShowQuery}
                  />
                ) : row.drop ? (
                  <DropRow
                    target={target}
                    drop={row.drop}
                    id={ids.get(row.drop.id) ?? null}
                    onShowQuery={onShowQuery}
                  />
                ) : null}
              </m.li>
            ))}
          </ul>
        </div>
      ) : null}
      <Closed
        title={`Keep ${plan.keep.length} ${plan.keep.length === 1 ? 'index' : 'indexes'}`}
        hint="Your queries read these, or they enforce a key"
        testId="index-plan-keep"
      >
        <KeepTable rows={plan.keep} />
      </Closed>
      {plan.watch?.length ? (
        <Closed
          title={`${plan.watch.length} unused ${plan.watch.length === 1 ? 'index' : 'indexes'} to check again later`}
          hint="Too small or too new to drop yet. They're checked again every few minutes."
          testId="index-plan-watch"
        >
          <ul className="divide-y divide-border-layout-1">
            {plan.watch.map((drop) => (
              <li key={drop.id}>
                <DropRow
                  target={target}
                  drop={drop}
                  id={null}
                  onShowQuery={onShowQuery}
                />
              </li>
            ))}
          </ul>
        </Closed>
      ) : null}
      {considered.length ? (
        <Closed
          title={`${considered.length} smaller ${considered.length === 1 ? 'win' : 'wins'} left out`}
          hint="Each would speed up little and still cost every write"
          testId="index-plan-considered"
        >
          <ul className="divide-y divide-border-layout-1">
            {considered.map((add) => (
              <li key={add.id}>
                <AddRow
                  target={target}
                  add={add}
                  id={null}
                  onShowQuery={onShowQuery}
                  muted
                />
              </li>
            ))}
          </ul>
        </Closed>
      ) : null}
    </div>
  )
}

function Kind({ verb, muted }: { verb: 'Add' | 'Drop'; muted?: boolean }) {
  return (
    <span
      className={cn(
        'col-start-1 row-start-1 inline-flex items-center gap-2 text-label-large',
        muted
          ? 'text-content-layout-3'
          : verb === 'Add'
            ? 'text-content-positive-soft'
            : 'text-content-negative-soft'
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'h-2 w-2 rounded-full',
          muted
            ? 'bg-content-layout-3'
            : verb === 'Add'
              ? 'bg-content-positive-plain'
              : 'bg-content-negative-plain'
        )}
      />
      {verb}
    </span>
  )
}

/** The table an index belongs to. */
function TableCell({ table }: { table: string }) {
  return (
    <span className="col-start-2 row-start-1 min-w-0 truncate text-label-large text-content-layout-1 tablet:col-start-2">
      <span className="text-content-layout-3 tablet:hidden">on </span>
      {table}
    </span>
  )
}

/** The columns in index order, and what kind of index that makes. */
function ColumnsCell({ columns, note }: { columns: string[]; note: string }) {
  return (
    <span className="col-start-2 row-start-2 min-w-0 space-y-0.5 tablet:col-start-3 tablet:row-start-1">
      <span className="block break-words text-body-medium text-content-layout-1">
        {columns.join(', ')}
      </span>
      <span className="block text-body-small text-content-layout-3">
        {note}
      </span>
    </span>
  )
}

/** What to do, in one sentence: the index and the queries it's for. */
function instruction(add: IndexPlanAdd): string {
  const kind = add.columns.length > 1 ? 'a composite index' : 'an index'
  const share = add.helps.reduce((sum, helped) => sum + helped.share, 0)
  const target =
    add.helps.length === 1
      ? (add.helps[0].name ?? 'its query')
      : `${add.helps.length} queries that take ${pct(share)} of database time`
  return `Add ${kind} on ${add.table} (${add.columns.join(', ')}) to speed up ${target}.`
}

function shape(columns: string[]): string {
  return columns.length > 1
    ? `Composite, ${columns.length} columns`
    : 'Single column'
}

function Row({
  head,
  testId,
  children,
}: {
  head: (open: boolean) => ReactNode
  testId: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <div data-testid={testId}>
      <Pressable
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className={cn(
          GRID,
          'w-full items-start px-6 py-4 text-left hover:bg-surface-layout-2/40'
        )}
      >
        {head(open)}
      </Pressable>
      <Reveal open={open}>
        <div className="space-y-5 border-t border-border-layout-1 px-6 py-5">
          {children}
        </div>
      </Reveal>
    </div>
  )
}

function Chevron({ open }: { open: boolean }) {
  return (
    <Icon
      name="chevron-down"
      label=""
      aria-hidden="true"
      className={cn(
        'col-start-3 row-start-1 h-4 w-4 self-center text-content-layout-3 transition-transform duration-fast tablet:col-start-5',
        open && 'rotate-180'
      )}
    />
  )
}

function Effect({ text }: { text: string }) {
  return (
    <span className="col-start-2 row-start-3 min-w-0 text-body-small text-content-layout-1 tablet:col-start-4 tablet:row-start-1">
      {text}
    </span>
  )
}

function AddRow({
  target,
  add,
  id,
  onShowQuery,
  muted = false,
}: {
  target: string
  add: IndexPlanAdd
  id: number | null
  onShowQuery: (key: string) => void
  muted?: boolean
}) {
  const share = add.helps.reduce((sum, helped) => sum + helped.share, 0)
  const cost = add.write_cost
  return (
    <Row
      testId="index-plan-add"
      head={(open) => (
        <>
          <Kind verb="Add" muted={muted} />
          <TableCell table={add.table} />
          <ColumnsCell
            columns={add.columns}
            note={
              add.replaces.length
                ? `${shape(add.columns)}, replaces ${add.replaces.length}`
                : shape(add.columns)
            }
          />
          <Effect
            text={`Speeds up ${add.helps.length === 1 ? '1 query' : `${add.helps.length} queries`}, ${pct(share)} of database time`}
          />
          <Chevron open={open} />
        </>
      )}
    >
      <Text as="p" level="body-large" className="text-content-layout-1">
        {instruction(add)}
      </Text>
      {id != null ? <Verbs target={target} id={id} inline /> : null}
      <div className="space-y-1.5">
        <Text as="h3" level="label-medium" className="text-content-layout-3">
          Queries it speeds up
        </Text>
        <ul className="space-y-1.5">
          {add.helps.map((helped) => (
            <li
              key={helped.key}
              className="flex flex-wrap items-baseline justify-between gap-x-4 text-body-small"
            >
              <Pressable
                onClick={() => onShowQuery(helped.key)}
                className="text-content-layout-1 underline decoration-content-layout-3 underline-offset-4 hover:decoration-content-layout-1"
              >
                {helped.name ?? helped.key}
              </Pressable>
              <span className="tabular-nums text-content-layout-3">
                {pct(helped.share)} of database time
                {helped.verified && helped.gain_pct != null
                  ? `, planner cost down ${Math.round(helped.gain_pct)}%`
                  : ''}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <Text as="p" level="body-small" className="text-content-layout-2">
        {cost.writes_per_s >= 1
          ? `Each of the ${cost.writes_per_s.toFixed(0)} writes a second to ${add.table} (${cost.peak_writes_per_s.toFixed(0)} in its busiest hour) also updates this index, on top of the ${cost.existing_indexes} the table already has.`
          : `${add.table} takes almost no writes, so the index costs next to nothing to keep up.`}
        {add.replaces.length
          ? ` Once it's built, ${add.replaces.join(' and ')} ${add.replaces.length === 1 ? 'is' : 'are'} redundant and can go.`
          : ''}
      </Text>
      <CodeBlock sql={add.sql} label="Copy SQL" />
    </Row>
  )
}

const WHY_DROP = {
  unused: 'No query read it in the time the statistics cover',
  duplicate: 'Another index already covers it',
  stale: 'No query has read it in over 30 days',
} as const

/** How often a statement runs; a batch insert runs once for many rows. */
function runs(perSecond: number): string {
  if (perSecond >= 10) return `${perSecond.toFixed(0)} runs a second`
  if (perSecond >= 0.1) return `${perSecond.toFixed(1)} runs a second`
  return `${Math.max(1, Math.round(perSecond * 3600))} runs an hour`
}

/** What dropping speeds up: the writes that keep the index current. */
function dropInstruction(drop: IndexPlanDrop): string {
  const slows = drop.slows ?? []
  const share = slows.reduce((sum, write) => sum + write.share, 0)
  const target =
    slows.length === 1
      ? (slows[0].name ?? 'its busiest write')
      : `${slows.length} writes that take ${pct(share)} of database time`
  return slows.length
    ? `Drop ${drop.index_name} on ${drop.table} to speed up ${target}.`
    : `Drop ${drop.index_name} on ${drop.table}.`
}

function DropRow({
  target,
  drop,
  id,
  onShowQuery,
}: {
  target: string
  drop: IndexPlanDrop
  id: number | null
  onShowQuery: (key: string) => void
}) {
  const slows = drop.slows ?? []
  const share = slows.reduce((sum, write) => sum + write.share, 0)
  return (
    <Row
      testId="index-plan-drop"
      head={(open) => (
        <>
          <Kind verb="Drop" />
          <TableCell table={drop.table} />
          <ColumnsCell
            columns={drop.columns}
            note={`${shape(drop.columns)}, ${drop.index_name}`}
          />
          <Effect
            text={
              slows.length
                ? `Speeds up ${slows.length === 1 ? '1 write' : `${slows.length} writes`}, ${pct(share)} of database time`
                : drop.reason === 'duplicate' && drop.covered_by
                  ? `Covered by ${drop.covered_by}, frees ${formatBytes(drop.size_bytes)}`
                  : `Unused, frees ${formatBytes(drop.size_bytes)}`
            }
          />
          <Chevron open={open} />
        </>
      )}
    >
      <Text as="p" level="body-large" className="text-content-layout-1">
        {dropInstruction(drop)}
      </Text>
      {id != null ? <Verbs target={target} id={id} inline /> : null}
      {slows.length ? (
        <div className="space-y-1.5">
          <Text as="h3" level="label-medium" className="text-content-layout-3">
            Writes it speeds up
          </Text>
          <ul className="space-y-1.5">
            {slows.map((write) => (
              <li
                key={write.key}
                className="flex flex-wrap items-baseline justify-between gap-x-4 text-body-small"
              >
                <Pressable
                  onClick={() => onShowQuery(write.key)}
                  className="text-content-layout-1 underline decoration-content-layout-3 underline-offset-4 hover:decoration-content-layout-1"
                >
                  {write.name ?? write.key}
                </Pressable>
                <span className="tabular-nums text-content-layout-3">
                  {runs(write.calls_per_s)}, {pct(write.share)} of database time
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <Text as="p" level="body-small" className="text-content-layout-2">
        {drop.reason === 'duplicate' && drop.covered_by
          ? `${drop.covered_by} already serves every lookup this index could.`
          : `${WHY_DROP[drop.reason]} (${span(drop.evidence_age_s)}), yet ${slows.length ? 'each of these writes' : `every write to ${drop.table}`} updates it. Dropping it frees ${formatBytes(drop.size_bytes)}.`}
        {slows.length
          ? ' After you drop it, RDST measures these writes before and after.'
          : ''}
        {drop.confidence === 'low'
          ? ' The statistics are young, so check for monthly jobs that read it before you drop.'
          : ''}
        {drop.replica_caveat
          ? " This database streams to replicas, and a replica can read an index the primary doesn't."
          : ''}
      </Text>
      <CodeBlock sql={drop.sql} label="Copy SQL" />
    </Row>
  )
}

function Closed({
  title,
  hint,
  testId,
  children,
}: {
  title: string
  hint: string
  testId: string
  children: ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <section className={cn(CARD, 'overflow-hidden')} data-testid={testId}>
      <Pressable
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-4 px-6 py-4 text-left hover:bg-surface-layout-2/40"
      >
        <span className="space-y-0.5">
          <span className="block text-label-large text-content-layout-1">
            {title}
          </span>
          <span className="block text-body-small text-content-layout-3">
            {hint}
          </span>
        </span>
        <Chevron open={open} />
      </Pressable>
      <Reveal open={open}>
        <div className="border-t border-border-layout-1">{children}</div>
      </Reveal>
    </section>
  )
}

function KeepTable({ rows }: { rows: IndexPlanKeep[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-body-small">
        <thead className="text-left text-label-small text-content-layout-3">
          <tr>
            <th className="px-6 py-2.5 font-normal">Table</th>
            <th className="px-3 py-2.5 font-normal">Columns</th>
            <th className="px-3 py-2.5 font-normal">Index</th>
            <th className="px-3 py-2.5 text-right font-normal">Reads</th>
            <th className="px-6 py-2.5 text-right font-normal">Size</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-layout-1">
          {rows.map((row) => (
            <tr key={`${row.table}.${row.index_name}`}>
              <td className="px-6 py-2.5 text-content-layout-1">{row.table}</td>
              <td className="px-3 py-2.5 text-content-layout-2">
                {row.columns.join(', ')}
              </td>
              <td className="px-3 py-2.5 text-content-layout-3">
                {row.primary
                  ? 'Primary key'
                  : row.unique
                    ? `${row.index_name} (unique)`
                    : row.index_name}
              </td>
              <td className="px-3 py-2.5 text-right tabular-nums text-content-layout-2">
                {row.scans.toLocaleString()}
              </td>
              <td className="px-6 py-2.5 text-right tabular-nums text-content-layout-2">
                {formatBytes(row.size_bytes)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
