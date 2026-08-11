import { render, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return {
    ...(await importOriginal<typeof import('@tanstack/react-router')>()),
    ...fileRouteModuleMock({ useNavigate: () => vi.fn() }),
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
