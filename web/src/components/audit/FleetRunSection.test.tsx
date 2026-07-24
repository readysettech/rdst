import { cleanup, screen } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { TRIAL_EXHAUSTED_MESSAGE } from '../../lib/errorContract'
import type { FleetAuditTargetState } from '../../lib/useFleet'
import { FleetRunSection } from './FleetRunSection'

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}))

afterEach(cleanup)

const callbacks = {
  onRetryTarget: vi.fn(),
  onSetPassword: vi.fn(),
  onAdjustTargets: vi.fn(),
}

type SectionProps = ComponentProps<typeof FleetRunSection>

const baseProps: SectionProps = {
  scopeLabel: 'Running on 1 target: alpha',
  state: 'running',
  phase: undefined,
  targets: {},
  statusMessage: 'Working',
  summary: undefined,
  snapshotId: undefined,
  error: undefined,
  errorCode: undefined,
  captureDuration: 60,
  ...callbacks,
}

/**
 * Render the section and return a `rerenderWith` bound to the same props and
 * QueryClient, so a test only re-states the props its next frame changes.
 */
function renderSection(overrides: Partial<SectionProps> = {}) {
  const props = { ...baseProps, ...overrides }
  const view = renderWithClient(<FleetRunSection {...props} />)
  return {
    ...view,
    rerenderWith: (next: Partial<SectionProps>) =>
      view.rerender(<FleetRunSection {...props} {...next} />),
  }
}

function renderRun(
  target: FleetAuditTargetState,
  phase: SectionProps['phase'] = target.phase,
  state: 'running' | 'complete' = 'running'
) {
  return renderSection({
    state,
    phase,
    targets: { alpha: target },
    summary: state === 'complete' ? { successes: 1, failures: 0 } : undefined,
  })
}

describe('FleetRunSection live activity', () => {
  it('shows only the latest target substep and advances with phase events', () => {
    const now = Date.now()
    const view = renderRun({
      status: 'running',
      phase: 'capture',
      phaseStartedAt: now,
      captureStartedAt: now,
      captureElapsedSeconds: 15,
      captureTotalSeconds: 60,
    })

    expect(screen.getByText('Capturing live database activity')).toBeTruthy()
    expect(screen.getByText('15s / 1m 0s')).toBeTruthy()
    expect(screen.queryByText('25%')).toBeNull()
    expect(screen.queryByText('Query capture')).toBeNull()
    expect(screen.queryByText('Starting Readyset')).toBeNull()
    expect(screen.queryByText('Warming cache')).toBeNull()

    view.rerenderWith({
      phase: 'analysis',
      targets: {
        alpha: { status: 'running', phase: 'analysis', phaseStartedAt: now },
      },
      statusMessage: 'Analyzing',
    })

    expect(screen.getByText('Analyzing captured workload')).toBeTruthy()
    expect(screen.queryByText('Capturing live database activity')).toBeNull()
  })

  it('collapses all Readyset work under one user-facing activity', () => {
    renderRun({
      status: 'running',
      phase: 'readyset',
      phaseStartedAt: Date.now(),
      benchmarkStep: 'deploying',
    })

    expect(
      screen.getAllByText('Benchmarking against Readyset').length
    ).toBeGreaterThan(0)
    expect(screen.queryByText('Starting Readyset')).toBeNull()
    expect(screen.queryByText(/Warming/)).toBeNull()
  })

  it('replaces target rows with the combined-insights step', () => {
    renderRun(
      {
        status: 'done',
        phase: 'readyset',
        verdict: 'right_sized',
      },
      'insights'
    )

    expect(screen.getByText('Generating combined insights')).toBeTruthy()
    expect(screen.queryByText('alpha')).toBeNull()
  })

  it('shows the Readyset comparison summary for a completed target', () => {
    renderRun({
      status: 'done',
      verdict: 'right_sized',
      cacheScore: 80,
      readysetComparison: {
        queries_tested: 4,
        supported_count: 3,
        avg_speedup: 8.5,
        queries: [],
      },
    })

    expect(screen.getByText('Readyset 3/4 cacheable · 8.5x avg')).toBeTruthy()
  })

  it('renders a no-query Readyset skip as a calm completed result', () => {
    renderRun(
      {
        status: 'done',
        notice: 'Readyset benchmark skipped; no live queries were captured',
      },
      'readyset',
      'complete'
    )

    expect(screen.getByText('Done')).toBeTruthy()
    expect(
      screen.getByText('Complete · Readyset benchmark not needed')
    ).toBeTruthy()
    expect(screen.queryByText('Failed')).toBeNull()
  })

  it('shows simultaneous captures and only one active benchmark afterward', () => {
    const now = Date.now()
    const capturing = {
      status: 'running' as const,
      phase: 'capture',
      captureStartedAt: now,
      captureElapsedSeconds: 5,
      captureTotalSeconds: 30,
    }
    const view = renderSection({
      scopeLabel: 'Running on 2 targets: alpha, beta',
      phase: 'capture',
      targets: { alpha: capturing, beta: capturing },
      statusMessage: 'Capturing both targets',
      captureDuration: 30,
    })

    expect(screen.getAllByText('Capturing', { exact: true })).toHaveLength(2)
    expect(
      screen.getByText('2 targets capturing active queries in parallel')
    ).toBeTruthy()

    view.rerenderWith({
      phase: 'readyset',
      targets: {
        alpha: {
          status: 'running',
          phase: 'readyset',
          benchmarkStep: 'deploying',
        },
        beta: { status: 'running', phase: 'capture_complete' },
      },
      statusMessage: 'Benchmarking alpha',
    })

    expect(
      screen.getByText('Benchmarking alpha; 1 capture queued')
    ).toBeTruthy()
    expect(screen.getAllByText('Benchmarking', { exact: true })).toHaveLength(1)
    expect(screen.getAllByText('Queued', { exact: true })).toHaveLength(1)
  })

  it('keeps overall progress below complete while combined insights run', () => {
    renderSection({
      scopeLabel: 'Running on 2 targets: alpha, beta',
      phase: 'insights',
      targets: {
        alpha: { status: 'done', verdict: 'right_sized' },
        beta: { status: 'done', verdict: 'right_sized' },
      },
      statusMessage: 'Generating fleet insights',
      captureDuration: 30,
    })

    expect(
      screen.getByText(
        'Generating combined insights; the report is still in progress'
      )
    ).toBeTruthy()
    const progress = screen.getByRole('progressbar')
    expect(Number(progress.getAttribute('aria-valuenow'))).toBeLessThan(100)
    expect(screen.queryByText('Health check complete')).toBeNull()
  })

  it('shows specific exhausted-trial recovery for a mid-run target failure', () => {
    renderRun(
      {
        status: 'error',
        error: 'TRIAL_EXHAUSTED: no trial credit left',
      },
      'insights',
      'complete'
    )

    expect(screen.getByText(TRIAL_EXHAUSTED_MESSAGE)).toBeTruthy()
    expect(screen.getByRole('button', { name: /Set key/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Start trial/ })).toBeTruthy()
    expect(screen.queryByText('Fleet health check failed')).toBeNull()
  })
})
