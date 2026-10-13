// Home reads the one payload (`GET /api/monitor/home`) through the contract's
// hook; this is the thin selector and the view helpers over it.
import type { StatusLineTone } from '@rs/ui-new/status-line'
import type { components } from '../../lib/api.generated'
import { useHome } from '../../lib/monitor/hooks'
import type {
  AnalysisOutcome,
  HomeDayCard,
  HomeModel,
  HomeStatus,
  HomeStripRow,
  MonitorLink,
} from '../../lib/monitor/types'

export type { HomeModel }

export type AppliedTotals = components['schemas']['AppliedTotals']

/** Home for the chosen database (the day card follows it), or for every database. */
export function useHomeModel(db: string | null) {
  return useHome(db ?? undefined)
}

/** "3 changes applied this week saved 41% of database time", or null with nothing to say. */
export function appliedLine(totals: AppliedTotals | null | undefined) {
  if (!totals || totals.changes_this_week <= 0) return null
  const count = totals.changes_this_week
  const head = `${count} ${count === 1 ? 'change' : 'changes'} applied this week`
  return totals.saved_share != null && totals.saved_share > 0
    ? `${head} saved ${Math.round(totals.saved_share * 100)}% of database time`
    : head
}

/** Every database's counts in attention order; older payloads derive them from the list. */
export function stripRows(model: HomeModel): HomeStripRow[] {
  if (model.databases_strip?.length) return model.databases_strip
  return model.databases.map((row) => ({
    target: row.target,
    state: row.state,
    act_now: row.act_now,
    to_do: row.to_do ?? 0,
    do_first: row.do_first ?? 0,
    fixes_ready: row.fixes_ready,
  }))
}

/** The database to start the Do first changes on: the most of them, ties in strip order. */
export function doFirstTarget(model: HomeModel): string | null {
  let best: HomeStripRow | null = null
  for (const row of stripRows(model))
    if (row.do_first > 0 && (!best || row.do_first > best.do_first)) best = row
  return best?.target ?? model.status.target
}

/** No database yet: nothing is monitored and nothing is configured. */
export function isEmptyHome(model: HomeModel): boolean {
  return (
    model.status.state === 'off' &&
    model.status.target === null &&
    model.databases.length === 0
  )
}

export function homeTone(status: HomeStatus): StatusLineTone {
  if (status.state === 'cant_reach') return 'negative'
  if (status.state === 'paused') return 'warning'
  if (status.state === 'off') return 'off'
  return 'live'
}

export interface RouteLink {
  to: string
  search?: Record<string, unknown>
}

/** Where a Home row leads: the destination the backend named, as a route. */
export function monitorLinkTo(link: MonitorLink): RouteLink {
  const target = link.target ? { target: link.target } : {}
  switch (link.kind) {
    case 'actions':
      return {
        to: '/actions',
        search: link.target ? { db: link.target } : undefined,
      }
    case 'inbox':
      return {
        to: '/inbox',
        search: link.item_id == null ? undefined : { item: link.item_id },
      }
    case 'query':
      return { to: '/database', search: { ...target, query: link.query_key } }
    case 'analysis':
      return {
        to: '/database',
        search: { ...target, query: link.query_key, tab: 'analysis' },
      }
    case 'report':
      return {
        to: '/database',
        search: {
          ...target,
          view: 'report',
          ...(link.report_id == null ? {} : { report: String(link.report_id) }),
        },
      }
    case 'settings':
      return { to: '/configure' }
    case 'sign_in':
      return { to: '/account-login' }
    default:
      return { to: '/database', search: link.target ? target : undefined }
  }
}

/** "same as yesterday", "faster than yesterday", "slower than yesterday". */
export function latencyWord(delta: number | null): string | null {
  if (delta == null) return null
  if (delta < -0.05) return 'faster than yesterday'
  if (delta > 0.05) return 'slower than yesterday'
  return 'same as yesterday'
}

/** "today", "yesterday", "2 days ago", by calendar day. */
export function relativeDay(ts: number, now: number): string {
  const day = (value: number) => {
    const date = new Date(value * 1000)
    return new Date(
      date.getFullYear(),
      date.getMonth(),
      date.getDate()
    ).getTime()
  }
  const days = Math.round((day(now) - day(ts)) / 86_400_000)
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  return `${days} days ago`
}

export const OUTCOME_WORD: Record<AnalysisOutcome, string> = {
  fix_suggested: 'fix suggested',
  healthy: 'healthy',
  failed: 'failed',
}

/** "Oct 8" from the report's day. */
export function reportDayLabel(day: string): string {
  const [year, month, date] = day.split('-').map(Number)
  return new Date(year, month - 1, date).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  })
}

/** The report link under the card title, when the day has one. */
export function reportLink(day: HomeDayCard): RouteLink | null {
  if (day.report_id == null) return null
  return {
    to: '/database',
    search: {
      target: day.target,
      view: 'report',
      report: String(day.report_id),
    },
  }
}
