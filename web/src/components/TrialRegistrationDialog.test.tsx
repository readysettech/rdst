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
  startAccountGithubOAuth,
  startAccountGoogleOAuth,
} from '../lib/accountAuth'
import { fetchAccountBrowserCallback, startAccountLogin } from '../lib/api'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { AccountLoginDialog } from './TrialRegistrationDialog'

vi.mock('../lib/accountAuth', () => ({
  completeAccountRedirect: vi.fn(),
  sendAccountMagicLink: vi.fn(),
  startAccountGithubOAuth: vi.fn(),
  startAccountGoogleOAuth: vi.fn(),
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
    vi.mocked(startAccountGithubOAuth).mockResolvedValue(undefined)
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
    expect(screen.getByText('Readyset account connected')).toBeTruthy()
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
    expect(screen.getByText('Readyset account connected')).toBeTruthy()
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

  it('offers GitHub OAuth from the same Readyset dialog', async () => {
    renderWithClient(
      <AccountLoginDialog isOpen onClose={vi.fn()} />,
      createTestQueryClient()
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Continue with GitHub' })
    )

    await waitFor(() =>
      expect(startAccountGithubOAuth).toHaveBeenCalledWith(context)
    )
  })
})
