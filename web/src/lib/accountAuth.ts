import { createClient, type Session } from '@supabase/supabase-js'
import {
  type AccountLoginResponse,
  completeAccountLogin,
  fetchAccountLoginContext,
  fetchAccountLoginStatus,
} from './api'
import { isDesktopRuntime } from './desktop'

const storageKey = (loginId: string) => `rdst-account-${loginId}`

function projectUrl(authUrl: string): string {
  return authUrl.replace(/\/auth\/v1\/?$/, '')
}

function authClient(context: AccountLoginResponse) {
  return createClient(projectUrl(context.auth_url), context.publishable_key, {
    auth: {
      autoRefreshToken: false,
      detectSessionInUrl: false,
      flowType: 'pkce',
      persistSession: true,
      storageKey: storageKey(context.login_id),
    },
  })
}

export async function sendAccountMagicLink(
  context: AccountLoginResponse,
  email: string
): Promise<void> {
  const { error } = await authClient(context).auth.signInWithOtp({
    email,
    options: { emailRedirectTo: context.callback_url },
  })
  if (error) throw error
}

export async function startAccountOAuth(
  context: AccountLoginResponse,
  provider: 'google' | 'github'
): Promise<void> {
  const { data, error } = await authClient(context).auth.signInWithOAuth({
    provider,
    options: {
      redirectTo: context.callback_url,
      skipBrowserRedirect: true,
    },
  })
  if (error) throw error
  if (!data.url)
    throw new Error('Readyset sign-in returned no authorization URL')
  if (isDesktopRuntime()) {
    window.open(data.url, '_blank', 'noopener,noreferrer')
  } else {
    window.location.assign(data.url)
  }
}

export const startAccountGoogleOAuth = (context: AccountLoginResponse) =>
  startAccountOAuth(context, 'google')

export const startAccountGithubOAuth = (context: AccountLoginResponse) =>
  startAccountOAuth(context, 'github')

function clearTemporarySession(loginId: string): void {
  const prefix = storageKey(loginId)
  for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
    const key = window.localStorage.key(index)
    if (key?.startsWith(prefix)) window.localStorage.removeItem(key)
  }
}

export async function completeAccountRedirect(
  loginId: string,
  callbackUrl?: string
): Promise<{ state: string; detail: string }> {
  const context = await fetchAccountLoginContext(loginId)
  const params = callbackUrl
    ? new URL(callbackUrl).searchParams
    : new URLSearchParams(window.location.search)
  const oauthError = params.get('error_description') ?? params.get('error')
  if (oauthError) throw new Error(oauthError)
  const code = params.get('code')
  if (!code) throw new Error('Supabase returned no authorization code')
  const client = authClient(context)
  const { data, error } = await client.auth.exchangeCodeForSession(code)
  if (error) throw error
  const session: Session | null = data.session
  if (!session?.access_token || !session.refresh_token) {
    throw new Error('Supabase did not return an account session')
  }
  let result = await completeAccountLogin(
    loginId,
    context.state,
    session.access_token,
    session.refresh_token,
    session.expires_in ?? 3600
  )
  for (let attempt = 0; result.state === 'running' && attempt < 8; attempt += 1) {
    await new Promise((resolve) =>
      window.setTimeout(resolve, Math.min(4_000, 500 * 2 ** attempt))
    )
    result = await fetchAccountLoginStatus(loginId)
  }
  if (result.state === 'running') {
    throw new Error(result.detail || 'Readyset sign-in is still completing. Try again.')
  }
  if (result.state !== 'success') {
    clearTemporarySession(loginId)
    throw new Error(result.detail || 'Readyset sign-in failed')
  }
  clearTemporarySession(loginId)
  if (!callbackUrl) window.history.replaceState({}, '', '/account-login')
  return result
}
