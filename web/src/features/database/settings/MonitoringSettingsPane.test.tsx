import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { settingsFixture } from '@/lib/monitor/fixtures'
import type { MonitorSettings, MonitorUsage } from '@/lib/monitor/types'
import { renderWithClient } from '@/test-utils'

let settings: MonitorSettings
const mutate = vi.hoisted(() => vi.fn())
const navigate = vi.hoisted(() => vi.fn())
const usage: MonitorUsage = {
  days: [
    {
      date: '2026-10-08',
      jev_calls: 1,
      analyses: 12,
      reports: 0,
      billable_calls: 9,
      notifications: 0,
    },
  ],
  caps: { jev_daily: 300, analysis_daily: 15, push_daily: 3 },
}

vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock({
    useRouter: () => null,
    useNavigate: () => navigate,
  })
})
vi.mock('@/lib/monitor/hooks', async (original) => ({
  ...(await original<typeof import('@/lib/monitor/hooks')>()),
  useMonitorSettings: () => ({ data: settings, isError: false }),
  useMonitorUsage: () => ({ data: usage, isError: false }),
  useUpdateMonitorSettings: () => ({ mutate, isPending: false }),
}))

const { MonitoringSettingsPane, statusCaption } = await import(
  './MonitoringSettingsPane'
)

beforeEach(() => {
  mutate.mockReset()
  settings = {
    ...settingsFixture('localdev'),
    enabled: true,
    ai_monitoring: 'on',
    deep_analysis: true,
    analysis_mode: 'plans_only',
    nightly_report: true,
    nightly_hour: 0,
    analysis_daily_cap: 15,
    newcomer_daily_cap: 100,
    attention_every_min: 5,
  }
})
afterEach(cleanup)

it("captions the master switch with the state and today's counts", () => {
  expect(statusCaption(settings, usage)).toEqual({
    word: 'AI checks running.',
    tone: 'live',
    counts: 'Today 12 analyses, 1 change check, 0 pushes.',
  })
})

it('lists the preference rows with Advanced folded and one ghost footer action', () => {
  renderWithClient(<MonitoringSettingsPane target="demo" onClose={vi.fn()} />)
  const pane = screen.getByRole('region', { name: 'Monitoring, demo' })
  expect(pane.textContent).toContain('Monitor this database')
  expect(pane.textContent).toContain(
    'AI checks running. Today 12 analyses, 1 change check, 0 pushes.'
  )
  expect(
    screen.getByRole('switch', { name: 'Analyze automatically' })
  ).toBeTruthy()
  expect(
    screen.getByText('Busiest queries first, new ones as they appear.')
  ).toBeTruthy()
  expect(
    screen.getByRole('switch', { name: 'Measure real timings' })
  ).toBeTruthy()
  expect(screen.getByText('Filed in the Inbox and pushed once.')).toBeTruthy()
  expect(
    screen.getByRole('button', { name: 'Report time: 12 AM' })
  ).toBeTruthy()
  expect(screen.getByText('Keep readings')).toBeTruthy()
  expect(screen.queryByText('Check for changes')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))
  expect(screen.getByText('Re-analyze the top queries each day')).toBeTruthy()
  expect(screen.getByRole('radio', { name: '5 min' })).toBeTruthy()
  expect(screen.getByRole('link', { name: 'Connection' })).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Stop monitoring' })).toBeTruthy()
  expect(
    screen
      .getAllByRole('button')
      .filter((button) => button.className.includes('bg-surface-primary-solid'))
  ).toHaveLength(0)
})

it('saves each control on its own', () => {
  renderWithClient(<MonitoringSettingsPane target="demo" onClose={vi.fn()} />)
  fireEvent.click(screen.getByRole('switch', { name: 'Measure real timings' }))
  expect(mutate).toHaveBeenCalledWith(
    { analysis_mode: 'execute' },
    expect.anything()
  )
  fireEvent.click(screen.getByRole('button', { name: /Advanced/ }))
  fireEvent.click(screen.getByRole('radio', { name: 'Hourly' }))
  expect(mutate).toHaveBeenCalledWith(
    { attention_every_min: 60 },
    expect.anything()
  )
})

it('turns monitoring on through the consent dialog, never a bare save', () => {
  settings = { ...settings, enabled: false }
  renderWithClient(<MonitoringSettingsPane target="demo" onClose={vi.fn()} />)
  fireEvent.click(screen.getByRole('switch', { name: 'Monitor this database' }))
  expect(mutate).not.toHaveBeenCalled()
  expect(
    screen.getByRole('button', { name: 'Turn on monitoring' })
  ).toBeTruthy()
  expect(screen.queryByTestId('ai-monitoring-panel')).toBeNull()
})

it('leads with why AI is off and the way to turn it on', () => {
  settings = { ...settings, ai_monitoring: 'local', ai_reason: 'limit' }
  renderWithClient(<MonitoringSettingsPane target="demo" onClose={vi.fn()} />)
  const panel = screen.getByTestId('ai-monitoring-panel')
  expect(panel.textContent).toContain('Local monitoring only')
  expect(panel.textContent).toContain(
    "You won't get automatic AI recommendations for this database."
  )
  expect(
    screen.getByRole('button', { name: 'Turn on AI monitoring' })
  ).toBeTruthy()
})

it('offers a sign-in when AI is off because the user is signed out', () => {
  settings = { ...settings, ai_monitoring: 'pending', ai_reason: 'signed_out' }
  renderWithClient(<MonitoringSettingsPane target="demo" onClose={vi.fn()} />)
  expect(screen.getByTestId('ai-monitoring-panel').textContent).toContain(
    'AI off: signed out'
  )
  expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy()
})
