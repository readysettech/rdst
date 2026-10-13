import { cn } from '@rs/tailwind-base'
import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Field } from '@rs/ui-new/field'
import { Icon } from '@rs/ui-new/icon'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useCanGoBack, useNavigate, useRouter } from '@tanstack/react-router'
import { useEffect, useId, useRef, useState } from 'react'
import { isValidEmail, normalizeEmail } from '../components/emailValidation'
import { takeLoginReturn } from '../components/TrialRegistrationDialog'
import {
  completeAccountRedirect,
  sendAccountMagicLink,
  startAccountGithubOAuth,
  startAccountGoogleOAuth,
} from '../lib/accountAuth'
import {
  type AccountLoginResponse,
  fetchAccountBrowserCallback,
  fetchAccountStatus,
  startAccountLogin,
} from '../lib/api'
import { isDesktopMac, isDesktopRuntime } from '../lib/desktop'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { VALUE_PROPOSITION } from '../lib/valueProposition'

function localCallbackUrl(): string {
  return new URL('/account-login', window.location.origin).toString()
}

function accountCallbackUrl(): string {
  return isDesktopRuntime()
    ? new URL('/api/account/oauth/callback', window.location.origin).toString()
    : localCallbackUrl()
}

function GithubMark() {
  return (
    <svg
      aria-hidden="true"
      className="h-4 w-4"
      viewBox="0 0 16 16"
      fill="currentColor"
    >
      <path d="M8 .2a8 8 0 0 0-2.5 15.6c.4.1.5-.2.5-.4v-1.4c-2.2.5-2.7-1-2.7-1-.4-.9-.9-1.2-.9-1.2-.7-.5.1-.5.1-.5.8.1 1.2.8 1.2.8.7 1.2 1.9.9 2.3.7.1-.5.3-.9.5-1.1-1.8-.2-3.6-.9-3.6-4 0-.9.3-1.6.8-2.1-.1-.2-.4-1 .1-2.1 0 0 .7-.2 2.2.8a7.5 7.5 0 0 1 4 0c1.5-1 2.2-.8 2.2-.8.4 1.1.2 1.9.1 2.1.5.6.8 1.3.8 2.1 0 3.1-1.9 3.8-3.6 4 .3.3.6.8.6 1.5v2.2c0 .2.1.5.6.4A8 8 0 0 0 8 .2z" />
    </svg>
  )
}

function GoogleMark() {
  return (
    <svg
      aria-hidden="true"
      className="h-4 w-4"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <path d="M13.6 8.2c0-.5 0-.9-.1-1.3H8v2.5h3.2a2.9 2.9 0 0 1-1.2 1.8v1.5h2c1.1-1.1 1.6-2.6 1.6-4.5z" />
      <path d="M8 14c1.6 0 3-.5 4-1.4l-2-1.5c-.5.4-1.2.6-2 .6a3.5 3.5 0 0 1-3.3-2.4H2.7v1.6A6 6 0 0 0 8 14z" />
      <path d="M4.7 9.3a3.6 3.6 0 0 1 0-2.6V5.1h-2a6 6 0 0 0 0 5.8z" />
      <path d="M8 4.4c.9 0 1.7.3 2.3.9l1.7-1.7A6 6 0 0 0 2.7 5.1l2 1.6A3.5 3.5 0 0 1 8 4.4z" />
    </svg>
  )
}

/**
 * In the desktop app the provider opens in the system browser; this polls for
 * its callback and completes the sign-in here.
 */
function useDesktopCallback(
  loginId: string | null,
  complete: (loginId: string, callbackUrl: string) => void,
  onError: (message: string) => void
) {
  const started = useRef(false)
  useEffect(() => {
    if (!loginId || started.current) return
    let cancelled = false
    let timer: number | undefined
    const poll = async () => {
      try {
        const result = await fetchAccountBrowserCallback(loginId)
        if (cancelled || started.current) return
        if (result.state === 'pending') {
          timer = window.setTimeout(poll, 500)
          return
        }
        const url = new URL(localCallbackUrl())
        if (result.code) url.searchParams.set('code', result.code)
        if (result.error) url.searchParams.set('error', result.error)
        if (result.error_description)
          url.searchParams.set('error_description', result.error_description)
        started.current = true
        complete(loginId, url.toString())
      } catch (error) {
        if (!cancelled)
          onError(
            error instanceof Error ? error.message : 'Readyset sign-in failed'
          )
      }
    }
    timer = window.setTimeout(poll, 0)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [loginId, complete, onError])
}

/**
 * In a browser the emailed link signs in from another tab; this one notices and
 * carries on.
 */
function useEmailSignIn(waiting: boolean, onSignedIn: () => void) {
  const signedIn = useRef(onSignedIn)
  signedIn.current = onSignedIn
  useEffect(() => {
    if (!waiting || isDesktopRuntime()) return
    let cancelled = false
    const id = window.setInterval(async () => {
      try {
        const status = await fetchAccountStatus()
        if (!cancelled && status.signed_in) {
          window.clearInterval(id)
          signedIn.current()
        }
      } catch {
        /* keep waiting for the link */
      }
    }, 2_000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [waiting])
}

/**
 * The one page where the rising purple appears: sign in with a provider. A provider
 * redirect lands here with `callbackLoginId`, and the column finishes that sign-in.
 */
export function SignInPage({
  callbackLoginId = null,
}: {
  callbackLoginId?: string | null
} = {}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const router = useRouter()
  const canGoBack = useCanGoBack()
  const [desktopLoginId, setDesktopLoginId] = useState<string | null>(null)
  const [pollError, setPollError] = useState<string | null>(null)
  const emailFieldId = useId()
  const [email, setEmail] = useState('')
  const [emailError, setEmailError] = useState<string | null>(null)

  const finish = async () => {
    await invalidateTrialRelatedQueries(queryClient)
    void navigate({ href: takeLoginReturn() })
  }
  const start = async (
    open: (context: AccountLoginResponse) => Promise<void>
  ) => {
    const context = await startAccountLogin(accountCallbackUrl())
    await open(context)
    if (isDesktopRuntime()) setDesktopLoginId(context.login_id)
  }
  const github = useMutation({
    mutationFn: () => start(startAccountGithubOAuth),
  })
  const google = useMutation({
    mutationFn: () => start(startAccountGoogleOAuth),
  })
  const magicLink = useMutation({
    mutationFn: () =>
      start((context) => sendAccountMagicLink(context, email.trim())),
  })
  useEmailSignIn(magicLink.isSuccess, () => void finish())
  const validateEmail = () => {
    const valid = isValidEmail(normalizeEmail(email))
    setEmailError(valid ? null : 'Enter a valid email address.')
    return valid
  }
  const callback = useMutation({
    mutationFn: ({ loginId, url }: { loginId: string; url?: string }) =>
      completeAccountRedirect(loginId, url),
    onSuccess: finish,
  })
  const completeRef = useRef((loginId: string, url?: string) =>
    callback.mutate({ loginId, url })
  )
  useDesktopCallback(desktopLoginId, completeRef.current, setPollError)

  const redirectStarted = useRef(false)
  useEffect(() => {
    if (!callbackLoginId || redirectStarted.current) return
    redirectStarted.current = true
    completeRef.current(callbackLoginId)
  }, [callbackLoginId])
  const finishing =
    !!callbackLoginId && !callback.isError && !callback.isSuccess
  const goBack = () =>
    canGoBack ? router.history.back() : void navigate({ to: '/' })
  // Escape leaves the way Back does.
  const leave = useRef<(() => void) | null>(goBack)
  leave.current = finishing ? null : goBack
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') leave.current?.()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const failure =
    github.error ??
    google.error ??
    magicLink.error ??
    callback.error ??
    pollError ??
    null
  const error =
    callbackLoginId && callback.isError
      ? 'This sign-in link has expired or was already used.'
      : failure instanceof Error
        ? failure.message
        : failure
  const pending =
    github.isPending ||
    google.isPending ||
    magicLink.isPending ||
    callback.isPending

  return (
    <main className="relative flex min-h-dvh items-center justify-center bg-surface-layout-2 px-6 py-12">
      {!finishing && (
        <div
          className={cn('absolute left-4', isDesktopMac() ? 'top-12' : 'top-4')}
        >
          <Button
            variant="primary"
            modifier="ghost"
            size="small"
            icon="arrow-left"
            label="Back"
            onClick={goBack}
          />
        </div>
      )}
      <div className="flex w-80 flex-col items-center text-center">
        <div className="mb-5 flex h-11 w-11 items-center justify-center rounded-panel border border-border-layout-2 bg-surface-primary-soft text-content-layout-1">
          <Icon name="database" label="" size="medium" />
        </div>
        <Text as="h1" level="headline-2" className="text-content-layout-1">
          RDST
        </Text>
        <Text
          as="p"
          level="body-medium"
          className="mt-1.5 text-content-layout-2"
        >
          {VALUE_PROPOSITION}
        </Text>
        {finishing ? (
          <Text
            as="p"
            level="body-small"
            className="mt-8 text-content-layout-3"
          >
            Finishing sign-in...
          </Text>
        ) : magicLink.isSuccess ? (
          <div className="mt-8 flex w-full flex-col gap-1">
            <Text as="p" level="body-small" className="text-content-layout-1">
              Check your email
            </Text>
            <Text as="p" level="body-small" className="text-content-layout-3">
              Open the sign-in link sent to {email.trim()}. It returns to this
              RDST application to finish securely.
            </Text>
          </div>
        ) : (
          <div className="mt-8 flex w-full flex-col gap-2.5">
            <Button
              variant="rising"
              modifier="solid"
              fullWidth
              label="Continue with GitHub"
              onClick={() => github.mutate()}
              loading={github.isPending}
              disabled={pending}
            >
              {!github.isPending && <GithubMark />}
              {!github.isPending && <span className="w-2" />}
            </Button>
            <Button
              variant="primary"
              modifier="outline"
              fullWidth
              label="Continue with Google"
              onClick={() => google.mutate()}
              loading={google.isPending}
              disabled={pending}
            >
              {!google.isPending && <GoogleMark />}
              {!google.isPending && <span className="w-2" />}
            </Button>
            <form
              className="mt-3 flex flex-col gap-2.5 text-left"
              onSubmit={(event) => {
                event.preventDefault()
                if (!pending && validateEmail()) magicLink.mutate()
              }}
            >
              <Field.Root className="px-0 py-0">
                <Field.Label htmlFor={emailFieldId}>Email address</Field.Label>
                <Field.Content>
                  <BaseInputText
                    id={emailFieldId}
                    type="email"
                    autoComplete="email"
                    value={email}
                    onChange={(event) => {
                      setEmail(event.target.value)
                      if (emailError) setEmailError(null)
                    }}
                    onBlur={() => email.trim() && validateEmail()}
                    disabled={pending}
                    placeholder="you@example.com"
                    error={!!emailError}
                    aria-invalid={emailError ? true : undefined}
                    aria-describedby={`${emailFieldId}-info`}
                  />
                  <div id={`${emailFieldId}-info`}>
                    <Field.Info
                      errorMessage={emailError ?? undefined}
                      infoMessage="We'll email you a secure sign-in link."
                    />
                  </div>
                </Field.Content>
              </Field.Root>
              <Button
                type="submit"
                variant="primary"
                modifier="outline"
                fullWidth
                label="Send sign-in link"
                loading={magicLink.isPending}
                disabled={!email.trim() || !!emailError || pending}
              />
            </form>
          </div>
        )}
        {error && (
          <div className="mt-4 w-full text-left">
            <Alert tone="quiet" variant="negative" icon="alert" label={error} />
          </div>
        )}
        <Text as="p" level="caption" className="mt-4 text-content-layout-3">
          Signing in turns on AI monitoring and the free AI included with a
          Readyset account for analyses, checks and Ask.
        </Text>
      </div>
    </main>
  )
}
