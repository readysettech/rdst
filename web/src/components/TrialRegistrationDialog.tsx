import { Alert } from '@rs/ui-new/alert'
import { BaseInputText } from '@rs/ui-new/base-input-text'
import { Button } from '@rs/ui-new/button'
import { Icon } from '@rs/ui-new/icon'
import { Modal, ModalContentContainer } from '@rs/ui-new/modal'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import {
  completeAccountRedirect,
  sendAccountMagicLink,
  startAccountGithubOAuth,
  startAccountGoogleOAuth,
} from '../lib/accountAuth'
import { fetchAccountBrowserCallback, startAccountLogin } from '../lib/api'
import { isDesktopRuntime } from '../lib/desktop'
import { invalidateTrialRelatedQueries } from '../lib/trialQueries'
import { TaskDialogContent } from './dialog/TaskDialogContent'

interface AccountLoginDialogProps {
  isOpen: boolean
  onClose: () => void
  onSuccess?: () => void
  callbackLoginId?: string | null
}

function GoogleMark() {
  return (
    <svg aria-hidden="true" className="h-4 w-4" viewBox="0 0 20 20" fill="none">
      <path
        fill="#4285F4"
        d="M19.6 10.227c0-.709-.064-1.39-.182-2.045H10v3.868h5.382a4.6 4.6 0 0 1-1.996 3.018v2.51h3.232c1.891-1.742 2.982-4.305 2.982-7.35Z"
      />
      <path
        fill="#34A853"
        d="M10 20c2.7 0 4.964-.896 6.618-2.423l-3.232-2.509c-.895.6-2.04.955-3.386.955-2.605 0-4.81-1.76-5.595-4.123H1.064v2.59A9.996 9.996 0 0 0 10 20Z"
      />
      <path
        fill="#FBBC05"
        d="M4.405 11.9A5.994 5.994 0 0 1 4.09 10c0-.66.114-1.3.314-1.9V5.509H1.064A9.996 9.996 0 0 0 0 9.999c0 1.615.386 3.142 1.064 4.492l3.34-2.591Z"
      />
      <path
        fill="#EA4335"
        d="M10 3.977c1.468 0 2.786.505 3.823 1.496l2.868-2.868C14.959.99 12.695 0 10 0 6.09 0 2.71 2.24 1.064 5.51l3.34 2.59C5.192 5.736 7.396 3.977 10 3.977Z"
      />
    </svg>
  )
}

function GithubMark() {
  return (
    <svg aria-hidden="true" className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.87c-2.78.6-3.37-1.18-3.37-1.18-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.61.07-.61 1 .07 1.53 1.03 1.53 1.03.9 1.53 2.35 1.09 2.92.83.09-.65.35-1.09.64-1.34-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02A9.58 9.58 0 0 1 12 6.82c.85 0 1.69.11 2.48.34 1.91-1.29 2.75-1.02 2.75-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.68-4.57 4.93.36.31.68.92.68 1.85v2.77c0 .27.18.58.69.48A10 10 0 0 0 12 2Z" />
    </svg>
  )
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
  const [email, setEmail] = useState('')
  const [linkSent, setLinkSent] = useState(false)
  const [complete, setComplete] = useState(false)
  const [desktopLoginId, setDesktopLoginId] = useState<string | null>(null)
  const [callbackPollingError, setCallbackPollingError] = useState<
    string | null
  >(null)
  const callbackStarted = useRef(false)

  const finish = async () => {
    setComplete(true)
    await invalidateTrialRelatedQueries(queryClient)
    onSuccess?.()
  }

  const magicLinkMutation = useMutation({
    mutationFn: async () => {
      const context = await startAccountLogin(accountCallbackUrl())
      await sendAccountMagicLink(context, email.trim())
      if (isDesktopRuntime()) setDesktopLoginId(context.login_id)
    },
    onSuccess: () => setLinkSent(true),
  })

  const googleMutation = useMutation({
    mutationFn: async () => {
      const context = await startAccountLogin(accountCallbackUrl())
      await startAccountGoogleOAuth(context)
      if (isDesktopRuntime()) setDesktopLoginId(context.login_id)
    },
  })

  const githubMutation = useMutation({
    mutationFn: async () => {
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
    setLinkSent(false)
    setComplete(false)
    setDesktopLoginId(null)
    setCallbackPollingError(null)
    callbackStarted.current = false
    magicLinkMutation.reset()
    googleMutation.reset()
    githubMutation.reset()
    callbackMutation.reset()
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

  return (
    <Modal open={isOpen} onOpenChange={(open) => !open && handleClose()}>
      <ModalContentContainer open={isOpen}>
        <TaskDialogContent
          size="base"
          icon="sparkles"
          title={complete ? 'Signed in to Readyset' : 'Sign in to Readyset'}
          description={
            complete
              ? 'Readyset-hosted AI is ready to use.'
              : 'Create an account or sign in to use Readyset-hosted AI for free.'
          }
          bodyClassName="space-y-4"
          footer={
            <HStack className="justify-end gap-3 items-center w-full">
              {!complete && (
                <Button
                  variant="primary"
                  modifier="ghost"
                  label="Cancel"
                  onClick={handleClose}
                  disabled={pending}
                />
              )}
              {!complete && !callbackLoginId && !linkSent && (
                <Button
                  variant="rising"
                  label="Send sign-in link"
                  icon="arrow-right"
                  iconPosition="right"
                  onClick={() => magicLinkMutation.mutate()}
                  loading={magicLinkMutation.isPending}
                  disabled={!email.trim() || pending}
                />
              )}
              {complete && (
                <Button variant="primary" label="Done" onClick={handleClose} />
              )}
            </HStack>
          }
        >
          {(error || callbackPollingError) && (
            <Alert
              variant="negative"
              modifier="outline"
              label={error ?? callbackPollingError ?? 'Readyset sign-in failed'}
            />
          )}

          {complete ? (
            <div className="py-4 flex flex-col items-center text-center gap-3">
              <div className="w-14 h-14 rounded-2xl bg-surface-positive-soft flex items-center justify-center">
                <Icon
                  name="tick"
                  label="Success"
                  className="w-7 h-7 text-content-positive-soft"
                />
              </div>
              <VStack className="gap-1 items-center">
                <Text level="subtitle-2" className="text-content-layout-1">
                  Readyset account connected
                </Text>
                <Text level="body-small" className="text-content-layout-3">
                  Readyset-hosted AI is ready to use.
                </Text>
              </VStack>
            </div>
          ) : callbackLoginId ? (
            <VStack className="gap-3 items-center py-6">
              <Text level="body-small" className="text-content-layout-3">
                Completing your Readyset sign-in…
              </Text>
            </VStack>
          ) : linkSent ? (
            <VStack className="gap-2 items-stretch py-3">
              <Text level="subtitle-2" className="text-content-layout-1">
                Check your email
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Open the sign-in link sent to {email.trim()}. It returns to this
                RDST application to finish securely.
              </Text>
            </VStack>
          ) : (
            <VStack className="gap-4 items-stretch py-1">
              <Button
                variant="primary"
                modifier="solid"
                size="large"
                fullWidth
                label="Continue with Google"
                onClick={() => googleMutation.mutate()}
                loading={googleMutation.isPending}
                disabled={pending}
              >
                {!googleMutation.isPending && <GoogleMark />}
                {!googleMutation.isPending && <span className="w-1" />}
              </Button>
              <Button
                variant="primary"
                modifier="outline"
                size="large"
                fullWidth
                label="Continue with GitHub"
                onClick={() => githubMutation.mutate()}
                loading={githubMutation.isPending}
                disabled={pending}
              >
                {!githubMutation.isPending && <GithubMark />}
                {!githubMutation.isPending && <span className="w-1" />}
              </Button>
              <HStack className="w-full items-center gap-3">
                <div className="h-px flex-1 bg-border-layout-1" />
                <Text level="caption" className="text-content-layout-3">
                  or continue with email
                </Text>
                <div className="h-px flex-1 bg-border-layout-1" />
              </HStack>
              <VStack className="gap-1 items-stretch">
                <Text level="label-small" className="text-content-layout-1">
                  Email address
                </Text>
                <BaseInputText
                  type="email"
                  aria-label="Email address"
                  autoComplete="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  disabled={pending}
                  placeholder="you@example.com"
                />
                <Text level="caption" className="text-content-layout-3">
                  We’ll email you a secure sign-in link.
                </Text>
              </VStack>
            </VStack>
          )}
        </TaskDialogContent>
      </ModalContentContainer>
    </Modal>
  )
}

// Keep the old export while call sites migrate. The UI no longer offers trial
// registration or trial tokens.
export const TrialRegistrationDialog = AccountLoginDialog
