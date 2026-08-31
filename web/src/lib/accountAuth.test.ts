import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  completeAccountRedirect,
  sendAccountMagicLink,
  startAccountGithubOAuth,
  startAccountGoogleOAuth,
} from './accountAuth'
import {
  completeAccountLogin,
  fetchAccountLoginContext,
  fetchAccountLoginStatus,
} from './api'

const signInWithOtp = vi.fn()
const signInWithOAuth = vi.fn()
const exchangeCodeForSession = vi.fn()

vi.mock('@supabase/supabase-js', () => ({
  createClient: vi.fn(() => ({
    auth: { signInWithOtp, signInWithOAuth, exchangeCodeForSession },
  })),
}))

vi.mock('./api', async () => {
  const actual = await vi.importActual('./api')
  return {
    ...actual,
    completeAccountLogin: vi.fn(),
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

  it('starts GitHub OAuth through the same desktop browser handoff', async () => {
    signInWithOAuth.mockResolvedValue({
      data: { url: 'https://github.com/login/oauth/authorize' },
      error: null,
    })

    await startAccountGithubOAuth(context)

    expect(signInWithOAuth).toHaveBeenCalledWith({
      provider: 'github',
      options: {
        redirectTo: context.callback_url,
        skipBrowserRedirect: true,
      },
    })
    expect(window.open).toHaveBeenCalledWith(
      'https://github.com/login/oauth/authorize',
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
})
