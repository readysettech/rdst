import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const startCacheTestRun = vi.fn()

vi.mock('../../../lib/backgroundRuns', () => ({
  startCacheTestRun: (...args: unknown[]) => startCacheTestRun(...args),
  useBackgroundRuns: () => [],
}))

import { LocalCompatibilityLab } from './LocalCompatibilityLab'

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('LocalCompatibilityLab', () => {
  it('stays secondary until the user opens the advanced lab', () => {
    render(<LocalCompatibilityLab target="demo" query="SELECT * FROM posts" />)

    expect(screen.getByText('Advanced')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Test query' })).toBeNull()

    fireEvent.click(
      screen.getByRole('button', { name: /Local compatibility lab/ })
    )

    expect(screen.getByRole('button', { name: 'Test query' })).toBeTruthy()
    expect(screen.getByText(/No persistent cache/)).toBeTruthy()
  })

  it('runs the shared temporary Readyset experiment', async () => {
    startCacheTestRun.mockResolvedValue('speed_test_demo_1')
    render(<LocalCompatibilityLab target="demo" query="SELECT * FROM posts" />)

    fireEvent.click(
      screen.getByRole('button', { name: /Local compatibility lab/ })
    )
    fireEvent.click(screen.getByRole('button', { name: 'Test query' }))

    await waitFor(() => {
      expect(startCacheTestRun).toHaveBeenCalledWith({
        target: 'demo',
        query: 'SELECT * FROM posts',
        label: 'Analyze compatibility test',
        iterations: 15,
        warmup: 5,
      })
    })
  })

  it('cannot open while the selected target is locked', () => {
    render(<LocalCompatibilityLab target="prod" disabled />)

    expect(
      (
        screen.getByRole('button', {
          name: /Local compatibility lab/,
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
  })
})
