import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createTestQueryClient, renderWithClient } from '@/test-utils'
import {
  invalidateTrialRelatedQueries,
  useTrialSource,
} from '../lib/trialQueries'
import { AiProviderGate } from './AiProviderGate'

vi.mock('../lib/trialQueries', async () => {
  const actual = await vi.importActual('../lib/trialQueries')
  return {
    ...actual,
    invalidateTrialRelatedQueries: vi.fn(),
    useTrialSource: vi.fn(),
  }
})

vi.mock('./TrialRegistrationDialog', () => ({
  AccountLoginDialog: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div role="dialog">Readyset account login</div> : null,
}))

vi.mock('./EnvSecretsDialog', () => ({
  EnvSecretsDialog: ({ isOpen }: { isOpen: boolean }) =>
    isOpen ? <div role="dialog">Anthropic key setup</div> : null,
}))

describe('AI provider startup gate', () => {
  beforeEach(() => {
    sessionStorage.clear()
    vi.clearAllMocks()
    vi.mocked(useTrialSource).mockReturnValue({
      envRequirements: {
        keyring_available: true,
        telemetry_enabled: true,
        requirements: [],
      },
      envRequirementsQuery: {
        isError: false,
        error: null,
        refetch: vi.fn(),
      } as never,
      anthropicRequirement: undefined,
      anthropicSource: undefined,
      isTrialSource: false,
      trialStatus: undefined,
    })
    vi.mocked(invalidateTrialRelatedQueries).mockResolvedValue(undefined)
  })

  afterEach(cleanup)

  it('keeps the page bar and offers hosted sign-in or an own key', () => {
    renderWithClient(
      <AiProviderGate
        title="Ask"
        gate={{ status: 'blocked', reason: 'missing' }}
      />,
      createTestQueryClient()
    )

    expect(screen.getByRole('heading', { name: 'Ask', level: 1 })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Set up AI' })).toBeTruthy()
    // Signing in is no longer a getting-started step.
    expect(screen.queryByTestId('get-started')).toBeNull()
    expect(document.body.textContent).not.toMatch(/trial|credit/i)
    const signIn = screen.getByRole('button', { name: 'Sign in' })
    expect(signIn.querySelector('svg')).toBeNull()

    fireEvent.click(signIn)
    expect(screen.getByText('Readyset account login')).toBeTruthy()

    fireEvent.click(
      screen.getByRole('button', { name: 'Use my own Anthropic key instead' })
    )
    expect(screen.getByText('Anthropic key setup')).toBeTruthy()
  })

  it('never pops sign-in up on its own', () => {
    renderWithClient(
      <AiProviderGate gate={{ status: 'blocked', reason: 'missing' }} />,
      createTestQueryClient()
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('blocks on a sign-in once the free AI has run out', () => {
    const client = createTestQueryClient()
    client.setQueryData(['account-status'], {
      signed_in: false,
      is_anonymous: true,
      signin_prompt: 'required',
      percent_used: 100,
      days_remaining: 12,
      trial_days: 30,
    })
    renderWithClient(
      <AiProviderGate
        gate={{ status: 'blocked', reason: 'signin_required' }}
      />,
      client
    )

    expect(
      screen.getByRole('heading', {
        name: 'Sign in to keep AI monitoring running',
      })
    ).toBeTruthy()
    expect(screen.getByTestId('signin-required').textContent).toContain(
      "You've used the free AI that comes with RDST on this computer."
    )
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(screen.getByText('Readyset account login')).toBeTruthy()
    fireEvent.click(
      screen.getByRole('button', { name: 'Use my own Anthropic key instead' })
    )
    expect(screen.getByText('Anthropic key setup')).toBeTruthy()
  })

  it('says the free days are up when the clock ended it', () => {
    const client = createTestQueryClient()
    client.setQueryData(['account-status'], {
      signed_in: false,
      is_anonymous: true,
      signin_prompt: 'required',
      percent_used: 40,
      days_remaining: 0,
      trial_days: 30,
    })
    renderWithClient(
      <AiProviderGate
        gate={{ status: 'blocked', reason: 'signin_required' }}
      />,
      client
    )
    expect(screen.getByTestId('signin-required').textContent).toContain(
      'Your 30 days of free AI on this computer are up.'
    )
  })

  it('does not pop sign-in up when a saved key stopped working', () => {
    renderWithClient(
      <AiProviderGate gate={{ status: 'blocked', reason: 'invalid' }} />,
      createTestQueryClient()
    )
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('says why the saved key stopped working', () => {
    renderWithClient(
      <AiProviderGate gate={{ status: 'blocked', reason: 'invalid' }} />,
      createTestQueryClient()
    )
    expect(
      screen.getByText(
        'Anthropic rejected the saved key. Add a valid key or sign in to Readyset.'
      )
    ).toBeTruthy()
  })

  it('does not flash the application while credentials are being checked', () => {
    renderWithClient(
      <AiProviderGate gate={{ status: 'checking' }} />,
      createTestQueryClient()
    )

    expect(screen.getByText('Checking your AI setup')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Sign in' })).toBeNull()
  })

  it('shows a retry action when setup discovery fails', () => {
    const refetch = vi.fn()
    vi.mocked(useTrialSource).mockReturnValue({
      envRequirements: undefined,
      envRequirementsQuery: {
        isError: true,
        error: new Error('RDST API unavailable'),
        refetch,
      } as never,
      anthropicRequirement: undefined,
      anthropicSource: undefined,
      isTrialSource: false,
      trialStatus: undefined,
    })
    renderWithClient(
      <AiProviderGate
        gate={{ status: 'error', message: 'RDST API unavailable' }}
      />,
      createTestQueryClient()
    )

    expect(screen.getByText('Could not check your AI setup')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(refetch).toHaveBeenCalledTimes(1)
  })
})
