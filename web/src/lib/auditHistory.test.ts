import { describe, expect, it } from 'vitest'

import {
  buildHistory,
  groupHistoryByDay,
  HISTORY_PAGE_SIZE,
} from './auditHistory'
import type { AuditRunSummary } from '../types/audit'
import type { FleetSnapshotSummary } from '../types/fleet'

const run = (overrides: Partial<AuditRunSummary>): AuditRunSummary => ({
  run_id: 'run_1',
  target_name: 'prod',
  started_at: '2026-07-20T10:00:00Z',
  duration_seconds: 0,
  total_queries: 0,
  source: 'audit',
  has_analysis: false,
  ...overrides,
})

const snapshot = (
  overrides: Partial<FleetSnapshotSummary>
): FleetSnapshotSummary => ({
  snapshot_id: 'fleet_1',
  name: 'fleet_1',
  created_at: '2026-07-20T09:00:00Z',
  targets_audited: 3,
  kind: 'fleet',
  ...overrides,
})

describe('buildHistory', () => {
  it('uses a compact initial page of eight rows', () => {
    expect(HISTORY_PAGE_SIZE).toBe(8)
  })
  it('merges runs and snapshots sorted newest first', () => {
    const entries = buildHistory(
      [
        run({ run_id: 'old', started_at: '2026-07-18T08:00:00Z' }),
        run({ run_id: 'new', started_at: '2026-07-20T10:00:00Z' }),
      ],
      [snapshot({ snapshot_id: 'mid', created_at: '2026-07-19T12:00:00Z' })]
    )
    expect(entries.map((entry) => entry.id)).toEqual(['new', 'mid', 'old'])
    expect(entries[1].kind).toBe('fleet')
  })

  it('classifies capture runs by duration and keeps query counts', () => {
    const entries = buildHistory(
      [
        run({ run_id: 'cap', duration_seconds: 60, total_queries: 12 }),
        run({ run_id: 'snap', duration_seconds: 0 }),
      ],
      []
    )
    const capture = entries.find((entry) => entry.id === 'cap')!
    const instant = entries.find((entry) => entry.id === 'snap')!
    expect(capture.mode).toBe('capture')
    expect(capture.queryCount).toBe(12)
    expect(instant.mode).toBe('snapshot')
  })

  it('degrades gracefully when snapshot rows lack fields', () => {
    const entries = buildHistory(
      [],
      [snapshot({ created_at: '', targets_audited: 0 })]
    )
    expect(entries).toHaveLength(1)
    expect(entries[0].scopeLabel).toBe('fleet_1')
    expect(entries[0].queryCount).toBeUndefined()
  })

  it('uses summary fields for report subtitles and fleet capture windows', () => {
    const single = run({ run_id: 'single', engine: 'postgresql' })
    const fleet = snapshot({
      name: 'Production fleet',
      target_names: ['reader-1', 'reader-2'],
      duration_seconds: 30,
    })

    const entries = buildHistory([single], [fleet])
    const singleEntry = entries.find((entry) => entry.kind === 'single')!
    const fleetEntry = entries.find((entry) => entry.kind === 'fleet')!
    expect(singleEntry.engine).toBe('postgresql')
    expect(fleetEntry.scopeLabel).toBe('Production fleet')
    expect(fleetEntry.targetNames).toEqual(['reader-1', 'reader-2'])
    expect(fleetEntry.durationSeconds).toBe(30)
    expect(fleetEntry.mode).toBe('capture')
  })
})

describe('groupHistoryByDay', () => {
  it('groups contiguous entries into per-day buckets, newest first', () => {
    // Dates are 2+ days apart so the bucket split holds in every timezone;
    // day labels (Today/Yesterday/date) are timezone-dependent presentation.
    const now = new Date('2026-07-20T12:00:00Z')
    const entries = buildHistory(
      [
        run({ run_id: 'a', started_at: '2026-07-20T12:00:00Z' }),
        run({ run_id: 'b', started_at: '2026-07-17T12:00:00Z' }),
        run({ run_id: 'c', started_at: '2026-07-01T12:00:00Z' }),
      ],
      []
    )
    const groups = groupHistoryByDay(entries, now)
    expect(groups).toHaveLength(3)
    expect(groups.map((group) => group.entries.length)).toEqual([1, 1, 1])
    expect(groups[0].entries[0].id).toBe('a')
    expect(groups[2].entries[0].id).toBe('c')
  })

  it('buckets unparseable timestamps under Unknown date', () => {
    const entries = buildHistory([run({ run_id: 'x', started_at: '' })], [])
    const groups = groupHistoryByDay(entries)
    expect(groups).toEqual([{ day: 'Unknown date', entries }])
  })
})
