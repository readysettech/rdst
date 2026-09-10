import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RunProgress } from './RunProgress'

vi.mock('../../lib/auditSession', () => ({ cancelActiveAudit: vi.fn() }))

describe('RunProgress', () => {
  afterEach(cleanup)

  it('shows the capture countdown during the capture phase', () => {
    render(
      <RunProgress
        phase="capture"
        statusMessage="Capturing queries for 60s..."
        durationSeconds={60}
        elapsedSeconds={12}
        totalElapsedSeconds={12}
      />
    )
    expect(screen.getByText('12s of 1m 0s · ~48s left')).toBeTruthy()
    const bar = screen.getByRole('progressbar', {
      name: 'Capturing live database activity progress',
    })
    expect(bar.getAttribute('aria-valuenow')).toBe('20')
  })

  it('carries the run scope inside the card, beside the status', () => {
    render(
      <RunProgress
        phase="capture"
        scopeLabel="Running on e2e-guard"
        statusMessage="Capturing queries for 60s..."
        durationSeconds={60}
        elapsedSeconds={12}
        totalElapsedSeconds={12}
      />
    )
    expect(
      screen.getByText('Running on e2e-guard · Capturing queries for 60s...')
    ).toBeTruthy()
  })

  it('keeps an indeterminate track through a phase with no countable end', () => {
    render(
      <RunProgress
        phase="analysis"
        statusMessage="Running final analysis..."
        durationSeconds={60}
        elapsedSeconds={60}
        totalElapsedSeconds={95}
      />
    )
    const bar = screen.getByRole('progressbar', {
      name: 'Analyzing captured workload progress',
    })
    expect(bar.getAttribute('aria-valuenow')).toBeNull()
  })

  it('keeps an elapsed clock visible through the analysis phase', () => {
    render(
      <RunProgress
        phase="analysis"
        statusMessage="Running final analysis..."
        durationSeconds={60}
        elapsedSeconds={60}
        totalElapsedSeconds={95}
      />
    )
    expect(screen.getByText('Analyzing captured workload')).toBeTruthy()
    expect(screen.getByText('1m 35s elapsed')).toBeTruthy()
  })

  it('shows the clock during collection phases before the window opens', () => {
    render(
      <RunProgress
        phase="audit"
        statusMessage="Collecting health and sizing metrics..."
        durationSeconds={60}
        elapsedSeconds={0}
        totalElapsedSeconds={8}
      />
    )
    expect(screen.getByText('8s elapsed')).toBeTruthy()
  })
})
