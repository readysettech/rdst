import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { FleetConnectivityEvent, FleetMember } from '../../types/fleet'
import { ScopeSelector } from './ScopeSelector'

afterEach(cleanup)

const members: FleetMember[] = [
  { name: 'alpha', engine: 'postgresql' } as FleetMember,
  { name: 'beta', engine: 'postgresql' } as FleetMember,
]

const unreachable = (name: string): Record<string, FleetConnectivityEvent> => ({
  [name]: {
    type: 'connectivity',
    target_name: name,
    status: 'error',
  } as FleetConnectivityEvent,
})

describe('ScopeSelector', () => {
  it('keeps a selected target in place when a probe later reports it unreachable', () => {
    const { rerender } = render(
      <ScopeSelector
        members={members}
        connectivity={{}}
        selection={['alpha']}
        onSelectionChange={() => {}}
      />
    )
    expect(screen.getByLabelText('Select alpha')).toBeTruthy()

    // The verdict arrives after the user has already chosen the target.
    rerender(
      <ScopeSelector
        members={members}
        connectivity={unreachable('alpha')}
        selection={['alpha']}
        onSelectionChange={() => {}}
      />
    )

    // Still reachable in the DOM (not collapsed into "Unavailable"), and
    // honestly labelled.
    expect(screen.getByLabelText('Select alpha')).toBeTruthy()
    expect(screen.getAllByText('Unreachable').length).toBeGreaterThan(0)
  })

  it('collapses an unreachable target that the user has not selected', () => {
    render(
      <ScopeSelector
        members={members}
        connectivity={unreachable('beta')}
        selection={[]}
        onSelectionChange={() => {}}
      />
    )

    expect(screen.queryByLabelText('Select beta')).toBeNull()
    expect(screen.getByText('Unavailable (1)')).toBeTruthy()
  })
})
