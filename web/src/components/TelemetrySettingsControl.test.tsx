import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithClient } from '@/test-utils'
import { fetchTelemetrySettings, setTelemetryConsent } from '../lib/api'
import { TelemetrySettingsControl } from './TelemetrySettingsControl'

vi.mock('../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../lib/api')>()
  return {
    ...actual,
    fetchTelemetrySettings: vi.fn(),
    setTelemetryConsent: vi.fn(),
  }
})

describe('TelemetrySettingsControl', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchTelemetrySettings).mockResolvedValue({
      consent: 'granted',
      enabled: true,
      consent_version: 1,
      consent_required: true,
    })
    vi.mocked(setTelemetryConsent).mockResolvedValue({
      consent: 'denied',
      enabled: false,
      consent_version: 1,
      consent_required: true,
    })
  })

  afterEach(() => {
    cleanup()
  })

  it('withdraws diagnostics consent', async () => {
    renderWithClient(<TelemetrySettingsControl />)

    const toggle = await screen.findByRole('switch', {
      name: 'Share diagnostics',
    })
    await waitFor(() => {
      expect(toggle.getAttribute('data-state')).toBe('checked')
    })

    fireEvent.click(toggle)

    await waitFor(() => {
      expect(vi.mocked(setTelemetryConsent).mock.calls[0]?.[0]).toBe('denied')
    })
  })

  it('does not let a stale settings response overwrite withdrawal', async () => {
    const granted = {
      consent: 'granted' as const,
      enabled: true,
      consent_version: 1,
      consent_required: true,
    }
    let resolveStale!: (value: typeof granted) => void
    const staleResponse = new Promise<typeof granted>((resolve) => {
      resolveStale = resolve
    })
    vi.mocked(fetchTelemetrySettings)
      .mockResolvedValueOnce(granted)
      .mockReturnValueOnce(staleResponse)
    const { client } = renderWithClient(<TelemetrySettingsControl />)

    const control = await screen.findByRole('switch', {
      name: 'Share diagnostics',
    })
    const refetch = client.invalidateQueries({
      queryKey: ['settings', 'telemetry'],
    })
    await waitFor(() => expect(fetchTelemetrySettings).toHaveBeenCalledTimes(2))
    fireEvent.click(control)
    await waitFor(() => {
      expect(vi.mocked(setTelemetryConsent).mock.calls[0]?.[0]).toBe('denied')
    })
    resolveStale(granted)
    await refetch

    await waitFor(() => {
      expect(
        client.getQueryData<Awaited<ReturnType<typeof fetchTelemetrySettings>>>(
          ['settings', 'telemetry']
        )?.consent
      ).toBe('denied')
    })
  })

  it('does not let a read started during the save overwrite it', async () => {
    const granted = {
      consent: 'granted' as const,
      enabled: true,
      consent_version: 1,
      consent_required: true,
    }
    let finishSave!: (value: typeof granted) => void
    vi.mocked(setTelemetryConsent).mockReturnValueOnce(
      new Promise((resolve) => {
        finishSave = resolve
      })
    )
    let resolveStale!: (value: typeof granted) => void
    vi.mocked(fetchTelemetrySettings)
      .mockResolvedValueOnce(granted)
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveStale = resolve
        })
      )
    const { client } = renderWithClient(<TelemetrySettingsControl />)

    const toggle = await screen.findByRole('switch', {
      name: 'Share diagnostics',
    })
    await waitFor(() =>
      expect(toggle.getAttribute('data-state')).toBe('checked')
    )
    fireEvent.click(toggle)
    await waitFor(() => expect(setTelemetryConsent).toHaveBeenCalled())
    const refetch = client.invalidateQueries({
      queryKey: ['settings', 'telemetry'],
    })
    await waitFor(() => expect(fetchTelemetrySettings).toHaveBeenCalledTimes(2))
    finishSave({ ...granted, consent: 'denied' as never, enabled: false })
    await waitFor(() =>
      expect(
        client.getQueryData<Awaited<ReturnType<typeof fetchTelemetrySettings>>>(
          ['settings', 'telemetry']
        )?.consent
      ).toBe('denied')
    )
    resolveStale(granted)
    await refetch.catch(() => {})

    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(
      client.getQueryData<Awaited<ReturnType<typeof fetchTelemetrySettings>>>([
        'settings',
        'telemetry',
      ])?.consent
    ).toBe('denied')
  })

  it('offers withdrawal when the saved preference cannot be loaded', async () => {
    vi.mocked(fetchTelemetrySettings).mockRejectedValue(
      new Error('status unavailable')
    )
    renderWithClient(<TelemetrySettingsControl />)

    fireEvent.click(
      await screen.findByRole('button', { name: 'Disable diagnostics' })
    )

    await waitFor(() => {
      expect(vi.mocked(setTelemetryConsent).mock.calls[0]?.[0]).toBe('denied')
    })
  })

  it('allows withdrawal while an environment override is active', async () => {
    vi.mocked(fetchTelemetrySettings).mockResolvedValue({
      consent: 'granted',
      enabled: false,
      consent_version: 1,
      consent_required: true,
      forced_off_reason: 'environment',
    })
    renderWithClient(<TelemetrySettingsControl />)

    const toggle = await screen.findByRole('switch', {
      name: 'Share diagnostics',
    })
    await waitFor(() => {
      expect(toggle.getAttribute('data-state')).toBe('checked')
      expect((toggle as HTMLButtonElement).disabled).toBe(false)
    })

    fireEvent.click(toggle)

    await waitFor(() => {
      expect(vi.mocked(setTelemetryConsent).mock.calls[0]?.[0]).toBe('denied')
    })
  })

  it('shows diagnostics on until turned off where no opt-in is needed', async () => {
    vi.mocked(fetchTelemetrySettings).mockResolvedValue({
      consent: 'pending',
      enabled: true,
      consent_version: 1,
      consent_required: false,
    })
    renderWithClient(<TelemetrySettingsControl />)

    const toggle = await screen.findByRole('switch', {
      name: 'Share diagnostics',
    })
    await waitFor(() =>
      expect(toggle.getAttribute('aria-checked')).toBe('true')
    )
  })
})
