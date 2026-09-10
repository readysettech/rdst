import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
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
  it('says the inventory is loading instead of "0 targets selected"', () => {
    render(
      <ScopeSelector
        members={[]}
        connectivity={{}}
        selection={[]}
        onSelectionChange={() => {}}
        isPending
      />
    )

    expect(screen.getByText('Loading targets')).toBeTruthy()
    expect(screen.queryByText('0 targets selected')).toBeNull()
    expect(
      screen
        .getByRole('button', { name: /Select all|Clear all/ })
        .hasAttribute('disabled')
    ).toBe(true)
  })

  it('replaces the picker with an empty state when no target is connected', () => {
    const onAddTarget = vi.fn()
    render(
      <ScopeSelector
        members={[]}
        connectivity={{}}
        selection={[]}
        onSelectionChange={() => {}}
        onAddTarget={onAddTarget}
      />
    )

    expect(screen.getByRole('heading', { name: 'No targets yet' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add a target' })).toBeTruthy()
    // The label read "Clear all" over an empty list: 0 === 0. [E-09]
    expect(
      screen.queryByRole('button', { name: /Select all|Clear all/ })
    ).toBeNull()
    expect(screen.queryByText('0 targets selected')).toBeNull()
  })

  it('reports an empty selection once the inventory has landed', () => {
    render(
      <ScopeSelector
        members={members}
        connectivity={{}}
        selection={[]}
        onSelectionChange={() => {}}
      />
    )

    expect(screen.getByText('0 targets selected')).toBeTruthy()
  })

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

  it('keeps an unreachable, unselected target in place with a badge', () => {
    render(
      <ScopeSelector
        members={members}
        connectivity={unreachable('beta')}
        selection={[]}
        onSelectionChange={() => {}}
      />
    )

    // The row holds its position (never collapsed into a separate section) and
    // is honestly labelled as unreachable.
    expect(screen.getByLabelText('Select beta')).toBeTruthy()
    expect(screen.getAllByText('Unreachable').length).toBeGreaterThan(0)
    expect(screen.queryByText(/^Unavailable/)).toBeNull()
  })

  it('renders every target in the delivered order regardless of connectivity', () => {
    render(
      <ScopeSelector
        members={members}
        connectivity={unreachable('alpha')}
        selection={['beta']}
        onSelectionChange={() => {}}
      />
    )
    const labels = screen
      .getAllByRole('checkbox')
      .map((node) => node.getAttribute('aria-label'))
    expect(labels).toEqual(['Select alpha', 'Select beta'])
  })
})
