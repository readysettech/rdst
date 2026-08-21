import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BenchmarkConfirmDialog } from './BenchmarkConfirmDialog'

afterEach(cleanup)

function renderDialog(overrides: { isRemote?: boolean; readyset: boolean }) {
  render(
    <BenchmarkConfirmDialog
      isOpen
      target="demo"
      isRemote={overrides.isRemote ?? false}
      queryCount={2}
      loadSummary="100ms rest after completion · 30s"
      includesReadyset={overrides.readyset}
      estimatedExecutions={300}
      executionCap={100_000}
      onConfirm={vi.fn()}
      onClose={vi.fn()}
    />
  )
}

describe('BenchmarkConfirmDialog lanes', () => {
  it('says nothing about Readyset for an origin-only run', () => {
    renderDialog({ readyset: false })

    expect(screen.getByText(/This runs real database load/)).toBeTruthy()
    expect(screen.queryByText(/Readyset/)).toBeNull()
  })

  it('names the Readyset lane and its temporary caches when the run drives it', () => {
    renderDialog({ readyset: true })

    expect(screen.getByText(/also drives Readyset alongside it/)).toBeTruthy()
    expect(screen.getByText(/temporary cache per query/)).toBeTruthy()
  })

  it('keeps the remote warning and adds the lane to it', () => {
    renderDialog({ isRemote: true, readyset: true })

    expect(screen.getByText(/is not a local target/)).toBeTruthy()
    expect(screen.getByText(/also drives Readyset alongside it/)).toBeTruthy()
  })
})
