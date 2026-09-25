import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { collectNow } from '@/lib/monitor/api'
import {
  MonitorHeartbeat,
  ReadingActions,
  readingLine,
} from './MonitorHeartbeat'
import { type MonitorPulseState, readingClock } from './useMonitorPulse'

vi.mock('@/lib/monitor/api', () => ({ collectNow: vi.fn() }))
const testConnection = vi.fn()
vi.mock('@/lib/useConfigure', () => ({
  useConfigure: () => ({ loading: false, testConnection }),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
const pulse = (
  overrides: Partial<MonitorPulseState> = {}
): MonitorPulseState => ({
  target: 'orders',
  pulses: [],
  last: null,
  lastCollect: null,
  state: 'late',
  nextAt: 1060,
  now: 1400,
  clock: readingClock(1000, 1060, 1400),
  beat: 0,
  isLoading: false,
  delayReason: 'Waiting for the database to answer',
  ...overrides,
})
function renderActions(value = pulse()) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ReadingActions pulse={value} />
    </QueryClientProvider>
  )
}
it('checks now and shows the returned reading result', async () => {
  vi.mocked(collectNow).mockResolvedValue({
    ok: true,
    detail: 'Reading complete',
  })
  renderActions()
  fireEvent.click(screen.getByRole('button', { name: 'Check now' }))
  expect(await screen.findByText('Reading complete')).toBeTruthy()
  expect(collectNow).toHaveBeenCalledWith('orders')
})
it('shows failed readings and offers connection recovery after five minutes', async () => {
  vi.mocked(collectNow).mockResolvedValue({
    ok: false,
    detail: 'The reading timed out',
  })
  testConnection.mockResolvedValue({ connected: true })
  renderActions()
  fireEvent.click(screen.getByRole('button', { name: 'Check now' }))
  expect(await screen.findByText('The reading timed out')).toBeTruthy()
  expect(
    screen
      .getByRole('link', { name: 'Connection settings' })
      .getAttribute('href')
  ).toBe('/configure?panel=connections&edit=orders')
  fireEvent.click(screen.getByRole('button', { name: 'Test connection' }))
  expect(await screen.findByText('Connection works')).toBeTruthy()
  expect(testConnection).toHaveBeenCalledWith('orders')
})
it('keeps connection recovery out of a short delay', () => {
  renderActions(pulse({ now: 1200, clock: readingClock(1000, 1060, 1200) }))
  expect(screen.queryByRole('button', { name: 'Test connection' })).toBeNull()
})
it('uses calm first-reading text with persisted readings from the old backend', () => {
  const value = pulse({ firstReading: true })
  expect(readingLine(value)).toBe('Taking the first reading')
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MonitorHeartbeat pulse={value} />
    </QueryClientProvider>
  )
  expect(screen.getByText('Taking the first reading')).toBeTruthy()
  expect(screen.queryByText('Readings delayed')).toBeNull()
  expect(screen.queryByRole('button', { name: 'Check now' })).toBeNull()
})
it('explains a delayed reading with the backend reason', () => {
  expect(readingLine(pulse())).toContain('Waiting for the database to answer')
})
