// One query in the legend list (DESIGN_V5 section 1). The swatch matches the query's
// band in the hero chart; hovering either raises the other. Collapsed: the score, the
// name with its one Status chip, one reason line when there is one, a one-line SQL
// preview, and calls/s, average latency and share of database time. Expanded:
// everything about the query inline (QueryDetail).
import { cn } from '@rs/tailwind-base'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { AnimatePresence, m, useReducedMotion } from '@rs/ui-new/motion'
import { Pressable } from '@rs/ui-new/pressable'
import { Tag } from '@rs/ui-new/tag'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import { memo, type ReactNode } from 'react'
import {
  formatMsWithUnit,
  formatQps,
  formatShare,
} from '@/features/monitor-kit/format'
import { fromScroll } from '@/features/monitor-kit/pointerIntent'
import type { MonitorQuery } from '@/lib/monitor/types'
import { analyzability, noScoreLine } from './analyzability'
import { queryName } from './querySelectors'
import type { QueryStatus } from './queryStatus'
import { inlineActivity, scoreSlot } from './rowActivity'
import {
  GradeTile,
  GradeTileActivity,
  GradeTilePlaceholder,
  rowScore,
} from './ScorePill'

/** What RDST is doing with a query that has no deep analysis yet. */
export interface RowActivity {
  kind: 'running' | 'queued' | 'retrying' | 'classifying' | 'note'
  label: string
  /** Running or starting today: shown even on a scored row. */
  soon?: boolean
  /** Queued: its place among today's jobs. */
  position?: number | null
  /** Running: the step it's on ("Writing recommendations"). */
  step?: string | null
  /** Queued but it won't start on its own; `label` says why. */
  held?: boolean
}

function ActivityMark({ activity }: { activity: RowActivity }) {
  const reduced = useReducedMotion()
  return (
    <output
      data-activity={activity.kind}
      className="inline-flex items-center gap-1.5 text-caption text-content-layout-2"
    >
      {activity.kind === 'running' ? (
        <span
          aria-hidden="true"
          className={cn(
            'h-1.5 w-1.5 rounded-full bg-content-rising-plain',
            !reduced && 'mk-breathe'
          )}
        />
      ) : activity.kind === 'queued' || activity.kind === 'retrying' ? (
        <span
          aria-hidden="true"
          className="h-1.5 w-1.5 rounded-full border border-content-layout-3"
        />
      ) : null}
      <span
        className={cn(
          activity.kind === 'classifying' && !reduced && 'mk-breathe',
          activity.kind === 'running' && 'text-content-layout-1'
        )}
      >
        {activity.label}
      </span>
    </output>
  )
}

/** The query's classification, on every row whether or not it has a score. */
function FindingChip({ label, clear }: { label: string; clear: boolean }) {
  return (
    <Tag
      size="small"
      variant={clear ? 'muted' : 'neutral'}
      modifier="ghost"
      label={label}
      data-chip="finding"
      data-clear={clear || undefined}
    />
  )
}

/** Before a deep analysis scores the query: what RDST is doing with it or "Analyze now". */
function RowAction({
  activity,
  onAnalyze,
}: {
  activity?: RowActivity | null
  onAnalyze?: () => void
}) {
  if (!activity && !onAnalyze) return null
  return (
    <span className="inline-flex items-center gap-2">
      {activity ? (
        <ActivityMark activity={activity} />
      ) : onAnalyze ? (
        <Button
          variant="primary"
          modifier="ghost"
          size="small"
          label="Analyze now"
          onClick={onAnalyze}
          data-testid="row-analyze"
          className="h-6 px-2"
        />
      ) : null}
    </span>
  )
}

function StatusMark({
  status,
  quiet = false,
}: {
  status: QueryStatus
  /** The row's own action says enough; "Not analyzed yet" would repeat it. */
  quiet?: boolean
}) {
  if (status.id === 'healthy') return null
  if (status.id === 'not_analyzed') {
    if (quiet) return null
    const text = (
      <span
        tabIndex={status.reason ? 0 : undefined}
        data-status={status.id}
        className="text-caption text-content-layout-3 focus-visible:shadow-focus focus-visible:outline-none"
      >
        {status.label}
      </span>
    )
    return status.reason ? (
      <TooltipProvider delayDuration={120}>
        <Tooltip>
          <TooltipTrigger asChild>{text}</TooltipTrigger>
          <TooltipContent label={status.reason} />
        </Tooltip>
      </TooltipProvider>
    ) : (
      text
    )
  }
  return (
    <Tag
      size="small"
      variant={status.tone}
      modifier="ghost"
      label={status.label}
      data-chip="status"
      data-status={status.id}
    />
  )
}

const INDEX_FIX = /^Add an index on ([^\s(]+)\s*\((.+)\)\.?$/i

/** An index suggestion reads as code: the action, then the index in mono. */
function ReasonText({ text }: { text: string }) {
  const index = INDEX_FIX.exec(text)
  if (!index) return text
  return (
    <>
      <span className="text-caption text-content-layout-3">Add index </span>
      <code className="font-mono text-mono-small text-content-layout-1">
        {index[1]} ({index[2]})
      </code>
    </>
  )
}

const GRID =
  'grid-cols-[2.75rem_minmax(0,1fr)_auto] laptop:grid-cols-[2.75rem_minmax(0,1fr)_4.5rem_5rem_minmax(7rem,9rem)_2rem]'

/** Column names once above the rows, so each row carries only numbers (laptop and up). */
export function LegendHeader() {
  return (
    <div
      aria-hidden="true"
      className={cn(
        'hidden items-end gap-x-3 border border-transparent pr-3 pl-3 text-caption text-content-layout-3 laptop:grid tablet:pl-4',
        GRID
      )}
    >
      <span>Score</span>
      <span>Query</span>
      <span className="text-right">Calls/s</span>
      <span className="text-right">Avg latency</span>
      <span className="text-right">Share of time</span>
      <span />
    </div>
  )
}

export interface LegendRowProps {
  query: MonitorQuery
  color: string
  /** The query's one Status and its reason. */
  status: QueryStatus
  open: boolean
  /** Raised from the chart (band hovered or focused). */
  raised: boolean
  /** Another query is raised: this row steps back. */
  dimmed: boolean
  onToggle: (key: string) => void
  onHover: (key: string | null) => void
  /** "Analyze now" for a query RDST can analyze; it goes to the front of the queue. */
  onAnalyze?: () => void
  /** Queued, analyzing or classifying; replaces "Analyze now" while it lasts. */
  activity?: RowActivity | null
  /** What the classification found ("Missing index"), shown on the row at all times. */
  finding?: string | null
  /** The classification found no risk: the chip steps back. */
  findingClear?: boolean
  /** Opens the row on its deep analysis. */
  onOpenAnalysis?: () => void
  /** Brought into view from the Analyses list: outlined for a moment. */
  highlighted?: boolean
  expansion?: ReactNode
}

function LegendRowImpl({
  query,
  color,
  status,
  open,
  raised,
  dimmed,
  onToggle,
  onHover,
  onAnalyze,
  activity = null,
  finding = null,
  findingClear = false,
  onOpenAnalysis,
  highlighted = false,
  expansion,
}: LegendRowProps) {
  const reduced = useReducedMotion()
  const name = queryName(query)
  const score = rowScore(query)
  const share = Math.max(0, Math.min(1, query.db_time_share))
  const latency = formatMsWithUnit(query.mean_ms)
  const missing = query.text_status === 'unavailable' || !query.text.trim()
  const reasonLine = status.id === 'not_analyzed' ? null : status.reason
  const slot = score ? null : scoreSlot(activity)
  const inline = inlineActivity(activity, Boolean(score))
  const waiting =
    activity?.kind === 'running' || activity?.kind === 'queued'
      ? activity.label
      : null

  return (
    <m.div
      layout={false}
      // A row that ranks in fades in place; one that ranks out leaves at once, so the
      // rows below never shift twice.
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2, ease: 'easeOut' }}
      // Rows scrolling under a still pointer don't change the hover; moving it does.
      onPointerEnter={(event) => {
        if (!fromScroll(event)) onHover(query.key)
      }}
      onPointerMove={(event) => {
        if (!raised && !fromScroll(event)) onHover(query.key)
      }}
      onPointerLeave={(event) => {
        if (!fromScroll(event)) onHover(null)
      }}
      id={`query-${query.key}`}
      data-testid="monitor-query-row"
      data-query-hash={query.key}
      data-raised={raised || undefined}
      data-highlighted={highlighted || undefined}
      className={cn(
        'group/row relative scroll-mt-24 overflow-hidden rounded-panel border bg-surface-layout-1 transition-[border-color,box-shadow,opacity] duration-slow ease-base',
        highlighted
          ? 'border-border-primary-soft shadow-elevation-2 ring-2 ring-border-primary-soft'
          : open
            ? 'border-border-layout-2 shadow-elevation-2'
            : raised
              ? 'border-border-layout-2 shadow-elevation-1'
              : 'border-border-layout-1 hover:border-border-layout-2',
        dimmed && !open && 'opacity-60'
      )}
    >
      {/* biome-ignore lint/a11y/noStaticElementInteractions: the row is a large target; the Details button is the keyboard control */}
      {/* biome-ignore lint/a11y/useKeyWithClickEvents: the Details button is the keyboard control */}
      <div
        onClick={(event) => {
          if (
            (event.target as HTMLElement).closest(
              'button, a, input, [role=checkbox], [data-no-toggle]'
            )
          )
            return
          onToggle(query.key)
        }}
        className={cn(
          'grid cursor-pointer items-center gap-x-3 gap-y-2 py-3 pr-3 pl-3 transition-colors duration-fast ease-base hover:bg-surface-layout-2/40 tablet:pl-4',
          GRID
        )}
      >
        <span className="flex self-start pt-0.5">
          {score ? (
            <GradeTile score={score} onOpen={onOpenAnalysis} />
          ) : slot ? (
            <GradeTileActivity {...slot} />
          ) : (
            <GradeTilePlaceholder
              label={noScoreLine(query, analyzability(query), waiting)}
              busy={activity?.kind === 'running'}
            />
          )}
        </span>

        <div className="min-w-0 space-y-1">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <span className="flex min-w-0 max-w-full items-center gap-2">
              <span
                aria-hidden="true"
                data-swatch
                className={cn(
                  'inline-block h-2.5 w-2.5 shrink-0 rounded-full transition-transform duration-fast ease-base',
                  (raised || open) && 'scale-125'
                )}
                style={{
                  background: color,
                  boxShadow:
                    raised || open
                      ? `0 0 0 3px color-mix(in oklab, ${color} 25%, transparent)`
                      : undefined,
                }}
              />
              <span
                className={cn(
                  'block min-w-0 max-w-full truncate text-label-large font-semibold text-content-layout-1 transition-colors duration-fast group-hover/row:text-content-primary-soft',
                  missing && 'italic text-content-layout-2'
                )}
                title={name}
              >
                {name}
              </span>
            </span>
            <StatusMark
              status={status}
              quiet={Boolean(activity || onAnalyze)}
            />
            {finding &&
            (status.id === 'healthy' || status.id === 'not_analyzed') ? (
              <FindingChip label={finding} clear={findingClear} />
            ) : null}
            {score ? (
              inline ? (
                <ActivityMark activity={inline} />
              ) : null
            ) : (
              <RowAction
                activity={inline}
                onAnalyze={slot ? undefined : onAnalyze}
              />
            )}
          </div>
          {reasonLine ? (
            <p
              className="line-clamp-2 text-body-small text-content-layout-1 laptop:line-clamp-1"
              data-testid="row-reason"
              title={reasonLine}
            >
              <ReasonText text={reasonLine} />
            </p>
          ) : null}
          {missing ? (
            <p className="truncate text-caption text-content-layout-3">
              {query.text_reason ??
                "The database didn't keep this statement's text."}
            </p>
          ) : reasonLine ? null : (
            <code className="block truncate font-mono text-mono-small text-content-layout-3">
              {query.text}
            </code>
          )}
        </div>

        <div className="col-span-2 col-start-2 flex items-center gap-4 laptop:contents">
          <span className="flex flex-col items-end tabular-nums max-laptop:items-start">
            <span className="text-label-medium text-content-layout-1">
              {formatQps(query.qps)}
            </span>
            <span className="text-caption text-content-layout-3 laptop:hidden">
              calls/s
            </span>
          </span>
          <span className="flex flex-col items-end tabular-nums max-laptop:items-start">
            <span className="text-label-medium text-content-layout-1">
              {latency.value}
              <span className="ml-0.5 text-caption text-content-layout-3">
                {latency.unit}
              </span>
            </span>
            <span className="text-caption text-content-layout-3 laptop:hidden">
              avg
            </span>
          </span>
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <div
              className="relative h-1.5 min-w-10 flex-1 overflow-hidden rounded-pill bg-surface-layout-2"
              aria-hidden="true"
            >
              <div
                className="absolute inset-y-0 left-0 rounded-pill transition-[width] duration-slow ease-out"
                style={{
                  width: `${Math.max(share * 100, share > 0 ? 2 : 0)}%`,
                  background: color,
                }}
              />
            </div>
            <span className="w-11 shrink-0 text-right text-label-medium font-semibold tabular-nums text-content-layout-1">
              {formatShare(share)}
              <span className="sr-only"> of database time</span>
            </span>
          </div>
        </div>

        <Pressable
          aria-label="Details"
          aria-expanded={open}
          onClick={() => onToggle(query.key)}
          className={cn(
            'col-start-3 row-start-1 inline-flex h-8 w-8 items-center justify-center self-start rounded-control text-content-layout-2 transition-colors duration-fast hover:bg-surface-layout-2 hover:text-content-layout-1 focus-visible:shadow-focus focus-visible:outline-none laptop:col-start-auto laptop:row-start-auto laptop:self-center'
          )}
        >
          <Icon
            name="chevron-down"
            label=""
            aria-hidden="true"
            className={cn(
              'h-4 w-4 transition-transform duration-normal ease-out',
              open && 'rotate-180'
            )}
          />
        </Pressable>
      </div>
      <AnimatePresence initial={false}>
        {expansion ? (
          <m.div
            key="detail"
            initial={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
            animate={reduced ? { opacity: 1 } : { height: 'auto', opacity: 1 }}
            exit={reduced ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="border-t border-border-layout-1 bg-surface-layout-2/30 px-4 pt-4 pb-5 tablet:px-5">
              {expansion}
            </div>
          </m.div>
        ) : null}
      </AnimatePresence>
    </m.div>
  )
}

export const LegendRow = memo(LegendRowImpl)
