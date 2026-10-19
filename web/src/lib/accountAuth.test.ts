import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  completeAccountRedirect,
  sendAccountMagicLink,
  startAccountGoogleOAuth,
} from './accountAuth'
import {
  completeAccountLogin,
  fetchAccountBrowserCallback,
  fetchAccountLoginContext,
  fetchAccountLoginStatus,
} from './api'

const signInWithOtp = vi.fn()
const signInWithOAuth = vi.fn()
const exchangeCodeForSession = vi.fn()
const setSession = vi.fn()
const updateUser = vi.fn()
const linkIdentity = vi.fn()
const refreshSession = vi.fn()

vi.mock('@supabase/supabase-js', () => ({
  createClient: vi.fn(() => ({
    auth: {
      signInWithOtp,
      signInWithOAuth,
      exchangeCodeForSession,
      setSession,
      updateUser,
      linkIdentity,
      refreshSession,
    },
  })),
}))

vi.mock('./api', async () => {
  const actual = await vi.importActual('./api')
  return {
    ...actual,
    completeAccountLogin: vi.fn(),
    fetchAccountBrowserCallback: vi.fn(),
    fetchAccountLoginContext: vi.fn(),
    fetchAccountLoginStatus: vi.fn(),
  }
})

const context = {
  login_id: 'login-1',
  state: 'state-1',
  auth_url: 'https://project.supabase.co/auth/v1',
  publishable_key: 'publishable',
  callback_url:
    'https://keyservice.example/account-auth/callback?login_id=login-1',
  expires_in: 600,
}

const anonymousContext = {
  ...context,
  anonymous_session: {
    access_token: 'anon-access',
    refresh_token: 'anon-refresh',
  },
}

describe('RDST account authentication', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(window, 'open').mockReturnValue(null)
    window.rdstDesktop = { isDesktop: true, platform: 'linux' }
    window.history.replaceState({}, '', '/account-login')
    signInWithOtp.mockResolvedValue({ error: null })
    signInWithOAuth.mockResolvedValue({
      data: { url: 'https://accounts.google.com/o/oauth2/auth' },
      error: null,
    })
    exchangeCodeForSession.mockResolvedValue({
      data: {
        session: {
          access_token: 'access',
          refresh_token: 'refresh',
          expires_in: 3600,
        },
      },
      error: null,
    })
    vi.mocked(fetchAccountLoginContext).mockResolvedValue(context)
    vi.mocked(completeAccountLogin).mockResolvedValue({
      state: 'success',
      detail: 'Signed in to Readyset',
    })
  })

  afterEach(() => {
    delete window.rdstDesktop
  })

  it('sends magic links back through the Keyservice callback', async () => {
    await sendAccountMagicLink(context, 'person@example.com')

    expect(signInWithOtp).toHaveBeenCalledWith({
      email: 'person@example.com',
      options: { emailRedirectTo: context.callback_url },
    })
  })

  it('starts Google OAuth from RDST with the same callback', async () => {
    await startAccountGoogleOAuth(context)

    expect(signInWithOAuth).toHaveBeenCalledWith({
      provider: 'google',
      options: {
        redirectTo: context.callback_url,
        skipBrowserRedirect: true,
      },
    })
    expect(window.open).toHaveBeenCalledWith(
      'https://accounts.google.com/o/oauth2/auth',
      '_blank',
      'noopener,noreferrer'
    )
  })

  it('exchanges the returned PKCE code in RDST and completes pickup', async () => {
    window.history.replaceState(
      {},
      '',
      '/account-login?login_id=login-1&code=oauth-code'
    )

    const result = await completeAccountRedirect('login-1')

    expect(exchangeCodeForSession).toHaveBeenCalledWith('oauth-code')
    expect(completeAccountLogin).toHaveBeenCalledWith(
      'login-1',
      'state-1',
      'access',
      'refresh',
      3600
    )
    expect(result.state).toBe('success')
    expect(window.location.search).toBe('')
  })

  it('exchanges a desktop deep-link code without navigating the renderer', async () => {
    const result = await completeAccountRedirect(
      'login-1',
      'rdst-dev://account-login?login_id=login-1&code=desktop-code'
    )

    expect(exchangeCodeForSession).toHaveBeenCalledWith('desktop-code')
    expect(completeAccountLogin).toHaveBeenCalledWith(
      'login-1',
      'state-1',
      'access',
      'refresh',
      3600
    )
    expect(result.state).toBe('success')
    expect(window.location.pathname).toBe('/account-login')
  })

  it('polls a recoverable pickup and rejects a terminal failure', async () => {
    vi.mocked(completeAccountLogin).mockResolvedValue({
      state: 'running',
      detail: 'Keyservice is temporarily unavailable',
    })
    vi.mocked(fetchAccountLoginStatus).mockResolvedValue({
      state: 'failed',
      detail: 'This sign-in expired. Start again.',
    })

    await expect(
      completeAccountRedirect(
        'login-1',
        'rdst-dev://account-login?login_id=login-1&code=desktop-code'
      )
    ).rejects.toThrow('This sign-in expired')
    expect(fetchAccountLoginStatus).toHaveBeenCalledWith('login-1')
  })

  describe('from an anonymous session', () => {
    beforeEach(() => {
      setSession.mockResolvedValue({ data: {}, error: null })
      linkIdentity.mockResolvedValue({
        data: { provider: 'google', url: 'https://project/link-google' },
        error: null,
      })
    })

    it('adds the email to the anonymous user so the link upgrades it', async () => {
      updateUser.mockResolvedValue({
        data: {
          user: { is_anonymous: true, new_email: 'person@example.com' },
        },
        error: null,
      })

      const outcome = await sendAccountMagicLink(
        anonymousContext,
        'person@example.com'
      )

      expect(outcome).toBe('sent')
      expect(setSession).toHaveBeenCalledWith({
        access_token: 'anon-access',
        refresh_token: 'anon-refresh',
      })
      expect(updateUser).toHaveBeenCalledWith(
        { email: 'person@example.com' },
        { emailRedirectTo: context.callback_url }
      )
      expect(signInWithOtp).not.toHaveBeenCalled()
      expect(window.localStorage.getItem('rdst-account-login-1')).toBeNull()
    })

    it('finishes at once when Supabase confirms the email without a link', async () => {
      updateUser.mockResolvedValue({
        data: {
          user: { is_anonymous: false, email: 'person@example.com' },
        },
        error: null,
      })
      refreshSession.mockResolvedValue({
        data: {
          session: {
            access_token: 'upgraded',
            refresh_token: 'upgraded-refresh',
            expires_in: 3600,
          },
        },
        error: null,
      })

      const outcome = await sendAccountMagicLink(
        anonymousContext,
        'person@example.com'
      )

      expect(outcome).toBe('signed_in')
      expect(completeAccountLogin).toHaveBeenCalledWith(
        'login-1',
        'state-1',
        'upgraded',
        'upgraded-refresh',
        3600
      )
    })

    it('signs in to the account that already owns the email', async () => {
      updateUser.mockResolvedValue({
        data: { user: null },
        error: {
          code: 'email_exists',
          message: 'A user with this email address has already been registered',
        },
      })

      const outcome = await sendAccountMagicLink(
        anonymousContext,
        'person@example.com'
      )

      expect(outcome).toBe('sent')
      expect(signInWithOtp).toHaveBeenCalledWith({
        email: 'person@example.com',
        options: { emailRedirectTo: context.callback_url },
      })
    })

    it('surfaces other email errors', async () => {
      updateUser.mockResolvedValue({
        data: { user: null },
        error: { code: 'email_address_invalid', message: 'Email is invalid' },
      })

      await expect(
        sendAccountMagicLink(anonymousContext, 'person@example.com')
      ).rejects.toMatchObject({ code: 'email_address_invalid' })
      expect(signInWithOtp).not.toHaveBeenCalled()
    })

    it('uses plain sign-in when Supabase rejects the anonymous session', async () => {
      setSession.mockResolvedValue({
        data: {},
        error: { message: 'Invalid Refresh Token' },
      })

      await sendAccountMagicLink(anonymousContext, 'person@example.com')

      expect(updateUser).not.toHaveBeenCalled()
      expect(signInWithOtp).toHaveBeenCalled()
    })

    it('links Google to the anonymous user', async () => {
      await startAccountGoogleOAuth(anonymousContext)

      expect(linkIdentity).toHaveBeenCalledWith({
        provider: 'google',
        options: {
          redirectTo: context.callback_url,
          skipBrowserRedirect: true,
        },
      })
      expect(signInWithOAuth).not.toHaveBeenCalled()
      expect(window.open).toHaveBeenCalledWith(
        'https://project/link-google',
        '_blank',
        'noopener,noreferrer'
      )
    })

    it('signs in with Google when linking is unavailable', async () => {
      linkIdentity.mockResolvedValue({
        data: { provider: 'google', url: null },
        error: { code: 'manual_linking_disabled', message: 'disabled' },
      })

      await startAccountGoogleOAuth(anonymousContext)

      expect(signInWithOAuth).toHaveBeenCalled()
      expect(window.open).toHaveBeenCalledWith(
        'https://accounts.google.com/o/oauth2/auth',
        '_blank',
        'noopener,noreferrer'
      )
    })

    it('signs in to the account that already owns the Google identity', async () => {
      vi.mocked(fetchAccountBrowserCallback).mockResolvedValue({
        state: 'ready',
        code: 'existing-code',
      })

      const result = await completeAccountRedirect(
        'login-1',
        'rdst-dev://account-login?login_id=login-1&error=server_error' +
          '&error_code=identity_already_exists' +
          '&error_description=Identity+is+already+linked+to+another+user'
      )

      expect(linkIdentity).not.toHaveBeenCalled()
      expect(signInWithOAuth).toHaveBeenCalledWith({
        provider: 'google',
        options: {
          redirectTo: context.callback_url,
          skipBrowserRedirect: true,
        },
      })
      expect(exchangeCodeForSession).toHaveBeenCalledWith('existing-code')
      expect(result.state).toBe('success')
    })

    it('recognizes the conflict from the description alone', async () => {
      vi.mocked(fetchAccountBrowserCallback).mockResolvedValue({
        state: 'ready',
        code: 'existing-code',
      })

      await completeAccountRedirect(
        'login-1',
        'rdst-dev://account-login?login_id=login-1&error=server_error' +
          '&error_description=Identity+is+already+linked+to+another+user'
      )

      expect(signInWithOAuth).toHaveBeenCalled()
    })

    it('still rejects other OAuth errors', async () => {
      await expect(
        completeAccountRedirect(
          'login-1',
          'rdst-dev://account-login?login_id=login-1&error=access_denied'
        )
      ).rejects.toThrow('access_denied')
      expect(signInWithOAuth).not.toHaveBeenCalled()
    })
  })
})
