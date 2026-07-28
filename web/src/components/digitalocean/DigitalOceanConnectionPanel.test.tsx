import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import {
  FleetProviderError,
  fetchFleetDigitaloceanLogin,
  fetchFleetDigitaloceanStatus,
  startFleetDigitaloceanLogin,
} from '../../lib/useFleet'
import { DigitalOceanConnectionPanel } from './DigitalOceanConnectionPanel'

vi.mock('../../lib/useFleet', () => ({
  fetchFleetDigitaloceanLogin: vi.fn(),
  fetchFleetDigitaloceanStatus: vi.fn(),
  FleetProviderError: class FleetProviderError extends Error {},
  fleetDigitaloceanLogout: vi.fn(),
  startFleetDigitaloceanLogin: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('DigitalOceanConnectionPanel compact', () => {
  it('states the signed-in session on the settings page', async () => {
    vi.mocked(fetchFleetDigitaloceanStatus).mockResolvedValue({
      connected: true,
      method: 'oauth',
      detail: null,
    })

    renderWithClient(<DigitalOceanConnectionPanel compact />)

    expect(
      await screen.findByText('Connected to DigitalOcean with OAuth')
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy()
  })

  it('stays a quiet sign-in row when the user never connected DigitalOcean', async () => {
    vi.mocked(fetchFleetDigitaloceanStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    const onSignIn = vi.fn()

    renderWithClient(
      <DigitalOceanConnectionPanel compact onSignIn={onSignIn} />
    )

    // No sign-in copy, no second affordance - one row and one way in.
    expect(await screen.findByText('DigitalOcean')).toBeTruthy()
    expect(screen.queryByText('Connect DigitalOcean')).toBeNull()
    expect(
      screen.getByText('Import your managed Postgres and MySQL databases')
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))

    await waitFor(() => expect(onSignIn).toHaveBeenCalledOnce())
  })

  it('names the rejection when DigitalOcean refused an existing session', async () => {
    vi.mocked(fetchFleetDigitaloceanStatus).mockResolvedValue({
      connected: false,
      method: 'oauth',
      detail:
        'DigitalOcean rejected the session credentials; sign in again (missing scopes).',
    })

    renderWithClient(<DigitalOceanConnectionPanel compact />)

    expect(await screen.findByText('DigitalOcean sign-in failed')).toBeTruthy()
    expect(screen.getByText(/missing scopes/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeTruthy()
  })
})

describe('DigitalOceanConnectionPanel', () => {
  it('explains a rejected session above the sign-in button', async () => {
    vi.mocked(fetchFleetDigitaloceanStatus).mockResolvedValue({
      connected: false,
      method: 'oauth',
      detail: 'DigitalOcean rejected the saved session; sign in again.',
    })

    renderWithClient(<DigitalOceanConnectionPanel />)

    expect(
      await screen.findByText('DigitalOcean rejected the saved credentials')
    ).toBeTruthy()
    expect(
      screen.getByText(
        'DigitalOcean rejected the saved session; sign in again.'
      )
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Sign in with DigitalOcean' })
    ).toBeTruthy()
  })

  it('holds the sign-in button busy while the browser approval is pending', async () => {
    vi.mocked(fetchFleetDigitaloceanStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    vi.mocked(startFleetDigitaloceanLogin).mockResolvedValue({
      login_id: 'login-1',
      authorize_url: 'https://cloud.digitalocean.com/v1/oauth/authorize',
    })
    vi.mocked(fetchFleetDigitaloceanLogin).mockResolvedValue({
      state: 'running',
      detail: 'Waiting for you to approve the request',
    })
    vi.stubGlobal('open', vi.fn())

    renderWithClient(<DigitalOceanConnectionPanel />)

    fireEvent.click(
      await screen.findByRole('button', { name: 'Sign in with DigitalOcean' })
    )

    const busy = await screen.findByRole('button', {
      name: 'Waiting for browser approval',
    })
    expect(busy.hasAttribute('disabled')).toBe(true)
    expect(
      await screen.findByText('Waiting for you to approve the request')
    ).toBeTruthy()
  })

  it('surfaces a broker failure instead of a silent dead end', async () => {
    vi.mocked(fetchFleetDigitaloceanStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    vi.mocked(startFleetDigitaloceanLogin).mockRejectedValue(
      new FleetProviderError(
        'The DigitalOcean sign-in service is unavailable.',
        'digitalocean_oauth_unavailable'
      )
    )

    renderWithClient(<DigitalOceanConnectionPanel />)

    fireEvent.click(
      await screen.findByRole('button', { name: 'Sign in with DigitalOcean' })
    )

    expect(await screen.findByText('DigitalOcean sign-in failed')).toBeTruthy()
    expect(
      screen.getByText('The DigitalOcean sign-in service is unavailable.')
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy()
  })
})
