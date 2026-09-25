import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BenchmarkDialog } from './BenchmarkDialog'
import type { QueryBenchmark } from './useQueryBenchmark'

vi.mock('@rs/ui-new/modal', () => ({
  Modal: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  ModalContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  ModalContentContainer: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  ModalTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
  ModalDescription: ({ children }: { children: ReactNode }) => (
    <p>{children}</p>
  ),
}))
vi.mock('./BenchmarkQueryPicker', () => ({
  BenchmarkQueryPicker: () => <div>Query picker</div>,
}))

afterEach(cleanup)

function bench(ready = false) {
  return {
    controller: {
      selectedCount: 2,
      destinationLock: { isLocked: false },
      connectivity: { failure: null },
      destinationIsRemote: false,
    },
    readyset: {
      state: ready ? 'setup' : 'unavailable',
      note: ready
        ? 'RDST starts Readyset in a temporary container.'
        : "Docker isn't running.",
    },
    runChoice: 'source',
    setRunChoice: vi.fn(),
    presetId: 'quick',
    setPresetId: vi.fn(),
    start: vi.fn().mockResolvedValue(undefined),
    blocker: null,
  } as unknown as QueryBenchmark
}

describe('Benchmark choices', () => {
  it('offers Quick and Standard and applies the same Docker readiness to both Readyset choices', () => {
    render(
      <BenchmarkDialog
        open
        onClose={vi.fn()}
        bench={bench()}
        target="shop"
        queryLabel="Query"
      />
    )
    expect(screen.getByRole('radio', { name: /Quick/ })).toBeTruthy()
    expect(screen.getByRole('radio', { name: /Standard/ })).toBeTruthy()
    expect(screen.getByRole('radio', { name: /Source only/ })).toBeTruthy()
    expect(
      screen
        .getByRole('radio', { name: /Source plus Readyset/ })
        .hasAttribute('disabled')
    ).toBe(true)
    expect(
      screen
        .getByRole('radio', { name: /Readyset only/ })
        .hasAttribute('disabled')
    ).toBe(true)
  })

  it('allows Readyset only without a deployed target', () => {
    const model = bench(true)
    const { rerender } = render(
      <BenchmarkDialog
        open
        onClose={vi.fn()}
        bench={model}
        target="shop"
        queryLabel="Query"
      />
    )
    const readyset = screen.getByRole('radio', { name: /Readyset only/ })
    expect(readyset.hasAttribute('disabled')).toBe(false)
    fireEvent.click(readyset)
    expect(model.setRunChoice).toHaveBeenCalledWith('readyset')
    expect(screen.getAllByText(/temporary container/)).toHaveLength(2)
    model.runChoice = 'readyset'
    rerender(
      <BenchmarkDialog
        open
        onClose={vi.fn()}
        bench={model}
        target="shop"
        queryLabel="Query"
      />
    )
    expect(
      screen.getByText(/first Readyset run downloads a small container/)
    ).toBeTruthy()
    expect(screen.getByText('2 queries')).toBeTruthy()
  })
})
