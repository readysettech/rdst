import { describe, expect, it } from 'vitest'

import {
  buildFleetAuditRequest,
  buildTargetSelectionRequest,
  isTargetCapError,
  parseAuditSearch,
  selectionFromAuditSearch,
  splitByAvailability,
} from './auditScope'
import type { FleetConnectivityEvent, FleetMember } from '../types/fleet'

const member = (
  name: string,
  extra: Partial<FleetMember> = {}
): FleetMember => ({
  name,
  engine: 'postgresql',
  host: 'db.internal',
  port: 5432,
  database: 'app',
  ...extra,
})

const conn = (
  name: string,
  status: string,
  error?: string
): FleetConnectivityEvent =>
  ({
    type: 'connectivity',
    target_name: name,
    status,
    error,
  }) as FleetConnectivityEvent

describe('parseAuditSearch', () => {
  it('parses valid scopes and preselections', () => {
    expect(
      parseAuditSearch({ scope: 'group', group: 'rdst-fleet-aurora' })
    ).toEqual({
      scope: 'group',
      group: 'rdst-fleet-aurora',
      target: undefined,
      targets: undefined,
    })
    expect(parseAuditSearch({ scope: 'fleet' })).toEqual({
      scope: 'fleet',
      group: undefined,
      target: undefined,
      targets: undefined,
    })
    expect(parseAuditSearch({ scope: 'multi' }).scope).toBe('multi')
    expect(parseAuditSearch({ target: 'prod-1' }).target).toBe('prod-1')
  })

  it('never throws — unknown or missing values fall back to defaults', () => {
    expect(() => parseAuditSearch({})).not.toThrow()
    expect(parseAuditSearch({})).toEqual({
      scope: undefined,
      group: undefined,
      target: undefined,
      targets: undefined,
    })
    expect(
      parseAuditSearch({ scope: 'bogus', group: 42 }).scope
    ).toBeUndefined()
    expect(
      parseAuditSearch({ scope: 'bogus', group: 42 }).group
    ).toBeUndefined()
    expect(parseAuditSearch({ group: '' }).group).toBeUndefined()
  })
})

describe('splitByAvailability', () => {
  it('moves only settled-failed targets to unavailable', () => {
    const members = [member('a'), member('b'), member('c'), member('d')]
    const connectivity = {
      a: conn('a', 'ok'),
      b: conn('b', 'failed', 'connection refused'),
      c: conn('c', 'checking'),
      // d: never checked
    }
    const { available, unavailable } = splitByAvailability(
      members,
      connectivity
    )
    expect(available.map((m) => m.name)).toEqual(['a', 'c', 'd'])
    expect(unavailable.map((m) => m.name)).toEqual(['b'])
  })

  it('keeps everything available with no connectivity data', () => {
    const members = [member('a'), member('b')]
    const { available, unavailable } = splitByAvailability(members, {})
    expect(available).toHaveLength(2)
    expect(unavailable).toHaveLength(0)
  })
})

describe('selectionFromAuditSearch', () => {
  const members = [
    member('current'),
    member('writer', { group: 'aurora' }),
    member('reader', { group: 'aurora' }),
    member('ungrouped', { group: null }),
  ]

  it('starts empty and maps explicit URL preselections', () => {
    expect(selectionFromAuditSearch({}, members, 'current')).toEqual([])
    expect(
      selectionFromAuditSearch({ target: 'writer' }, members, 'current')
    ).toEqual(['writer'])
    expect(
      selectionFromAuditSearch({ group: 'aurora' }, members, 'current')
    ).toEqual(['writer', 'reader'])
    expect(
      selectionFromAuditSearch({ scope: 'fleet' }, members, 'current')
    ).toEqual(members.map((item) => item.name))
  })

  it('does not invent a group for null-group targets', () => {
    expect(
      selectionFromAuditSearch({ group: 'missing' }, members, 'current')
    ).toEqual([])
  })

  it('preselects an explicit reachable-target list from Fleet', () => {
    expect(
      selectionFromAuditSearch(
        { scope: 'multi', targets: 'reader,current,missing' },
        members,
        'writer'
      )
    ).toEqual(['current', 'reader'])
  })
})

describe('buildFleetAuditRequest', () => {
  it('builds a group request', () => {
    expect(buildFleetAuditRequest({ scope: 'group', group: 'aurora' })).toEqual(
      { group: 'aurora' }
    )
  })

  it('builds a multi-target request with explicit targets', () => {
    expect(
      buildFleetAuditRequest({ scope: 'multi', selectedTargets: ['a', 'b'] })
    ).toEqual({ targets: ['a', 'b'] })
  })

  it('builds a capture request when a duration is set', () => {
    expect(
      buildFleetAuditRequest({
        scope: 'group',
        group: 'aurora',
        durationSeconds: 60,
      })
    ).toEqual({ group: 'aurora', duration: 60 })
  })

  it('fleet scope skips unavailable targets via an explicit list', () => {
    expect(
      buildFleetAuditRequest({
        scope: 'fleet',
        availableTargets: ['a', 'c'],
      })
    ).toEqual({ targets: ['a', 'c'] })
  })

  it('fleet scope sends an empty body when including everything', () => {
    expect(
      buildFleetAuditRequest({
        scope: 'fleet',
        availableTargets: ['a', 'c'],
        includeUnavailable: true,
      })
    ).toEqual({})
    expect(buildFleetAuditRequest({ scope: 'fleet' })).toEqual({})
  })
})

describe('buildTargetSelectionRequest', () => {
  it('always sends the checkbox selection explicitly', () => {
    expect(buildTargetSelectionRequest(['writer', 'reader'])).toEqual({
      targets: ['writer', 'reader'],
    })
    expect(buildTargetSelectionRequest(['writer', 'reader'], 3600)).toEqual({
      targets: ['writer', 'reader'],
      duration: 3600,
    })
  })
})

describe('isTargetCapError', () => {
  it('recognizes both cap error codes', () => {
    expect(isTargetCapError('too_many_targets')).toBe(true)
    expect(isTargetCapError('FLEET_AUDIT_TARGET_CAP')).toBe(true)
    expect(isTargetCapError('other')).toBe(false)
    expect(isTargetCapError(undefined)).toBe(false)
  })
})
