import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RunHistory } from './RunHistory'

afterEach(cleanup)

describe('RunHistory empty state (C3)', () => {
  it('replaces a blank panel with what Reports are for and a way to start one', () => {
    const onStartRun = vi.fn()
    render(
      <RunHistory
        entries={[]}
        activeId={null}
        loadingId={null}
        onOpen={vi.fn()}
        onStartRun={onStartRun}
      />
    )

    expect(screen.getByText('No reports yet')).toBeTruthy()
    const cta = screen.getByRole('button', { name: 'Run a health check' })
    cta.click()
    expect(onStartRun).toHaveBeenCalledTimes(1)
  })

  it('omits the action when no handler is supplied', () => {
    render(
      <RunHistory
        entries={[]}
        activeId={null}
        loadingId={null}
        onOpen={vi.fn()}
      />
    )

    expect(screen.getByText('No reports yet')).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'Run a health check' })
    ).toBeNull()
  })
})
