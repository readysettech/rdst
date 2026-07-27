import type { AuditRunSummary } from '../types/audit'
import type { FleetSnapshotSummary } from '../types/fleet'

// ---------------------------------------------------------------------------
// Unified run history
// ---------------------------------------------------------------------------
//
// Single-target audit runs (GET /api/audit/runs) and fleet snapshots
// (GET /api/fleet/snapshots) merge into one day-grouped timeline. Both list
// endpoints carry every field this timeline reads, so building it needs no
// per-run detail fetch. Fleet snapshot summaries carry fewer fields than run
// summaries; entries degrade to `undefined` rather than inventing values.

export interface HistoryEntry {
  id: string
  kind: 'single' | 'fleet'
  /** Target name for single runs; a fleet label for snapshot rows. */
  scopeLabel: string
  engine: string | undefined
  targetNames: string[]
  mode: 'snapshot' | 'capture'
  durationSeconds: number
  queryCount: number | undefined
  targetsAudited: number | undefined
  startedAt: string
  hasAnalysis: boolean
}

export const HISTORY_PAGE_SIZE = 8

function timestamp(value: string): number {
  const parsed = new Date(value).getTime()
  return Number.isNaN(parsed) ? 0 : parsed
}

export function buildHistory(
  runs: AuditRunSummary[],
  snapshots: FleetSnapshotSummary[]
): HistoryEntry[] {
  const entries: HistoryEntry[] = []
  for (const run of runs) {
    const duration = run.duration_seconds ?? 0
    entries.push({
      id: run.run_id,
      kind: 'single',
      scopeLabel: run.target_name || run.run_id,
      engine: run.engine || undefined,
      targetNames: [run.target_name].filter(Boolean),
      mode: duration > 0 ? 'capture' : 'snapshot',
      durationSeconds: duration,
      queryCount: run.total_queries ?? undefined,
      targetsAudited: undefined,
      startedAt: run.started_at || '',
      hasAnalysis: !!run.has_analysis,
    })
  }
  for (const snapshot of snapshots) {
    const targetNames = snapshot.target_names ?? []
    const duration = snapshot.duration_seconds ?? 0
    entries.push({
      id: snapshot.snapshot_id,
      kind: 'fleet',
      scopeLabel: snapshot.name || 'Fleet',
      engine: undefined,
      targetNames,
      mode: duration > 0 ? 'capture' : 'snapshot',
      durationSeconds: duration,
      queryCount: undefined,
      targetsAudited: snapshot.targets_audited ?? undefined,
      startedAt: snapshot.created_at || '',
      hasAnalysis: false,
    })
  }
  return entries.sort((a, b) => timestamp(b.startedAt) - timestamp(a.startedAt))
}

export interface HistoryDayGroup {
  /** Human day label: "Today", "Yesterday", or a locale date. */
  day: string
  entries: HistoryEntry[]
}

/** Group already-sorted entries into contiguous day buckets, newest first. */
export function groupHistoryByDay(
  entries: HistoryEntry[],
  now: Date = new Date()
): HistoryDayGroup[] {
  const groups: HistoryDayGroup[] = []
  let currentKey = ''
  const todayKey = now.toDateString()
  const yesterdayKey = new Date(now.getTime() - 86_400_000).toDateString()

  for (const entry of entries) {
    const date = new Date(entry.startedAt)
    const valid = !Number.isNaN(date.getTime())
    const key = valid ? date.toDateString() : 'Unknown date'
    if (key !== currentKey) {
      currentKey = key
      const day = !valid
        ? 'Unknown date'
        : key === todayKey
          ? 'Today'
          : key === yesterdayKey
            ? 'Yesterday'
            : date.toLocaleDateString(undefined, {
                month: 'short',
                day: 'numeric',
                year: 'numeric',
              })
      groups.push({ day, entries: [] })
    }
    groups[groups.length - 1].entries.push(entry)
  }
  return groups
}
