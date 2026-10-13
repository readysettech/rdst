// One "Analyze database" run per target, shared by every consumer (the Reports band,
// the header button, the report page) so starting it anywhere shows it everywhere.
import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useSyncExternalStore } from 'react'
import {
  applyRunEvent,
  emptyRunView,
  type RunView,
} from '@/features/database/analyze/runModel'
import {
  cancelReport,
  MonitorApiError,
  startReport,
  subscribeReportEvents,
} from '@/lib/monitor/api'
import { useReport } from '@/lib/monitor/hooks'
import { monitorKeys } from '@/lib/monitor/keys'

export type ReportRunStatus =
  | 'idle'
  | 'starting'
  | 'running'
  | 'cancelling'
  | 'done'
  | 'failed'
  | 'cancelled'

export interface ReportRunSnapshot {
  status: ReportRunStatus
  reportId: number | null
  view: RunView | null
  /** Why starting failed (409 when monitoring is off, network, ...). */
  error: string | null
  /** ms timestamp of the last status change, for one-shot transitions. */
  changedAt: number
}

const IDLE: ReportRunSnapshot = {
  status: 'idle',
  reportId: null,
  view: null,
  error: null,
  changedAt: 0,
}

interface Entry {
  snapshot: ReportRunSnapshot
  unsubscribe: (() => void) | null
  listeners: Set<() => void>
}

const entries = new Map<string, Entry>()

function entry(target: string): Entry {
  let found = entries.get(target)
  if (!found) {
    found = { snapshot: IDLE, unsubscribe: null, listeners: new Set() }
    entries.set(target, found)
  }
  return found
}

function update(target: string, patch: Partial<ReportRunSnapshot>) {
  const current = entry(target)
  const statusChanged =
    patch.status !== undefined && patch.status !== current.snapshot.status
  current.snapshot = {
    ...current.snapshot,
    ...patch,
    changedAt: statusChanged ? Date.now() : current.snapshot.changedAt,
  }
  for (const listener of current.listeners) listener()
}

function follow(target: string, reportId: number, onEnd: () => void) {
  const current = entry(target)
  current.unsubscribe?.()
  update(target, {
    status: 'running',
    reportId,
    view: emptyRunView(),
    error: null,
  })
  current.unsubscribe = subscribeReportEvents(target, reportId, (event) => {
    const live = entry(target)
    if (live.snapshot.reportId !== reportId || !live.snapshot.view) return
    const view = applyRunEvent(live.snapshot.view, event)
    if (event.type === 'run' && event.state !== 'running') {
      update(target, { view, status: event.state })
      live.unsubscribe = null
      onEnd()
    } else {
      update(target, { view })
    }
  })
}

/** Test hook: forget every run and stop every stream. */
export function resetReportRuns() {
  for (const current of entries.values()) current.unsubscribe?.()
  entries.clear()
}

export interface ReportRun extends ReportRunSnapshot {
  /** True from the click until the run ends (starting, running, cancelling). */
  active: boolean
  start: () => Promise<void>
  cancel: () => Promise<void>
  /** Return to idle after a finished run has been shown. */
  dismiss: () => void
}

/**
 * The target's "Analyze database" run. Attaches to a run already in progress (a
 * nightly, or one started in another tab) when the latest report is still running.
 */
export function useReportRun(target: string): ReportRun {
  const client = useQueryClient()
  const snapshot = useSyncExternalStore(
    useCallback(
      (listener: () => void) => {
        const current = entry(target)
        current.listeners.add(listener)
        return () => current.listeners.delete(listener)
      },
      [target]
    ),
    () => (target ? entry(target).snapshot : IDLE),
    () => IDLE
  )

  const refresh = useCallback(
    () => client.invalidateQueries({ queryKey: monitorKeys.reports(target) }),
    [client, target]
  )

  const latest = useReport(target, 'latest')
  const latestRunningId =
    latest.data?.state === 'running' ? latest.data.id : null
  useEffect(() => {
    if (!target || latestRunningId === null) return
    const current = entry(target).snapshot
    if (current.status === 'idle' || current.reportId === null)
      follow(target, latestRunningId, refresh)
  }, [target, latestRunningId, refresh])

  const start = useCallback(async () => {
    if (!target) return
    const current = entry(target).snapshot
    if (['starting', 'running', 'cancelling'].includes(current.status)) return
    update(target, { status: 'starting', error: null, view: emptyRunView() })
    try {
      const report = await startReport(target)
      follow(target, report.id, refresh)
      void refresh()
    } catch (error) {
      update(target, {
        status: 'idle',
        view: null,
        error:
          error instanceof MonitorApiError && error.status === 409
            ? 'Turn on monitoring to analyze this database.'
            : "The analysis couldn't start. Check that RDST is running.",
      })
    }
  }, [target, refresh])

  const cancel = useCallback(async () => {
    const current = entry(target).snapshot
    if (current.reportId === null || current.status !== 'running') return
    update(target, { status: 'cancelling' })
    try {
      await cancelReport(target, current.reportId)
    } catch {
      // The stream still reports how the run ended.
    }
  }, [target])

  const dismiss = useCallback(() => {
    const current = entry(target)
    if (['starting', 'running', 'cancelling'].includes(current.snapshot.status))
      return
    update(target, { ...IDLE })
  }, [target])

  return {
    ...snapshot,
    active: ['starting', 'running', 'cancelling'].includes(snapshot.status),
    start,
    cancel,
    dismiss,
  }
}
