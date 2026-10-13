// The working indicator: a radar mark per database (sidebar, Database page bar, Home rows)
// and the tooltip that says what monitoring is doing there now.
import { cn } from '@rs/tailwind-base'
import { focusRingClass } from '@rs/ui-new/focus'
import { useReducedMotion } from '@rs/ui-new/motion'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@rs/ui-new/tooltip'
import type { CSSProperties, ReactElement } from 'react'
import { readingClock } from '@/features/database/databaseModel'
import { serverOffset } from '@/lib/monitor/serverClock'
import type { DatabaseActivity, HomeDatabaseState } from '@/lib/monitor/types'
import { plural } from './format'
import { useNow } from './useMonitorPulse'

export type RadarState = 'watching' | 'off' | 'unreachable' | 'paused'

export const RADAR_STATE: Record<HomeDatabaseState, RadarState> = {
  watching: 'watching',
  ai_paused: 'paused',
  cant_reach: 'unreachable',
  not_monitored: 'off',
}

const STATE_WORD: Record<RadarState, string> = {
  watching: 'actively monitoring',
  paused: 'AI checks paused',
  unreachable: "can't reach",
  off: 'not monitored',
}

const WORK_KINDS = new Set(['analyzing', 'checking', 'report'])

export type RadarRowTone = 'work' | 'ok' | 'dim'

export interface RadarRow {
  tone: RadarRowTone
  text: string
}

/** "demo, actively monitoring", or "Actively monitoring" without a name. */
export function radarHead(state: RadarState, name?: string): string {
  const word = STATE_WORD[state]
  return name ? `${name}, ${word}` : word[0].toUpperCase() + word.slice(1)
}

function cadence(seconds: number): string {
  // Readings drift a few seconds around the minute.
  if (seconds >= 55 && seconds <= 65) return 'every minute'
  if (seconds % 60 === 0) return `every ${seconds / 60} minutes`
  return `every ${seconds} seconds`
}

/** The tooltip's rows under the head: what runs now, the last reading, what comes next. */
export function radarRows(
  state: RadarState,
  activity: DatabaseActivity | null | undefined,
  now: number,
  toDo?: number | null
): RadarRow[] {
  if (state === 'off' || !activity) return []
  const rows: RadarRow[] = []
  const progress = activity.total
    ? `${activity.done ?? 0} of ${activity.total}`
    : null
  if (WORK_KINDS.has(activity.kind))
    rows.push({
      tone: 'work',
      text: progress ? `${activity.label}, ${progress}` : activity.label,
    })
  else if (progress)
    rows.push({ tone: 'work', text: `Analyzed ${progress} queries` })
  if (activity.last_reading_at != null)
    rows.push({
      tone: 'ok',
      text: `Last reading ${readingClock(activity.last_reading_at)}, ${cadence(activity.every_s ?? 60)}`,
    })
  if (toDo)
    rows.push({
      tone: 'dim',
      text: `${plural(toDo, 'change')} to do in Actions`,
    })
  else if (activity.next_check_at != null && state !== 'unreachable') {
    const seconds = activity.next_check_at - now
    rows.push({
      tone: 'dim',
      text:
        seconds <= 60
          ? 'Next check in under a minute'
          : `Next check in ${Math.ceil(seconds / 60)} min`,
    })
  }
  return rows
}

const ROW_DOT: Record<RadarRowTone, string> = {
  work: 'bg-series-1',
  ok: 'bg-content-positive-plain',
  dim: 'bg-content-layout-3',
}

function RadarDetails({
  state,
  activity,
  name,
  toDo,
  note,
}: Omit<RadarTooltipProps, 'children' | 'side'>) {
  const now = useNow() + serverOffset()
  return (
    <span className="flex flex-col gap-1 text-left">
      <span className="font-medium">{radarHead(state, name)}</span>
      {radarRows(state, activity, now, toDo).map((row) => (
        <span key={row.text} className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className={cn(
              'h-1.5 w-1.5 shrink-0 rounded-full',
              ROW_DOT[row.tone]
            )}
          />
          {row.text}
        </span>
      ))}
      {note && <span className="text-content-layout-3">{note}</span>}
    </span>
  )
}

export interface RadarTooltipProps {
  state: RadarState
  activity?: DatabaseActivity | null
  /** The database the head names. */
  name?: string
  /** Changes to do in Actions, for the last row. */
  toDo?: number | null
  /** One line under the rows: what the state waits on ("Sign in to run AI checks."). */
  note?: string | null
  side?: 'top' | 'right' | 'bottom' | 'left'
  /** The element hovering or focusing opens the tooltip. */
  children: ReactElement
}

/** The radar's tooltip around any trigger: the mark itself or the row that holds it. */
export function RadarTooltip({
  side = 'bottom',
  children,
  ...details
}: RadarTooltipProps) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent
          label={radarHead(details.state, details.name)}
          side={side}
          align={side === 'right' ? 'center' : 'start'}
          className="whitespace-nowrap"
        >
          <RadarDetails {...details} />
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

/** Each mark sweeps on its own clock: a stable offset from the database name. */
function sweepDelay(name: string | undefined): string {
  let hash = 0
  for (const char of name ?? '') hash = (hash * 31 + char.charCodeAt(0)) % 64
  return `${-hash / 10}s`
}

export interface RadarProps extends Omit<RadarTooltipProps, 'children'> {
  size?: 'small' | 'large'
  /** False when a parent row carries the tooltip. */
  tooltip?: boolean
  /** The mark takes focus and names itself; leave off inside a link or button. */
  label?: string
  className?: string
}

export function Radar({
  state,
  activity,
  name,
  toDo,
  note,
  side,
  size = 'small',
  tooltip = true,
  label,
  className,
}: RadarProps) {
  const reduced = useReducedMotion()
  const live = state === 'watching' || state === 'paused'
  const progress =
    live && activity?.total
      ? Math.min(1, (activity.done ?? 0) / activity.total)
      : null
  const mark = (
    <span
      className={cn(
        'rdst-radar',
        live && !reduced && 'rdst-radar-sweeping',
        label && cn('rounded-full', focusRingClass),
        className
      )}
      data-size={size}
      data-state={state}
      data-testid="radar"
      style={
        {
          '--radar-delay': sweepDelay(name),
          '--radar-fill': progress == null ? undefined : `${progress * 360}deg`,
        } as CSSProperties
      }
      {...(label
        ? { role: 'img', 'aria-label': label, tabIndex: 0 }
        : { 'aria-hidden': true })}
    >
      <span className="rdst-radar-ring" />
      {live && <span className="rdst-radar-sweep" />}
      {progress != null && (
        <span className="rdst-radar-fill" data-testid="radar-fill" />
      )}
      <span className="rdst-radar-dot" />
      {live && (
        <>
          <span
            key={activity?.last_reading_at ?? 0}
            className="rdst-radar-blip"
            data-blip="1"
          />
          <span className="rdst-radar-blip" data-blip="2" />
          <span className="rdst-radar-blip" data-blip="3" />
        </>
      )}
    </span>
  )
  if (!tooltip) return mark
  return (
    <RadarTooltip
      state={state}
      activity={activity}
      name={name}
      toDo={toDo}
      note={note}
      side={side}
    >
      {mark}
    </RadarTooltip>
  )
}
