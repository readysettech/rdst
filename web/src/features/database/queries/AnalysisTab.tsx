import { Alert } from '@rs/ui-new/alert'
import { Button } from '@rs/ui-new/button'
import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState } from '@rs/ui-new/error-state'
import { Skeleton } from '@rs/ui-new/skeleton'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Tag } from '@rs/ui-new/tag'
import { Text } from '@rs/ui-new/text'
import { type ReactNode, useEffect, useRef, useState } from 'react'
import {
  type ProgressState,
  ProgressTimeline,
} from '@/features/monitor-kit/ProgressTimeline'
import { AnalysisResults } from '@/features/queries/results/AnalysisResults'
import { useStoredAnalysis } from '@/features/queries/results/useStoredAnalysis'
import {
  useAnalyzeJobs,
  useEnqueueAnalysis,
  useMonitorSettings,
  useQueryAnalyzeJobs,
} from '@/lib/monitor/hooks'
import type {
  AnalyzeJob,
  AnalyzeQueueSummary,
  LastAnalysis,
  MonitorQuery,
  MonitorSettings,
} from '@/lib/monitor/types'
import { canAnalyzeNow, queueLine } from './analysisQueue'
import { analyzability, notAnalyzedLine } from './analyzability'
import { analysisValuesLine, relativeTime } from './format'
import { phaseLabel } from './rowActivity'

/** True when the target consents to running queries (EXPLAIN ANALYZE). */
function runsQueries(settings: MonitorSettings | undefined) {
  if (!settings) return false
  return settings.analysis_mode
    ? settings.analysis_mode === 'execute'
    : settings.execute
}

/** "Plan only (EXPLAIN)" or "Ran the query (EXPLAIN ANALYZE)" for a finished analysis. */
export function analysisModeLabel(analysis: LastAnalysis | null | undefined) {
  if (analysis?.state !== 'done') return null
  if (analysis.mode_label) return analysis.mode_label
  if (analysis.mode === 'explain') return 'Plan only (EXPLAIN)'
  if (analysis.mode === 'analyze') return 'Ran the query (EXPLAIN ANALYZE)'
  return null
}

/** Splits a stored-analysis reference into registry hash and analysis id. */
export function parseAnalysisRef(
  ref: string | null | undefined,
  fallbackHash: string
): { hash: string; analysisId: string } | null {
  if (!ref) return null
  const marker = '/analysis/'
  const at = ref.indexOf(marker)
  if (at < 0) return { hash: fallbackHash, analysisId: ref }
  const hash = ref.slice(0, at).replace(/\/+$/, '').split('/').pop()
  const analysisId = ref.slice(at + marker.length).replace(/\/+$/, '')
  return hash && analysisId ? { hash, analysisId } : null
}

function jobStepState(
  job: AnalyzeJob | null,
  step: 'queue' | 'run' | 'write'
): ProgressState {
  if (!job) return 'queued'
  if (job.state === 'failed')
    return step === 'queue' ? 'done' : step === 'run' ? 'failed' : 'skipped'
  if (job.state === 'done') return 'done'
  if (job.state === 'running')
    return step === 'queue' ? 'done' : step === 'run' ? 'running' : 'queued'
  return step === 'queue' ? 'running' : 'queued'
}

function RunningAnalysis({
  job,
  ahead,
  queue,
  measure,
}: {
  job: AnalyzeJob | null
  ahead: number | null
  queue: AnalyzeQueueSummary | null | undefined
  measure: boolean
}) {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const started = job?.started_at ?? null
  const elapsed = started ? Math.max(0, Math.round(now - started)) : null
  const clock = (seconds: number) =>
    `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
  return (
    <ProgressTimeline
      label="Analysis steps"
      items={[
        {
          id: 'queue',
          label: 'Wait for analysis to start',
          state: jobStepState(job, 'queue'),
          detail:
            job?.state === 'queued'
              ? queueLine(job, now, queue, ahead)
              : undefined,
        },
        {
          id: 'run',
          label: measure
            ? 'Run EXPLAIN ANALYZE with sampled values'
            : 'Read the plan with EXPLAIN',
          state: jobStepState(job, 'run'),
          detail:
            job?.state === 'running'
              ? measure
                ? 'Running the query read-only, with a time limit'
                : 'Reading the plan without running the query'
              : undefined,
          meta: elapsed !== null ? clock(elapsed) : undefined,
        },
        {
          id: 'write',
          label: 'Write findings and index advice',
          state: jobStepState(job, 'write'),
        },
      ]}
    />
  )
}

export function StoredAnalysis({
  refParts,
  mode,
  target,
  onCompare,
}: {
  refParts: { hash: string; analysisId: string }
  mode?: string | null
  target: string
  onCompare: () => void
}) {
  const stored = useStoredAnalysis({ ...refParts, mode })
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    if (!stored.isLoading) return
    const timer = window.setTimeout(() => setSlow(true), 8000)
    return () => window.clearTimeout(timer)
  }, [stored.isLoading])
  if (stored.isLoading)
    return (
      <VStack className="items-stretch gap-3" aria-busy="true">
        <output className="block text-caption text-content-layout-3">
          {slow
            ? 'Still opening the saved analysis. RDST reads it from its local store.'
            : 'Opening the saved analysis'}
        </output>
        <Skeleton className="h-24 w-full rounded-panel" />
        <Skeleton className="h-40 w-full rounded-panel" />
      </VStack>
    )
  if (stored.error || !stored.hasBody)
    return (
      <EmptyState
        layout="compact"
        icon="document-validation"
        title="The saved analysis couldn't be opened"
        body="RDST keeps the newest analyses per query. Analyze again to write a fresh one."
      />
    )
  return (
    <AnalysisResults
      state="complete"
      results={stored.results}
      rewriteTesting={stored.rewriteTesting}
      readysetCacheability={stored.readysetCacheability}
      target={target}
      onCacheQuery={onCompare}
    />
  )
}

export function AnalysisTab({
  query,
  target,
  onCompare,
  startSignal = 0,
}: {
  query: MonitorQuery
  target: string
  onCompare: () => void
  /** Bumped by an Analyze action outside the tab: start an analysis now. */
  startSignal?: number
}) {
  const jobs = useQueryAnalyzeJobs(target, query.key)
  const measure = runsQueries(useMonitorSettings(target).data)
  const allJobs = useAnalyzeJobs(target)
  const queue = allJobs.data?.queue
  const enqueue = useEnqueueAnalysis(target)
  const [queuedJob, setQueuedJob] = useState<AnalyzeJob | null>(null)
  const ownJob = queuedJob
    ? (jobs.data?.jobs.find((job) => job.id === queuedJob.id) ?? queuedJob)
    : (jobs.data?.jobs[0] ?? null)
  // Job states advance even when the two query caches refresh separately.
  const listed = ownJob
    ? allJobs.data?.jobs.find((job) => job.id === ownJob.id)
    : undefined
  const rank = (job: AnalyzeJob) =>
    job.state === 'queued' ? 0 : job.state === 'running' ? 1 : 2
  const latestJob =
    listed && (!ownJob || rank(listed) >= rank(ownJob)) ? listed : ownJob
  useEffect(() => {
    if (
      latestJob &&
      latestJob.state !== 'queued' &&
      latestJob.state !== 'running'
    )
      setQueuedJob(null)
  }, [latestJob])
  const moveUp = canAnalyzeNow(latestJob)
  const analysis = query.last_analysis
  const state =
    latestJob && (latestJob.state === 'queued' || latestJob.state === 'running')
      ? latestJob.state
      : analysis?.state
  const ahead =
    latestJob && latestJob.state === 'queued'
      ? (allJobs.data?.jobs ?? []).filter(
          (job) =>
            job.state === 'queued' &&
            job.enqueued_at < latestJob.enqueued_at &&
            job.query_key !== query.key
        ).length
      : null
  const refParts = parseAnalysisRef(analysis?.analysis_ref, query.key)
  const gate = analyzability(query)
  const modeLabel = analysisModeLabel(analysis)
  const planOnly = analysis?.state === 'done' && analysis.mode === 'explain'
  const valuesLine =
    analysis?.state === 'done' ? analysisValuesLine(analysis) : null

  const idle = state !== 'queued' && state !== 'running'
  const startedFor = useRef(0)
  useEffect(() => {
    if (!startSignal || startedFor.current === startSignal) return
    startedFor.current = startSignal
    if (gate.ok && idle && query.text_status !== 'unavailable')
      enqueue.mutate(query.key, { onSuccess: (job) => setQueuedJob(job) })
  }, [startSignal, gate.ok, idle, query.text_status, query.key, enqueue])

  // A statement RDST never runs has no queue, job or result to wait on.
  if (!gate.ok)
    return (
      <VStack className="items-stretch gap-4" data-testid="deep-analysis">
        <EmptyState
          layout="compact"
          icon="lock-sync"
          title="RDST doesn't analyze this query"
          body={notAnalyzedLine(query, gate)}
        />
      </VStack>
    )

  const analyze = () =>
    enqueue.mutate(query.key, { onSuccess: (job) => setQueuedJob(job) })

  const header = (
    <HStack className="flex-wrap items-center justify-between gap-3">
      <VStack className="min-w-0 items-start gap-0.5">
        <HStack className="flex-wrap items-center gap-2">
          <Text level="label-medium" className="text-content-layout-1">
            {state === 'running'
              ? latestJob?.error?.startsWith('retrying') ||
                analysis?.error_kind === 'retrying'
                ? 'Retrying'
                : phaseLabel(analysis?.phase)
                  ? `Analyzing now · ${phaseLabel(analysis?.phase)}`
                  : 'Analyzing now'
              : state === 'queued'
                ? latestJob?.queue_state === 'held'
                  ? 'Analysis on hold'
                  : 'Analysis queued'
                : analysis?.state === 'done' && analysis.finished_at
                  ? `Analyzed ${relativeTime(analysis.finished_at)}`
                  : 'Deep analysis'}
          </Text>
          {modeLabel && state !== 'running' && state !== 'queued' ? (
            <Tag
              size="small"
              variant={planOnly ? 'informative' : 'neutral'}
              modifier="ghost"
              label={modeLabel}
              data-analysis-mode={analysis?.mode}
            />
          ) : null}
        </HStack>
        <Text level="caption" className="text-content-layout-3">
          {valuesLine
            ? `${valuesLine}.`
            : planOnly
              ? 'EXPLAIN without running the query, so there are no measured timings.'
              : analysis?.state === 'done' && analysis.mode === 'analyze'
                ? 'Read-only and time-limited.'
                : measure
                  ? 'Runs EXPLAIN ANALYZE on your database, read-only and time-limited.'
                  : 'Plans only: RDST runs EXPLAIN and your queries never run. Turn on real timings in monitoring settings.'}
        </Text>
      </VStack>
      {moveUp ? (
        <Button
          variant="primary"
          modifier="outline"
          size="small"
          icon="speedometer"
          iconPosition="left"
          label="Analyze now"
          loading={enqueue.isPending}
          onClick={analyze}
        />
      ) : state !== 'queued' && state !== 'running' ? (
        <Button
          variant="primary"
          modifier="outline"
          size="small"
          icon="speedometer"
          iconPosition="left"
          label={analysis?.state === 'done' ? 'Analyze again' : 'Analyze'}
          loading={enqueue.isPending}
          onClick={analyze}
        />
      ) : null}
    </HStack>
  )

  let body: ReactNode
  if (state === 'queued' || state === 'running') {
    body = (
      <RunningAnalysis
        measure={measure}
        job={
          latestJob ?? {
            ...({} as AnalyzeJob),
            state,
            started_at: null,
            enqueued_at: Date.now() / 1000,
          }
        }
        ahead={ahead}
        queue={queue}
      />
    )
  } else if (
    analysis?.state === 'failed' &&
    (analysis.error_kind === 'retrying' ||
      analysis.error_kind === 'ai_unavailable')
  ) {
    // The AI service's failure, not the query's: say it calmly, and when RDST tries again.
    const retrying = analysis.error_kind === 'retrying'
    body = (
      <EmptyState
        layout="compact"
        icon="speedometer"
        title={retrying ? 'Retrying soon' : "The AI service didn't answer"}
        body={
          retrying && analysis.retry_at
            ? `The AI service didn't answer this time. RDST tries again at ${new Date(analysis.retry_at * 1000).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}. Nothing on your database was changed.`
            : `${analysis.reason ?? 'RDST tries again later.'} Nothing on your database was changed.`
        }
      />
    )
  } else if (analysis?.state === 'failed') {
    body = (
      <ErrorState
        errorClass="database"
        title="The last analysis didn't finish"
        message={
          analysis.reason ?? 'The analysis stopped before it wrote a result.'
        }
        trustworthy="Nothing on your database was changed."
        onRetry={analyze}
      />
    )
  } else if (refParts) {
    body = (
      <StoredAnalysis
        refParts={refParts}
        mode={analysis?.mode}
        target={target}
        onCompare={onCompare}
      />
    )
  } else {
    body = (
      <EmptyState
        layout="compact"
        icon="speedometer"
        title="Not analyzed yet"
        body={
          query.text_status === 'unavailable'
            ? 'RDST needs the query text to analyze it. It will analyze this query once the text is captured.'
            : measure
              ? 'Analyze runs EXPLAIN ANALYZE with sampled values and suggests indexes.'
              : 'Analyze reads the plan with EXPLAIN and suggests indexes.'
        }
        action={
          query.text_status === 'unavailable'
            ? undefined
            : { label: 'Analyze', icon: 'speedometer', onClick: analyze }
        }
      />
    )
  }

  return (
    <VStack className="items-stretch gap-4" data-testid="deep-analysis">
      {header}
      {state !== 'running' && !refParts ? (
        <Text as="p" level="caption" className="text-content-layout-3">
          Automatic analyses cover the busiest queries. You can analyze this one
          now. Raise how many run automatically in{' '}
          <a
            className="underline"
            href={`/database?target=${encodeURIComponent(target)}&panel=settings#monitor-cap`}
          >
            Settings
          </a>
          .
        </Text>
      ) : null}
      {enqueue.isError ? (
        <Alert
          variant="warning"
          modifier="outline"
          icon="alert"
          iconPosition="left"
          label={
            enqueue.error instanceof Error && enqueue.error.message
              ? `The analysis didn't start: ${enqueue.error.message}`
              : "The analysis didn't start. Try again in a moment."
          }
        />
      ) : null}
      {body}
    </VStack>
  )
}
