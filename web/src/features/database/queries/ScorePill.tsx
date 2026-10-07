// The deep-analysis score on a query row (owner review round 3): a small tile with the
// number out of 100, tinted on its own quiet red, amber and green scale so it never
// matches a chart band. A tile, never a gauge, so it can't read as progress. A query
// without a score gets a dashed tile of the same size that says why on hover.
import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Pressable } from '@rs/ui-new/pressable'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import type { ReactNode } from 'react'
import type { MonitorQuery } from '@/lib/monitor/types'
import './scoreActivity.css'

const RATING_SCORE: Record<string, number> = {
  excellent: 95,
  good: 80,
  fair: 60,
  poor: 35,
}

export interface RowScore {
  /** 0-100 when the analysis carries a score. */
  score: number | null
  rating: string | null
}

/** The analysis's score or rating; null until it has one, so a row never says "Analyzed"
 *  over results that are still being written. */
export function rowScore(query: MonitorQuery): RowScore | null {
  const summary = query.last_analysis?.summary
  const rating = summary?.rating?.toLowerCase() ?? null
  const score =
    typeof summary?.efficiency_score === 'number'
      ? Math.round(summary.efficiency_score)
      : null
  return score == null && !rating ? null : { score, rating }
}

/** The score the tile shows: the number, else the rating's midpoint. */
export function scoreValue(score: RowScore): number | null {
  if (score.score != null) return score.score
  return score.rating ? (RATING_SCORE[score.rating] ?? null) : null
}

/** The tile's own three-step scale, quiet tints so it never reads as a chart band. */
export type ScoreBand = 'good' | 'fair' | 'poor'

export function scoreBand(score: number): ScoreBand {
  if (score >= 70) return 'good'
  if (score >= 40) return 'fair'
  return 'poor'
}

const BAND_CLASS: Record<ScoreBand, string> = {
  good: 'bg-surface-positive-soft text-content-positive-soft border-border-positive-soft',
  fair: 'bg-surface-warning-soft text-content-warning-soft border-border-warning-soft',
  poor: 'bg-surface-negative-soft text-content-negative-soft border-border-negative-soft',
}

const TILE =
  'relative inline-flex h-10 w-11 shrink-0 select-none flex-col items-center justify-center rounded-control border leading-none'

function OneLineTip({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <TooltipProvider delayDuration={120}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent label={label} />
      </Tooltip>
    </TooltipProvider>
  )
}

export function gradeLabel(score: RowScore): string {
  const value = scoreValue(score)
  if (score.score != null) return `Score ${score.score} of 100`
  if (score.rating) return `Rated ${score.rating}`
  return value == null ? 'Analyzed' : `Score ${value} of 100`
}

/** The deep-analysis score as a number out of 100, tinted by its own quiet scale. */
export function GradeTile({
  score,
  onOpen,
}: {
  score: RowScore
  onOpen?: () => void
}) {
  const value = scoreValue(score)
  const band = value == null ? null : scoreBand(value)
  const label = gradeLabel(score)
  return (
    <OneLineTip label={`${label}. Open the analysis`}>
      <Pressable
        onClick={onOpen}
        aria-label={`${label}. Open the analysis`}
        data-chip="grade"
        data-band={band ?? undefined}
        data-score={score.score ?? undefined}
        className={cn(
          TILE,
          'gap-0.5 transition-[transform,filter] duration-fast ease-base hover:brightness-110 focus-visible:shadow-focus focus-visible:outline-none active:scale-95',
          band
            ? BAND_CLASS[band]
            : 'border-border-layout-1 bg-surface-layout-2 text-content-layout-2'
        )}
      >
        {value == null ? (
          <Icon name="tick" label="" aria-hidden="true" className="h-4 w-4" />
        ) : (
          <>
            <span className="text-[15px] font-semibold tabular-nums">
              {value}
            </span>
            <span className="text-[10px] tabular-nums opacity-70">/100</span>
          </>
        )}
      </Pressable>
    </OneLineTip>
  )
}

/** The same footprint as GradeTile for a query without a score; the hover says why. */
export function GradeTilePlaceholder({
  label,
  busy = false,
}: {
  /** One line on hover ("No score: RDST doesn't run UPDATE statements"), or none. */
  label: string | null
  busy?: boolean
}) {
  const tile = (
    <span
      role="img"
      tabIndex={label ? 0 : undefined}
      aria-label={label ?? 'No score yet'}
      data-chip="grade-placeholder"
      className={cn(
        TILE,
        'border-dashed text-[10px] font-medium text-content-layout-3 focus-visible:shadow-focus focus-visible:outline-none',
        busy
          ? 'border-border-primary-soft motion-safe:animate-pulse'
          : 'border-content-layout-3/45'
      )}
    >
      <span aria-hidden="true">No</span>
      <span aria-hidden="true">score</span>
    </span>
  )
  return label ? <OneLineTip label={label}>{tile}</OneLineTip> : tile
}

/**
 * The score column for a query RDST is working on: "Queued" with its place, "Analyzing"
 * with an arc tracing the tile, or "Retrying". Same footprint as the score tile.
 */
export function GradeTileActivity({
  kind,
  word,
  detail,
  title,
}: {
  kind: 'queued' | 'running' | 'retrying'
  word: string
  detail: string | null
  title: string
}) {
  const tile = (
    <span
      role="img"
      tabIndex={title ? 0 : undefined}
      aria-label={title}
      data-chip="grade-activity"
      data-activity={kind}
      className={cn(
        TILE,
        'gap-0.5 overflow-visible text-[9px] font-medium focus-visible:shadow-focus focus-visible:outline-none',
        kind === 'running'
          ? 'border-border-primary-soft/40 bg-surface-primary-soft/40 text-content-primary-soft'
          : kind === 'retrying'
            ? 'rdst-score-breathe border-dashed border-border-warning-soft text-content-warning-soft'
            : 'rdst-score-queued border-dashed border-border-primary-soft text-content-layout-2'
      )}
    >
      {kind === 'running' ? (
        <svg
          aria-hidden="true"
          className="pointer-events-none absolute -inset-px h-[calc(100%+2px)] w-[calc(100%+2px)] overflow-visible"
        >
          <rect
            x="0.75"
            y="0.75"
            style={{
              width: 'calc(100% - 1.5px)',
              height: 'calc(100% - 1.5px)',
            }}
            rx="5"
            pathLength={100}
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            className="rdst-score-trace"
          />
        </svg>
      ) : null}
      <span
        aria-hidden="true"
        className={cn(
          'leading-none tracking-tight',
          word.length > 7 && 'text-[8px]'
        )}
      >
        {word}
        {detail ? ', ' : ''}
      </span>
      {detail ? (
        <span
          aria-hidden="true"
          className="text-[11px] font-semibold leading-none tabular-nums"
        >
          {detail}
        </span>
      ) : null}
    </span>
  )
  return <OneLineTip label={title}>{tile}</OneLineTip>
}
