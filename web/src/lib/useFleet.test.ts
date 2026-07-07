import { describe, expect, it } from 'vitest'

import { mapSnapshotVerdicts, type FleetSnapshotDetail } from './useFleet'

describe('mapSnapshotVerdicts', () => {
  it('maps target names to their sizing verdict and cache score', () => {
    const detail: FleetSnapshotDetail = {
      snapshot_id: 'fleet_1',
      name: 'fleet_1',
      created_at: '2026-07-07T08:08:21Z',
      targets_audited: 2,
      results: [
        { target_name: 'pgtest', sizing: { verdict: 'oversized' }, cache_opportunity: { score: 65 } },
        { target_name: 'testdb', sizing: { verdict: 'right_sized' }, cache_opportunity: { score: 12 } },
      ],
    }

    expect(mapSnapshotVerdicts(detail)).toEqual({
      pgtest: { verdict: 'oversized', cacheScore: 65 },
      testdb: { verdict: 'right_sized', cacheScore: 12 },
    })
  })

  it('leaves verdict and score undefined when a target has no sizing data', () => {
    const detail: FleetSnapshotDetail = {
      snapshot_id: 'fleet_1',
      name: 'fleet_1',
      created_at: '2026-07-07T08:08:21Z',
      targets_audited: 1,
      results: [{ target_name: 'errored', sizing: null, cache_opportunity: null }],
    }

    expect(mapSnapshotVerdicts(detail)).toEqual({
      errored: { verdict: undefined, cacheScore: undefined },
    })
  })

  it('returns an empty map for undefined detail or missing results', () => {
    expect(mapSnapshotVerdicts(undefined)).toEqual({})
    expect(
      mapSnapshotVerdicts({
        snapshot_id: 'x',
        name: 'x',
        created_at: '',
        targets_audited: 0,
      }),
    ).toEqual({})
  })
})
