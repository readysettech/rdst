import { fireEvent, render, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const navigate = vi.hoisted(() => vi.fn())

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return {
    ...(await importOriginal<typeof import('@tanstack/react-router')>()),
    ...fileRouteModuleMock({ useNavigate: () => navigate }),
  }
})

vi.mock('../../components/AnimatedSurfaceBackdrop', () => ({
  AnimatedSurfaceBackdrop: () => null,
}))

vi.mock('../../components/HandRaiser', () => ({
  HandRaiser: () => null,
}))

import { ActiveHome, ConnectedHome, FirstRunHome } from './HomePage'

describe('FirstRunHome', () => {
  it('leads with the ungated demo and keeps both setup paths visible', () => {
    const { container } = render(<FirstRunHome aiKeyState="missing" />)
    const scope = within(container)

    expect(scope.getByText('See Readyset under real load')).toBeTruthy()
    expect(scope.getByText('Launch the demo')).toBeTruthy()
    expect(scope.getByText('Connect your database')).toBeTruthy()
    expect(scope.getAllByText('Configure AI')).toHaveLength(2)
  })

  it('reflects an already configured AI key', () => {
    const { container } = render(<FirstRunHome aiKeyState="ready" />)
    expect(within(container).getByText('Key configured')).toBeTruthy()
  })
})

describe('ConnectedHome', () => {
  it('makes semantic discovery primary without hiding the demo', () => {
    const { container } = render(
      <ConnectedHome target="large" aiKeyState="ready" />
    )
    const scope = within(container)

    expect(scope.getByText('Build the semantic layer for large')).toBeTruthy()
    expect(scope.getByText('Discover schema')).toBeTruthy()
    expect(scope.getByText('See Readyset under real load')).toBeTruthy()
    expect(scope.getByText('Run a health check')).toBeTruthy()
    expect(scope.getByText('Ask with live introspection')).toBeTruthy()
  })

  it('routes discovery through AI setup when the key is missing', () => {
    const { container } = render(
      <ConnectedHome target="large" aiKeyState="missing" />
    )
    const scope = within(container)

    expect(scope.getByText('Configure AI first')).toBeTruthy()
    expect(scope.queryByText('Discover schema')).toBeNull()
  })
})

describe('ActiveHome', () => {
  it('keeps the demo prominent beside an honest operational summary', () => {
    const { container } = render(
      <ActiveHome
        target="demo"
        counts={{ asked: 3, analyzed: 8, compared: 2, candidates: 4 }}
        recents={[]}
        registryState="ready"
        auditState="ready"
        lastAuditLabel={null}
        retentionDays={null}
        retryRegistry={vi.fn()}
      />
    )
    const scope = within(container)

    expect(scope.getByText('4 Readyset candidates to measure')).toBeTruthy()
    expect(scope.getByText('See Readyset under real load')).toBeTruthy()
    expect(scope.getByText('Compare candidates')).toBeTruthy()
    expect(scope.getByText('Never run')).toBeTruthy()
  })

  it('leads with recall when a recent item exists (C2: recall over portfolio tiles)', () => {
    const { container } = render(
      <ActiveHome
        target="demo"
        counts={{ asked: 3, analyzed: 8, compared: 2, candidates: 4 }}
        recents={[
          {
            hash: 'abc123',
            label: 'select * from orders',
            kind: 'analyzed',
            nextAction: 'Review',
            sql: 'select * from orders',
            lastActivity: '2026-08-20T00:00:00Z',
            avgDurationMs: 12,
            frequency: 4,
          },
        ]}
        registryState="ready"
        auditState="ready"
        lastAuditLabel={null}
        retentionDays={null}
        retryRegistry={vi.fn()}
      />
    )
    const scope = within(container)
    const headings = scope.getAllByText(
      /Continue where you left off|Readyset candidates to measure/
    )
    expect(headings[0].textContent).toBe('Continue where you left off')
  })

  it('reopens the analysis an item already has instead of re-running it (A6)', () => {
    navigate.mockClear()
    const { container } = render(
      <ActiveHome
        target="demo"
        counts={{ asked: 0, analyzed: 1, compared: 0, candidates: 0 }}
        recents={[
          {
            hash: 'abc123',
            label: 'select * from orders',
            kind: 'analyzed',
            nextAction: 'Review',
            sql: 'select * from orders',
            lastActivity: '2026-08-20T00:00:00Z',
            avgDurationMs: 12,
            frequency: 4,
          },
        ]}
        registryState="ready"
        auditState="ready"
        lastAuditLabel={null}
        retentionDays={null}
        retryRegistry={vi.fn()}
      />
    )

    fireEvent.click(
      within(container).getByRole('button', {
        name: 'Continue with review for select * from orders',
      })
    )

    expect(navigate).toHaveBeenCalledWith({
      to: '/queries',
      search: { analyze: 'abc123' },
    })
  })

  it('does not render false zero metrics while the Query Library is loading', () => {
    const { container } = render(
      <ActiveHome
        target="demo"
        counts={{ asked: 0, analyzed: 0, compared: 0, candidates: 0 }}
        recents={[]}
        registryState="loading"
        auditState="loading"
        lastAuditLabel={null}
        retentionDays={null}
        retryRegistry={vi.fn()}
      />
    )
    const scope = within(container)

    expect(scope.queryByText('Asked')).toBeNull()
    expect(scope.getByText('See Readyset under real load')).toBeTruthy()
    expect(scope.getByText('Checking history')).toBeTruthy()
  })
})
