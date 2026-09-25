// The bottom of the daily snapshot: the queries to look at, each open to its fix (B6) with
// the full deep analysis a click away, and every query that ran, sortable, each opening in
// place, with the whole table as a CSV (B7).
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import { Tag } from '@rs/ui-new/tag'
import { Fragment, useMemo, useState } from 'react'
import {
  GradeTile,
  GradeTilePlaceholder,
} from '@/features/database/queries/ScorePill'
import {
  formatMsWithUnit,
  formatQps,
  formatShare,
} from '@/features/monitor-kit/format'
import type { SnapshotQuery } from '@/lib/monitor/types'
import {
  AnalysisToggle,
  CARD,
  Change,
  CodeBlock,
  FixDetails,
  QueryName,
  QueryPanel,
  Section,
  type SnapshotContext,
  StatusTag,
  useOpenDatabase,
} from './snapshotKit'
import {
  csvFileName,
  type QuerySort,
  queriesCsv,
  STATUS_LOOK,
  sortQueries,
} from './snapshotModel'

function Grade({ query }: { query: SnapshotQuery }) {
  if (query.score == null && !query.grade)
    return (
      <GradeTilePlaceholder
        label={query.analysis_ref ? 'Analyzed, no score' : 'Not analyzed yet'}
      />
    )
  return <GradeTile score={{ score: query.score, rating: null }} />
}

function LookAtRow({
  query,
  context,
}: {
  query: SnapshotQuery
  context: SnapshotContext
}) {
  const [open, setOpen] = useState(true)
  const expanded = open || context.printing
  const openDatabase = useOpenDatabase(context.target, context.snapshot)
  const latency = formatMsWithUnit(query.mean_ms)
  const panel = `look-${query.query_key}`
  const note = query.note
  return (
    <li
      className="transition-colors duration-fast hover:bg-surface-layout-2/20"
      data-testid="look-at-row"
      data-status={query.status}
    >
      <div className="grid grid-cols-[2.25rem_minmax(0,1fr)] items-start gap-3 px-4 py-3 tablet:grid-cols-[2.25rem_minmax(0,1fr)_auto]">
        <Grade query={query} />
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <QueryName queryKey={query.query_key} context={context} strong />
            <StatusTag query={query} />
            {query.provisional ? (
              <Tag
                size="small"
                variant="neutral"
                modifier="outline"
                label="Plan only"
              />
            ) : null}
          </div>
          {note ? (
            <p className="text-body-medium text-content-layout-1">
              <span className="font-semibold">{note.title}. </span>
              <span className="text-content-layout-2">{note.why}</span>
            </p>
          ) : query.status_reason ? (
            <p className="text-body-small text-content-layout-1">
              {query.status_reason}.
            </p>
          ) : null}
          <p className="flex flex-wrap gap-x-3 text-caption tabular-nums text-content-layout-3">
            {query.qps != null ? <span>{formatQps(query.qps)}/s</span> : null}
            {query.mean_ms != null ? (
              <span>
                {latency.value} {latency.unit} a call
              </span>
            ) : null}
            <span>{formatShare(query.share)} of database time</span>
            <Change query={query} />
          </p>
        </div>
        <div className="col-start-2 flex items-center gap-1 tablet:col-start-3 print:hidden">
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            label="Open in Database"
            icon="arrow-up-right"
            iconPosition="right"
            aria-label={`Open ${context.snapshot.names[query.query_key] ?? 'the query'} on the Database page for this day`}
            onClick={() => openDatabase(query.query_key)}
          />
          <Pressable
            onClick={() => setOpen((value) => !value)}
            aria-expanded={expanded}
            aria-controls={panel}
            aria-label={expanded ? 'Hide the fix' : 'Show the fix'}
            className="flex h-8 w-8 items-center justify-center rounded-md text-content-layout-2 hover:bg-surface-layout-2"
          >
            <Icon
              name="chevron-down"
              label=""
              aria-hidden="true"
              className={cn(
                'h-4 w-4 transition-transform duration-fast',
                expanded && 'rotate-180'
              )}
            />
          </Pressable>
        </div>
      </div>
      {expanded ? (
        <div
          id={panel}
          className="space-y-3 px-4 pb-5 tablet:pl-[4.25rem]"
          data-testid="look-at-detail"
        >
          {note?.action ? (
            <p className="flex items-start gap-1.5 text-body-medium text-content-layout-1">
              <Icon
                name="arrow-right"
                label=""
                aria-hidden="true"
                className="mt-1 h-3.5 w-3.5 shrink-0 text-content-primary-soft"
              />
              {note.action}
            </p>
          ) : null}
          <FixDetails query={query} />
          {query.text ? (
            <details className="group/sql space-y-1.5" open={context.printing}>
              <summary className="cursor-pointer list-none text-label-medium text-content-layout-2 hover:text-content-layout-1">
                <span className="inline-flex items-center gap-1.5">
                  <Icon
                    name="chevron-right"
                    label=""
                    aria-hidden="true"
                    className="h-4 w-4 transition-transform duration-fast group-open/sql:rotate-90"
                  />
                  The query
                </span>
              </summary>
              <CodeBlock sql={query.text} />
            </details>
          ) : null}
          <AnalysisToggle query={query} context={context} />
        </div>
      ) : null}
    </li>
  )
}

export function LookAt({ context }: { context: SnapshotContext }) {
  const rows = context.snapshot.look_at
  return (
    <Section
      id="snapshot-look-at"
      title="Queries to look at"
      aside={
        rows.length
          ? 'Open alerts first, then fixes by database time'
          : undefined
      }
    >
      {rows.length ? (
        <ul
          className={cn(
            CARD,
            'divide-y divide-border-layout-1 overflow-hidden'
          )}
        >
          {rows.map((query) => (
            <LookAtRow key={query.query_key} query={query} context={context} />
          ))}
        </ul>
      ) : (
        <p
          className={cn(
            CARD,
            'flex items-center gap-2 px-5 py-4 text-body-medium text-content-layout-2'
          )}
        >
          <Icon
            name="tick"
            label=""
            aria-hidden="true"
            className="h-4 w-4 text-content-positive-soft"
          />
          No query needed a look this day.
        </p>
      )}
    </Section>
  )
}

const COLUMNS: { sort: QuerySort; label: string; className: string }[] = [
  { sort: 'name', label: 'Query', className: 'text-left' },
  { sort: 'calls', label: 'Calls', className: 'text-right' },
  { sort: 'mean_ms', label: 'Avg latency', className: 'text-right' },
  { sort: 'share', label: 'Database time', className: 'text-right' },
  { sort: 'change', label: 'Vs day before', className: 'text-right' },
]

function downloadCsv(context: SnapshotContext) {
  const blob = new Blob([queriesCsv(context.snapshot.queries)], {
    type: 'text/csv;charset=utf-8',
  })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = csvFileName(context.target, context.snapshot.period.day)
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function AllQueries({ context }: { context: SnapshotContext }) {
  const [open, setOpen] = useState(false)
  const [sort, setSort] = useState<QuerySort>('share')
  const [openKey, setOpenKey] = useState<string | null>(null)
  const rows = useMemo(
    () => sortQueries(context.snapshot.queries, sort),
    [context.snapshot.queries, sort]
  )
  const expanded = open || context.printing
  if (!rows.length) return null
  return (
    <section
      aria-labelledby="snapshot-all"
      className="scroll-mt-20 space-y-3"
      id="snapshot-all-section"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Pressable
          onClick={() => setOpen((value) => !value)}
          aria-expanded={expanded}
          aria-controls="snapshot-all-table"
          className="flex items-center gap-2 rounded-md text-left"
        >
          <Icon
            name="chevron-right"
            label=""
            aria-hidden="true"
            className={cn(
              'h-4 w-4 text-content-layout-3 transition-transform duration-fast print:hidden',
              expanded && 'rotate-90'
            )}
          />
          <h2
            id="snapshot-all"
            className="text-headline-5 text-content-layout-1"
          >
            All queries that day
          </h2>
          <span className="text-body-small text-content-layout-3">
            {rows.length}
          </span>
        </Pressable>
        <Button
          variant="primary"
          modifier="outline"
          size="small"
          label="Download CSV"
          icon="arrow-down"
          iconPosition="left"
          className="print:hidden"
          onClick={() => downloadCsv(context)}
          data-testid="download-csv"
        />
      </div>
      {expanded ? (
        <div id="snapshot-all-table" className={cn(CARD, 'overflow-x-auto')}>
          <table className="w-full min-w-[40rem] text-body-small">
            <thead>
              <tr className="border-b border-border-layout-1 text-label-small text-content-layout-2">
                {COLUMNS.map((column) => (
                  <th
                    key={column.sort}
                    scope="col"
                    className={cn('px-4 py-2 font-normal', column.className)}
                    aria-sort={sort === column.sort ? 'descending' : undefined}
                  >
                    <Pressable
                      onClick={() => setSort(column.sort)}
                      className={cn(
                        'rounded-sm px-1 hover:text-content-layout-1',
                        sort === column.sort &&
                          'font-semibold text-content-layout-1'
                      )}
                    >
                      {column.label}
                    </Pressable>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border-layout-1">
              {rows.map((row) => {
                const latency = formatMsWithUnit(row.mean_ms)
                const look = STATUS_LOOK[row.status]
                const active = openKey === row.query_key
                return (
                  <Fragment key={row.query_key}>
                    <tr
                      className={cn(
                        'transition-colors duration-fast hover:bg-surface-layout-2/40',
                        active && 'bg-surface-layout-2/40'
                      )}
                    >
                      <td className="max-w-[22rem] px-4 py-1.5">
                        <span className="flex min-w-0 items-center gap-2">
                          <QueryName
                            queryKey={row.query_key}
                            context={context}
                            onToggle={(key) =>
                              setOpenKey((current) =>
                                current === key ? null : key
                              )
                            }
                            active={active}
                          />
                          {look && row.status !== 'not_analyzed' ? (
                            <span
                              className={cn(
                                'shrink-0 text-caption',
                                look.tone === 'negative'
                                  ? 'text-content-negative-soft'
                                  : look.tone === 'warning'
                                    ? 'text-content-warning-soft'
                                    : 'text-content-positive-soft'
                              )}
                            >
                              {look.label}
                            </span>
                          ) : null}
                        </span>
                      </td>
                      <td className="px-4 py-1.5 text-right tabular-nums text-content-layout-1">
                        {row.calls != null
                          ? Math.round(row.calls).toLocaleString('en-US')
                          : '-'}
                      </td>
                      <td className="px-4 py-1.5 text-right tabular-nums text-content-layout-1">
                        {latency.value} {latency.unit}
                      </td>
                      <td className="px-4 py-1.5 text-right tabular-nums text-content-layout-1">
                        {formatShare(row.share)}
                      </td>
                      <td className="px-4 py-1.5 text-right text-caption">
                        <Change query={row} />
                      </td>
                    </tr>
                    {active ? (
                      <tr>
                        <td colSpan={COLUMNS.length} className="px-4 pb-3">
                          <QueryPanel
                            queryKey={row.query_key}
                            context={context}
                            onClose={() => setOpenKey(null)}
                          />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}
