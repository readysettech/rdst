import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { BenchmarkFixesDialog } from './BenchmarkFixesDialog'

afterEach(cleanup)

it('names the queries with fixes, says what it compares and offers one action', () => {
  const onRun = vi.fn()
  render(
    <BenchmarkFixesDialog count={9} open onClose={vi.fn()} onRun={onRun} />
  )
  expect(
    screen.getByRole('heading', { name: 'Benchmark 9 queries with fixes' })
  ).toBeTruthy()
  expect(screen.queryByRole('switch')).toBeNull()
  expect(
    screen.getByText(
      "Compares each query's current time with the planner's estimate for its fix, the same estimate Actions shows. Nothing runs on the database."
    )
  ).toBeTruthy()
  expect(screen.queryByText(/scratch schema|20 times/)).toBeNull()
  expect(screen.getByText('Takes a few seconds.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /Cancel|Not now/ })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'Run benchmark' }))
  expect(onRun).toHaveBeenCalledOnce()
})
