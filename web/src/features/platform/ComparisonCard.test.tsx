import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import type { LoadSample } from '../../lib/useDemo'
import { ComparisonCard } from './ComparisonCard'

afterEach(cleanup)

const base = new Date(2026, 9, 8, 9, 47, 0).getTime() / 1000

function sample(t: number, directQps: number, routerQps: number): LoadSample {
  return {
    t,
    direct: { qps: directQps, p50_ms: 5, p95_ms: 38 },
    router: { qps: routerQps, p50_ms: 1, p95_ms: 2 },
  }
}

const samples = Array.from({ length: 61 }, (_, i) =>
  sample(base + i, 100, i < 30 ? 100 : 250)
)

describe('ComparisonCard', () => {
  it('draws both paths with the legend, the window and the cache marker', () => {
    renderWithClient(
      <ComparisonCard
        samples={samples}
        events={[
          { t: base + 30, type: 'manual_cache', label: 'you cached A' },
          { t: base + 31, type: 'manual_cache', label: 'you cached B' },
        ]}
        metric="qps"
        onMetric={vi.fn()}
      />
    )
    expect(
      screen.getByRole('img', { name: 'Queries per second on each path' })
    ).toBeTruthy()
    expect(screen.getByText('Readyset path')).toBeTruthy()
    expect(screen.getByText('Postgres direct')).toBeTruthy()
    expect(screen.getByText('last 1 minute')).toBeTruthy()
    expect(screen.getByTestId('chart-marker').textContent).toBe('2 caches 9:47')
    expect(
      screen
        .getByRole('radio', { name: 'Queries/s' })
        .getAttribute('aria-checked')
    ).toBe('true')
  })

  it('switches to latency through the segmented control', () => {
    const onMetric = vi.fn()
    renderWithClient(
      <ComparisonCard
        samples={samples}
        events={[]}
        metric="latency"
        onMetric={onMetric}
      />
    )
    expect(
      screen.getByRole('img', { name: 'Latency on each path' })
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: 'Queries/s' }))
    expect(onMetric).toHaveBeenCalledWith('qps')
  })

  it('waits for traffic with no samples', () => {
    renderWithClient(
      <ComparisonCard
        samples={[]}
        events={[]}
        metric="qps"
        onMetric={vi.fn()}
      />
    )
    expect(screen.getByText('Waiting for traffic')).toBeTruthy()
    expect(screen.queryByTestId('chart-marker')).toBeNull()
  })
})
