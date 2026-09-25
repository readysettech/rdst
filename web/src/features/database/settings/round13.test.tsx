import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { settingsFixture } from '@/lib/monitor/fixtures'
import type { MonitorSettings, MonitorUsage } from '@/lib/monitor/types'
import { renderWithClient } from '@/test-utils'

let settings: MonitorSettings
const mutate = vi.fn()
const usage: MonitorUsage = {
  days: [
    {
      date: '2026-10-02',
      jev_calls: 4,
      analyses: 2,
      reports: 0,
      billable_calls: 9,
      notifications: 1,
    },
  ],
  caps: { jev_daily: 300, analysis_daily: 10, push_daily: 3 },
}

vi.mock('@/lib/monitor/hooks', async (original) => ({
  ...(await original<typeof import('@/lib/monitor/hooks')>()),
  useMonitorSettings: () => ({ data: settings, isError: false }),
  useMonitorUsage: () => ({ data: usage, isError: false }),
  useAnalyzeJobs: () => ({ data: undefined }),
  useMonitorWork: () => ({ data: undefined }),
  useMonitoringLimit: () => ({
    data: { contact: 'hello@readyset.io', allowance: [] },
  }),
  useClaimAiMonitoring: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateMonitorSettings: () => ({ mutate, isPending: false }),
}))
vi.mock('@/components/TrialRegistrationDialog', () => ({
  TrialRegistrationDialog: () => null,
}))
vi.mock('./EnableMonitoringDialog', () => ({
  EnableMonitoringDialog: () => null,
}))

const { MonitoringSwitch } = await import('./MonitoringSwitch')
const { MonitoringSettingsSheet, MANUAL_ANALYSIS_NOTE } = await import(
  './MonitoringSettingsSheet'
)

beforeEach(() => {
  mutate.mockReset()
  settings = { ...settingsFixture('localdev'), analysis_daily_cap: 10 }
})
afterEach(cleanup)

describe('the monitoring switch without AI', () => {
  it('warns and offers sign-in when signed out', () => {
    settings = {
      ...settings,
      ai_monitoring: 'pending',
      ai_reason: 'signed_out',
    }
    renderWithClient(<MonitoringSwitch target="demoshop" />)
    expect(screen.getByTestId('no-ai-warning').textContent).toContain(
      "You won't get automatic AI recommendations for this database."
    )
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy()
    expect(screen.queryByText(/Anthropic key/)).toBeNull()
  })

  it('points a full account at its databases', () => {
    settings = { ...settings, ai_monitoring: 'local', ai_reason: 'limit' }
    renderWithClient(<MonitoringSwitch target="demoshop" />)
    expect(screen.getByTestId('no-ai-warning')).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Manage databases' })
    ).toBeTruthy()
  })

  it('stays out of the way while AI monitoring is on', () => {
    settings = { ...settings, ai_monitoring: 'on', ai_reason: null }
    renderWithClient(<MonitoringSwitch target="demoshop" />)
    expect(screen.queryByTestId('no-ai-warning')).toBeNull()
  })
})

describe('monitoring settings', () => {
  it('says the no-AI warning once, in the AI panel under the switch row', () => {
    settings = {
      ...settings,
      ai_monitoring: 'pending',
      ai_reason: 'signed_out',
    }
    renderWithClient(
      <MonitoringSettingsSheet target="demoshop" open onClose={vi.fn()} />
    )
    const warnings = screen.getAllByTestId('no-ai-warning')
    expect(warnings).toHaveLength(1)
    expect(
      warnings[0].closest('[data-testid="ai-monitoring-panel"]')
    ).toBeTruthy()
    expect(warnings[0].closest('[data-testid="monitoring-switch"]')).toBeNull()
  })

  it('notes that manual analysis always runs, and offers the newcomer setting', () => {
    renderWithClient(
      <MonitoringSettingsSheet target="demoshop" open onClose={vi.fn()} />
    )
    expect(screen.getByTestId('manual-analysis-note').textContent).toBe(
      MANUAL_ANALYSIS_NOTE
    )
    expect(screen.getByText('New top queries per day')).toBeTruthy()
  })

  it('shows the work done without any "of" limit counters', () => {
    renderWithClient(
      <MonitoringSettingsSheet target="demoshop" open onClose={vi.fn()} />
    )
    const counts = screen
      .getAllByTestId('usage-count')
      .map((node) => node.textContent)
    expect(counts).toEqual([
      'Change checks today4',
      'Deep analyses today2',
      'Pushes today1',
    ])
    expect(screen.queryByText(/of 300|of 10 /)).toBeNull()
  })

  it('saves the newcomer ceiling', () => {
    renderWithClient(
      <MonitoringSettingsSheet target="demoshop" open onClose={vi.fn()} />
    )
    fireEvent.click(
      screen.getByRole('combobox', {
        name: /New top queries per day/,
      })
    )
    fireEvent.click(screen.getByRole('option', { name: '50' }))
    expect(mutate).toHaveBeenCalledWith(
      { auto_analyze_new: true, newcomer_daily_cap: 50 },
      expect.anything()
    )
  })

  it('turns newcomer analysis off from the same setting', () => {
    renderWithClient(
      <MonitoringSettingsSheet target="demoshop" open onClose={vi.fn()} />
    )
    fireEvent.click(
      screen.getByRole('combobox', {
        name: /New top queries per day/,
      })
    )
    fireEvent.click(screen.getByRole('option', { name: 'Off' }))
    expect(mutate).toHaveBeenCalledWith(
      { auto_analyze_new: false },
      expect.anything()
    )
  })
})
