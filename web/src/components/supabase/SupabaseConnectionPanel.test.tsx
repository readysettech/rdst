import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { type RdstEvent, trackEvent } from '../../lib/analytics'
import {
  fetchFleetSupabaseLogin,
  fetchFleetSupabaseStatus,
  startFleetSupabaseLogin,
} from '../../lib/useFleet'
import { SupabaseConnectionPanel } from './SupabaseConnectionPanel'

vi.mock('../../lib/analytics', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/analytics')>()),
  trackEvent: vi.fn(),
}))
vi.mock('../../lib/useFleet', () => ({
  fetchFleetSupabaseLogin: vi.fn(),
  fetchFleetSupabaseStatus: vi.fn(),
  fleetSupabaseLogout: vi.fn(),
  startFleetSupabaseLogin: vi.fn(),
}))

const tracked = (event: RdstEvent) =>
  vi
    .mocked(trackEvent)
    .mock.calls.filter(([name]) => name === event)
    .map(([, properties]) => properties)

afterEach(() => {
  vi.mocked(trackEvent).mockClear()
  cleanup()
  vi.unstubAllGlobals()
  delete window.rdstDesktop
})

describe('SupabaseConnectionPanel compact', () => {
  it('states the signed-in session on the settings page', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: true,
      method: 'oauth',
      detail: null,
      organizations: [{ slug: 'acme', name: 'Acme' }],
    })

    renderWithClient(<SupabaseConnectionPanel compact />)

    expect(await screen.findByText('Signed in to Supabase (Acme)')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy()
  })

  it('stays a quiet sign-in row when the user never connected Supabase', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    const onSignIn = vi.fn()

    renderWithClient(<SupabaseConnectionPanel compact onSignIn={onSignIn} />)

    // No sign-in copy, no token field - one row and one affordance.
    expect(await screen.findByText('Supabase')).toBeTruthy()
    expect(screen.queryByText('Connect Supabase')).toBeNull()
    expect(screen.queryByText(/personal access token/)).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(onSignIn).toHaveBeenCalledOnce())
  })

  it('names the rejection when Supabase refused an existing session', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: false,
      method: 'oauth',
      detail:
        'Supabase rejected the session credentials; sign in again (missing scopes).',
    })

    renderWithClient(<SupabaseConnectionPanel compact />)

    expect(await screen.findByText('Supabase sign-in failed')).toBeTruthy()
    expect(screen.getByText(/missing scopes/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy()
  })
})

describe('SupabaseConnectionPanel', () => {
  it('explains a rejected session above the sign-in button', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: false,
      method: 'oauth',
      detail: 'Supabase rejected the saved session; sign in again.',
    })

    renderWithClient(<SupabaseConnectionPanel />)

    expect(
      await screen.findByText('Supabase rejected the saved credentials')
    ).toBeTruthy()
    expect(
      screen.getByText('Supabase rejected the saved session; sign in again.')
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Sign in with Supabase' })
    ).toBeTruthy()
  })

  it('holds the sign-in button busy while the browser approval is pending', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    vi.mocked(startFleetSupabaseLogin).mockResolvedValue({
      login_id: 'login-1',
      authorize_url: 'https://supabase.example/authorize',
    })
    vi.mocked(fetchFleetSupabaseLogin).mockResolvedValue({
      state: 'running',
      detail: 'Waiting for you to approve the request',
    })
    vi.stubGlobal('open', vi.fn())

    renderWithClient(<SupabaseConnectionPanel />)

    fireEvent.click(
      await screen.findByRole('button', { name: 'Sign in with Supabase' })
    )

    // The label stays; the button holds busy until the browser approves.
    await waitFor(() =>
      expect(
        screen
          .getByRole('button', { name: 'Sign in with Supabase' })
          .hasAttribute('disabled')
      ).toBe(true)
    )
    expect(
      await screen.findByText('Waiting for you to approve the request')
    ).toBeTruthy()
  })

  it('explains a sign-in that cannot start from this browser', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    vi.mocked(startFleetSupabaseLogin).mockRejectedValue(
      Object.assign(
        new Error(
          'Connect Supabase from a browser on the machine running RDST.'
        ),
        { code: 'supabase_oauth_remote_browser' }
      )
    )
    vi.mocked(fetchFleetSupabaseLogin).mockClear()
    const popup = { close: vi.fn() }
    vi.stubGlobal('open', vi.fn().mockReturnValue(popup))

    renderWithClient(<SupabaseConnectionPanel />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Sign in with Supabase' })
    )

    expect(
      await screen.findAllByText(
        'Connect Supabase from a browser on the machine running RDST.'
      )
    ).not.toHaveLength(0)
    expect(popup.close).toHaveBeenCalled()
    expect(fetchFleetSupabaseLogin).not.toHaveBeenCalled()
  })

  it('offers the authorization link when the popup is blocked', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    vi.mocked(startFleetSupabaseLogin).mockResolvedValue({
      login_id: 'login-1',
      authorize_url: 'https://supabase.example/authorize',
    })
    vi.mocked(fetchFleetSupabaseLogin).mockResolvedValue({
      state: 'running',
      detail: 'Waiting for approval',
    })
    vi.stubGlobal('open', vi.fn().mockReturnValue(null))

    renderWithClient(<SupabaseConnectionPanel />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Sign in with Supabase' })
    )

    const fallback = await screen.findByRole('link', {
      name: 'Popup blocked. Open sign-in.',
    })
    expect(fallback.getAttribute('href')).toBe(
      'https://supabase.example/authorize'
    )
    expect(window.open).toHaveBeenCalledWith('about:blank', '_blank')
  })

  it('hands Electron the provider URL without opening about:blank', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    vi.mocked(startFleetSupabaseLogin).mockResolvedValue({
      login_id: 'login-1',
      authorize_url: 'https://supabase.example/authorize',
    })
    vi.mocked(fetchFleetSupabaseLogin).mockResolvedValue({
      state: 'running',
      detail: 'Waiting for approval',
    })
    window.rdstDesktop = { isDesktop: true, platform: 'win32' }
    const open = vi.fn().mockReturnValue(null)
    vi.stubGlobal('open', open)

    renderWithClient(<SupabaseConnectionPanel />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Sign in with Supabase' })
    )

    await waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        'https://supabase.example/authorize',
        '_blank'
      )
    )
    expect(open).not.toHaveBeenCalledWith('about:blank', '_blank')
    expect(
      screen.queryByRole('link', { name: 'Popup blocked. Open sign-in.' })
    ).toBeNull()
  })

  it('reports one sign-in outcome when two polls in flight both see success', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    vi.mocked(startFleetSupabaseLogin).mockResolvedValue({
      login_id: 'login-1',
      authorize_url: 'https://supabase.example/authorize',
    })
    const settle: ((value: { state: 'success' }) => void)[] = []
    vi.mocked(fetchFleetSupabaseLogin).mockReset()
    vi.mocked(fetchFleetSupabaseLogin).mockImplementation(
      () => new Promise((resolve) => settle.push(resolve))
    )
    vi.stubGlobal('open', vi.fn().mockReturnValue(null))

    renderWithClient(<SupabaseConnectionPanel />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Sign in with Supabase' })
    )
    fireEvent.click(
      await screen.findByRole('link', { name: 'Popup blocked. Open sign-in.' })
    )
    await waitFor(() => expect(settle).toHaveLength(2), { timeout: 3_000 })
    for (const resolve of settle) resolve({ state: 'success' })

    await waitFor(() =>
      expect(tracked('provider_sign_in_finished')).toHaveLength(1)
    )
    expect(tracked('provider_sign_in_started')).toEqual([
      { provider: 'supabase', method: 'oauth' },
    ])
    expect(tracked('provider_sign_in_popup_blocked')).toEqual([
      { provider: 'supabase' },
    ])
    expect(tracked('provider_sign_in_finished')).toEqual([
      {
        provider: 'supabase',
        method: 'oauth',
        ok: true,
        error_class: null,
        seconds: expect.any(Number),
      },
    ])
    expect(tracked('provider_action')).toEqual([
      { provider: 'supabase', action: 'sign_in', surface: 'panel' },
      { provider: 'supabase', action: 'open_sign_in_link', surface: 'panel' },
    ])
  })

  it('reports a sign-in that could not start by its error code', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    vi.mocked(startFleetSupabaseLogin).mockRejectedValue(
      Object.assign(new Error('Connect from https://host.example'), {
        code: 'supabase_oauth_remote_browser',
      })
    )
    vi.stubGlobal('open', vi.fn().mockReturnValue({ close: vi.fn() }))

    renderWithClient(<SupabaseConnectionPanel />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Sign in with Supabase' })
    )

    await waitFor(() =>
      expect(tracked('provider_sign_in_finished')).toEqual([
        {
          provider: 'supabase',
          method: 'oauth',
          ok: false,
          error_class: 'supabase_oauth_remote_browser',
          seconds: expect.any(Number),
        },
      ])
    )
  })

  it('reports a failed poll with a coarse class, never its text', async () => {
    vi.mocked(fetchFleetSupabaseStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    vi.mocked(startFleetSupabaseLogin).mockResolvedValue({
      login_id: 'login-1',
      authorize_url: 'https://supabase.example/authorize',
    })
    vi.mocked(fetchFleetSupabaseLogin).mockReset()
    vi.mocked(fetchFleetSupabaseLogin).mockResolvedValue({
      state: 'failed',
      detail: 'This sign-in expired. Start the sign-in again.',
    })
    vi.stubGlobal('open', vi.fn().mockReturnValue(null))

    renderWithClient(<SupabaseConnectionPanel />)
    fireEvent.click(
      await screen.findByRole('button', { name: 'Sign in with Supabase' })
    )

    await waitFor(() =>
      expect(tracked('provider_sign_in_finished')).toEqual([
        expect.objectContaining({ ok: false, error_class: 'login_failed' }),
      ])
    )
  })
})
