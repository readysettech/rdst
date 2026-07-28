import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import {
  fetchFleetSupabaseLogin,
  fetchFleetSupabaseStatus,
  startFleetSupabaseLogin,
} from '../../lib/useFleet'
import { SupabaseConnectionPanel } from './SupabaseConnectionPanel'

vi.mock('../../lib/useFleet', () => ({
  fetchFleetSupabaseLogin: vi.fn(),
  fetchFleetSupabaseStatus: vi.fn(),
  fleetSupabaseLogout: vi.fn(),
  startFleetSupabaseLogin: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
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

    expect(
      await screen.findByText(/Connected to Supabase with OAuth - Acme/)
    ).toBeTruthy()
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

    const busy = await screen.findByRole('button', {
      name: 'Waiting for browser approval',
    })
    expect(busy.hasAttribute('disabled')).toBe(true)
    expect(
      await screen.findByText('Waiting for you to approve the request')
    ).toBeTruthy()
  })
})
