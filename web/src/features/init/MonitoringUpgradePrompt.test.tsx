import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { jsonResponse, renderWithClient } from '@/test-utils'
import { MonitoringUpgradePrompt } from './MonitoringUpgradePrompt'

vi.mock('@tanstack/react-router', () => ({ useRouterState: () => '/' }))
vi.mock('@/lib/monitor/hooks', () => ({
  useMonitoringLimit: () => ({ data: { signed_in: false } }),
}))
vi.mock('@/features/database/settings/EnableMonitoringDialog', () => ({
  SIGNED_OUT_LINE: 'Sign in to turn on AI monitoring.',
  EnableMonitoringDialog: ({ target }: { target: string }) => (
    <div role="dialog">Turn on monitoring for {target}</div>
  ),
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})
let pending: boolean
const targets = [
  { name: 'orders', engine: 'postgresql' },
  { name: 'shop', engine: 'mysql' },
]
beforeEach(() => {
  pending = true
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url, options) => {
      if (options?.method === 'POST') {
        pending = false
        return jsonResponse({ success: true })
      }
      return jsonResponse({ targets: pending ? targets : [] })
    })
  )
})

it('never invites a fresh install', async () => {
  pending = false
  const view = renderWithClient(<MonitoringUpgradePrompt />)
  await waitFor(() =>
    expect(view.client.getQueryData(['monitoring-upgrade'])).toEqual({
      targets: [],
    })
  )
  expect(screen.queryByRole('region', { name: 'Monitoring is new' })).toBeNull()
})

it('invites an upgrade and opens the existing turn-on dialog for each database', async () => {
  renderWithClient(<MonitoringUpgradePrompt />)
  await screen.findByRole('region', { name: 'Monitoring is new' })
  expect(screen.getByText('orders uses PostgreSQL.')).toBeTruthy()
  expect(screen.getByText('shop uses MySQL.')).toBeTruthy()
  expect(screen.getByText(/Sign in to turn on AI monitoring/)).toBeTruthy()
  fireEvent.click(screen.getAllByRole('button', { name: 'Turn on' })[1])
  expect(screen.getByRole('dialog').textContent).toBe(
    'Turn on monitoring for shop'
  )
})

it('shows once and never returns after dismissal, including a new launch', async () => {
  const view = renderWithClient(<MonitoringUpgradePrompt />)
  fireEvent.click(await screen.findByRole('button', { name: 'Not now' }))
  await waitFor(() =>
    expect(
      screen.queryByRole('region', { name: 'Monitoring is new' })
    ).toBeNull()
  )
  view.unmount()
  const next = renderWithClient(<MonitoringUpgradePrompt />)
  await waitFor(() =>
    expect(next.client.getQueryData(['monitoring-upgrade'])).toEqual({
      targets: [],
    })
  )
  expect(screen.queryByRole('region', { name: 'Monitoring is new' })).toBeNull()
})
