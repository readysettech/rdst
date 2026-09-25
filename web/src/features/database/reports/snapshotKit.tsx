// Shared pieces of the daily snapshot: the report context, section frame, code blocks,
// query name chips in their chart color, and the query panel that opens in place (B4).
// Clicking a query anywhere in the report opens it right there; the Database page is a
// secondary action on the panel.
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { CopyButton } from '@rs/ui-new/copy-button'
import { EmptyState } from '@rs/ui-new/empty-state'
import { Icon } from '@rs/ui-new/icon'
import { IconButton } from '@rs/ui-new/icon-button'
import { Pressable } from '@rs/ui-new/pressable'
import { Tag } from '@rs/ui-new/tag'
import { useNavigate } from '@tanstack/react-router'
import { Fragment, type ReactNode, useCallback, useMemo, useState } from 'react'
import { SqlTokens } from '@/components/SqlTokens'
import {
  parseAnalysisRef,
  StoredAnalysis,
} from '@/features/database/queries/AnalysisTab'
import {
  formatMsWithUnit,
  formatQps,
  formatShare,
} from '@/features/monitor-kit/format'
import type { DaySnapshot, SnapshotQuery } from '@/lib/monitor/types'
import { parseReportText } from './reportModel'
import { databaseHref, fixLine, STATUS_LOOK } from './snapshotModel'

export interface SnapshotContext {
  target: string
  snapshot: DaySnapshot
  colors: Map<string, string>
  /** Everything expanded, for print. */
  printing: boolean
}

function searchOf(href: string): Record<string, string> {
  return Object.fromEntries(
    new URL(href, 'http://local').searchParams.entries()
  )
}

export function useOpenDatabase(target: string, snapshot: DaySnapshot) {
  const navigate = useNavigate()
  return useCallback(
    (queryKey: string | null) =>
      void navigate({
        to: '/database' as never,
        search: searchOf(
          databaseHref({ target, queryKey, period: snapshot.period })
        ) as never,
      }),
    [navigate, target, snapshot.period]
  )
}

export const CARD =
  'rounded-card border border-border-layout-1 bg-surface-layout-1 print:shadow-none'

export function Swatch({ color }: { color: string | undefined }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'inline-block h-2.5 w-2.5 shrink-0 rounded-sm',
        !color && 'border border-content-layout-3/50'
      )}
      style={color ? { background: color } : undefined}
    />
  )
}

export function Section({
  id,
  title,
  aside,
  action,
  children,
}: {
  id: string
  title: string
  aside?: ReactNode
  action?: ReactNode
  children: ReactNode
}) {
  return (
    <section
      aria-labelledby={id}
      className="scroll-mt-20 space-y-3"
      id={`${id}-section`}
    >
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-0.5">
          <h2 id={id} className="text-headline-5 text-content-layout-1">
            {title}
          </h2>
          {aside ? (
            <span className="text-body-small text-content-layout-3">
              {aside}
            </span>
          ) : null}
        </div>
        {action ? <div className="shrink-0 print:hidden">{action}</div> : null}
      </div>
      {children}
    </section>
  )
}

export function CodeBlock({
  sql,
  label = 'Copy SQL',
}: {
  sql: string
  label?: string
}) {
  return (
    <div className="relative overflow-hidden rounded-panel border border-border-layout-1 bg-surface-layout-2/50">
      <div className="max-h-72 overflow-auto px-4 pt-11 pb-3 tablet:pt-3 tablet:pr-36 print:max-h-none">
        <SqlTokens sql={sql} className="text-mono-medium" />
      </div>
      <div className="absolute top-2 right-2 print:hidden">
        <CopyButton text={sql} label={label} />
      </div>
    </div>
  )
}

/**
 * A query's name in its chart color. With `onToggle` it opens the query in place; the
 * chip shows it's open with a ring.
 */
export function QueryName({
  queryKey,
  context,
  strong = false,
  onToggle,
  active = false,
}: {
  queryKey: string
  context: SnapshotContext
  strong?: boolean
  onToggle?: (key: string) => void
  active?: boolean
}) {
  const name =
    context.snapshot.names[queryKey] ?? `Query ${queryKey.slice(0, 8)}`
  const color = context.colors.get(queryKey)
  const body = (
    <>
      <Swatch color={color} />
      <span className="truncate text-content-layout-1">{name}</span>
    </>
  )
  const look = cn(
    'inline-flex max-w-full items-center gap-1.5 rounded-md px-1.5 py-0.5 align-baseline',
    strong ? 'text-label-large' : 'text-label-medium'
  )
  const tint = color
    ? { background: `color-mix(in oklab, ${color} 12%, transparent)` }
    : { background: 'var(--color-surface-layout-2)' }
  if (!onToggle)
    return (
      <span className={look} style={tint} data-query-key={queryKey}>
        {body}
      </span>
    )
  return (
    <Pressable
      onClick={(event) => {
        event.stopPropagation()
        onToggle(queryKey)
      }}
      title={active ? `Hide ${name}` : `Show ${name} here`}
      aria-expanded={active}
      data-query-key={queryKey}
      className={cn(
        look,
        'transition-[box-shadow,background-color] duration-fast hover:ring-1 hover:ring-border-layout-2',
        active &&
          'ring-2 ring-border-primary-solid hover:ring-border-primary-solid'
      )}
      style={tint}
    >
      {body}
    </Pressable>
  )
}

/** A sentence with {{q:key}} tokens as query chips. */
export function Sentence({
  text,
  context,
  onToggle,
  activeKey,
}: {
  text: string
  context: SnapshotContext
  onToggle?: (key: string) => void
  activeKey?: string | null
}) {
  const parts = useMemo(
    () => parseReportText(text, new Set(Object.keys(context.snapshot.names))),
    [text, context.snapshot.names]
  )
  return (
    <>
      {parts.map((part, index) =>
        part.kind === 'text' ? (
          <Fragment key={index}>{part.text}</Fragment>
        ) : (
          <QueryName
            key={index}
            queryKey={part.key}
            context={context}
            onToggle={onToggle}
            active={activeKey === part.key}
          />
        )
      )}
    </>
  )
}

/** Which query a row has open in place; clicking the open one closes it. */
export function useInlineQuery() {
  const [openKey, setOpenKey] = useState<string | null>(null)
  const toggle = useCallback(
    (key: string) => setOpenKey((current) => (current === key ? null : key)),
    []
  )
  return { openKey, toggle, close: () => setOpenKey(null) }
}

export function findQuery(
  snapshot: DaySnapshot,
  key: string
): SnapshotQuery | undefined {
  return (
    snapshot.look_at.find((row) => row.query_key === key) ??
    snapshot.queries.find((row) => row.query_key === key)
  )
}

export function Change({ query }: { query: SnapshotQuery }) {
  if (query.new) return <span className="text-content-primary-soft">new</span>
  const change = query.latency_change
  if (change == null || Math.abs(change) < 0.2) return null
  const up = change > 0
  return (
    <span
      className={cn(
        'tabular-nums',
        up ? 'text-content-negative-soft' : 'text-content-positive-soft'
      )}
    >
      {up
        ? `${(change + 1).toFixed(1)}x slower`
        : `${Math.round(-change * 100)}% faster`}
    </span>
  )
}

export function StatusTag({ query }: { query: SnapshotQuery }) {
  const look = STATUS_LOOK[query.status]
  if (!look) return null
  return (
    <Tag
      size="small"
      variant={look.tone}
      modifier={look.tone === 'neutral' ? 'outline' : 'ghost'}
      label={look.label}
    />
  )
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-caption text-content-layout-3">{label}</dt>
      <dd className="text-label-large tabular-nums text-content-layout-1">
        {value}
      </dd>
    </div>
  )
}

/** The query's numbers for the day as a compact stat row. */
export function QueryStats({ query }: { query: SnapshotQuery }) {
  const latency = formatMsWithUnit(query.mean_ms)
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-2 tablet:grid-cols-4">
      <Stat
        label="Calls"
        value={
          query.calls != null
            ? Math.round(query.calls).toLocaleString('en-US')
            : '-'
        }
      />
      <Stat
        label="Per second"
        value={query.qps != null ? formatQps(query.qps) : '-'}
      />
      <Stat
        label="Average latency"
        value={
          <>
            {latency.value} {latency.unit} <Change query={query} />
          </>
        }
      />
      <Stat label="Database time" value={formatShare(query.share)} />
    </dl>
  )
}

/** The deep analysis behind a toggle, loaded only when opened. */
export function AnalysisToggle({
  query,
  context,
  defaultOpen = false,
}: {
  query: SnapshotQuery
  context: SnapshotContext
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const shown = open || context.printing
  const openDatabase = useOpenDatabase(context.target, context.snapshot)
  const ref = parseAnalysisRef(query.analysis_ref, query.query_key)
  const panel = `analysis-${query.query_key}`
  return (
    <div className="space-y-2" data-testid="embedded-analysis">
      <Pressable
        onClick={() => setOpen((value) => !value)}
        aria-expanded={shown}
        aria-controls={panel}
        aria-label={shown ? 'Hide the full analysis' : 'Show the full analysis'}
        className="group inline-flex items-center gap-1.5 rounded-md py-0.5 text-label-medium text-content-primary-soft hover:text-content-primary-plain print:hidden"
      >
        <Icon
          name="chevron-right"
          label=""
          aria-hidden="true"
          className={cn(
            'h-4 w-4 transition-transform duration-fast',
            shown && 'rotate-90'
          )}
        />
        {shown ? 'Hide the full analysis' : 'Show the full analysis'}
        {query.analyzed_at ? (
          <span className="font-normal text-content-layout-3">
            from{' '}
            {new Date(query.analyzed_at * 1000).toLocaleString('en-US', {
              month: 'short',
              day: 'numeric',
              hour: 'numeric',
              minute: '2-digit',
            })}
          </span>
        ) : null}
      </Pressable>
      {shown ? (
        <div id={panel}>
          {ref ? (
            <div className="rounded-panel border border-border-layout-1 bg-surface-layout-1 p-4">
              <StoredAnalysis
                refParts={ref}
                mode={query.analysis_mode}
                target={context.target}
                onCompare={() => openDatabase(query.query_key)}
              />
            </div>
          ) : (
            <EmptyState
              layout="compact"
              icon="speedometer"
              title="No deep analysis for this query"
              body={
                query.provisional
                  ? "Its analysis didn't finish in time, so this day only has its plan."
                  : "RDST hadn't analyzed it when this snapshot was written."
              }
            />
          )}
        </div>
      ) : null}
    </div>
  )
}

/** The fix and what the analysis found: shown open by default wherever a query appears. */
export function FixDetails({ query }: { query: SnapshotQuery }) {
  const findings = query.findings ?? []
  if (!query.fix && !findings.length && !query.headline) return null
  return (
    <div className="space-y-3">
      {query.fix ? (
        <div className="space-y-1.5">
          <p className="flex items-center gap-2 text-label-medium text-content-layout-1">
            <span className="inline-flex h-5 items-center rounded-sm bg-surface-positive-soft px-1.5 text-caption font-semibold text-content-positive-soft">
              Suggested fix
            </span>
            {fixLine(query.fix.title).replace(/^./, (c) => c.toUpperCase())}
          </p>
          {query.fix.sql ? (
            <CodeBlock sql={query.fix.sql} label="Copy the fix" />
          ) : null}
        </div>
      ) : null}
      {findings.length ? (
        <div className="space-y-1">
          <p className="text-label-medium text-content-layout-1">
            What the analysis found
          </p>
          <ul className="space-y-1">
            {findings.map((finding) => (
              <li
                key={finding}
                className="flex gap-2 text-body-small text-content-layout-2"
              >
                <span
                  aria-hidden="true"
                  className="mt-[0.55em] h-1 w-1 shrink-0 rounded-full bg-content-layout-3"
                />
                <span className="min-w-0">{finding}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : query.headline && !query.fix ? (
        <p className="text-body-small text-content-layout-2">
          {query.headline}
        </p>
      ) : null}
    </div>
  )
}

/** A query opened in place: its day, its fix, its SQL, and the full analysis on demand. */
export function QueryPanel({
  queryKey,
  context,
  onClose,
}: {
  queryKey: string
  context: SnapshotContext
  onClose: () => void
}) {
  const openDatabase = useOpenDatabase(context.target, context.snapshot)
  const query = findQuery(context.snapshot, queryKey)
  const name =
    context.snapshot.names[queryKey] ?? `Query ${queryKey.slice(0, 8)}`
  return (
    <section
      className="mk-fade relative mt-2 space-y-4 rounded-panel border border-border-layout-1 bg-surface-layout-2/40 p-4 print:hidden"
      data-testid="query-panel"
      data-query-key={queryKey}
      aria-label={name}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <QueryName queryKey={queryKey} context={context} strong />
          {query ? <StatusTag query={query} /> : null}
          {query?.status_reason ? (
            <span className="text-body-small text-content-layout-2">
              {query.status_reason}
            </span>
          ) : null}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="primary"
            modifier="outline"
            size="small"
            label="Open in Database"
            icon="arrow-up-right"
            iconPosition="right"
            onClick={() => openDatabase(queryKey)}
          />
          <IconButton
            icon="close"
            label="Close"
            modifier="ghost"
            size="small"
            onClick={onClose}
          />
        </div>
      </div>
      {query ? (
        <>
          <QueryStats query={query} />
          <FixDetails query={query} />
          {query.text ? (
            <div className="space-y-1.5">
              <p className="text-label-medium text-content-layout-1">
                The query
              </p>
              <CodeBlock sql={query.text} />
            </div>
          ) : null}
          {query.analysis_ref ? (
            <AnalysisToggle query={query} context={context} />
          ) : null}
        </>
      ) : (
        <p className="text-body-small text-content-layout-2">
          RDST has no calls for this query in this day's recording.
        </p>
      )}
    </section>
  )
}
