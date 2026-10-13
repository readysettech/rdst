import type { QueryClient } from '@tanstack/react-query'
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createTestQueryClient, renderWithClient } from '@/test-utils'
import {
  fetchAccountStatus,
  fetchEnvRequirements,
  fetchStatus,
  fetchTrialStatus,
} from '../lib/api'
import { ConfigWarning } from './ConfigWarning'

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    fetchStatus: vi.fn(),
    fetchEnvRequirements: vi.fn(),
    fetchTrialStatus: vi.fn(),
    fetchAccountStatus: vi.fn(),
  }
})

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: null }),
}))

// The banner shows on feature pages and stays off the home page, so the
// default mocked route is a feature page; tests flip mockPathname to '/'
// to cover the home suppression.
let mockPathname = '/ask'
const mockNavigate = vi.fn()

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useLocation: () => ({ pathname: mockPathname }),
  }
})

vi.mock('./EnvSecretsDialog', () => ({
  EnvSecretsDialog: ({
    isOpen,
    onSuccess,
    requirements,
    showManualAnthropicInput = false,
  }: {
    isOpen: boolean
    onSuccess?: () => void
    requirements: Array<{ accepted_names: string[] }>
    showManualAnthropicInput?: boolean
  }) =>
    isOpen ? (
      <div>
        <div data-testid="dialog-requirements">
          {(requirements.length > 0 || !showManualAnthropicInput
            ? requirements
            : [{ accepted_names: ['ANTHROPIC_API_KEY'] }]
          )
            .map((item) => item.accepted_names[0])
            .join(',')}
        </div>
        {(requirements.length > 0 || !showManualAnthropicInput
          ? requirements
          : [{ accepted_names: ['ANTHROPIC_API_KEY'] }]
        ).map((item) => (
          <label key={item.accepted_names[0]}>
            {`Secret ${item.accepted_names[0]}`}
            <input
              aria-label={`Secret ${item.accepted_names[0]}`}
              onChange={() => undefined}
            />
          </label>
        ))}
        <button type="button" onClick={() => onSuccess?.()}>
          Mock Secret Save
        </button>
      </div>
    ) : null,
}))

vi.mock('./configure/AddDatabaseDialog', () => ({
  AddDatabaseHost: () => null,
}))

vi.mock('./TrialRegistrationDialog', () => ({
  TrialRegistrationDialog: ({
    isOpen,
    onSuccess,
  }: {
    isOpen: boolean
    onSuccess?: () => void
  }) =>
    isOpen ? (
      <div data-testid="trial-dialog">
        <button type="button" onClick={() => onSuccess?.()}>
          Mock Trial Success
        </button>
      </div>
    ) : null,
}))

function renderWarning(queryClient: QueryClient = createTestQueryClient()) {
  return renderWithClient(<ConfigWarning />, queryClient)
}

describe('ConfigWarning env secret flow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPathname = '/ask'
    vi.mocked(fetchAccountStatus).mockResolvedValue({ signed_in: false })
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: true,
      default_target: 'prod',
      targets: [{ name: 'prod', has_password: true, is_default: true }],
      version: '1.0.0',
      error: null,
    })
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      telemetry_enabled: true,
      requirements: [
        {
          kind: 'anthropic_api_key',
          accepted_names: ['RDST_ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY'],
          target: null,
          satisfied: false,
          source: 'missing',
        },
      ],
    })
  })

  afterEach(() => {
    cleanup()
  })

  it('shows Readyset sign-in and key actions when AI access is missing', async () => {
    const queryClient = createTestQueryClient()

    renderWarning(queryClient)

    expect(
      await screen.findByRole('button', { name: /Sign in to Readyset/i })
    ).toBeTruthy()
    expect(screen.getByRole('button', { name: /Set API Key/i })).toBeTruthy()
    // Env-var plumbing stays out of the UI: no export command, no raw names.
    expect(screen.queryByText(/ANTHROPIC_API_KEY/)).toBeNull()
  })

  it('never asks for AI access while the account is signed in', async () => {
    mockPathname = '/actions'
    vi.mocked(fetchAccountStatus).mockResolvedValue({
      signed_in: true,
      email: 'mike@example.com',
    })

    renderWarning()

    await waitFor(() =>
      expect(vi.mocked(fetchEnvRequirements)).toHaveBeenCalled()
    )
    await waitFor(() =>
      expect(vi.mocked(fetchAccountStatus)).toHaveBeenCalled()
    )
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(screen.queryByTestId('config-notice')).toBeNull()
  })

  it('stays off the demo page even when the key is missing', async () => {
    mockPathname = '/demo'
    const queryClient = createTestQueryClient()

    renderWarning(queryClient)

    await waitFor(() =>
      expect(vi.mocked(fetchEnvRequirements)).toHaveBeenCalled()
    )
    expect(
      screen.queryByRole('button', { name: /Sign in to Readyset/i })
    ).toBeNull()
    expect(screen.queryByText(/Missing Anthropic API key/i)).toBeNull()
  })

  it('stays off the home page even when the key is missing', async () => {
    mockPathname = '/'
    const queryClient = createTestQueryClient()

    renderWarning(queryClient)

    await waitFor(() =>
      expect(vi.mocked(fetchEnvRequirements)).toHaveBeenCalled()
    )
    expect(
      screen.queryByRole('button', { name: /Sign in to Readyset/i })
    ).toBeNull()
    expect(screen.queryByText(/Missing Anthropic API key/i)).toBeNull()
  })

  it.each([
    '/',
    '/inbox',
    '/database',
    '/platform',
    '/configure',
  ])('leaves a zero-database install on %s, which shows its own empty state', async (path) => {
    mockPathname = path
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: false,
      default_target: null,
      targets: [],
      version: '1.0.0',
      error: null,
    })

    renderWarning()

    await waitFor(() => expect(vi.mocked(fetchStatus)).toHaveBeenCalled())
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('shows the notice as one line with ghost actions and no icons', async () => {
    renderWarning()

    const line = await screen.findByText(
      'Sign in to Readyset for the included AI, or add your own Anthropic API key.'
    )
    const notice = screen.getByTestId('config-notice')
    expect(notice.contains(line)).toBe(true)
    expect(notice.querySelector('svg')).toBeNull()
    expect(notice.querySelectorAll('button')).toHaveLength(3)
    fireEvent.click(screen.getByRole('button', { name: 'Hide' }))
    expect(screen.queryByTestId('config-notice')).toBeNull()
  })

  it('does not show top banner for target-password-only requirements', async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      telemetry_enabled: true,
      requirements: [
        {
          kind: 'target_password',
          accepted_names: ['DOCS_READYSET_PASSWORD'],
          target: 'docs_readyset',
          satisfied: false,
          source: 'missing',
        },
      ],
    })

    const queryClient = createTestQueryClient()

    renderWarning(queryClient)

    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: /Sign in to Readyset/i })
      ).toBeNull()
    })
  })

  it('uses Anthropic-only requirements in dialog when both are missing', async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      telemetry_enabled: true,
      requirements: [
        {
          kind: 'anthropic_api_key',
          accepted_names: ['RDST_ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY'],
          target: null,
          satisfied: false,
          source: 'missing',
        },
        {
          kind: 'target_password',
          accepted_names: ['DOCS_READYSET_PASSWORD'],
          target: 'docs_readyset',
          satisfied: false,
          source: 'missing',
        },
      ],
    })

    const queryClient = createTestQueryClient()

    renderWarning(queryClient)
    fireEvent.click(await screen.findByRole('button', { name: /Set API Key/i }))

    const requirementsText =
      (await screen.findByTestId('dialog-requirements')).textContent || ''
    expect(requirementsText.includes('RDST_ANTHROPIC_API_KEY')).toBe(true)
    expect(requirementsText.includes('DOCS_READYSET_PASSWORD')).toBe(false)
  })

  it('invalidates relevant queries after successful secret save', async () => {
    const queryClient = createTestQueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')

    renderWarning(queryClient)

    fireEvent.click(await screen.findByRole('button', { name: /Set API Key/i }))
    fireEvent.click(
      await screen.findByRole('button', { name: /Mock Secret Save/i })
    )

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: ['status'], refetchType: 'all' })
      )
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['init-status'],
          refetchType: 'all',
        })
      )
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['env-requirements'],
          refetchType: 'all',
        })
      )
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          queryKey: ['trial-status'],
          refetchType: 'all',
        })
      )
    })
  })

  it('does not revive the removed trial flow from legacy local state', async () => {
    vi.mocked(fetchTrialStatus).mockResolvedValue({
      active: false,
      status: 'exhausted',
      percent_remaining: 0,
      remaining_tokens_display: '0',
      limit_tokens_display: '100',
    })
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      telemetry_enabled: true,
      requirements: [
        {
          kind: 'anthropic_api_key',
          accepted_names: ['RDST_ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
          target: null,
          satisfied: true,
          source: 'trial_exhausted',
        },
      ],
    })

    const queryClient = createTestQueryClient()

    renderWarning(queryClient)

    await waitFor(() => {
      expect(fetchTrialStatus).not.toHaveBeenCalled()
      expect(screen.queryByText(/Included AI used up/i)).toBeNull()
    })
  })

  it('does not show a trial warning when source is no longer trial even if trial status cache is exhausted', async () => {
    const queryClient = createTestQueryClient()
    queryClient.setQueryData(['trial-status'], {
      active: false,
      status: 'exhausted',
      percent_remaining: 0,
      remaining_tokens_display: '0',
      limit_tokens_display: '100',
    })

    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      telemetry_enabled: true,
      requirements: [
        {
          kind: 'anthropic_api_key',
          accepted_names: ['RDST_ANTHROPIC_API_KEY', 'RDST_TRIAL_TOKEN'],
          target: null,
          satisfied: true,
          source: 'process_env',
        },
      ],
    })

    renderWarning(queryClient)

    await waitFor(() => {
      expect(screen.queryByText(/Included AI used up/i)).toBeNull()
    })
  })
})
