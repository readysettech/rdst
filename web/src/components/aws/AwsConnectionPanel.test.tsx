import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { type RdstEvent, trackEvent } from '../../lib/analytics'
import {
  fetchFleetAwsSsoAccounts,
  fetchFleetAwsSsoRoles,
  fetchFleetAwsStatus,
  finalizeFleetAwsSsoProfile,
  fleetAwsLogout,
  startFleetAwsSsoLogin,
} from '../../lib/useFleet'
import { AwsConnectionPanel } from './AwsConnectionPanel'

vi.mock('../../lib/analytics', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/analytics')>()),
  trackEvent: vi.fn(),
}))
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

const tracked = (event: RdstEvent) =>
  vi
    .mocked(trackEvent)
    .mock.calls.filter(([name]) => name === event)
    .map(([, properties]) => properties)

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
    'arn:aws:sts::111122223333:assumed-role/AdministratorAccess/mike',
  account: '111122223333',
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
        identity_arn: 'arn:aws:sts::123456789012:assumed-role/production/mike',
        account: '123456789012',
      })
      .mockResolvedValueOnce({
        ...SIGNED_OUT_NO_PROFILES,
        active_profile: 'production',
        available_profiles: ['development', 'production'],
      })
    vi.mocked(fleetAwsLogout).mockResolvedValue(undefined)

    renderWithClient(<AwsConnectionPanel profile="production" />)

    expect(await screen.findByText(/Signed in to AWS as mike/)).toBeTruthy()
    // The active profile's account and role are surfaced, not just its name.
    expect(
      await screen.findByText('Role production in account 123456789012')
    ).toBeTruthy()
    expect(fetchFleetAwsStatus).toHaveBeenCalledWith('production')

    fireEvent.click(screen.getByRole('button', { name: /Sign out/ }))
    await waitFor(() => expect(fleetAwsLogout).toHaveBeenCalledOnce())
    // With saved profiles present, signing out returns to the profile picker.
    expect(
      await screen.findByText(
        'Sign in with AWS SSO so we can read details about your imported databases.'
      )
    ).toBeTruthy()
    expect(tracked('provider_action')).toEqual([
      {
        provider: 'aws',
        action: 'sign_out',
        surface: 'panel',
        ok: true,
        error_class: null,
      },
    ])
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
        { account_id: '111122223333', account_name: 'readyset-cloud-prod' },
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

    expect(
      await screen.findByText('Sign in with AWS SSO (step 1 of 3)')
    ).toBeTruthy()
    fireEvent.change(
      container.querySelector('input[name="aws-sso-start-url"]')!,
      { target: { value: 'https://readyset.awsapps.com/start' } }
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Sign in with AWS SSO' })
    )

    await waitFor(() =>
      expect(fetchFleetAwsSsoAccounts).toHaveBeenCalledWith(
        'https://readyset.awsapps.com/start'
      )
    )
    await pickOption(/readyset-cloud-prod \(111122223333\)/)

    await waitFor(() =>
      expect(fetchFleetAwsSsoRoles).toHaveBeenCalledWith(
        'https://readyset.awsapps.com/start',
        '111122223333'
      )
    )
    await pickOption(/AdministratorAccess/)

    fireEvent.click(
      screen.getByRole('button', { name: 'Create profile and connect' })
    )
    await waitFor(() =>
      // The profile name is derived from role + account, never typed.
      expect(finalizeFleetAwsSsoProfile).toHaveBeenCalledWith({
        name: 'AdministratorAccess-111122223333',
        start_url: 'https://readyset.awsapps.com/start',
        region: 'us-east-1',
        account_id: '111122223333',
        role_name: 'AdministratorAccess',
      })
    )
    expect(await screen.findByText(/Signed in to AWS as mike/)).toBeTruthy()
    expect(tracked('provider_sign_in_started')).toEqual([
      { provider: 'aws', method: 'sso' },
    ])
    expect(tracked('provider_sign_in_finished')).toEqual([
      {
        provider: 'aws',
        method: 'sso',
        ok: true,
        error_class: null,
        seconds: expect.any(Number),
      },
    ])
    expect(
      tracked('provider_action').map(
        (properties) => (properties as { action: string }).action
      )
    ).toEqual(['sign_in', 'choose_account', 'choose_role', 'create_profile'])
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

    expect(
      await screen.findByText('Sign in with AWS SSO (step 1 of 3)')
    ).toBeTruthy()
    fireEvent.change(
      container.querySelector('input[name="aws-sso-start-url"]')!,
      { target: { value: 'https://dev.awsapps.com/start' } }
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Sign in with AWS SSO' })
    )

    await waitFor(() => expect(fetchFleetAwsSsoAccounts).toHaveBeenCalled())
    await pickOption(/dev \(111\)/)
    await waitFor(() => expect(fetchFleetAwsSsoRoles).toHaveBeenCalled())
    await pickOption(/Developer/)

    fireEvent.click(
      screen.getByRole('button', { name: 'Create profile and connect' })
    )
    // The duplicate is the same account + role, so it signs in rather than
    // asking the user to invent a different name.
    expect(await screen.findByText(/Signed in to AWS as mike/)).toBeTruthy()
  })
})

describe('credentials the server provides', () => {
  const IRSA = {
    has_credentials: true,
    method: 'web_identity',
    identity_arn:
      'arn:aws:sts::123456789012:assumed-role/rdst-irsa/botocore-session-1795206441',
    account: '123456789012',
    active_profile: null,
    available_profiles: [],
    region: 'us-east-2',
  }

  afterEach(() => {
    document.head.querySelector('meta[name="rdst-runtime"]')?.remove()
    cleanup()
  })

  it("names the server's role and offers no sign-out", async () => {
    const meta = document.createElement('meta')
    meta.name = 'rdst-runtime'
    meta.content = 'container'
    document.head.appendChild(meta)
    vi.mocked(fetchFleetAwsStatus).mockResolvedValue(IRSA)
    renderWithClient(<AwsConnectionPanel />)
    expect(
      await screen.findByText("Using this server's AWS role rdst-irsa")
    ).toBeTruthy()
    expect(screen.queryByText(/botocore-session/)).toBeNull()
    expect(screen.queryByRole('button', { name: 'Sign out' })).toBeNull()
  })

  it('says environment credentials come from the environment', async () => {
    vi.mocked(fetchFleetAwsStatus).mockResolvedValue({
      ...IRSA,
      method: 'env',
      identity_arn: 'arn:aws:iam::123456789012:user/ci',
    })
    renderWithClient(<AwsConnectionPanel />)
    expect(
      await screen.findByText('Using AWS credentials from the environment')
    ).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Sign out' })).toBeNull()
  })
})
