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
    expect(screen.getByText('12s / 1m 0s')).toBeTruthy()
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
