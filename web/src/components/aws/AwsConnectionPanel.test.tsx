import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { AwsConnectionPanel } from './AwsConnectionPanel'
import {
  fetchFleetAwsSsoAccounts,
  fetchFleetAwsSsoRoles,
  fetchFleetAwsStatus,
  finalizeFleetAwsSsoProfile,
  fleetAwsLogout,
  startFleetAwsSsoLogin,
} from '../../lib/useFleet'

vi.mock('../../lib/useFleet', () => ({
  fetchFleetAwsLogin: vi.fn(),
  fetchFleetAwsStatus: vi.fn(),
  fetchFleetAwsSsoAccounts: vi.fn(),
  fetchFleetAwsSsoRoles: vi.fn(),
  finalizeFleetAwsSsoProfile: vi.fn(),
  fleetAwsLogout: vi.fn(),
  startFleetAwsLogin: vi.fn(),
  startFleetAwsSsoLogin: vi.fn(),
  FleetAwsLoginError: class FleetAwsLoginError extends Error {
    code?: string
    fallbackCommand?: string
    constructor(message: string, code?: string, fallbackCommand?: string) {
      super(message)
      this.code = code
      this.fallbackCommand = fallbackCommand
    }
  },
}))

const SIGNED_OUT_NO_PROFILES = {
  has_credentials: false,
  method: null,
  identity_arn: null,
  account: null,
  active_profile: null,
  available_profiles: [],
  region: 'us-east-1',
}

const SIGNED_IN = {
  has_credentials: true,
  method: 'sso',
  identity_arn:
    'arn:aws:sts::828804413457:assumed-role/AdministratorAccess/mike',
  account: '828804413457',
  active_profile: 'sso',
  available_profiles: ['sso'],
  region: 'us-east-1',
}

beforeAll(() => {
  // jsdom omits the pointer/scroll APIs Radix Select calls when opening.
  const proto = HTMLElement.prototype as unknown as {
    hasPointerCapture: () => boolean
    setPointerCapture: () => void
    releasePointerCapture: () => void
    scrollIntoView: () => void
  }
  proto.hasPointerCapture = () => false
  proto.setPointerCapture = () => {}
  proto.releasePointerCapture = () => {}
  proto.scrollIntoView = () => {}
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function pickOption(optionName: RegExp) {
  const trigger = screen.getByRole('combobox')
  fireEvent.keyDown(trigger, { key: 'ArrowDown' })
  const option = await screen.findByRole('option', { name: optionName })
  fireEvent.click(option)
}

describe('AwsConnectionPanel', () => {
  it('signs out of a connected profile and shows its account and role', async () => {
    vi.mocked(fetchFleetAwsStatus)
      .mockResolvedValueOnce({
        ...SIGNED_IN,
        active_profile: 'production',
        available_profiles: ['development', 'production'],
        identity_arn:
          'arn:aws:sts::123456789012:assumed-role/production/mike',
        account: '123456789012',
      })
      .mockResolvedValueOnce({
        ...SIGNED_OUT_NO_PROFILES,
        active_profile: 'production',
        available_profiles: ['development', 'production'],
      })
    vi.mocked(fleetAwsLogout).mockResolvedValue(undefined)

    renderWithClient(<AwsConnectionPanel profile="production" />)

    expect(await screen.findByText(/Signed into AWS as mike/)).toBeTruthy()
    // The active profile's account and role are surfaced, not just its name.
    expect(await screen.findByText('production - 123456789012')).toBeTruthy()
    expect(fetchFleetAwsStatus).toHaveBeenCalledWith('production')

    fireEvent.click(screen.getByRole('button', { name: /Sign out/ }))
    await waitFor(() => expect(fleetAwsLogout).toHaveBeenCalledOnce())
    // With saved profiles present, signing out returns to the profile picker.
    expect(await screen.findByText('Sign into AWS')).toBeTruthy()
  })

  it('runs the guided wizard: sign in, pick account, pick role, connect', async () => {
    vi.mocked(fetchFleetAwsStatus)
      .mockResolvedValueOnce(SIGNED_OUT_NO_PROFILES)
      .mockResolvedValue(SIGNED_IN)
    vi.mocked(startFleetAwsSsoLogin).mockResolvedValue({
      login_id: null,
      state: 'already_signed_in',
      detail: 'AWS SSO session is already active',
    })
    vi.mocked(fetchFleetAwsSsoAccounts).mockResolvedValue({
      accounts: [
        { account_id: '828804413457', account_name: 'readyset-cloud-prod' },
      ],
      error: null,
    })
    vi.mocked(fetchFleetAwsSsoRoles).mockResolvedValue({
      roles: ['AdministratorAccess'],
      error: null,
    })
    vi.mocked(finalizeFleetAwsSsoProfile).mockResolvedValue({
      created: true,
      profile: 'sso',
      detail: 'ok',
    })

    const { container } = renderWithClient(<AwsConnectionPanel />)

    expect(await screen.findByText('Connect AWS with SSO')).toBeTruthy()
    fireEvent.change(
      container.querySelector('input[name="aws-sso-start-url"]')!,
      { target: { value: 'https://readyset.awsapps.com/start' } }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Sign in with AWS' }))

    await waitFor(() =>
      expect(fetchFleetAwsSsoAccounts).toHaveBeenCalledWith(
        'https://readyset.awsapps.com/start'
      )
    )
    await pickOption(/readyset-cloud-prod \(828804413457\)/)

    await waitFor(() =>
      expect(fetchFleetAwsSsoRoles).toHaveBeenCalledWith(
        'https://readyset.awsapps.com/start',
        '828804413457'
      )
    )
    await pickOption(/AdministratorAccess/)

    fireEvent.click(
      screen.getByRole('button', { name: 'Create profile and connect' })
    )
    await waitFor(() =>
      // The profile name is derived from role + account, never typed.
      expect(finalizeFleetAwsSsoProfile).toHaveBeenCalledWith({
        name: 'AdministratorAccess-828804413457',
        start_url: 'https://readyset.awsapps.com/start',
        region: 'us-east-1',
        account_id: '828804413457',
        role_name: 'AdministratorAccess',
      })
    )
    expect(await screen.findByText(/Signed into AWS as mike/)).toBeTruthy()
  })

  it('treats a duplicate profile as success and signs in with it', async () => {
    const { FleetAwsLoginError } = await import('../../lib/useFleet')
    vi.mocked(fetchFleetAwsStatus)
      .mockResolvedValueOnce(SIGNED_OUT_NO_PROFILES)
      .mockResolvedValue({
        ...SIGNED_IN,
        active_profile: 'Developer-111',
        available_profiles: ['Developer-111'],
        identity_arn: 'arn:aws:sts::111:assumed-role/Developer/mike',
        account: '111',
      })
    vi.mocked(startFleetAwsSsoLogin).mockResolvedValue({
      login_id: null,
      state: 'already_signed_in',
      detail: 'active',
    })
    vi.mocked(fetchFleetAwsSsoAccounts).mockResolvedValue({
      accounts: [{ account_id: '111', account_name: 'dev' }],
      error: null,
    })
    vi.mocked(fetchFleetAwsSsoRoles).mockResolvedValue({
      roles: ['Developer'],
      error: null,
    })
    vi.mocked(finalizeFleetAwsSsoProfile).mockRejectedValueOnce(
      new FleetAwsLoginError(
        "AWS profile 'Developer-111' already exists",
        'profile_exists'
      )
    )

    const { container } = renderWithClient(<AwsConnectionPanel />)

    expect(await screen.findByText('Connect AWS with SSO')).toBeTruthy()
    fireEvent.change(
      container.querySelector('input[name="aws-sso-start-url"]')!,
      { target: { value: 'https://dev.awsapps.com/start' } }
    )
    fireEvent.click(screen.getByRole('button', { name: 'Sign in with AWS' }))

    await waitFor(() => expect(fetchFleetAwsSsoAccounts).toHaveBeenCalled())
    await pickOption(/dev \(111\)/)
    await waitFor(() => expect(fetchFleetAwsSsoRoles).toHaveBeenCalled())
    await pickOption(/Developer/)

    fireEvent.click(
      screen.getByRole('button', { name: 'Create profile and connect' })
    )
    // The duplicate is the same account + role, so it signs in rather than
    // asking the user to invent a different name.
    expect(await screen.findByText(/Signed into AWS as mike/)).toBeTruthy()
  })
})
