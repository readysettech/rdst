import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithClient } from '@/test-utils'
import {
  fetchAIDataSettings,
  fetchTelemetrySettings,
  setAIDataConsent,
} from '../lib/api'
import { AIDataSettingsControl } from './AIDataSettingsControl'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    fetchAIDataSettings: vi.fn(),
    fetchTelemetrySettings: vi.fn(),
    setAIDataConsent: vi.fn(),
  }
})

const settings = (consent: 'granted' | 'denied' | 'pending') => ({
  consent,
  enabled: consent === 'granted',
  consent_version: 1,
  consent_required: true,
})

describe('AIDataSettingsControl', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  it('confirms what stops before turning AI off', async () => {
    vi.mocked(fetchAIDataSettings).mockResolvedValue(settings('granted'))
    vi.mocked(setAIDataConsent).mockResolvedValue(settings('denied'))
    renderWithClient(<AIDataSettingsControl />)

    const control = await screen.findByRole('switch', { name: 'AI' })
    await waitFor(() =>
      expect(control.getAttribute('data-state')).toBe('checked')
    )
    fireEvent.click(control)

    expect(await screen.findByText('Turn off AI?')).toBeTruthy()
    expect(screen.getByText('Slow-query and regression alerts')).toBeTruthy()
    expect(setAIDataConsent).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Turn off AI' }))
    await waitFor(() =>
      expect(vi.mocked(setAIDataConsent).mock.calls[0]?.[0]).toBe('denied')
    )
  })

  it('shows what AI sees before turning it back on', async () => {
    vi.mocked(fetchAIDataSettings).mockResolvedValue(settings('denied'))
    vi.mocked(setAIDataConsent).mockResolvedValue(settings('granted'))
    renderWithClient(<AIDataSettingsControl />)

    const control = await screen.findByRole('switch', { name: 'AI' })
    await waitFor(() =>
      expect(control.getAttribute('data-state')).toBe('unchecked')
    )
    fireEvent.click(control)

    expect(await screen.findByText('SQL text and query plans')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Turn on AI' }))
    await waitFor(() =>
      expect(vi.mocked(setAIDataConsent).mock.calls[0]?.[0]).toBe('granted')
    )
  })

  it('does not let a stale settings response overwrite turning AI off', async () => {
    let resolveStale!: (value: ReturnType<typeof settings>) => void
    const staleResponse = new Promise<ReturnType<typeof settings>>(
      (resolve) => {
        resolveStale = resolve
      }
    )
    vi.mocked(fetchAIDataSettings).mockResolvedValue(settings('granted'))
    vi.mocked(setAIDataConsent).mockResolvedValue(settings('denied'))
    const { client } = renderWithClient(<AIDataSettingsControl />)

    const control = await screen.findByRole('switch', { name: 'AI' })
    await waitFor(() =>
      expect(control.getAttribute('data-state')).toBe('checked')
    )
    fireEvent.click(control)
    const confirm = await screen.findByRole('button', { name: 'Turn off AI' })
    const fetches = vi.mocked(fetchAIDataSettings).mock.calls.length
    vi.mocked(fetchAIDataSettings).mockReturnValueOnce(staleResponse)
    const refetch = client.invalidateQueries({
      queryKey: ['settings', 'ai-data'],
    })
    await waitFor(() =>
      expect(fetchAIDataSettings).toHaveBeenCalledTimes(fetches + 1)
    )
    vi.mocked(fetchAIDataSettings).mockResolvedValue(settings('denied'))
    fireEvent.click(confirm)
    await waitFor(() =>
      expect(vi.mocked(setAIDataConsent).mock.calls[0]?.[0]).toBe('denied')
    )
    resolveStale(settings('granted'))
    await refetch

    await waitFor(() =>
      expect(
        client.getQueryData<ReturnType<typeof settings>>(['settings', 'ai-data'])
          ?.consent
      ).toBe('denied')
    )
  })

  it('offers turning AI off when the saved choice cannot be loaded', async () => {
    vi.mocked(fetchAIDataSettings).mockRejectedValue(new Error('offline'))
    vi.mocked(fetchTelemetrySettings).mockResolvedValue({
      consent: 'pending',
      enabled: false,
      consent_version: 1,
      consent_required: true,
      forced_off_reason: null,
    })
    vi.mocked(setAIDataConsent).mockResolvedValue(settings('denied'))
    renderWithClient(<AIDataSettingsControl />)

    fireEvent.click(await screen.findByRole('button', { name: 'Turn off AI' }))
    await waitFor(() =>
      expect(vi.mocked(setAIDataConsent).mock.calls[0]?.[0]).toBe('denied')
    )
  })

  it('shows nothing in a build that never asks', async () => {
    vi.mocked(fetchAIDataSettings).mockRejectedValue(new Error('offline'))
    vi.mocked(fetchTelemetrySettings).mockResolvedValue({
      consent: 'pending',
      enabled: true,
      consent_version: 1,
      consent_required: false,
      forced_off_reason: null,
    })
    const { container } = renderWithClient(<AIDataSettingsControl />)

    await waitFor(() => expect(fetchTelemetrySettings).toHaveBeenCalled())
    await waitFor(() => expect(container.textContent).toBe(''))
  })
})
