// The five stations of the Analyze database run view (DESIGN_V4 section 4), and one
// honest overall progress figure. Pure; derived from the folded progress stream.
import type { StepState } from '@/lib/monitor/types'
import { type RunView, runCounts } from './runModel'

export type StationId =
  | 'history'
  | 'select'
  | 'analyses'
  | 'health'
  | 'narrative'

export interface Station {
  id: StationId
  label: string
  state: StepState
  detail: string
}

const LABEL: Record<StationId, string> = {
  history: 'Read 24 h of history',
  select: 'Pick the top queries',
  analyses: 'Analyze each one',
  health: 'Check health',
  narrative: 'Write the summary',
}

// Share of the bar each station owns. Analyses dominate a real run.
const WEIGHT: Record<StationId, number> = {
  history: 5,
  select: 10,
  analyses: 60,
  health: 10,
  narrative: 15,
}

/** Backends that don't stream the history step read it as part of picking. */
function historyState(view: RunView): StepState {
  if (view.historyStreamed) return view.steps.history.state
  const select = view.steps.select.state
  if (select === 'queued') return view.state === 'running' ? 'running' : select
  return select === 'running' ? 'running' : 'done'
}

export function stations(view: RunView): Station[] {
  const counts = runCounts(view)
  const history = historyState(view)
  const detail: Record<StationId, string> = {
    history:
      view.steps.history.detail ||
      (history === 'done' ? 'Readings loaded' : 'Reading'),
    select: counts.total
      ? `${counts.total} ${counts.total === 1 ? 'query' : 'queries'} picked`
      : view.steps.select.detail,
    analyses: counts.total
      ? `${counts.finished} of ${counts.total}`
      : view.steps.analyses.detail,
    health: view.healthChecks.length
      ? `${view.healthChecks.filter((check) => finished(check.state)).length} of ${healthTotal(view)}`
      : view.steps.health.detail,
    narrative: view.steps.narrative.detail,
  }
  return (Object.keys(LABEL) as StationId[]).map((id) => ({
    id,
    label: LABEL[id],
    state: id === 'history' ? history : view.steps[id].state,
    detail: detail[id],
  }))
}

/** Checks announce themselves one by one; the step detail ('0 of 6') has the total. */
function healthTotal(view: RunView): number {
  const match = /of (\d+)/.exec(view.steps.health.detail)
  return Math.max(view.healthChecks.length, match ? Number(match[1]) : 0, 1)
}

const finished = (state: StepState) =>
  state === 'done' || state === 'skipped' || state === 'failed'

/** 0..1. Each finished station counts fully; analyses count query by query. */
export function overallProgress(view: RunView): number {
  if (view.state === 'done') return 1
  const counts = runCounts(view)
  let total = 0
  for (const station of stations(view)) {
    const weight = WEIGHT[station.id]
    if (finished(station.state)) {
      total += weight
      continue
    }
    let fraction = station.state === 'running' ? 0.15 : 0
    if (station.id === 'analyses' && counts.total)
      fraction = Math.max(fraction, counts.finished / counts.total)
    if (station.id === 'health' && view.healthChecks.length)
      fraction = Math.max(
        fraction,
        view.healthChecks.filter((check) => finished(check.state)).length /
          healthTotal(view)
      )
    total += weight * fraction
  }
  return Math.min(0.99, total / 100)
}

/** The station the run is on now (the first one not finished). */
export function currentStation(view: RunView): StationId | null {
  if (view.state !== 'running') return null
  return stations(view).find((station) => !finished(station.state))?.id ?? null
}
