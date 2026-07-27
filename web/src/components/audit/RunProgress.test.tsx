import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { RunProgress } from './RunProgress'

afterEach(cleanup)

describe('RunProgress', () => {
  it('shows only the current phase with capture-window timing', () => {
    const view = render(
      <RunProgress
        phase="capture"
        statusMessage="Capturing"
        durationSeconds={60}
        elapsedSeconds={15}
      />
    )

    expect(screen.getByText('Capturing live database activity')).toBeTruthy()
    expect(screen.getByText('15s / 1m 0s')).toBeTruthy()
    expect(screen.queryByText('25%')).toBeNull()
    expect(screen.queryByText('Connect')).toBeNull()
    expect(screen.queryByText('Snapshot')).toBeNull()
    expect(screen.queryByText('Query capture')).toBeNull()

    view.rerender(
      <RunProgress
        phase="readyset"
        statusMessage="Starting Readyset and warming cache"
        durationSeconds={60}
        elapsedSeconds={60}
      />
    )

    expect(screen.getByText('Benchmarking against Readyset')).toBeTruthy()
    expect(screen.getByText('Running the Readyset comparison…')).toBeTruthy()
    expect(screen.queryByText(/Starting Readyset/)).toBeNull()
    expect(screen.queryByText(/warming cache/)).toBeNull()
  })
})
