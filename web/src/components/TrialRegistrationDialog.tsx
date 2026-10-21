import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Field } from '@rs/ui-new/field'
import { ModalDescription } from '@rs/ui-new/modal'
import { VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { SignInTrustNote } from '../features/onboarding/TrustNote'
import {
  completeAccountRedirect,
  sendAccountMagicLink,
  startAccountGoogleOAuth,
} from '../lib/accountAuth'
import { isSignedInAccount } from '../lib/accountStatus'
import { type SigninSource, trackEvent } from '../lib/analytics'
import {
  fetchAccountBrowserCallback,
  fetchAccountStatus,
  startAccountLogin,
} from '../lib/api'
import { isDesktopRuntime } from '../lib/desktop'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { DialogShell } from './DialogShell'
import { isValidEmail, normalizeEmail } from './emailValidation'

interface AccountLoginDialogProps {
  isOpen: boolean
  onClose: () => void
  onSuccess?: () => void
  callbackLoginId?: string | null
  /** Where the sign-in was asked for, sent with the sign-in events. */
  source?: SigninSource
}

// The mark on the sign-in page, so the dialog offers the same button.
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

const RETURN_KEY = 'rdst_login_return_to'

/** Remember the page sign-in started on, so the sign-in tab can come back to it. */
function rememberReturn() {
  try {
    const here = `${window.location.pathname}${window.location.search}`
    if (!here.startsWith('/account-login'))
      localStorage.setItem(RETURN_KEY, here)
  } catch {
    /* the sign-in tab lands on Home instead */
  }
}

/** Where a finished sign-in goes back to: the page it started on, or Home. */
export function takeLoginReturn(): string {
  try {
    const path = localStorage.getItem(RETURN_KEY)
    localStorage.removeItem(RETURN_KEY)
    return path?.startsWith('/') && !path.startsWith('//') ? path : '/'
  } catch {
    return '/'
  }
}

function localCallbackUrl(): string {
  return new URL('/account-login', window.location.origin).toString()
}

function accountCallbackUrl(): string {
  return isDesktopRuntime()
    ? new URL('/api/account/oauth/callback', window.location.origin).toString()
    : localCallbackUrl()
}

export function AccountLoginDialog({
  isOpen,
  onClose,
  onSuccess,
  callbackLoginId = null,
  source,
}: AccountLoginDialogProps) {
  const queryClient = useQueryClient()
  const emailFieldId = useId()
  const [email, setEmail] = useState('')
  const [emailError, setEmailError] = useState<string | null>(null)
  const [linkSent, setLinkSent] = useState(false)
  const [complete, setComplete] = useState(false)
  const [desktopLoginId, setDesktopLoginId] = useState<string | null>(null)
  const [callbackPollingError, setCallbackPollingError] = useState<
    string | null
  >(null)
  // A dead callback link is abandoned locally: the `login_id` prop stays in the
  // URL, so without this the dialog keeps claiming the sign-in is in progress
  // underneath the error that ended it. [A-15]
  const [callbackAbandoned, setCallbackAbandoned] = useState(false)
  const callbackStarted = useRef(false)
  const emailInputRef = useRef<HTMLInputElement>(null)

  const finish = async () => {
    setComplete(true)
    trackEvent('signin_completed', { source })
    await invalidateTrialRelatedQueries(queryClient)
    onSuccess?.()
  }

  const finishRef = useRef(finish)
  finishRef.current = finish

  const magicLinkMutation = useMutation({
    mutationFn: async () => {
      trackEvent('signin_started', { method: 'email', source })
      rememberReturn()
      const context = await startAccountLogin(accountCallbackUrl())
      await sendAccountMagicLink(context, email.trim())
      if (isDesktopRuntime()) setDesktopLoginId(context.login_id)
    },
    onSuccess: () => setLinkSent(true),
  })

  const googleMutation = useMutation({
    mutationFn: async () => {
      trackEvent('signin_started', { method: 'google', source })
      rememberReturn()
      const context = await startAccountLogin(accountCallbackUrl())
      await startAccountGoogleOAuth(context)
      if (isDesktopRuntime()) setDesktopLoginId(context.login_id)
    },
  })

  const callbackMutation = useMutation({
    mutationFn: ({
      loginId,
      callbackUrl,
    }: {
      loginId: string
      callbackUrl?: string
    }) => completeAccountRedirect(loginId, callbackUrl),
    onSuccess: finish,
  })

  // In a browser, the emailed link signs in from another tab. This one notices, so the
  // user carries on right where they were (an open dialog keeps its state).
  useEffect(() => {
    if (!isOpen || !linkSent || isDesktopRuntime() || complete) return
    let cancelled = false
    const id = window.setInterval(async () => {
      try {
        const status = await fetchAccountStatus()
        if (!cancelled && isSignedInAccount(status)) {
          window.clearInterval(id)
          void finishRef.current()
        }
      } catch {
        /* keep waiting for the link */
      }
    }, 2_000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [isOpen, linkSent, complete])

  useEffect(() => {
    if (!isOpen || !callbackLoginId || callbackStarted.current) return
    callbackStarted.current = true
    callbackMutation.mutate({ loginId: callbackLoginId })
  }, [callbackLoginId, callbackMutation, isOpen])

  useEffect(() => {
    if (!isOpen || !desktopLoginId || callbackStarted.current) return
    let cancelled = false
    let timer: number | undefined

    const poll = async () => {
      try {
        const result = await fetchAccountBrowserCallback(desktopLoginId)
        if (cancelled || callbackStarted.current) return
        if (result.state === 'pending') {
          timer = window.setTimeout(poll, 500)
          return
        }

        const callbackUrl = new URL(localCallbackUrl())
        if (result.code) callbackUrl.searchParams.set('code', result.code)
        if (result.error) callbackUrl.searchParams.set('error', result.error)
        if (result.error_description) {
          callbackUrl.searchParams.set(
            'error_description',
            result.error_description
          )
        }
        callbackStarted.current = true
        callbackMutation.mutate({
          loginId: desktopLoginId,
          callbackUrl: callbackUrl.toString(),
        })
      } catch (error) {
        if (!cancelled) {
          setCallbackPollingError(
            error instanceof Error ? error.message : 'Readyset sign-in failed'
          )
        }
      }
    }

    timer = window.setTimeout(poll, 0)
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [callbackMutation, desktopLoginId, isOpen])

  const reset = () => {
    setEmail('')
    setEmailError(null)
    setLinkSent(false)
    setComplete(false)
    setDesktopLoginId(null)
    setCallbackPollingError(null)
    setCallbackAbandoned(false)
    callbackStarted.current = false
    magicLinkMutation.reset()
    googleMutation.reset()
    callbackMutation.reset()
  }

  // The address is checked on blur and again before the request, so an obvious
  // typo is answered under the field rather than by a red block at the top of
  // the dialog, 285px away from it. [A-16]
  const validateEmailField = () => {
    const valid = isValidEmail(normalizeEmail(email))
    setEmailError(valid ? null : 'Enter a valid email address.')
    return valid
  }

  const handleClose = () => {
    if (!complete)
      trackEvent('signin_abandoned', {
        stage: linkSent
          ? 'link_sent'
          : callbackLoginId || desktopLoginId
            ? 'callback'
            : 'choose',
      })
    reset()
    onClose()
  }

  const mutationError =
    magicLinkMutation.error ?? googleMutation.error ?? callbackMutation.error
  const error = mutationError instanceof Error ? mutationError.message : null
  const pending =
    magicLinkMutation.isPending ||
    googleMutation.isPending ||
    callbackMutation.isPending
  const callbackFailed =
    !!callbackLoginId &&
    !callbackAbandoned &&
    (callbackMutation.isError || !!callbackPollingError)
  const completingCallback =
    !!callbackLoginId && !callbackAbandoned && !callbackFailed
  const startOver = () => {
    setCallbackAbandoned(true)
    setCallbackPollingError(null)
    callbackStarted.current = false
    callbackMutation.reset()
  }

  const showProviders =
    !complete && !completingCallback && !callbackFailed && !linkSent

  return (
    <DialogShell
      open={isOpen}
      onClose={handleClose}
      title={complete ? 'Signed in to Readyset' : 'Sign in to Readyset'}
      // Radix would open with focus on the first control, which here is a
      // one-Enter trip into a third-party OAuth redirect. The email field
      // is the safe landing spot. [F-45]
      onOpenAutoFocus={(event) => {
        event.preventDefault()
        emailInputRef.current?.focus()
      }}
      action={
        <>
          {!complete && (
            <Button
              variant="primary"
              modifier="ghost"
              label="Cancel"
              onClick={handleClose}
              disabled={pending}
            />
          )}
          {callbackFailed && (
            <Button
              variant="primary"
              modifier="solid"
              label="Start over"
              onClick={startOver}
            />
          )}
          {showProviders && (
            <Button
              variant="primary"
              modifier="solid"
              label="Send sign-in link"
              onClick={() => {
                if (validateEmailField()) magicLinkMutation.mutate()
              }}
              loading={magicLinkMutation.isPending}
              disabled={!email.trim() || !!emailError || pending}
            />
          )}
          {complete && (
            <Button
              variant="primary"
              modifier="solid"
              label="Done"
              onClick={handleClose}
            />
          )}
        </>
      }
    >
      <VStack className="items-stretch gap-4">
        {showProviders && (
          <ModalDescription>
            New here? This creates your Readyset account.
          </ModalDescription>
        )}
        {(error || callbackPollingError) && (
          <Alert
            tone="quiet"
            variant="negative"
            icon="alert"
            label={
              callbackFailed
                ? 'This sign-in link has expired or was already used.'
                : (error ?? callbackPollingError ?? 'Readyset sign-in failed')
            }
          />
        )}

        {complete ? (
          <ModalDescription>
            Readyset account connected. Your free AI is ready to use.
          </ModalDescription>
        ) : completingCallback ? (
          <ModalDescription>
            Completing your Readyset sign-in...
          </ModalDescription>
        ) : callbackFailed ? (
          <VStack className="items-stretch gap-1">
            <Text level="body-small" className="text-content-layout-1">
              Sign-in could not be completed
            </Text>
            <ModalDescription>
              Start over to request a fresh sign-in link or use a provider.
            </ModalDescription>
          </VStack>
        ) : linkSent ? (
          <VStack className="items-stretch gap-1">
            <Text level="body-small" className="text-content-layout-1">
              Check your email
            </Text>
            <ModalDescription>
              Open the sign-in link sent to {email.trim()}. It returns to this
              RDST application to finish securely.
            </ModalDescription>
          </VStack>
        ) : (
          <VStack className="items-stretch gap-2.5">
            <Button
              variant="primary"
              modifier="outline"
              fullWidth
              label="Continue with Google"
              onClick={() => googleMutation.mutate()}
              loading={googleMutation.isPending}
              disabled={pending}
            >
              {!googleMutation.isPending && <GoogleMark />}
              {!googleMutation.isPending && <span className="w-2" />}
            </Button>
            <Text as="p" level="caption" className="text-content-layout-3">
              A Readyset account is free and comes with AI for monitoring,
              analyses, checks and Ask.
            </Text>
            <Field.Root className="mt-2 px-0 py-0">
              <Field.Label htmlFor={emailFieldId}>Email address</Field.Label>
              <Field.Content>
                <BaseInputText
                  ref={emailInputRef}
                  id={emailFieldId}
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => {
                    setEmail(event.target.value)
                    if (emailError) setEmailError(null)
                  }}
                  // An empty field isn't checked on blur: leaving it for
                  // Google isn't a mistake.
                  onBlur={() => {
                    if (email.trim()) validateEmailField()
                  }}
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
            <SignInTrustNote className="mt-1" />
          </VStack>
        )}
      </VStack>
    </DialogShell>
  )
}

// Keep the old export while call sites migrate. The UI no longer offers trial
// registration or trial tokens.
export const TrialRegistrationDialog = AccountLoginDialog
