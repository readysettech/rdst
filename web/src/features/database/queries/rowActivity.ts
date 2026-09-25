// What a query row says while RDST works on it: "Writing recommendations", "Queued, 2nd",
// "Will retry at 3:15 PM", "Classifying...". A running or queued analysis shows on every
// row, scored or not. Pure, so the rules are unit-tested; the row shows "Analyze now" when
// none applies.
import type { JevAssessmentSummary } from '@/lib/api'
import type { AnalyzeJob, MonitorQuery } from '@/lib/monitor/types'
import type { Analyzability } from './analyzability'
import { CLASSIFICATION_LABELS, isClassificationFilter } from './jev'
import type { RowActivity } from './LegendRow'

/** Classification statuses that are still on their way (not stuck on sign-in or schema). */
const CLASSIFYING = new Set(['pending', 'running', 'retry_wait'])

/** Statements RDST treats as writes: hidden from the list by default. */
export function isWrite(gate: Analyzability): boolean {
  return gate.code === 'write' || gate.code === 'ddl'
}

/** The step a running analysis is on, in the row's words. */
export const PHASE_LABELS: Record<string, string> = {
  sample: 'Sampling values',
  plan: 'Running EXPLAIN',
  run: 'Running EXPLAIN ANALYZE',
  schema: 'Reading the schema',
  advise: 'Writing recommendations',
  verify: 'Testing fixes',
}

export function phaseLabel(phase: string | null | undefined): string | null {
  return phase ? (PHASE_LABELS[phase] ?? null) : null
}

/** Queue positions also come from the dispatch list when a job's row omits them. */
export function waitingJobs(page: {
  jobs: AnalyzeJob[]
  queue?: { next: AnalyzeJob[] } | null
}): Map<string, AnalyzeJob> {
  const positions = new Map(
    (page.queue?.next ?? []).map((job, index) => [
      job.id,
      job.queue_position ?? index + 1,
    ])
  )
  const waiting = new Map<string, AnalyzeJob>()
  for (const job of page.jobs) {
    if (job.state !== 'queued' && job.state !== 'running') continue
    waiting.set(job.query_key, {
      ...job,
      queue_position: job.queue_position ?? positions.get(job.id) ?? null,
    })
  }
  return waiting
}

/** "Queued, 2nd" and friends, for a waiting or running job. */
export function jobActivity(
  job: AnalyzeJob | null | undefined,
  phase: string | null = null
): RowActivity | null {
  if (!job) return null
  if (job.state === 'running') {
    const step = phaseLabel(phase)
    return { kind: 'running', label: 'Analyzing now', step, soon: true }
  }
  if (job.state !== 'queued') return null
  if (job.queue_state === 'held')
    return {
      kind: 'queued',
      label: job.reason_text ?? 'Waiting, automatic analysis is off',
      held: true,
    }
  return {
    kind: 'queued',
    label: 'Queued',
    position: job.queue_position ?? null,
    soon: true,
  }
}

/** A failure on the AI service with a retry pending: a soft "Will retry at 3:15 PM". */
export function retryActivity(
  analysis: MonitorQuery['last_analysis'] | null | undefined
): RowActivity | null {
  if (
    !analysis ||
    !['failed', 'running'].includes(analysis.state) ||
    analysis.error_kind !== 'retrying'
  )
    return null
  const at = analysis.retry_at
  return {
    kind: 'retrying',
    label:
      analysis.state === 'failed' && at && at > Date.now() / 1000 + 30
        ? `Will retry at ${new Date(at * 1000).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}`
        : 'Retrying',
  }
}

/** The row's own last analysis as a job, when the queue list hasn't caught up yet. */
function lastAnalysisJob(query: Pick<MonitorQuery, 'last_analysis'>) {
  const analysis = query.last_analysis
  const state = analysis?.state
  return state === 'queued' || state === 'running'
    ? ({
        state,
        queue_state: analysis?.queue_state ?? null,
        queue_position: analysis?.queue_position ?? null,
        reason_text: analysis?.reason ?? null,
      } as AnalyzeJob)
    : null
}

/** A click on a query already in line: where it stands. */
export function alreadyQueuedNote(job: AnalyzeJob): string {
  if (job.state === 'running') return 'Already analyzing it'
  return 'Already in the queue'
}

export interface RowActivityInput {
  query: Pick<MonitorQuery, 'last_analysis'>
  gate: Analyzability
  job?: AnalyzeJob | null
  classification?: JevAssessmentSummary | null
  /** The target's classifier is at work, so a query without one yet is on its way. */
  classifying?: boolean
  /** A short-lived note from a click ("Already in the queue (3rd)"). */
  note?: string | null
}

export function rowActivity({
  query,
  gate,
  job,
  classification,
  classifying = false,
  note,
}: RowActivityInput): RowActivity | null {
  if (!gate.ok) return null
  if (note) return { kind: 'note', label: note }
  if (query.last_analysis?.state === 'running') {
    const retry = retryActivity(query.last_analysis)
    if (retry) return retry
  }
  // A queued or running analysis shows even when the row's data still says done. The
  // row's own facts fill in what the queue list leaves out (the step, the place).
  const own = lastAnalysisJob(query)
  const phase =
    query.last_analysis?.state === 'running'
      ? (query.last_analysis.phase ?? null)
      : null
  const listed =
    job && own && job.state === own.state && job.queue_position == null
      ? { ...job, queue_position: own.queue_position }
      : job
  const waiting =
    jobActivity(listed, phase) ??
    (job === undefined ? jobActivity(own, phase) : null)
  if (waiting) return waiting
  if (query.last_analysis?.state === 'done') return null
  const retry = retryActivity(query.last_analysis)
  if (retry) return retry
  const status = classification?.status
  if (status ? CLASSIFYING.has(status) : classifying)
    return { kind: 'classifying', label: 'Classifying...' }
  return null
}

/** The classification's first finding, in the list's words ("Missing index"). */
export function findingLabel(
  classification: JevAssessmentSummary | null | undefined
): string | null {
  if (classification?.status !== 'complete') return null
  for (const finding of classification.findings ?? [])
    if (isClassificationFilter(finding.id) && finding.id !== 'none')
      return CLASSIFICATION_LABELS[finding.id]
  return null
}

/** What the score column says for a row without a score: queued with its place,
 * analyzing, or retrying. Null when the placeholder's "No score" applies. */
export function scoreSlot(activity: RowActivity | null | undefined): {
  kind: 'queued' | 'running' | 'retrying'
  word: string
  detail: string | null
  title: string
} | null {
  if (!activity) return null
  if (activity.kind === 'running')
    return {
      kind: 'running',
      word: 'Analyzing',
      detail: null,
      title: activity.step
        ? `Analyzing now: ${activity.step.charAt(0).toLowerCase()}${activity.step.slice(1)}`
        : 'Analyzing now',
    }
  if (activity.kind === 'retrying')
    return {
      kind: 'retrying',
      word: 'Retrying',
      detail: null,
      title:
        activity.label === 'Retrying'
          ? "The AI service didn't answer. RDST is trying again."
          : `The AI service didn't answer. RDST ${activity.label.charAt(0).toLowerCase()}${activity.label.slice(1)}.`,
    }
  if (activity.kind === 'queued' && !activity.held)
    return {
      kind: 'queued',
      word: 'Queued',
      detail: null,
      title: 'Queued for deep analysis',
    }
  return null
}

/** The line beside the name on a row the score column already explains: the running
 * step, a retry time, or a held reason. Null when the tile says it all. */
export function inlineActivity(
  activity: RowActivity | null | undefined,
  scored: boolean
): RowActivity | null {
  if (!activity) return null
  if (scored) {
    if (!activity.soon && activity.kind !== 'retrying') return null
    return activity.kind === 'running' && activity.step
      ? { ...activity, label: `${activity.label} · ${activity.step}` }
      : activity
  }
  if (!scoreSlot(activity)) return activity
  if (activity.kind === 'running')
    return activity.step ? { ...activity, label: activity.step } : null
  if (activity.kind === 'retrying' && activity.label !== 'Retrying')
    return activity
  return null
}

/** The classification's first finding, as the row shows it. A schema-only classification
 * can't clear a query, so no finding shows nothing. Null until it's complete. */
export function classificationChip(
  classification: JevAssessmentSummary | null | undefined
): { label: string; clear: boolean } | null {
  if (classification?.status !== 'complete') return null
  const finding = findingLabel(classification)
  return finding ? { label: finding, clear: false } : null
}
