import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { AnalyzeRun } from './AnalyzeRun'
import { emptyRunView } from './runModel'

afterEach(cleanup)

it('counts completed analyses when a report finishes with eleven failures', () => {
  const view = emptyRunView(1000)
  view.state = 'done'
  view.queries = Array.from({ length: 12 }, (_, index) => ({
    key: `q${index}`,
    displayName: `Query ${index}`,
    dbTimeShare: 0.01,
    state: index === 0 ? 'done' : 'failed',
    reason: null,
    resultLine: null,
    durationS: null,
    phase: null,
    headline: null,
  }))
  const { container } = render(
    <AnalyzeRun
      target="db"
      onShowReport={vi.fn()}
      run={{
        view,
        active: false,
        status: 'done',
        reportId: 1,
        error: null,
        changedAt: 1000,
        start: vi.fn(),
        cancel: vi.fn(),
        dismiss: vi.fn(),
      }}
    />
  )
  expect(
    screen.getAllByText(/1 of 12 analyses finished/).length
  ).toBeGreaterThan(0)
  expect(container.textContent).toContain('11 failed')
  expect(container.textContent).not.toMatch(/12 queries analyzed/)
})
