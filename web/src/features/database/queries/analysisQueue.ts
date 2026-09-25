import type { AnalyzeJob, AnalyzeQueueSummary } from '@/lib/monitor/types'

/** "about 3 min" from now to an estimated start, never under a minute. */
export function aboutMinutes(startAt: number, now: number) {
  const minutes = Math.max(1, Math.round((startAt - now) / 60))
  return minutes >= 90
    ? `about ${Math.round(minutes / 60)} h`
    : `about ${minutes} min`
}

/**
 * Where a queued job stands. Uses the backend's queue facts when present; an older backend
 * only gives the count of jobs ahead.
 */
export function queueLine(
  job: AnalyzeJob,
  now: number,
  _queue?: AnalyzeQueueSummary | null,
  ahead?: number | null
) {
  if (job.state === 'running') return 'Analyzing now'
  if (job.state !== 'queued') return undefined
  switch (job.queue_state) {
    case 'today': {
      const when =
        job.estimated_start_at != null
          ? `, ${aboutMinutes(job.estimated_start_at, now)}`
          : ''
      return `Queued${when}`
    }
    case 'held':
      return (
        job.reason_text ??
        'Automatic analysis is off. Click Analyze now to run this one.'
      )
    default:
      return ahead
        ? `${ahead} ${ahead === 1 ? 'analysis' : 'analyses'} ahead of this one`
        : 'Next in line'
  }
}

/** A held job the person can start with Analyze now. A click always runs and never
 * counts against the automatic allowance. */
export function canAnalyzeNow(job: AnalyzeJob | null) {
  return Boolean(
    job?.state === 'queued' &&
      job.reason !== 'user' &&
      job.queue_state === 'held'
  )
}
