import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { HistoryEntry } from '../../lib/auditHistory'
import { RunHistory } from './RunHistory'

afterEach(cleanup)

function entry(partial: Partial<HistoryEntry> = {}): HistoryEntry {
  return {
    id: 'run-1',
    kind: 'single',
    scopeLabel: 'e2e-guard',
    engine: 'postgres',
    targetNames: ['e2e-guard'],
    mode: 'snapshot',
    durationSeconds: 0,
    queryCount: 4,
    targetsAudited: 1,
    startedAt: '2026-09-05T14:00:00.000Z',
    hasAnalysis: false,
    ...partial,
  }
}

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

describe('RunHistory row names (E-17)', () => {
  it('separates same-target runs by scope, kind and time', () => {
    render(
      <RunHistory
        entries={[
          entry({ id: 'run-1', startedAt: '2026-09-05T14:00:00.000Z' }),
          entry({ id: 'run-2', startedAt: '2026-09-05T09:30:00.000Z' }),
          entry({
            id: 'run-3',
            kind: 'fleet',
            scopeLabel: 'All targets',
            startedAt: '2026-09-05T08:00:00.000Z',
          }),
        ]}
        activeId={null}
        loadingId={null}
        onOpen={vi.fn()}
      />
    )

    const names = screen
      .getAllByRole('button')
      .map((row) => row.getAttribute('aria-label'))
      .filter((name): name is string => !!name && name.startsWith('Open the '))

    expect(names).toHaveLength(3)
    expect(new Set(names).size).toBe(3)
    for (const name of names) {
      expect(name).toMatch(/^Open the .+ health check from .+/)
    }
    expect(names[2]).toContain('All targets fleet health check')
  })
})
