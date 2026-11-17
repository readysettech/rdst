import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { renderWithClient } from '@/test-utils'
import { tourPending } from '../features/onboarding/onboardingState'
import { syncAnalyticsConsent } from '../lib/analytics'
import { fetchTelemetrySettings, setTelemetryConsent } from '../lib/api'
import { ConsentChoicesDialog } from './ConsentChoicesDialog'

vi.mock('../lib/analytics', () => ({
  syncAnalyticsConsent: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('../features/onboarding/onboardingState', () => ({
  tourPending: vi.fn(() => false),
}))

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    fetchTelemetrySettings: vi.fn(),
    setTelemetryConsent: vi.fn(),
  }
})

const telemetry = (
  consent: 'granted' | 'denied' | 'pending',
  consent_required = true
) => ({
  consent,
  enabled: consent === 'granted',
  consent_version: 1,
  consent_required,
  forced_off_reason: null,
})

function desktop({ opens = 2, ask = true } = {}) {
  const setStartAtLogin = vi.fn(async (enabled: boolean) => ({
    enabled,
    available: true,
    ask: false,
  }))
  window.rdstDesktop = {
    isDesktop: true,
    platform: 'win32',
    getWindowOpens: vi.fn(async () => opens),
    getStartAtLogin: vi.fn(async () => ({
      enabled: false,
      available: true,
      ask,
    })),
    setStartAtLogin,
  }
  return setStartAtLogin
}

describe('ConsentChoicesDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(tourPending).mockReturnValue(false)
    vi.mocked(fetchTelemetrySettings).mockResolvedValue(telemetry('pending'))
    vi.mocked(setTelemetryConsent).mockImplementation(async (consent) =>
      telemetry(consent)
    )
  })
  afterEach(() => {
    cleanup()
    delete window.rdstDesktop
  })

  it('asks both questions once on the second open, ticked and saved only on Save', async () => {
    const setLogin = desktop()
    renderWithClient(<ConsentChoicesDialog />)

    await screen.findByText('Diagnostics and startup')
    const share = screen.getByRole('checkbox', { name: 'Share diagnostics' })
    const login = screen.getByRole('checkbox', {
      name: 'Start RDST when you sign in',
    })
    expect(share.getAttribute('aria-checked')).toBe('true')
    expect(login.getAttribute('aria-checked')).toBe('true')
    expect(screen.getByText(/Never your SQL/)).toBeTruthy()
    expect(setTelemetryConsent).not.toHaveBeenCalled()
    expect(setLogin).not.toHaveBeenCalled()

    fireEvent.click(login)
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(vi.mocked(setTelemetryConsent).mock.calls[0]?.[0]).toBe('granted')
    )
    await waitFor(() => expect(setLogin).toHaveBeenCalledWith(false))
    expect(syncAnalyticsConsent).toHaveBeenCalledWith(true)
    await waitFor(() =>
      expect(screen.queryByTestId('consent-choices')).toBeNull()
    )
  })

  it('records Skip as no to both', async () => {
    const setLogin = desktop()
    renderWithClient(<ConsentChoicesDialog />)

    fireEvent.click(await screen.findByRole('button', { name: 'Skip' }))
    await waitFor(() =>
      expect(vi.mocked(setTelemetryConsent).mock.calls[0]?.[0]).toBe('denied')
    )
    await waitFor(() => expect(setLogin).toHaveBeenCalledWith(false))
    await waitFor(() =>
      expect(screen.queryByTestId('consent-choices')).toBeNull()
    )
  })

  it('asks only what is still unanswered', async () => {
    const setLogin = desktop({ ask: false })
    renderWithClient(<ConsentChoicesDialog />)

    await screen.findByText('Share diagnostics?')
    expect(
      screen.queryByRole('checkbox', { name: 'Start RDST when you sign in' })
    ).toBeNull()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Share diagnostics' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(vi.mocked(setTelemetryConsent).mock.calls[0]?.[0]).toBe('denied')
    )
    expect(setLogin).not.toHaveBeenCalled()
  })

  it.each([
    ['on the first open', () => desktop({ opens: 1 })],
    [
      'once both are answered',
      () => {
        vi.mocked(fetchTelemetrySettings).mockResolvedValue(telemetry('denied'))
        desktop({ ask: false })
      },
    ],
    [
      'in a build that never asks',
      () => {
        vi.mocked(fetchTelemetrySettings).mockResolvedValue(
          telemetry('pending', false)
        )
        desktop({ ask: false })
      },
    ],
    [
      'while the first-run tour is still to come',
      () => {
        vi.mocked(tourPending).mockReturnValue(true)
        desktop()
      },
    ],
  ])('stays away %s', async (_name, arrange) => {
    arrange()
    renderWithClient(<ConsentChoicesDialog />)
    await waitFor(() =>
      expect(window.rdstDesktop?.getStartAtLogin).toHaveBeenCalled()
    )
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.queryByTestId('consent-choices')).toBeNull()
  })

  it('waits for another dialog to close, then asks', async () => {
    desktop()
    const other = document.createElement('div')
    other.setAttribute('role', 'dialog')
    document.body.append(other)
    renderWithClient(<ConsentChoicesDialog />)
    await new Promise((resolve) => setTimeout(resolve, 900))
    expect(screen.queryByTestId('consent-choices')).toBeNull()
    other.remove()
    expect(await screen.findByText('Diagnostics and startup')).toBeTruthy()
  })

  it('waits for the add-database prompt still loading', async () => {
    desktop()
    window.history.replaceState(null, '', '/?add=agent')
    renderWithClient(<ConsentChoicesDialog />)
    await new Promise((resolve) => setTimeout(resolve, 900))
    expect(screen.queryByTestId('consent-choices')).toBeNull()
    window.history.replaceState(null, '', '/')
    expect(await screen.findByText('Diagnostics and startup')).toBeTruthy()
  })

  it('stays away in a browser', async () => {
    renderWithClient(<ConsentChoicesDialog />)
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.queryByTestId('consent-choices')).toBeNull()
    expect(fetchTelemetrySettings).not.toHaveBeenCalled()
  })
})
