import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { fetchFleetNeonStatus, setFleetNeonKey } from '../../lib/useFleet'
import { NeonConnectionPanel } from './NeonConnectionPanel'

vi.mock('../../lib/useFleet', () => ({
  fetchFleetNeonStatus: vi.fn(),
  FleetProviderError: class FleetProviderError extends Error {
    code?: string
    constructor(message: string, code?: string) {
      super(message)
      this.code = code
    }
  },
  fleetNeonLogout: vi.fn(),
  setFleetNeonKey: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('NeonConnectionPanel compact', () => {
  it('states the stored key on the settings page', async () => {
    vi.mocked(fetchFleetNeonStatus).mockResolvedValue({
      connected: true,
      method: 'api_key',
      detail: null,
    })

    renderWithClient(<NeonConnectionPanel compact />)

    expect(
      await screen.findByText('Connected to Neon with an API key')
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Sign out' })).toBeTruthy()
  })

  it('stays a quiet connect row when the user never connected Neon', async () => {
    vi.mocked(fetchFleetNeonStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    const onSignIn = vi.fn()

    renderWithClient(<NeonConnectionPanel compact onSignIn={onSignIn} />)

    // No explainer, no key field - one row and one affordance.
    expect(await screen.findByText('Neon')).toBeTruthy()
    expect(screen.queryByText('Connect Neon')).toBeNull()
    expect(document.querySelector('input[name="neon-api-key"]')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))

    await waitFor(() => expect(onSignIn).toHaveBeenCalledOnce())
  })

  it('names the rejection when Neon refused the stored key', async () => {
    vi.mocked(fetchFleetNeonStatus).mockResolvedValue({
      connected: false,
      method: 'api_key',
      detail: 'Neon rejected the saved API key (revoked).',
    })

    renderWithClient(<NeonConnectionPanel compact />)

    expect(await screen.findByText('Neon connection failed')).toBeTruthy()
    expect(screen.getByText(/revoked/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Connect' })).toBeTruthy()
  })
})

describe('NeonConnectionPanel', () => {
  it('explains a rejected key above the key field', async () => {
    vi.mocked(fetchFleetNeonStatus).mockResolvedValue({
      connected: false,
      method: 'api_key',
      detail: 'Neon rejected the saved API key; paste a new one.',
    })

    renderWithClient(<NeonConnectionPanel />)

    expect(
      await screen.findByText('Neon rejected the saved API key')
    ).toBeTruthy()
    expect(
      screen.getByText('Neon rejected the saved API key; paste a new one.')
    ).toBeTruthy()
    expect(document.querySelector('input[name="neon-api-key"]')).toBeTruthy()
  })

  it('stores the pasted key and re-reads the connection', async () => {
    vi.mocked(fetchFleetNeonStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    vi.mocked(setFleetNeonKey).mockResolvedValue(undefined)

    renderWithClient(<NeonConnectionPanel />)

    expect(await screen.findByText('Connect Neon')).toBeTruthy()
    // Nothing to save yet, so the button stays inert.
    expect(
      (screen.getByRole('button', { name: 'Connect' }) as HTMLButtonElement)
        .disabled
    ).toBe(true)

    fireEvent.change(
      document.querySelector('input[name="neon-api-key"]') as HTMLInputElement,
      { target: { value: '  napi_secret  ' } }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))

    await waitFor(() =>
      expect(setFleetNeonKey).toHaveBeenCalledWith('napi_secret')
    )
    await waitFor(() => expect(fetchFleetNeonStatus).toHaveBeenCalledTimes(2))
  })

  it('names a rejected key inline instead of the raw error', async () => {
    vi.mocked(fetchFleetNeonStatus).mockResolvedValue({
      connected: false,
      method: null,
      detail: null,
    })
    const { FleetProviderError } = await import('../../lib/useFleet')
    vi.mocked(setFleetNeonKey).mockRejectedValue(
      new FleetProviderError('Neon says no', 'invalid_token')
    )

    renderWithClient(<NeonConnectionPanel />)

    fireEvent.change(
      (await screen.findByText('Connect Neon')) &&
        (document.querySelector(
          'input[name="neon-api-key"]'
        ) as HTMLInputElement),
      { target: { value: 'napi_bad' } }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Connect' }))

    expect(
      await screen.findByText(
        'That API key was rejected by Neon. Check it and try again.'
      )
    ).toBeTruthy()
  })
})
