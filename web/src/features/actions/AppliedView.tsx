// Applied: the changes made in the last 30 days by day, each with what it did to the queries
// it was meant to speed up, its status and when RDST checks next; Undo shows the rollback SQL
// to run and marks the change undone. Undone and Dismissed sit below, quiet.
import { cn } from '@rs/tailwind-base'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { EmptyState } from '@rs/ui-new/empty-state'
import { Popover, PopoverContent, PopoverTrigger } from '@rs/ui-new/popover'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useId, useState } from 'react'
import { SqlCode } from '@/features/database/queries/SqlCode'
import {
  type AppliedPage,
  type AppliedRow,
  usePlanVerb,
} from '@/lib/monitor/actions'
import {
  appliedBy,
  appliedLead,
  checkLine,
  clock,
  dateOf,
  dayLabel,
  queryChange,
  STATUS_LABEL,
  saves,
} from './actionsModel'
import { QuietRow } from './parts'

/** when, change and what it did, time saved, status, undo */
const APPLIED_COLUMNS = '72px minmax(0,1fr) 110px 130px 72px'

const HEAD = [
  { label: 'When' },
  { label: 'Change and what it did' },
  { label: 'Time saved', align: 'end' as const },
  { label: 'Status', align: 'end' as const },
  { label: '' },
]

export function AppliedView({
  page,
  rows,
  now,
  filters,
}: {
  page: AppliedPage
  /** The rows in view by day: one kind, or every kind. */
  rows: AppliedPage['days']
  now: number
  /** The kind chips, over the table. */
  filters?: ReactNode
}) {
  const lead = appliedLead(page.totals)
  return (
    <>
      {rows.length ? (
        <>
          <Text
            as="p"
            level="headline-3"
            className="max-w-4xl text-content-layout-1"
            data-testid="applied-lead"
          >
            {lead.lead}
            <span className="font-normal text-content-layout-2">
              {' '}
              {lead.rest}
            </span>
          </Text>
          <Text as="p" level="caption" className="mt-2.5 text-content-layout-3">
            RDST measures each change against the 7 days before it, on the
            queries it was meant to speed up.
          </Text>
          {filters ? <div className="mt-10">{filters}</div> : null}
          <DividedList
            columns={APPLIED_COLUMNS}
            head={HEAD}
            className={filters ? 'mt-6' : 'mt-9'}
          >
            {rows.map((day) => {
              const label = dayLabel(day.start, now)
              return (
                <section key={day.day} aria-label={label.label}>
                  <div className="flex items-baseline gap-2.5 border-b border-border-layout-2 pt-8 pb-2">
                    <Text
                      as="h2"
                      level="label-medium"
                      className="text-content-layout-1"
                    >
                      {label.label}
                    </Text>
                    <Text
                      as="span"
                      level="caption"
                      className="text-content-layout-3"
                    >
                      {label.date}
                    </Text>
                  </div>
                  {day.rows.map((row) => (
                    <AppliedLine key={row.id} row={row} now={now} />
                  ))}
                </section>
              )
            })}
          </DividedList>
        </>
      ) : (
        <>
          {filters ? <div className="mb-8">{filters}</div> : null}
          <EmptyState
            tone="quiet"
            title="Nothing applied in the last 30 days"
            body="Copy a change from the plan and say I did this, or apply it your own way: RDST notices it land and measures what it did."
          />
        </>
      )}
      <ClosedLists page={page} />
    </>
  )
}

function AppliedLine({ row, now }: { row: AppliedRow; now: number }) {
  const check = checkLine(row, now)
  return (
    <ListRow
      className="items-start py-4"
      data-testid="applied-row"
      data-status={row.status}
    >
      <span className="pt-0.5 font-mono text-caption tabular-nums text-content-layout-3">
        {clock(row.applied_at)}
      </span>
      <span className="min-w-0">
        <Text
          as="span"
          level="body-medium"
          className="block font-medium text-content-layout-1"
        >
          {row.title}
        </Text>
        <Text
          as="span"
          level="caption"
          className="mt-0.5 block text-content-layout-3"
        >
          {row.database}, {appliedBy(row)}
        </Text>
        {row.queries.length ? (
          <span className="mt-2.5 block max-w-xl space-y-1">
            {row.queries.map((query) => (
              <span
                key={query.key}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 text-body-small text-content-layout-2"
              >
                <span className="truncate">{query.name}</span>
                <span className="tabular-nums text-content-layout-1">
                  {queryChange(query, row.status)}
                </span>
              </span>
            ))}
          </span>
        ) : null}
        {check ? (
          <Text
            as="span"
            level="caption"
            className="mt-1.5 block text-content-layout-3"
          >
            {check}
          </Text>
        ) : null}
      </span>
      <span
        className={cn(
          'text-right text-body-small tabular-nums',
          row.saved_ms_per_min
            ? 'text-content-layout-1'
            : 'text-content-layout-3'
        )}
      >
        {row.saved_ms_per_min
          ? `${saves(row.saved_ms_per_min)} a min`
          : row.status === 'measuring'
            ? 'not yet'
            : 'none'}
      </span>
      <span className="justify-self-end">
        <Tag
          size="small"
          modifier="ghost"
          variant={row.status === 'verified' ? 'positive' : 'neutral'}
          className="rounded-pill whitespace-nowrap"
          label={STATUS_LABEL[row.status]}
        />
      </span>
      <span className="justify-self-end">
        {row.rollback_sql ? (
          <Undo row={row} rollback={row.rollback_sql} />
        ) : null}
      </span>
    </ListRow>
  )
}

function Undo({ row, rollback }: { row: AppliedRow; rollback: string }) {
  const verb = usePlanVerb()
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const id = useId()
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button variant="primary" modifier="ghost" size="small" label="Undo" />
      </PopoverTrigger>
      <PopoverContent
        align="end"
        aria-label={`Undo ${row.title}`}
        className="w-96 max-w-lg flex-col gap-3 p-4"
      >
        <Text as="p" level="body-small" className="text-content-layout-2">
          Run this on {row.database} to undo the change, then say so here.
        </Text>
        <SqlCode sql={rollback} testId="undo-sql" />
        <div>
          <CopyButton text={rollback} label="Copy rollback SQL" />
        </div>
        <div>
          <label
            htmlFor={id}
            className="mb-1 block text-caption text-content-layout-3"
          >
            Reason
          </label>
          <BaseInputText
            id={id}
            value={reason}
            placeholder="Optional"
            onChange={(event) => setReason(event.target.value)}
            className="h-8.5 bg-surface-layout-2 text-body-small"
          />
        </div>
        <Button
          variant="primary"
          modifier="outline"
          size="small"
          label="I undid this"
          className="self-end"
          disabled={verb.isPending}
          onClick={() => {
            verb.mutate({
              target: row.database,
              id: row.id,
              verb: 'undid_it',
              reason: reason.trim() || undefined,
            })
            setOpen(false)
          }}
        />
      </PopoverContent>
    </Popover>
  )
}

function ClosedLists({ page }: { page: AppliedPage }) {
  if (!page.undone.length && !page.dismissed.length) return null
  return (
    <div className="mt-11 border-t border-border-layout-2">
      {page.undone.length ? (
        <QuietRow
          name={`Undone (${page.undone.length})`}
          sub={
            page.undone.length === 1
              ? `${page.undone[0].title}, undone on ${dateOf(page.undone[0].undone_at ?? page.undone[0].applied_at)}`
              : page.undone.some((row) => row.reason)
                ? 'With your reasons'
                : undefined
          }
          testId="applied-undone"
        >
          <ReasonList
            rows={page.undone.map((row) => ({
              id: row.id,
              title: row.title,
              line: [
                row.database,
                `undone on ${dateOf(row.undone_at ?? row.applied_at)}`,
                row.reason,
              ]
                .filter(Boolean)
                .join(', '),
            }))}
          />
        </QuietRow>
      ) : null}
      {page.dismissed.length ? (
        <QuietRow
          name={`Dismissed (${page.dismissed.length})`}
          sub={
            page.dismissed.some((row) => row.reason)
              ? 'With your reasons'
              : undefined
          }
          testId="applied-dismissed"
        >
          <ReasonList
            rows={page.dismissed.map((row) => ({
              id: row.id,
              title: row.title,
              line: [
                row.database,
                row.reason ?? `dismissed on ${dateOf(row.dismissed_at)}`,
              ].join(', '),
            }))}
          />
        </QuietRow>
      ) : null}
    </div>
  )
}

function ReasonList({
  rows,
}: {
  rows: { id: number; title: string; line: string }[]
}) {
  return (
    <div className="space-y-2">
      {rows.map((row) => (
        <div
          key={row.id}
          className="grid grid-cols-[minmax(0,1fr)_auto] gap-4 text-body-small"
        >
          <span className="truncate text-content-layout-2">{row.title}</span>
          <span className="text-caption text-content-layout-3">{row.line}</span>
        </div>
      ))}
    </div>
  )
}
