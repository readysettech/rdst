import { createClient, type Session } from '@supabase/supabase-js'
import {
  type AccountLoginResponse,
  completeAccountLogin,
  fetchAccountBrowserCallback,
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

type AuthClient = ReturnType<typeof authClient>

/**
 * A client signed in as the install's anonymous user, so a new email or Google identity
 * upgrades that same user instead of creating another one. Null without an anonymous
 * session, or when Supabase no longer accepts it; plain sign-in still works then.
 */
async function anonymousClient(
  context: AccountLoginResponse
): Promise<AuthClient | null> {
  const anonymous = context.anonymous_session
  if (!anonymous?.access_token || !anonymous.refresh_token) return null
  const client = authClient(context)
  const { error } = await client.auth.setSession({
    access_token: anonymous.access_token,
    refresh_token: anonymous.refresh_token,
  })
  if (error) {
    dropAnonymousSession(context.login_id)
    return null
  }
  return client
}

// RDST keeps the anonymous session; the browser copy only lives for the upgrade call. The
// PKCE verifier stays for the callback.
function dropAnonymousSession(loginId: string): void {
  window.localStorage.removeItem(storageKey(loginId))
}

// The email or Google identity already belongs to a Readyset account.
const ALREADY_REGISTERED_CODES = new Set([
  'email_exists',
  'identity_already_exists',
])
const ALREADY_REGISTERED =
  /already (been )?registered|already linked|already exists/i

function belongsToAnotherAccount(
  code?: string | null,
  message?: string | null
) {
  return (
    (!!code && ALREADY_REGISTERED_CODES.has(code)) ||
    (!!message && ALREADY_REGISTERED.test(message))
  )
}

export type MagicLinkOutcome = 'sent' | 'signed_in'

/**
 * Email an RDST sign-in link. From an anonymous session the address is added to that
 * user, so the link upgrades it. An address that already has an account gets a normal
 * sign-in link to it instead. When Supabase accepts the address without a link, the
 * upgrade finishes here and this returns `signed_in`.
 */
export async function sendAccountMagicLink(
  context: AccountLoginResponse,
  email: string
): Promise<MagicLinkOutcome> {
  const anonymous = await anonymousClient(context)
  if (anonymous) {
    const { data, error } = await anonymous.auth.updateUser(
      { email },
      { emailRedirectTo: context.callback_url }
    )
    if (
      !error &&
      data.user &&
      !data.user.is_anonymous &&
      !data.user.new_email
    ) {
      // The address was confirmed on the spot. A refreshed token carries the upgrade.
      const refreshed = await anonymous.auth.refreshSession()
      dropAnonymousSession(context.login_id)
      if (refreshed.error) throw refreshed.error
      await finishAccountLogin(
        context.login_id,
        context.state,
        refreshed.data.session
      )
      return 'signed_in'
    }
    dropAnonymousSession(context.login_id)
    if (!error) return 'sent'
    if (!belongsToAnotherAccount(error.code, error.message)) throw error
  }
  const { error } = await authClient(context).auth.signInWithOtp({
    email,
    options: { emailRedirectTo: context.callback_url },
  })
  if (error) throw error
  return 'sent'
}

function openAuthorizationUrl(url: string | null | undefined): void {
  if (!url) throw new Error('Readyset sign-in returned no authorization URL')
  if (isDesktopRuntime()) {
    window.open(url, '_blank', 'noopener,noreferrer')
  } else {
    window.location.assign(url)
  }
}

/**
 * Start OAuth sign-in. From an anonymous session the provider identity is linked to that
 * user (`link: false` skips this); an identity owned by another account comes back to
 * the callback as `identity_already_exists`, which signs in to that account instead.
 */
export async function startAccountOAuth(
  context: AccountLoginResponse,
  provider: 'google',
  { link = true }: { link?: boolean } = {}
): Promise<void> {
  const options = {
    redirectTo: context.callback_url,
    skipBrowserRedirect: true,
  }
  const anonymous = link ? await anonymousClient(context) : null
  if (anonymous) {
    const { data, error } = await anonymous.auth.linkIdentity({
      provider,
      options,
    })
    dropAnonymousSession(context.login_id)
    if (!error) return openAuthorizationUrl(data.url)
    // Linking unavailable: a plain sign-in still carries this machine's usage over.
  }
  const { data, error } = await authClient(context).auth.signInWithOAuth({
    provider,
    options,
  })
  if (error) throw error
  openAuthorizationUrl(data.url)
}

export const startAccountGoogleOAuth = (context: AccountLoginResponse) =>
  startAccountOAuth(context, 'google')

function clearTemporarySession(loginId: string): void {
  const prefix = storageKey(loginId)
  for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
    const key = window.localStorage.key(index)
    if (key?.startsWith(prefix)) window.localStorage.removeItem(key)
  }
}

async function finishAccountLogin(
  loginId: string,
  state: string,
  session: Session | null
): Promise<{ state: string; detail: string }> {
  if (!session?.access_token || !session.refresh_token) {
    throw new Error('Supabase did not return an account session')
  }
  let result = await completeAccountLogin(
    loginId,
    state,
    session.access_token,
    session.refresh_token,
    session.expires_in ?? 3600
  )
  for (
    let attempt = 0;
    result.state === 'running' && attempt < 8;
    attempt += 1
  ) {
    await new Promise((resolve) =>
      window.setTimeout(resolve, Math.min(4_000, 500 * 2 ** attempt))
    )
    result = await fetchAccountLoginStatus(loginId)
  }
  if (result.state === 'running') {
    throw new Error(
      result.detail || 'Readyset sign-in is still completing. Try again.'
    )
  }
  clearTemporarySession(loginId)
  if (result.state !== 'success') {
    throw new Error(result.detail || 'Readyset sign-in failed')
  }
  return result
}

/**
 * The Google account picked while linking already has a Readyset account: sign in to it.
 * A browser tab navigates away; the desktop app waits for the new callback.
 */
async function signInToExistingAccount(
  context: AccountLoginResponse
): Promise<{ state: string; detail: string }> {
  await startAccountOAuth(context, 'google', { link: false })
  if (!isDesktopRuntime()) return new Promise(() => {})
  const deadline = Date.now() + context.expires_in * 1000
  while (Date.now() < deadline) {
    await new Promise((resolve) => window.setTimeout(resolve, 500))
    const callback = await fetchAccountBrowserCallback(context.login_id)
    if (callback.state === 'ready' && callback.code) {
      const url = new URL(context.callback_url)
      url.search = new URLSearchParams({
        login_id: context.login_id,
        code: callback.code,
      }).toString()
      return completeAccountRedirect(context.login_id, url.toString())
    }
  }
  throw new Error('This sign-in expired. Start again.')
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
  if (oauthError) {
    if (belongsToAnotherAccount(params.get('error_code'), oauthError)) {
      return signInToExistingAccount(context)
    }
    throw new Error(oauthError)
  }
  const code = params.get('code')
  if (!code) throw new Error('Supabase returned no authorization code')
  const client = authClient(context)
  const { data, error } = await client.auth.exchangeCodeForSession(code)
  if (error) throw error
  const result = await finishAccountLogin(loginId, context.state, data.session)
  if (!callbackUrl) window.history.replaceState({}, '', '/account-login')
  return result
}
