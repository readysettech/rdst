import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { availableRanges, buildLoadModel } from '@/features/monitor-kit/charts'
import { CA_NOW, caFixtures } from '@/lib/monitor/fixturesCa'
import { LoadCard, type LoadCardProps } from './LoadCard'

vi.mock('@/features/monitor-kit/charts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/features/monitor-kit/charts')>()),
  StackedLoadChart: ({
    ariaLabel,
    markers,
    mode,
    series,
  }: {
    ariaLabel: string
    markers?: { label?: string }[]
    mode: string
    series: { label: string }[]
  }) => (
    <svg
      role="img"
      aria-label={ariaLabel}
      data-mode={mode}
      data-series={series.map((item) => item.label).join(', ')}
    >
      <title>{markers?.map((marker) => marker.label).join(', ')}</title>
    </svg>
  ),
  MonitorTimeChart: ({ ariaLabel }: { ariaLabel: string }) => (
    <svg role="img" aria-label={ariaLabel} />
  ),
}))

afterEach(cleanup)

const health = caFixtures('ca-database').health('demo', '1h')

function props(extra: Partial<LoadCardProps> = {}): LoadCardProps {
  return {
    selected: 'qps',
    metric: 'calls',
    onMetricChange: vi.fn(),
    range: '1h',
    ranges: availableRanges(health.history, ['15m', '1h', '24h']),
    onRangeChange: vi.fn(),
    caption: '12 min recorded',
    model: null,
    health,
    domain: [CA_NOW - 3600, CA_NOW],
    markers: [{ x: CA_NOW - 60, label: 'Stuck query 9:55' }],
    loading: false,
    hoverKey: null,
    onHoverKey: vi.fn(),
    focusKey: null,
    onSelectKey: vi.fn(),
    ...extra,
  }
}

it('switches the series, names the range and carries the markers', () => {
  const onMetricChange = vi.fn()
  render(<LoadCard {...props({ onMetricChange })} />)
  expect(screen.getByRole('radio', { name: 'Queries/s' })).toBeTruthy()
  fireEvent.click(screen.getByRole('radio', { name: 'Latency' }))
  expect(onMetricChange).toHaveBeenCalledWith('latency')
  expect(screen.getByTestId('range-menu').textContent).toBe('Last hour')
  expect(screen.getByText('12 min recorded')).toBeTruthy()
  expect(
    screen.getByRole('img', { name: 'Queries/s, last hour' }).textContent
  ).toBe('Stuck query 9:55')
})

it('draws the selected health number on its own chart', () => {
  render(<LoadCard {...props({ selected: 'hit' })} />)
  expect(screen.queryByRole('radio', { name: 'Queries/s' })).toBeNull()
  expect(screen.getByRole('heading', { name: 'Cache hit' })).toBeTruthy()
  expect(screen.getByRole('img', { name: 'Cache hit, last hour' })).toBeTruthy()
})

it('holds the range on the first window and says why in a tooltip', async () => {
  render(
    <LoadCard
      {...props({
        rangeLabel: 'First 10 min',
        rangeHint: 'More ranges open after 15 minutes of readings.',
      })}
    />
  )
  const menu = screen.getByTestId('range-menu')
  expect(menu.textContent).toBe('First 10 min')
  expect(menu.hasAttribute('disabled')).toBe(true)
  fireEvent.pointerMove(screen.getByTestId('range-hint'), {
    pointerType: 'mouse',
  })
  expect((await screen.findByRole('tooltip')).textContent).toContain(
    'More ranges open after 15 minutes of readings.'
  )
})

it('draws the busiest queries on the first window, with the line under the chart', () => {
  const xs = [CA_NOW - 120, CA_NOW - 60]
  const model = buildLoadModel(xs, [
    { key: 'a', label: 'orders by id', qps: [4, 5], meanMs: [2, 3] },
    { key: 'b', label: 'cart lines', qps: [1, 2], meanMs: [9, 8] },
  ])
  const firstRun = { firstReport: true }
  const { rerender } = render(
    <LoadCard
      {...props({
        model,
        rangeLabel: 'First 10 min',
        firstRun,
        metric: 'time',
      })}
    />
  )
  const chart = screen.getByRole('img', { name: 'Time spent, first 10 min' })
  expect(chart.getAttribute('data-mode')).toBe('stack')
  expect(chart.getAttribute('data-series')).toBe('cart lines, orders by id')
  expect(screen.getByTestId('first-run-chart').textContent).toContain(
    'Your first daily report runs tonight, just after midnight.'
  )
  rerender(
    <LoadCard
      {...props({
        model,
        rangeLabel: 'First 10 min',
        firstRun,
        metric: 'latency',
      })}
    />
  )
  expect(
    screen
      .getByRole('img', { name: 'Latency, first 10 min' })
      .getAttribute('data-mode')
  ).toBe('lines')
})
