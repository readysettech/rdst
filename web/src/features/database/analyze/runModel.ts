// Folds the report-progress stream (brief 5.8) into one view of an "Analyze database"
// run. Pure, so the progress panel, the header button and the tests share one model.
import type {
  ReportEvent,
  ReportRunState,
  ReportSectionName,
  ReportStep,
  StepState,
} from '@/lib/monitor/types'

export type AnalysisRowState = StepState | 'provisional'

/** Steps of a run; older backends never send `history`. */
export type RunStep = ReportStep

export interface AnalysisRow {
  key: string
  displayName: string
  dbTimeShare: number
  state: AnalysisRowState
  reason: string | null
  resultLine: string | null
  durationS: number | null
  /** What the analyzer is doing now ('plan', 'run', 'advise'), when streamed. */
  phase: string | null
  /** Short verdict ("Needs an index on orders(total)"), when streamed. */
  headline: string | null
}

export interface HealthCheckRow {
  check: string
  label: string
  state: StepState
}

export interface StepView {
  state: StepState
  detail: string
  /** ms when the step started running and when it finished (null until then). */
  startedAtMs: number | null
  endedAtMs: number | null
}

export interface RunView {
  state: ReportRunState
  startedAt: number | null
  /** Seconds elapsed at the last run event, and when (ms) that event arrived. */
  elapsedS: number
  elapsedAtMs: number
  steps: Record<RunStep, StepView>
  /** True once the stream reported the history step itself. */
  historyStreamed: boolean
  queries: AnalysisRow[]
  healthChecks: HealthCheckRow[]
  sections: Partial<Record<ReportSectionName, 'writing' | 'ready'>>
  budgetMessage: string | null
  /** The summary as the backend streams it (empty when it doesn't stream). */
  narrative: string
}

export const REPORT_STEPS: readonly ReportStep[] = [
  'select',
  'analyses',
  'health',
  'narrative',
]

export const REPORT_SECTIONS: readonly ReportSectionName[] = [
  'summary',
  'fix_first',
  'one_fix_several_queries',
  'health',
  'changed',
]

const queuedStep = (): StepView => ({
  state: 'queued',
  detail: '',
  startedAtMs: null,
  endedAtMs: null,
})

export function emptyRunView(nowMs = Date.now()): RunView {
  return {
    state: 'running',
    startedAt: null,
    elapsedS: 0,
    elapsedAtMs: nowMs,
    steps: {
      history: queuedStep(),
      select: queuedStep(),
      analyses: queuedStep(),
      health: queuedStep(),
      narrative: queuedStep(),
    },
    historyStreamed: false,
    queries: [],
    healthChecks: [],
    sections: {},
    budgetMessage: null,
    narrative: '',
  }
}

const TERMINAL: readonly AnalysisRowState[] = [
  'done',
  'skipped',
  'failed',
  'provisional',
]

export function isTerminalRow(state: AnalysisRowState) {
  return TERMINAL.includes(state)
}

export function applyRunEvent(
  view: RunView,
  event: ReportEvent,
  nowMs = Date.now()
): RunView {
  switch (event.type) {
    case 'narrative':
      return { ...view, narrative: view.narrative + event.delta }
    case 'run':
      return {
        ...view,
        state: event.state,
        startedAt: event.started_at,
        elapsedS: Math.max(view.elapsedS, event.elapsed_s),
        elapsedAtMs: nowMs,
      }
    case 'step': {
      const step = event.step as RunStep
      const previous = view.steps[step] ?? queuedStep()
      const running = event.state === 'running'
      const ended = event.state !== 'running' && event.state !== 'queued'
      return {
        ...view,
        historyStreamed: view.historyStreamed || step === 'history',
        steps: {
          ...view.steps,
          [step]: {
            state: event.state,
            detail: event.detail,
            startedAtMs:
              previous.startedAtMs ?? (running || ended ? nowMs : null),
            endedAtMs: ended ? (previous.endedAtMs ?? nowMs) : null,
          },
        },
      }
    }
    case 'selected': {
      const known = new Map(view.queries.map((row) => [row.key, row]))
      return {
        ...view,
        queries: event.queries.map(
          (query) =>
            known.get(query.key) ?? {
              key: query.key,
              displayName: query.display_name,
              dbTimeShare: query.db_time_share,
              state: 'queued',
              reason: null,
              resultLine: null,
              durationS: null,
              phase: null,
              headline: null,
            }
        ),
      }
    }
    case 'analysis': {
      const index = view.queries.findIndex((row) => row.key === event.key)
      const previous = index >= 0 ? view.queries[index] : null
      // A late "queued" never rewinds a row that already moved on.
      if (previous && event.state === 'queued' && previous.state !== 'queued')
        return view
      const row: AnalysisRow = {
        key: event.key,
        displayName: previous?.displayName ?? event.display_name ?? event.key,
        dbTimeShare: previous?.dbTimeShare ?? 0,
        state: event.state,
        reason: event.reason,
        resultLine: event.result_line,
        durationS: event.duration_s,
        phase: event.phase ?? null,
        headline: event.headline ?? previous?.headline ?? null,
      }
      const queries = [...view.queries]
      if (index >= 0) queries[index] = row
      else queries.push(row)
      return { ...view, queries }
    }
    case 'health_check': {
      const index = view.healthChecks.findIndex(
        (row) => row.check === event.check
      )
      const row = { check: event.check, label: event.label, state: event.state }
      const healthChecks = [...view.healthChecks]
      if (index >= 0) healthChecks[index] = row
      else healthChecks.push(row)
      return { ...view, healthChecks }
    }
    case 'section':
      return {
        ...view,
        sections: { ...view.sections, [event.section]: event.state },
      }
    case 'budget':
      return { ...view, budgetMessage: event.message }
  }
}

export function foldRunEvents(
  events: readonly ReportEvent[],
  nowMs = Date.now()
): RunView {
  return events.reduce(
    (view, event) => applyRunEvent(view, event, nowMs),
    emptyRunView(nowMs)
  )
}

export interface RunCounts {
  total: number
  finished: number
  done: number
  skipped: number
  failed: number
  provisional: number
  running: number
}

export function runCounts(view: RunView): RunCounts {
  const counts: RunCounts = {
    total: view.queries.length,
    finished: 0,
    done: 0,
    skipped: 0,
    failed: 0,
    provisional: 0,
    running: 0,
  }
  for (const row of view.queries) {
    if (row.state === 'running') counts.running += 1
    else if (row.state !== 'queued') {
      counts.finished += 1
      counts[row.state] += 1
    }
  }
  return counts
}

/** Seconds since the run started, advancing between events while it runs. */
export function elapsedSeconds(view: RunView, nowMs = Date.now()) {
  if (view.state !== 'running') return view.elapsedS
  return view.elapsedS + Math.max(0, (nowMs - view.elapsedAtMs) / 1000)
}

export type RunPhase = 'starting' | 'select' | 'analyses' | 'narrative' | 'end'

/** Which step leads the headline and whether the bar can count. */
export function runPhase(view: RunView): RunPhase {
  if (view.state !== 'running') return 'end'
  if (view.steps.narrative.state === 'running') return 'narrative'
  if (view.queries.length > 0) return 'analyses'
  if (view.steps.select.state === 'running') return 'select'
  return 'starting'
}

export function activityLabel(view: RunView): string {
  const counts = runCounts(view)
  switch (runPhase(view)) {
    case 'starting':
      return 'Starting'
    case 'select':
      return 'Picking the queries with the most database time'
    case 'analyses':
      return counts.finished >= counts.total && counts.total > 0
        ? 'Finishing health checks'
        : `Analyzing top queries, ${counts.finished} of ${counts.total}`
    case 'narrative':
      return 'Writing the report'
    case 'end':
      return view.state === 'done'
        ? 'Report ready'
        : view.state === 'cancelled'
          ? 'Cancelled'
          : "The analysis didn't finish"
  }
}

/** 0-100 while analyses are countable; undefined (indeterminate) otherwise. */
export function progressPercent(view: RunView): number | undefined {
  if (view.state === 'done') return 100
  const phase = runPhase(view)
  if (phase !== 'analyses') return undefined
  const counts = runCounts(view)
  if (!counts.total) return undefined
  return Math.round((counts.finished / counts.total) * 100)
}
