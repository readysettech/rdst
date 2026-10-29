// The work in progress at the top of Activity, each run with its Cancel: the analysis the
// query pane cancels, the report the Report tab cancels, and the runs this browser started.
import { Button } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { Text } from '@rs/ui-new/text'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { useReportRun } from '@/features/database/reports/useReportRun'
import { agentActivityKeys } from '@/lib/agentActivity'
import {
  type BackgroundRunKind,
  type BackgroundRunState,
  cancelBackgroundRun,
  useBackgroundRuns,
} from '@/lib/backgroundRuns'
import { useAnalyzeJobs, useCancelAnalyzeJob } from '@/lib/monitor/hooks'

/** Runs this browser started that are still going. */
export const ACTIVE_RUN = new Set(['running', 'reconnecting', 'stopping'])

/** A run's entry is written as it wraps up, shortly after it stops showing as running. */
const ENTRY_WRITTEN_MS = 3_000

/** The activity list refetches when one of these runs ends, and again once its entry is
 * written, so the finished entry replaces the running row. */
function useRefreshActivityOnEnd(runIds: (string | null)[]) {
  const client = useQueryClient()
  const key = runIds.filter((id) => id !== null).join('\n')
  const previous = useRef(key)
  useEffect(() => {
    const running = new Set(key.split('\n'))
    const ended = previous.current
      .split('\n')
      .some((id) => id && !running.has(id))
    previous.current = key
    if (!ended) return
    const refresh = () =>
      void client.invalidateQueries({ queryKey: agentActivityKeys.all })
    refresh()
    window.setTimeout(refresh, ENTRY_WRITTEN_MS)
  }, [client, key])
}

const RUN_TEXT: Record<BackgroundRunKind, string> = {
  bootstrap: 'Setting up',
  schema_annotation: 'Writing AI descriptions',
  cache_test: 'Testing the cache',
  speed_test: 'Measuring the query',
  load_test: 'Load test',
  audit: 'Health check',
  audit_capture: 'Health check',
  fleet_audit: 'Health check',
  cache_compare: 'Compare against Readyset',
  analyze: 'Analyzing',
}

/** "Analyzing SELECT on posts by id", "Load test". */
export function backgroundRunText(run: BackgroundRunState): string {
  const text = RUN_TEXT[run.kind]
  return run.kind === 'analyze'
    ? `${text} ${run.queryLabel || 'a query'}`
    : text
}

function RunningRow({
  text,
  target,
  stopping,
  onCancel,
}: {
  text: string
  target: string
  stopping: boolean
  onCancel: () => void
}) {
  return (
    <ListRow data-running className="min-h-11.5 py-2">
      <span className="flex min-w-0 flex-wrap items-center gap-2 text-body-small text-content-layout-1">
        {text}
        {target && <span className="text-content-layout-3">on {target}</span>}
      </span>
      <Button
        variant="primary"
        modifier="ghost"
        size="small"
        label="Cancel"
        loading={stopping}
        disabled={stopping}
        onClick={onCancel}
      />
    </ListRow>
  )
}

function DatabaseRuns({ target }: { target: string }) {
  const running = useAnalyzeJobs(target).data?.queue?.running ?? null
  const cancelAnalysis = useCancelAnalyzeJob(target)
  const report = useReportRun(target)
  useRefreshActivityOnEnd([
    report.active ? 'report' : null,
    running ? `analysis:${running.query_key}` : null,
  ])
  return (
    <>
      {report.active && (
        <RunningRow
          text="Running the report"
          target={target}
          stopping={report.status === 'cancelling'}
          onCancel={() => void report.cancel()}
        />
      )}
      {running && (
        <RunningRow
          text={`Analyzing ${running.display_name}`}
          target={target}
          stopping={cancelAnalysis.isPending}
          onCancel={() => cancelAnalysis.mutate(running.query_key)}
        />
      )}
    </>
  )
}

/** Hidden while nothing runs. */
export function RunningNow({ databases }: { databases: string[] }) {
  const runs = useBackgroundRuns().filter((run) => ACTIVE_RUN.has(run.status))
  useRefreshActivityOnEnd(runs.map((run) => run.runId))
  return (
    <section
      aria-label="Running now"
      className="mb-9 hidden has-[[data-running]]:block"
    >
      <Text as="h2" level="caption" className="mb-2 text-content-layout-3">
        Running now
      </Text>
      <DividedList columns="1fr auto">
        {databases.map((name) => (
          <DatabaseRuns key={name} target={name} />
        ))}
        {runs.map((run) => (
          <RunningRow
            key={run.runId}
            text={backgroundRunText(run)}
            target={run.target}
            stopping={run.status === 'stopping'}
            onCancel={() => void cancelBackgroundRun(run.runId)}
          />
        ))}
      </DividedList>
    </section>
  )
}
