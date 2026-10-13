import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Field } from '@rs/ui-new/field'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalDescription,
  ModalTitle,
} from '@rs/ui-new/modal'
import { Scrollable } from '@rs/ui-new/scrollable'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import {
  completeAccountRedirect,
  sendAccountMagicLink,
  startAccountGithubOAuth,
  startAccountGoogleOAuth,
} from '../lib/accountAuth'
import {
  fetchAccountBrowserCallback,
  fetchAccountStatus,
  startAccountLogin,
} from '../lib/api'
import { isDesktopRuntime } from '../lib/desktop'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { isValidEmail, normalizeEmail } from './emailValidation'

interface AccountLoginDialogProps {
  isOpen: boolean
  onClose: () => void
  onSuccess?: () => void
  callbackLoginId?: string | null
}

// The marks on the sign-in page, so the dialog offers the same two buttons.
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
    await invalidateTrialRelatedQueries(queryClient)
    onSuccess?.()
  }

  const finishRef = useRef(finish)
  finishRef.current = finish

  const magicLinkMutation = useMutation({
    mutationFn: async () => {
      rememberReturn()
      const context = await startAccountLogin(accountCallbackUrl())
      await sendAccountMagicLink(context, email.trim())
      if (isDesktopRuntime()) setDesktopLoginId(context.login_id)
    },
    onSuccess: () => setLinkSent(true),
  })

  const googleMutation = useMutation({
    mutationFn: async () => {
      rememberReturn()
      const context = await startAccountLogin(accountCallbackUrl())
      await startAccountGoogleOAuth(context)
      if (isDesktopRuntime()) setDesktopLoginId(context.login_id)
    },
  })

  const githubMutation = useMutation({
    mutationFn: async () => {
      rememberReturn()
      const context = await startAccountLogin(accountCallbackUrl())
      await startAccountGithubOAuth(context)
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
        if (!cancelled && status.signed_in) {
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
    githubMutation.reset()
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
    reset()
    onClose()
  }

  const mutationError =
    magicLinkMutation.error ??
    googleMutation.error ??
    githubMutation.error ??
    callbackMutation.error
  const error = mutationError instanceof Error ? mutationError.message : null
  const pending =
    magicLinkMutation.isPending ||
    googleMutation.isPending ||
    githubMutation.isPending ||
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
    <Modal open={isOpen} onOpenChange={(open) => !open && handleClose()}>
      <ModalContentContainer open={isOpen}>
        <ModalContent
          tone="quiet"
          // Radix would open with focus on the first control, which here is a
          // one-Enter trip into a third-party OAuth redirect. The email field
          // is the safe landing spot. [F-45]
          onOpenAutoFocus={(event) => {
            event.preventDefault()
            emailInputRef.current?.focus()
          }}
        >
          <div className="shrink-0 pt-4.5 pr-14 pl-6">
            <ModalTitle>
              {complete ? 'Signed in to Readyset' : 'Sign in to Readyset'}
            </ModalTitle>
          </div>
          <Scrollable rootClassName="min-h-0 flex-1" className="px-6 pt-2 pb-5">
            <VStack className="items-stretch gap-4">
              {(error || callbackPollingError) && (
                <Alert
                  tone="quiet"
                  variant="negative"
                  icon="alert"
                  label={
                    callbackFailed
                      ? 'This sign-in link has expired or was already used.'
                      : (error ??
                        callbackPollingError ??
                        'Readyset sign-in failed')
                  }
                />
              )}

              {complete ? (
                <ModalDescription>
                  Readyset account connected. Your included AI is ready to use.
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
                    Start over to request a fresh sign-in link or use a
                    provider.
                  </ModalDescription>
                </VStack>
              ) : linkSent ? (
                <VStack className="items-stretch gap-1">
                  <Text level="body-small" className="text-content-layout-1">
                    Check your email
                  </Text>
                  <ModalDescription>
                    Open the sign-in link sent to {email.trim()}. It returns to
                    this RDST application to finish securely.
                  </ModalDescription>
                </VStack>
              ) : (
                <VStack className="items-stretch gap-2.5">
                  <Button
                    variant="rising"
                    modifier="solid"
                    fullWidth
                    label="Continue with GitHub"
                    onClick={() => githubMutation.mutate()}
                    loading={githubMutation.isPending}
                    disabled={pending}
                  >
                    {!githubMutation.isPending && <GithubMark />}
                    {!githubMutation.isPending && <span className="w-2" />}
                  </Button>
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
                  <ModalDescription className="text-caption text-content-layout-3">
                    Signing in turns on AI monitoring and the free AI included
                    with a Readyset account for analyses, checks and Ask.
                  </ModalDescription>
                  <Field.Root className="mt-2 px-0 py-0">
                    <Field.Label htmlFor={emailFieldId}>
                      Email address
                    </Field.Label>
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
                        onBlur={validateEmailField}
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
                </VStack>
              )}
            </VStack>
          </Scrollable>
          <HStack className="shrink-0 items-center justify-end gap-3 border-t border-border-layout-2 px-6 py-3.5">
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
                modifier="outline"
                label="Start over"
                onClick={startOver}
              />
            )}
            {showProviders && (
              <Button
                variant="primary"
                modifier="outline"
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
          </HStack>
        </ModalContent>
      </ModalContentContainer>
    </Modal>
  )
}

// Keep the old export while call sites migrate. The UI no longer offers trial
// registration or trial tokens.
export const TrialRegistrationDialog = AccountLoginDialog
