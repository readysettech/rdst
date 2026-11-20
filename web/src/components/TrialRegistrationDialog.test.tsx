import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { createTestQueryClient, renderWithClient } from '@/test-utils'

import {
  completeAccountRedirect,
  sendAccountMagicLink,
  startAccountGoogleOAuth,
} from '../lib/accountAuth'
import { trackEvent } from '../lib/analytics'
import { fetchAccountBrowserCallback, startAccountLogin } from '../lib/api'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { AccountLoginDialog } from './TrialRegistrationDialog'

vi.mock('../lib/accountAuth', () => ({
  completeAccountRedirect: vi.fn(),
  sendAccountMagicLink: vi.fn(),
  startAccountGoogleOAuth: vi.fn(),
}))

vi.mock('../lib/analytics', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/analytics')>()),
  trackEvent: vi.fn(),
}))
vi.mock('../lib/api', async () => {
  const actual = await vi.importActual('../lib/api')
  return {
    ...actual,
    fetchAccountBrowserCallback: vi.fn(),
    startAccountLogin: vi.fn(),
  }
})

vi.mock('../lib/trialQueries', async () => {
  const actual = await vi.importActual('../lib/trialQueries')
  return { ...actual, invalidateTrialRelatedQueries: vi.fn() }
})

const context = {
  login_id: 'login-1',
  state: 'state-1',
  auth_url: 'https://example.supabase.co/auth/v1',
  publishable_key: 'publishable',
  callback_url: 'https://keyservice.example/account-auth/callback',
  expires_in: 600,
}

describe('AccountLoginDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(startAccountLogin).mockResolvedValue(context)
    vi.mocked(fetchAccountBrowserCallback).mockResolvedValue({
      state: 'pending',
      code: null,
      error: null,
      error_description: null,
    })
    vi.mocked(sendAccountMagicLink).mockResolvedValue(undefined)
    vi.mocked(startAccountGoogleOAuth).mockResolvedValue(undefined)
    vi.mocked(completeAccountRedirect).mockResolvedValue({
      state: 'success',
      detail: 'Signed in to Readyset',
    })
    vi.mocked(invalidateTrialRelatedQueries).mockResolvedValue(undefined)
  })

  afterEach(() => {
    cleanup()
    delete window.rdstDesktop
  })

  it('opens as getting started with Google sign-up leading', async () => {
    renderWithClient(
      <AccountLoginDialog isOpen signUp onClose={vi.fn()} />,
      createTestQueryClient()
    )
    expect(
      screen.getByRole('heading', { name: 'Get started with Readyset' })
    ).toBeTruthy()
    const google = screen.getByRole('button', { name: 'Sign up with Google' })
    fireEvent.click(google)
    await waitFor(() => expect(startAccountGoogleOAuth).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: 'Email me a link' })).toBeTruthy()
  })

  it('says so when Google sign-up cannot start', async () => {
    vi.mocked(startAccountLogin).mockRejectedValueOnce(
      new Error(
        "Readyset's sign-in service didn't respond. Try again in a minute."
      )
    )
    renderWithClient(
      <AccountLoginDialog isOpen onClose={vi.fn()} signUp />,
      createTestQueryClient()
    )
    fireEvent.click(screen.getByRole('button', { name: 'Sign up with Google' }))
    expect(
      await screen.findByText(
        "Readyset's sign-in service didn't respond. Try again in a minute."
      )
    ).toBeTruthy()
    expect(startAccountGoogleOAuth).not.toHaveBeenCalled()
  })

  it('starts magic-link authentication from the RDST dialog', async () => {
    renderWithClient(
      <AccountLoginDialog isOpen onClose={vi.fn()} />,
      createTestQueryClient()
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'Email address' }), {
      target: { value: 'person@example.com' },
    })
    expect(screen.queryByText('Prefer your own Anthropic key?')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Send sign-in link' }))

    await waitFor(() => expect(startAccountLogin).toHaveBeenCalledTimes(1))
    expect(vi.mocked(startAccountLogin).mock.calls[0]?.[0]).toContain(
      '/account-login'
    )
    await waitFor(() =>
      expect(sendAccountMagicLink).toHaveBeenCalledWith(
        context,
        'person@example.com'
      )
    )
    expect(screen.getByText('Check your email')).toBeTruthy()
  })

  it('tags its sign-in events with the screen that asked', async () => {
    renderWithClient(
      <AccountLoginDialog isOpen onClose={vi.fn()} source="monitoring_gate" />,
      createTestQueryClient()
    )
    fireEvent.change(screen.getByRole('textbox', { name: 'Email address' }), {
      target: { value: 'person@example.com' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send sign-in link' }))
    await waitFor(() =>
      expect(trackEvent).toHaveBeenCalledWith('signin_started', {
        method: 'email',
        source: 'monitoring_gate',
      })
    )
  })

  it('completes a Supabase redirect inside RDST', async () => {
    const onSuccess = vi.fn()
    renderWithClient(
      <AccountLoginDialog
        isOpen
        callbackLoginId="login-1"
        onClose={vi.fn()}
        onSuccess={onSuccess}
      />,
      createTestQueryClient()
    )

    await waitFor(() =>
      expect(completeAccountRedirect).toHaveBeenCalledWith('login-1', undefined)
    )
    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1))
    expect(screen.getByText(/Readyset account connected/)).toBeTruthy()
  })

  it('returns desktop OAuth through its local RDST server', async () => {
    window.rdstDesktop = {
      isDesktop: true,
      platform: 'linux',
    }
    vi.mocked(fetchAccountBrowserCallback).mockResolvedValue({
      state: 'ready',
      code: 'desktop-code',
      error: null,
      error_description: null,
    })
    renderWithClient(
      <AccountLoginDialog isOpen onClose={vi.fn()} />,
      createTestQueryClient()
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Continue with Google' })
    )

    await waitFor(() => {
      const returnUrl = vi.mocked(startAccountLogin).mock.calls[0]?.[0]
      expect(new URL(returnUrl ?? '').pathname).toBe(
        '/api/account/oauth/callback'
      )
    })
    await waitFor(() =>
      expect(startAccountGoogleOAuth).toHaveBeenCalledWith(context)
    )

    await waitFor(() =>
      expect(fetchAccountBrowserCallback).toHaveBeenCalledWith('login-1')
    )
    await waitFor(() => {
      const callbackUrl = vi.mocked(completeAccountRedirect).mock.calls[0]?.[1]
      expect(completeAccountRedirect).toHaveBeenCalledWith(
        'login-1',
        expect.any(String)
      )
      expect(new URL(callbackUrl ?? '').searchParams.get('code')).toBe(
        'desktop-code'
      )
    })
    await act(async () => {
      await Promise.resolve()
    })
    expect(screen.getByText(/Readyset account connected/)).toBeTruthy()
  })

  it('shows a desktop sign-in RDST finished from the link as signed in', async () => {
    window.rdstDesktop = {
      isDesktop: true,
      platform: 'linux',
    }
    vi.mocked(fetchAccountBrowserCallback).mockResolvedValue({
      state: 'success',
      code: null,
      error: null,
      error_description: null,
    })
    const onSuccess = vi.fn()
    renderWithClient(
      <AccountLoginDialog isOpen onClose={vi.fn()} onSuccess={onSuccess} />,
      createTestQueryClient()
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Continue with Google' })
    )

    await waitFor(() => expect(onSuccess).toHaveBeenCalledTimes(1))
    expect(completeAccountRedirect).not.toHaveBeenCalled()
    expect(screen.getByText(/Readyset account connected/)).toBeTruthy()
  })

  it('answers an invalid address under the field and holds the request', async () => {
    renderWithClient(
      <AccountLoginDialog isOpen onClose={vi.fn()} />,
      createTestQueryClient()
    )

    const input = screen.getByLabelText('Email address')
    fireEvent.change(input, { target: { value: 'not-an-email' } })
    fireEvent.blur(input)

    // Was: submit stayed enabled and a red block landed at the top of the
    // dialog, 285px above the field it was about to reject. [A-16]
    expect(screen.getByText('Enter a valid email address.')).toBeTruthy()
    expect(input.getAttribute('aria-invalid')).toBe('true')
    expect(screen.queryByText(/secure sign-in link/i)).toBeNull()
    expect(
      screen
        .getByRole('button', { name: 'Send sign-in link' })
        .hasAttribute('disabled')
    ).toBe(true)
  })

  it('leaves an empty field alone when focus moves to Google', () => {
    renderWithClient(
      <AccountLoginDialog isOpen onClose={vi.fn()} />,
      createTestQueryClient()
    )

    const input = screen.getByLabelText('Email address')
    fireEvent.blur(input)

    expect(screen.queryByText('Enter a valid email address.')).toBeNull()
    expect(input.getAttribute('aria-invalid')).toBeNull()
  })

  it('offers Google and an emailed link, and says what signing in is for', () => {
    renderWithClient(
      <AccountLoginDialog isOpen onClose={vi.fn()} />,
      createTestQueryClient()
    )

    expect(
      screen.getByRole('heading', { name: 'Sign in to Readyset' })
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Continue with Google' })
    ).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'Continue with GitHub' })
    ).toBeNull()
    // The dialog's one solid action is the emailed link.
    expect(
      screen.getByRole('button', { name: 'Send sign-in link' }).className
    ).toContain('bg-surface-primary-solid')
    expect(
      screen.getByText(
        'A Readyset account is free and comes with AI for monitoring, analyses, checks and Ask.'
      )
    ).toBeTruthy()
  })
})
