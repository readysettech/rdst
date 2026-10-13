import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { at, caFixtures } from '@/lib/monitor/fixturesCa'
import { healthMetrics } from '../databaseModel'
import { HealthMetrics } from './HealthMetrics'

afterEach(cleanup)

const metrics = healthMetrics(caFixtures('ca-database').health('demo', '1h'), {
  day: false,
  cacheReason: 'shared_buffers is the default',
  firstRun: false,
})

it('shows four numbers with the selected one pressed and the reason in tone', () => {
  const onSelect = vi.fn()
  render(<HealthMetrics metrics={metrics} selected="qps" onSelect={onSelect} />)
  const buttons = screen.getAllByRole('button')
  expect(buttons).toHaveLength(4)
  expect(buttons[0].getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByText('14.2')).toBeTruthy()
  expect(screen.getByText('of 100')).toBeTruthy()
  expect(screen.getByText('shared_buffers is the default')).toBeTruthy()
  expect(screen.getByText('0 deadlocks')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: /Cache hit/ }))
  expect(onSelect).toHaveBeenCalledWith('hit')
})

it('keeps the last numbers without a selection while readings are missing', () => {
  render(
    <HealthMetrics
      metrics={metrics}
      selected="qps"
      onSelect={vi.fn()}
      staleSince={at(9, 40, 58)}
    />
  )
  expect(screen.queryAllByRole('button')).toHaveLength(0)
  expect(screen.getByText('last reading 9:40:58')).toBeTruthy()
  // The trend words describe readings that stopped; the reasons still explain the numbers.
  expect(screen.queryByText('0 deadlocks')).toBeNull()
  expect(screen.getByText('shared_buffers is the default')).toBeTruthy()
})

it('says nothing is steady once the readings stop', () => {
  const steady = metrics.map((metric) =>
    metric.id === 'connections' ? { ...metric, detail: 'steady' } : metric
  )
  render(
    <HealthMetrics
      metrics={steady}
      selected="qps"
      onSelect={vi.fn()}
      staleSince={at(9, 40, 58)}
    />
  )
  expect(screen.queryByText('steady')).toBeNull()
})
