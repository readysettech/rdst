import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { AwsConnectionPanel } from './AwsConnectionPanel'
import {
  fetchFleetAwsStatus,
  fleetAwsLogout,
} from '../../lib/useFleet'

vi.mock('../../lib/useFleet', () => ({
  createFleetAwsProfile: vi.fn(),
  fetchFleetAwsLogin: vi.fn(),
  fetchFleetAwsStatus: vi.fn(),
  FleetAwsLoginError: class FleetAwsLoginError extends Error {},
  fleetAwsLogout: vi.fn(),
  startFleetAwsLogin: vi.fn(),
}))

afterEach(cleanup)

function renderPanel() {
  return renderWithClient(<AwsConnectionPanel profile="production" />)
}

describe('AwsConnectionPanel', () => {
  it('uses the selected signed-in profile, signs out, and re-checks status', async () => {
    vi.mocked(fetchFleetAwsStatus)
      .mockResolvedValueOnce({
        has_credentials: true,
        method: 'sso',
        identity_arn:
          'arn:aws:sts::123456789012:assumed-role/production/mike',
        account: '123456789012',
        active_profile: 'production',
        available_profiles: ['development', 'production'],
        region: 'us-east-1',
      })
      .mockResolvedValueOnce({
        has_credentials: false,
        method: null,
        identity_arn: null,
        account: null,
        active_profile: 'production',
        available_profiles: ['development', 'production'],
        region: 'us-east-1',
      })
    vi.mocked(fleetAwsLogout).mockResolvedValue(undefined)

    renderPanel()

    expect(await screen.findByText(/Signed into AWS as mike/)).toBeTruthy()
    expect(fetchFleetAwsStatus).toHaveBeenCalledWith('production')
    fireEvent.click(
      screen.getByRole('button', { name: /Sign out of AWS/ })
    )

    await waitFor(() => expect(fleetAwsLogout).toHaveBeenCalledOnce())
    await waitFor(() =>
      expect(fetchFleetAwsStatus).toHaveBeenCalledTimes(2)
    )
    expect(await screen.findByText('Sign into AWS')).toBeTruthy()
  })
})
